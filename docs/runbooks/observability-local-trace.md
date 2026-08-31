# 步骤 39：本地可观测性、成本和版本关联 Runbook

本手册适用于 Local/CI 合成环境的 Trace Viewer。它帮助复核一次请求或业务链路，不构成
生产监控、计费、告警、审计留存、真实模型或真实 OMS/支付集成的运行批准。

## 运行边界

- 先升级数据库：`cd apps/api && uv run alembic upgrade head`。
- `DEPLOYMENT_VERSION` 必须由部署流程给出安全版本标识；默认 `local-dev` 只能表示本机。
  `OBSERVABILITY_TRACE_RETENTION_HOURS` 默认 `72`，允许范围 `1..720`。
- Local Trace 仅保留 allow-list 元数据：关联 ID、tenant、角色、路由、时长、DB 操作类型、
  检索统计、工具结果、模型/Prompt/Schema/知识/部署版本、token 和明确的成本可用状态。
  不得将客户正文、Prompt、模型输出、SQL/参数、API Key、Cookie、密码或原始异常复制到
  Trace、日志、截图或工单。
- Demo Header 仅用于本机；主管接口并不提供真实认证。生产必须由 OIDC、RBAC、数据分类、
  保留和审计策略替代。

## 产生并验证一条 Trace

用一个固定 W3C Trace ID 调用安全的 Local 端点：

```bash
curl -i http://127.0.0.1:8000/v1/customer-tools/order-status \
  -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-customer-01' \
  -H 'X-Request-ID: obs-r1-demo-001' \
  -H 'traceparent: 00-0123456789abcdef0123456789abcdef-0123456789abcdef-01' \
  --data '{"order_number":"NW-DEMO-ORDER-1001"}'
```

响应必须有 `X-Request-ID`、`X-Trace-ID` 和 `traceparent`。然后用同 tenant 的主管身份读取：

```bash
curl -sS \
  -H 'X-Demo-User-ID: user-nw-supervisor-01' \
  http://127.0.0.1:8000/v1/admin/observability/traces/by-request/obs-r1-demo-001
```

预期能看到同一 Trace 下的 HTTP、`tool.order_status` 和 `db.query` 摘要，但不能看到订单号、
客户 ID、地址、电话或请求 body。客户/坐席读该接口应为 `403`；其他 tenant 主管得到空数组。

主管可在 `/queue` 的“步骤 39 · 本地 Trace Viewer”查看五类概览，并按自己的
`conversation_id` 查询会话链路。还可调用：

```text
GET /v1/admin/observability/overview
GET /v1/admin/observability/traces/by-request/{request_id}
GET /v1/admin/observability/traces/conversations/{conversation_id}
GET /v1/admin/observability/traces/workflows/{workflow_id}
```

会话链路应包含 `conversation.reply_generation`、`knowledge.retrieval` 和 `gen_ai.chat`（只有
实际经过 R0/RAG 时）；R2 链路应把 `workflow.draft_create` 与 `workflow.execution` 或
`workflow.reconciliation` 关联。导入 Worker 与 R2 Worker 使用持久化的受限 `traceparent`；
它只用于关联，不是公开 DTO 或访问凭据。

## 成本和版本的正确解读

- `customer_care.prompt.version`、`customer_care.prompt.schema_version`、
  `customer_care.model.config`、`customer_care.knowledge.versions`、`deployment.version`
  是复核某次合成路径所需的版本元数据，不包含 Prompt/回答正文。
- Fake Gateway 显示 `measured_cost_usd: 0.0`。这只是“不发生外部费用”，不是供应商报价。
- 真实 Provider 返回的输入/输出 token 可以出现在摘要中，但当
  `customer_care.cost.status=unavailable` 时，表示尚未提供经批准、版本化的价格合同；不能
  用 token 乘猜测单价形成财务结论。
- 概览明确只计算最近 500 条 tenant span；保留总数与分析窗口数分开显示。不要据此声称
  全量 p95、完整错误率、容量上限、SLO 或月度账单。

## 失败处置与复核

1. Trace 查询为空时，先确认数据库已升级、请求的 `X-Trace-ID` 与 `traceparent`、主管 tenant
   和关联 ID 一致；不要以关闭 tenant 过滤或记录原始消息来“排查”。
2. 若出现 Trace/日志中有客户正文、Prompt、模型输出、SQL 参数、密钥或支付信息，停止将该
   路径用于演示，保存最小化的合成复现与 commit，新增回归，再按组织的隐私/密钥流程处置。
3. Provider 费用或 token 异常时，不填补估算值；记录 Provider、模型、部署版本、时间范围和
   `unavailable` 原因，等待批准的价格/账单契约。
4. Viewer 写入失败、保留增长或 DB 负载异常时，当前 Local 实现没有队列/DLQ/Collector 可
   自动补偿。降低/停止本地演示并调查数据库，不要把缺失 Trace 当成业务成功或失败证据。

## 本地回归

```bash
cd apps/api
uv run pytest -q tests/test_observability.py
uv run alembic check

cd ../web
pnpm lint
pnpm typecheck
pnpm exec playwright test e2e/observability.spec.ts
```

完整回归应继续运行 README 的 API、评测与 Web E2E 命令。步骤 41 虽提供可选
Prometheus/Grafana 容器，但它只验证观测底座启动与 Prometheus 自抓取，尚未接入本地 span
或应用 `/metrics`。真实生产还缺 OTel Collector、应用指标、集中日志、告警/值班、服务间
可信传播、采样/背压、Outbox/DLQ、真实价格和账单契约、OIDC、DLP/保留/删除、备份恢复与
故障演练，因此仍为 **NO-GO**。
