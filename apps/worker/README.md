# Knowledge worker

步骤 28 的 Worker 以独立进程运行，不在 HTTP 请求中执行文本处理或
Embedding。实际实现位于 API 包的 `app.modules.knowledge.worker`，便于与
Alembic/SQLAlchemy 契约共享；步骤 41 已由 `apps/worker/Dockerfile` 形成独立运行镜像，
完整 Compose 分别启动知识导入和 R2 售后两个 Worker 进程。

在已完成迁移的专用本地数据库上运行一次批处理：

```bash
cd apps/api
uv run knowledge_worker --once
```

持续轮询可省略 `--once`，用 `--limit` 和 `--poll-seconds` 控制批量与间隔。
空队列时持续模式会等待后重试，不会成功退出；只有显式 `--once` 才在当前批次无任务时
返回。这一语义由 Worker CLI 回归和完整 Compose 存活验收共同保护。
Worker 会领取 `queued`/可重试 `failed` 任务；超过 5 分钟未更新的
`processing` 任务会标记为 `WORKER_INTERRUPTED` 并重新进入重试路径，最多
3 次。每个导入都持久化状态、错误码、尝试次数和完成时间，API 可通过
`GET /v1/admin/knowledge/imports/{import_id}` 轮询。

当前本地 embedding 是确定性的 `hash-v1`（32 维），只用于验证 pgvector
写入、幂等、状态和回滚路径，不代表语义检索质量。真实 embedding provider、
对象存储和队列 broker 仍属于后续生产化工作；当前独立容器继续直接轮询 PostgreSQL，
没有 Outbox、消息 broker、DLQ、跨主机租约或生产告警。
