# Local Docker infrastructure

本目录只用于 CustomerCare AI 的“容器基础设施 + 宿主机应用”开发模式，不是生产部署配置。
需要一次启动 API、两个 Worker、Web 和基础设施时，使用仓库根目录的
[`compose.yaml`](../../compose.yaml) 与
[`完整 Compose Runbook`](../../docs/runbooks/complete-local-compose.md)。

当前只启动 PostgreSQL、Redis 和 MinIO。前端、API、Worker、监控及数据库迁移不属于这份 Compose 配置。
它与完整 Compose 默认使用相同主机端口，因此切换模式前应先停止另一套拓扑；两者的
project name 和命名卷不同，数据不会自动同步。

## 服务与主机端口

| 服务 | 主机地址 | 容器地址 | 用途 |
|---|---|---|---|
| PostgreSQL | 127.0.0.1:15432 | postgres:5432 | P0 事务数据 |
| Redis | 127.0.0.1:16379 | redis:6379 | 后续缓存、限流和任务 |
| MinIO API | 127.0.0.1:19000 | minio:9000 | 本地 S3 兼容对象存储 |
| MinIO Console | 127.0.0.1:19001 | minio:9001 | 本地管理界面 |

所有端口都只绑定到 `127.0.0.1`，不会直接监听局域网网卡。

## 首次启动

所有命令都从仓库根目录执行。先创建本机配置：

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 24
openssl rand -hex 24
```

两次生成的随机值不要复用：分别写入 `.env` 的 `POSTGRES_PASSWORD` 和 `MINIO_ROOT_PASSWORD`。同时把 PostgreSQL 密码同步到 `DATABASE_URL`；不要提交或分享 `.env`。

随后验证配置并启动服务：

```bash
docker compose --env-file .env -f infra/compose/compose.dev.yaml config --quiet
docker compose --env-file .env -f infra/compose/compose.dev.yaml up -d --wait
docker compose --env-file .env -f infra/compose/compose.dev.yaml ps
docker compose --env-file .env -f infra/compose/compose.dev.yaml logs --tail=100
```

预期 `ps` 中三个服务均为 `healthy`。MinIO Console 地址为 <http://127.0.0.1:19001>，登录信息来自本机 `.env` 的 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD`。

pgvector 镜像已经包含 `vector` 扩展；步骤 28 的 Alembic 迁移会显式执行
`CREATE EXTENSION vector`，并创建知识导入、文档版本和 32 维 chunk 向量表。

## 停止与数据卷

普通停止会删除容器和默认网络，但保留三个命名卷：

```bash
docker compose --env-file .env -f infra/compose/compose.dev.yaml down
```

再次执行 `up -d --wait` 时，PostgreSQL、Redis 和 MinIO 会继续使用原来的数据。Redis 已启用 AOF 持久化。

以下命令会永久删除三个本地数据卷及其中数据，只能在明确需要重置全部本地数据时使用：

```bash
docker compose --env-file .env -f infra/compose/compose.dev.yaml down -v
```

## 本地开发边界

- 三个服务均使用固定镜像版本，不使用 `latest`。
- 本配置没有生产级 TLS、密钥管理、备份、高可用或公网防护。
- `.env.example` 只能放占位值；真实密码只保存在被 Git 忽略的 `.env`。
- 完整 Compose 改用 Git 忽略的文件型 secrets；本文件保留 `.env` 密码只是为了兼容宿主机
  开发模式，不应把这套注入方式复制到 Staging/生产。
