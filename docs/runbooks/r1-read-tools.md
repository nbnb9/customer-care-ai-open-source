# R1 只读订单/保修 Mock Runbook

本 Runbook 覆盖步骤 34 的 Local/CI 合成读取路径。它不是生产 OMS/CRM、真实保修政策、生产 OIDC 或外部供应商接入批准。

## 契约与安全边界

- 客户传统页面：`/support/order-lookup`。它不依赖 LLM；模型不可用时仍可查询或回到人工客服入口。
- API：`POST /v1/customer-tools/order-status` 与 `POST /v1/customer-tools/warranty-eligibility`。
- 请求体只有 `{"order_number":"..."}`。`tenant_id`、`customer_id`、`verified_customer_id` 和所有其他字段均被拒绝；订单号采用 POST body，避免进入 URL、路由缓存键或常见访问日志路径。
- 仅有可信 customer `CurrentPrincipal`（customer role、customer ID、`demo_verified`/`verified`）可以调用。服务端绑定 `tenant_id + verified_customer_id`；Mock Adapter 再次按相同范围过滤，不能信任浏览器或模型传入的归属。
- 同租户其他客户、跨租户和未知订单统一 `404 Order not found`。没有保修记录只会在订单已证明归属后返回 `eligible=false`。
- 成功响应只输出 UI 所需字段，绝不输出订单内部 ID、客户/租户 ID、商品、地址、电话或原始 fixture 记录。
- 每次实际读取写入 `customer_read_tool.<tool>.read` 审计事件。摘要只记录工具名、候选参数已提供、验证客户由服务端派生、结果状态和 `request_id`；不要在新增日志、Trace 或告警里补写订单号、地址、电话。

## 本地验证

启动 API 后，以 Northwind 客户身份查询自己的订单：

```bash
curl -sS -X POST http://localhost:8000/v1/customer-tools/order-status \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-customer-01' \
  --data '{"order_number":"NW-DEMO-ORDER-1001"}'
```

应只返回订单号、`shipped` 和固定下单时间。然后以相同身份查询 `NW-DEMO-ORDER-1002` 或 `CT-DEMO-ORDER-2001`，两者都必须返回相同的 404；不要根据响应、错误文本或审计摘要区分客户归属与订单不存在。

验证保修资格：

```bash
curl -sS -X POST http://localhost:8000/v1/customer-tools/warranty-eligibility \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-ct-customer-01' \
  --data '{"order_number":"CT-DEMO-ORDER-2001"}'
```

请求体中故意添加 `verified_customer_id` 时应返回 `422 VALIDATION_ERROR`。坐席或主管调用应返回 `403`，且不应实际调用 Mock Port 或生成读取审计。

## 故障与人工处理

- `404`：向客户显示“未找到属于当前已验证账户的订单”，不要说明该订单是否存在、属于谁或在哪个租户。
- `403`：验证状态或客户身份不足。不要通过让浏览器自报 customer ID 来修复；进入人工核验流程。
- `500`：记录并使用 `request_id` 排查 fixture/服务故障；不要在客服对话、浏览器或外部日志回显原始请求体。客户应返回人工客服入口。
- 当前 Mock 不模拟真实供应商超时、熔断或重试。接入真实 Port 前必须定义超时、可用性、速率限制、降级、审计保留、字段分类和人工升级规则。

## 回归命令

```bash
cd apps/api
uv run pytest -q tests/test_customer_tools_adapter.py tests/test_customer_tools_api.py

cd ../web
pnpm exec playwright test --grep '传统页面查询'
```
