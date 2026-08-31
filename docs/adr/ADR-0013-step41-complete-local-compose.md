# ADR-0013：完整本地 Compose 采用一次性迁移引导、文件密钥与显式持久化边界

- 状态：已采纳
- 日期：2026-08-28

## 背景

步骤 1–40 可以在宿主机和 CI 中运行，但仓库根目录没有把 API、两个 Worker、Web、
PostgreSQL、Redis 和 MinIO 组成同一个可重复启动的拓扑。基础设施 Compose 只服务于
宿主机开发，不能证明应用镜像可构建、依赖会按健康状态启动、Worker 能持续运行，或普通
停启不会清空数据。

完整 Compose 仍是本地合成验收环境，不是生产部署。把个人 `.env` 中的真实模型 Key
自动传入容器会产生意外数据外发和费用；把密码写进 Compose、镜像层或 Git 同样不可接受。
另外，`docker compose down -v` 是不可恢复的数据删除，不能作为日常停止命令。

## 决策

- 根目录 `compose.yaml` 是完整本地验收入口；`infra/compose/compose.dev.yaml` 继续保留为
  “容器基础设施 + 宿主机 API/Web”开发入口。两套拓扑默认占用相同基础设施端口，不能同时
  启动，除非显式改端口和 project name。
- API、Worker、Web 使用锁文件、多阶段 Dockerfile 和固定基础镜像 digest。API/Worker 以
  UID/GID 10001 运行，Web 以镜像内 `node` 用户运行；应用容器启用只读根文件系统、临时
  `/tmp`、`no-new-privileges`、能力删除和有界日志。
- `migrate` 在 PostgreSQL 健康后运行 `alembic upgrade head`；`bootstrap` 只在迁移成功后
  检查所有应用表。只有全部为空时才在单个事务中加载合成 world；任一应用表已有数据就
  保留整个数据库，不尝试补写或覆盖部分状态。API 和 Worker 只在 bootstrap 成功后启动，
  Web 再等待 API readiness。
- PostgreSQL、Redis、MinIO 和可选观测服务使用命名卷。普通 `down` 保留卷；删除卷必须
  通过 `scripts/reset-compose-data.sh`，并输入与 project name 绑定的确认口令。
- PostgreSQL、MinIO 和 Grafana 密码由 `scripts/init-compose-secrets.sh` 生成到 Git 忽略且
  权限为 0600 的本机文件，再作为 Compose secrets 只读挂载。Python entrypoint 从文件构造
  URL-encoded `DATABASE_URL`，不把密钥写进 Dockerfile、build arg 或 Compose 环境配置。
- 基础 Compose 强制 `LLM_PROVIDER=fake`，不读取个人 `.env` 中的模型 Key。真实 Provider
  必须在后续独立 Staging 配置中显式决定密钥来源、数据外发、费用和限额，不能通过本地
  默认值意外启用。
- Prometheus/Grafana 放在 `observability` profile，仅用于验收。Prometheus 当前只自抓取；
  Grafana只预配置该数据源、关闭匿名访问和默认插件联网安装。这不表示应用指标、Dashboard、
  告警、SLO、Collector 或值班已经落地。

## 后果

正面结果：

- 新环境可以用同一套镜像、迁移、引导、健康依赖和合成数据启动完整业务界面。
- Worker 空队列不再导致进程退出；`--once` 仍保留给测试和手工批处理。
- 普通停机/重启与销毁数据的意图被分开，敏感文件也不会进入构建上下文或 Git。
- 可选观测容器不会增加日常 M1 开发负担，且其镜像和数据源配置可重复。

代价与限制：

- 首次构建和可观测性镜像拉取需要额外时间与磁盘；Grafana 镜像尤其较大。
- bootstrap 有意 fail-safe：发现部分数据时不会自动“修复”或重新 seed。若首次初始化中断，
  应先检查日志和数据，再由操作者显式重置本地卷，不能自动清库。
- Compose secrets 保护了配置和镜像边界，但不是 Vault/KMS，也不提供轮换、审计或远程秘密
  分发。容器内业务进程仍必须在运行时持有其所需凭据。
- 单机 Compose 没有 TLS、HA、备份恢复、资源容量证明、OIDC、真实 OMS/支付契约、
  Outbox/DLQ、应用指标、告警或故障演练，因此企业生产仍为 **NO-GO**。

## 拒绝的替代方案

- 把密码和模型 Key 放进 `.env` 后直接注入所有容器：简单但会扩大 `docker inspect`、错误
  日志、构建参数和误调用真实 Provider 的风险。
- API 启动时自动迁移并无条件 seed：多副本时存在竞态，也可能覆盖已有业务数据。
- 日常使用 `down -v` 保证“干净”：会把正常停启变成默认数据删除，无法验证持久性。
- 默认启动 Grafana/Prometheus：增加资源占用，却不能在没有应用指标和告警规则时形成生产
  观测能力。

## 验证

步骤 41 使用独立 project name 和端口完成：三套应用镜像构建、空库迁移和合成初始化、
API/Web 烟测、非 root 身份、两个持续 Worker、完整 `down`/`up` 后探针工单保留、bootstrap
输出 `preserved data`，以及 Prometheus/Grafana profile 健康和数据源 provisioning。完整命令、
故障处置和仍未完成的边界见
[`docs/runbooks/complete-local-compose.md`](../runbooks/complete-local-compose.md)。
