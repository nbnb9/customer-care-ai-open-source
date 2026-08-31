# ADR-0006：R1 客户订单/保修查询采用服务端绑定范围的只读 Mock Port

## 背景

步骤 33 的 R0 会话会把订单/物流问题确定性升级，避免低风险 FAQ 路径误把自然语言当作业务事实。步骤 34 需要让已验证客户读取自己的订单状态或保修资格，但还没有真实 OMS/CRM、生产 OIDC 或可证明安全的模型工具编排。

若让浏览器提交客户 ID、直接按订单号查询 fixture，或由模型自由选择和填充工具参数，客户 A 可以尝试读取客户 B 的订单，且 Trace 容易复制订单号或其他不必要字段。

## 候选方案

1. 直接从浏览器请求 `world.json` 或按订单号公开 GET 接口。
2. 浏览器提交 `customer_id` / `tenant_id`，后端据此选择订单。
3. 让模型决定工具、客户和订单参数，再由 Adapter 查询。
4. 用服务端身份注入范围的 Port，先接 fixture-backed Mock，再提供传统非模型页面。

## 决策

选择方案 4：

- 定义 `OrderQueryPort` 与 `WarrantyEligibilityPort`，当前唯一实现是读取步骤 12 `data/fixtures/world.json` 的 `MockCustomerToolsAdapter`；不新建订单数据库表，不连接真实 OMS/CRM。
- `POST /v1/customer-tools/order-status` 与 `POST /v1/customer-tools/warranty-eligibility` 只接受严格的 `order_number` 候选值。请求体额外字段（包括 `tenant_id`、`customer_id`、`verified_customer_id`）一律校验失败。
- 仅 role 为 customer、带 customer ID 且验证等级为 `demo_verified`（开发/测试）或 `verified`（生产映射目标）的 `CurrentPrincipal` 可调用。服务端从该 principal 注入 tenant/customer 范围；Adapter 用相同范围再次过滤。
- 外部可见成功响应最小化：订单只含编号、状态和下单时间；保修只含编号、资格与截止时间。没有客户 ID、租户 ID、订单内部 ID、商品、地址或电话。
- 外部订单/保修、跨租户和不存在订单统一返回 `404 Order not found`。已证明归属但没有保修记录时才返回 `eligible=false`。
- 每次实际读取均创建 `customer_read_tool.<tool>.read` 审计事件。事件保留工具名、参数存在性/服务端派生标记、结果状态和 `request_id`，不保存订单号、地址或电话。
- 当前传统查询页面不调用 LLM Gateway；模型工具选择未在本步开放。若以后让模型提议调用，仍必须通过严格 Schema、固定白名单、服务端范围注入和同一 Adapter/审计路径。

## 后果

正面影响：

- R1 的可验证边界集中在身份、资源归属、最小字段和审计，不依赖模型遵从性。
- 模型故障不会阻断传统查询或人工升级入口。
- fixture-backed 行为可在 Local/CI 重复，跨客户和跨租户读取可稳定测试。

代价与限制：

- `demo_verified` 和 Demo Header 绝不能进入生产；真实 IdP 必须将 assurance 可靠映射为 `verified`，并确保 `customer_id` 不可由客户端伪造。
- Mock 不能证明真实 OMS/CRM 的鉴权、字段语义、超时、限流、可用性、审计保留和异常恢复；外部接入需要独立契约测试和故障策略。
- fixture 中的 `active` 保修状态是固定合成事实，不是实时保修判定。
- 此步骤不执行退款、取消订单、地址变更或保修申请，也不把 R0 会话自动升级为模型工具调用。

## 验证

- Adapter 单元测试验证候选 ID、客户范围和租户范围篡改均返回空结果，并只产生最小 DTO。
- API 测试验证客户 A 读取自己订单成功，读取客户 B、其他租户或未知订单统一 404；请求体试图提交验证客户/租户字段返回 422。
- API 测试验证审计不含订单号、地址或电话，并带调用请求 ID；另验证传统路径不调用 LLM Gateway。
- Playwright 验证客户页可查询自己的订单/保修，查询客户 B 的订单只显示通用人工升级提示。
