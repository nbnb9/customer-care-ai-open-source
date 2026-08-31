# ADR-0011：以最小化本地 Trace 关联请求、版本和已知成本

- 状态：已采纳
- 日期：2026-08-25

## 背景

步骤 1–38 已有 `request_id`、审计记录、知识版本和部分模型版本字段，但无法从一次
会话、R1 查询或 R2 Worker 回查完整的 API、检索、模型/工具和最终工作流状态。把原始
HTTP body、SQL、客户消息、Prompt、模型输出或供应商异常直接送入日志/Trace 虽然看似
方便，却会扩大隐私与密钥泄漏面；把“已采集 token”误写成“已知真实成本”同样是错误结论。

当前项目尚未部署 OTel Collector、Prometheus、Grafana、告警、SLO、集中身份或生产数据
治理。步骤 39 的正确目标是让 Local/CI 的合成路径可复核，不是伪装成生产监控平台。

## 决策

采用手工 OpenTelemetry instrumentation 加数据库本地 Trace Viewer：

- 浏览器每个 API 调用生成 W3C `traceparent`；API 返回 `X-Request-ID`、`X-Trace-ID` 和
  `traceparent`。服务端从该上下文建立 HTTP root span，并在同一 Trace 中记录允许的
  DB、检索、模型、R1 工具和 R2 Workflow 元数据。
- 仅存严格 allow-list：请求/租户/会话/工单/Workflow 关联 ID、角色、低基数路由与耗时、
  DB 操作类别和行数、检索候选/结果数、工具结果、模型/Prompt/Schema/知识/部署版本，及
  Provider 报告的 token。不会采集 HTTP body/header、SQL 文本或参数、客户正文、Prompt、
  模型输出、Cookie、API Key 或原始异常栈/消息。
- `knowledge_imports` 和 `after_sales_workflows` 持久化受限的 `traceparent`，让后续
  Worker 可以延续同一 Trace；会话 BackgroundTask 只从会话记录重建 tenant，不复制客户或
  用户 ID 到遥测。
- 本地 `observability_spans` 没有业务外键，以免 Trace 保留阻止合法业务/客户数据删除。
  默认保留 72 小时（`OBSERVABILITY_TRACE_RETENTION_HOURS=1..720`）；主管仅能读取自己
  tenant 的 request、conversation、workflow Trace 与最多最近 500 条的概览窗口。
- Supervisor `/queue` 显示五类摘要：客户体验、AI 质量、安全、Workflow、成本/容量。概览
  同时显示实际保留量与窗口量，避免把“最近 500 条”误称为全量指标。
- Fake Gateway 的成本明确为 `$0 USD`。真实 Provider 只在 SDK 返回时记录 token；未配置
  带生效期的价格/币种/折扣/地区合同前，成本状态为 `unavailable`，绝不估算金额。
- DB span 保留在本地 Viewer，但不逐条写 INFO 日志，避免日志量和成本反过来吞没业务信号；
  高层 HTTP、模型、检索、工具和 Worker span 仍有结构化、脱敏日志关联。

## 后果

正面结果：

- 主管可从合成会话/请求/Workflow ID 复核 API、检索版本、模型摘要和最终状态，并受 tenant
  过滤；浏览器也可验证其请求携带的 W3C 关联头与 API 返回的 Trace ID 相同。
- 版本和成本可用性以可验证字段表达：有数据就显示数据，没有契约就显示未知，而不是推测。
- 手工 span 和 allow-list 缩小默认采集面；异常只记录类型，结构化日志不回显非结构化消息。

代价与限制：

- 本地数据库 exporter 是单进程/单库基线，不具备 Collector 的批处理、背压、跨服务采样、
  高可用、审计导出、不可篡改保留或告警能力。高流量生产使用它会增加数据库写入开销。
- `request_id` 和 Trace ID 是关联元数据，不证明用户身份、业务事实或端到端真实外部调用。
  浏览器传入的 Trace 上下文也不能替代受信任服务间的身份和签名传播。
- p95、错误率、token 与成本概览仅基于本地最近 500 条 tenant span；不是全量计费、容量
  预测、SLO 或财务结算。
- 真实 Provider 的 token 字段依赖其响应；DeepSeek/其他供应商的模型实际调用、价格表、
  限流、失败语义和数据处理仍需单独验证与批准。

## 验证

```bash
cd apps/api
uv run pytest -q tests/test_observability.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run alembic check

cd ../web
pnpm lint
pnpm typecheck
pnpm test:e2e
```

专用测试覆盖：W3C 关联头、R1 查询的脱敏与 tenant 隔离、R0 会话到 BackgroundTask/检索/
模型/知识版本、R2 草稿到 Worker/终态，以及日志不回显 Prompt、密钥和原始异常。它们只证明
当前 Local/CI 合成路径，不证明生产 Collector、真实模型计费或告警值班已通过验收。
