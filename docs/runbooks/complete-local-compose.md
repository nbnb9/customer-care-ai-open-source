# 步骤 41：完整本地 Compose Runbook

本手册用于在单台开发机上以合成数据启动完整 CustomerCare AI。它是 Staging 风格的本地
验收入口，不是生产部署方案。需要宿主机热更新时，继续使用
[`infra/compose/compose.dev.yaml`](../../infra/compose/compose.dev.yaml) 和原有 API/Web 命令。

## 前置条件与边界

- Docker Desktop/Engine 必须已启动并支持 Docker Compose v2；宿主机还需提供 `openssl`，
  用于生成仅供本机使用的随机 secrets。
- 默认只绑定 `127.0.0.1`；Demo Header 身份仍可伪造，不得暴露到局域网或公网。
- 默认模型固定为 Fake，不读取个人 `.env` 中的 `LLM_API_KEY`，不会调用 DeepSeek 或其他
  外部模型。
- `infra/compose/secrets/` 中除 `.gitignore` 外的文件是本机秘密，不得提交、截图或粘贴到
  日志。脚本会以 umask 077 创建它们，并在已有非空文件时保留原值。
- 完整 Compose 和基础设施-only Compose 默认共用 15432/16379/19000/19001 端口。先停止
  另一套拓扑，或显式覆盖端口和 `COMPOSE_PROJECT_NAME`。

## 首次启动

从仓库根目录执行：

```bash
./scripts/init-compose-secrets.sh
docker compose config --quiet
docker compose up -d --build --wait
./scripts/compose-smoke.sh
docker compose ps -a
```

预期结果：

- `migrate` 和 `bootstrap` 为 `Exited (0)`；它们是成功完成的一次性任务，不应保持运行。
- PostgreSQL、Redis、MinIO、API、Web 正常运行；两个 Worker 即使队列为空也持续轮询。
- Smoke 输出 `compose smoke passed`。
- Web 为 <http://127.0.0.1:3000>，API 为 <http://127.0.0.1:8000>，MinIO Console 为
  <http://127.0.0.1:19001>。

启动顺序是 PostgreSQL healthy → migration → 空库 bootstrap → API/Worker → API healthy →
Web。bootstrap 只有在所有应用表都为空时加载合成数据；已有任意应用数据时会输出
`bootstrap: preserved data`，不会覆盖或补写数据库。

## 日常查看、停止与重启

```bash
docker compose ps -a
docker compose logs --tail=200 api knowledge-worker after-sales-worker
docker compose down
docker compose up -d --wait
./scripts/compose-smoke.sh
```

普通 `down` 只移除容器和网络，保留 PostgreSQL、Redis、MinIO 和 Web cache 命名卷。再次
启动后，手工创建的工单和其他持久状态必须仍然存在。不要把 `migrate/bootstrap` 的正常
`Exited (0)` 误判为崩溃。

如果只希望停止而保留容器，可使用 `docker compose stop`；随后使用
`docker compose start`。代码或 `NEXT_PUBLIC_*` 构建参数变化后应重新执行
`docker compose up -d --build --wait`。

## 显式重置所有本地 Compose 数据

先让脚本显示当前 project name 对应的确认口令：

```bash
./scripts/reset-compose-data.sh
```

默认 project 的实际删除命令为：

```bash
./scripts/reset-compose-data.sh \
  --confirm customer-care-ai-full/delete-local-volumes
```

这会永久删除该 Compose project 的 PostgreSQL、Redis、MinIO、Web cache、Prometheus 和
Grafana 卷；本机 secret 文件不会被删除。下一次 `up` 会迁移空数据库并重新加载合成 world。
自定义 `COMPOSE_PROJECT_NAME` 时，确认口令也必须使用同一名称。不要对不确定来源或包含
需要保留数据的 project 执行此命令。

## 可选 Prometheus/Grafana 验收

日常开发不启动观测 profile。需要验收时执行：

```bash
docker compose --profile observability up -d --wait
docker compose --profile observability ps -a
curl --fail http://127.0.0.1:19090/-/ready
curl --fail http://127.0.0.1:13000/api/health
```

Prometheus 为 <http://127.0.0.1:19090>，Grafana 为 <http://127.0.0.1:13000>。Grafana 用户名
默认为 `customer-care-admin`；可用以下命令把本机密码复制到剪贴板而不打印到终端：

```bash
pbcopy < infra/compose/secrets/grafana_admin_password
```

Grafana 会自动配置 `Local Prometheus` 数据源并关闭匿名登录与默认插件下载。当前
`prometheus.yml` 只抓取 Prometheus 自身，因为应用还没有 `/metrics` exporter；步骤 39 的
本地 Trace Viewer 仍存储在 PostgreSQL。不要据此宣称已有应用 Dashboard、SLO、告警或
生产 Collector。

## 宿主机开发模式

需要 Next/FastAPI 热更新时，不必运行完整应用容器：

```bash
docker compose down
docker compose --env-file .env -f infra/compose/compose.dev.yaml up -d --wait
```

然后按根 README 在宿主机运行 API、Worker 和 Web。两种模式使用不同 Compose project 和
不同命名卷；切换不会自动复制数据。若必须并行运行，至少覆盖完整栈的
`POSTGRES_HOST_PORT`、`REDIS_PORT`、`MINIO_API_PORT`、`MINIO_CONSOLE_PORT`、`API_PORT`、
`WEB_PORT`、`NEXT_PUBLIC_API_BASE_URL`、`COMPOSE_CORS_ALLOWED_ORIGINS` 和
`COMPOSE_PROJECT_NAME`。

## 常见故障

- `secret ... not found`：先运行 `./scripts/init-compose-secrets.sh`，再运行 config/up。
- `port is already allocated`：检查 `docker compose ls` 和 `docker ps`；停止另一套本地栈，
  或使用独立 project name 与端口。不要删除未知卷来解决端口冲突。
- `migrate` 非 0：运行 `docker compose logs migrate postgres`。先修复迁移或连接问题；不要
  跳过 migration 强行启动 API。
- `bootstrap` 非 0：运行 `docker compose logs bootstrap`。部分数据存在时它应保留数据；
  只有确认该 project 全部为可丢弃合成数据后，才使用受保护的 reset 脚本。
- Web unhealthy：运行 `docker compose logs web`。Next standalone 必须包含动态 SWC helper；
  依赖或 Next 版本变化后同时运行 frozen install、生产构建和容器启动验收。
- Worker 退出：查看对应 Worker 日志和数据库连接；空队列本身不应退出。一次性批处理必须
  显式传 `--once`。
- Grafana provisioning 异常：确认 datasource 目录只读挂载、Prometheus healthy，且没有
  把整个镜像自带 provisioning 根目录覆盖掉。

## 验收和生产 NO-GO 清单

步骤 41 至少复核：

```bash
docker compose config --quiet
docker compose --profile observability config --quiet
./scripts/compose-smoke.sh
cd apps/api && uv run pytest -q
cd ../web && pnpm lint && pnpm typecheck && pnpm test:unit && pnpm build
```

当前实现仍缺真实 OIDC、TLS/不可信网络入口、真实 OMS/支付契约、业务级跨请求去重、
Outbox/队列/DLQ、Vault/KMS、镜像漏洞扫描与签名、备份恢复、应用指标、集中日志、SLO/告警、
容量/负载和故障演练。因此步骤 41 可以判定“完整本地 Compose 基线 GO”，不能判定企业生产
GO，也不能接入真实客户数据。
