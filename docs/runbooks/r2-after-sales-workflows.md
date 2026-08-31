# R2 售后草稿、确认、审批与幂等 Runbook

本 Runbook 仅覆盖步骤 36 的本机合成 Mock OMS。它证明状态机、最小权限、幂等和恢复语义；不批准真实退款、取消订单、订单变更、补发、投诉外发或任何生产售后操作。

## API 与权限边界

```text
POST /v1/after-sales/workflows
GET  /v1/after-sales/workflows?ticket_id=...
GET  /v1/after-sales/workflows/{workflow_id}
GET  /v1/after-sales/workflows/{workflow_id}/events
POST /v1/after-sales/workflows/{workflow_id}/customer-confirmation
POST /v1/after-sales/workflows/{workflow_id}/agent-approval
```

- 所有 `POST` 都要求长度合规的 `Idempotency-Key`。同一个 tenant、principal、操作和 key 重放相同请求时返回原响应；相同 key 改变请求体返回 `409`。
- 只有 agent/supervisor 可创建、编辑、批准或拒绝草稿；客户只能查看和确认自己的工作流，且确认时必须是可信的已验证客户上下文。知识运营角色、其他客户和其他租户不能访问工作流。
- 创建 API 只接受 `ticket_id + action_type`。服务端从 Ticket 固定 tenant/customer/internal order binding，再运行确定性政策；浏览器不能提交 customer、tenant、订单号、金额、审批状态、外部键或回执。
- 动作白名单只有 `return`、`replacement`、`complaint`。没有 `refund`、`cancel`、通知或任意 Tool Call 入口。模型不会调用这些端点；未来模型只能给坐席提供非执行性建议。
- 客户可见摘要固定显示动作、订单编号、影响、金额/权益（当前明确为未计算）、风险提示和人工审批要求。响应不包含 customer/tenant ID、内部订单 ID、外部幂等键或原始 OMS payload。

## 本地演示流程

先按仓库 README 启动本地 PostgreSQL 和 API，并确保已升级到 head。以下例子只使用合成 Northwind Ticket：

```bash
cd apps/api
uv run alembic upgrade head
ALLOW_DEMO_IDENTITY=true uv run fastapi dev app/main.py
```

另开终端，以坐席身份创建投诉草稿。投诉在合成规则中可用于已验证归属的订单；这不意味着真实投诉已外发：

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/after-sales/workflows \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-agent-01' \
  -H 'Idempotency-Key: r2-demo-draft-0001' \
  --data '{"ticket_id":"ticket-nw-open-01","action_type":"complaint"}'
```

从响应复制 `id` 作为下文的 `<workflow_id>`，客户确认后才会进入坐席审批：

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/after-sales/workflows/<workflow_id>/customer-confirmation \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-customer-01' \
  -H 'Idempotency-Key: r2-demo-confirm-0001' \
  --data '{"decision":"confirm"}'

curl -sS -X POST http://127.0.0.1:8000/v1/after-sales/workflows/<workflow_id>/agent-approval \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-agent-01' \
  -H 'Idempotency-Key: r2-demo-approve-0001' \
  --data '{"decision":"approve"}'
```

审批响应必须是 `queued`；此时还没有外部操作。只有受控 Worker 可消费：

```bash
cd apps/api
uv run after_sales_worker --once
```

默认 Mock OMS 返回 `completed`，并以稳定外部键创建一条合成请求记录；它不会退款、取消订单、扣减库存、发送邮件或调用互联网服务。客户/坐席 UI 也可在工单详情和坐席工作台完成相同演示。

## 状态与恢复

```text
draft -> policy_checked -> awaiting_customer_confirmation
      -> awaiting_agent_approval -> queued -> executing
      -> completed | failed | compensation_required | cancelled
```

坐席编辑动作类型时，`awaiting_agent_approval -> awaiting_customer_confirmation` 是唯一受控回退边：旧客户确认会被清除，客户必须重新确认。审批前、取消后或终态工作流不能进入 `queued`。

- `queued`：Worker 尚未开始；不要从浏览器或 curl 伪造执行。检查审批事件和 `Idempotency-Key`，再启动 Worker。
- `executing + pending_reconciliation`：外部调用超时或连接不确定。不要新建 key 或调用一次新的 `execute`；Worker 后续只能按同一外部 key `lookup` 最终态。
- `failed`：有明确失败码，例如 `EXTERNAL_OPERATION_NOT_FOUND` 或执行前重验证失败。当前没有自动重试端点；由人工复核后决定是否创建新的、完整双确认的草稿。
- `compensation_required`：可能存在部分外部效果。停止任何重试，记录 `workflow_id`、安全的外部 reference、审计事件与补偿原因，再走真实运营补偿流程。当前 Mock 不会造成真实客户影响。
- Worker 崩溃：租约到期后恢复流程只查询最终状态。若查不到外部操作，工作流明确落为 `failed`，而不是假设未发送后重发。

## 回归与发布门禁

```bash
cd apps/api
uv run pytest -q tests/test_after_sales_api.py tests/test_after_sales_worker.py
uv run alembic check

cd ../web
pnpm exec playwright test --grep 'R2 售后草稿'
```

生产前至少补齐：真实 IdP assurance、业务批准的价格/退款/库存政策、真实 OMS 契约与外部幂等/查询保证、Outbox/队列/死信/报警、服务账号最小权限、DLP/数据留存/加密、审计导出、回调签名验证、超时/限流/SLA、人工补偿 SOP 和故障演练。没有这些证据，不得将本 Mock 路径描述为企业级生产售后。
