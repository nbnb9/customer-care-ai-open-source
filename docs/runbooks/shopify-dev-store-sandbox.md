# 步骤 41.5：Shopify Dev Store 真实 OMS 沙箱方案

- 状态：只读适配器、离线契约和手动 Job 已实现；真实凭据和真实 Job 结果尚未提供
- 供应商环境：Shopify Dev Store，生成测试数据，Bogus payment gateway
- API：GraphQL Admin API，固定 `2026-07`
- 生产结论：NO-GO；本文只定义开发沙箱接入和验收边界

## 为什么选它

Shopify Dev Store 是供应商维护的真实测试环境，可生成订单等 commerce primitives，并明确禁止
真实交易。GraphQL Admin API 提供订单、履约和 return 契约，`returnCreate` 也明确适用于批准/
拒绝流程发生在 Shopify 外部的 return management 场景。这与当前客户确认、坐席审批、Worker
执行的边界接近。

它仍有三项不可忽略的限制：

1. Shopify 不是完整工单系统，也不是所有目标企业使用的 OMS。
2. 保修/设备序列号不是标准 Order 契约，仍需品牌 warranty/RMA 适配器。
3. `returnCreate` 文档没有提供调用方 idempotency key；真实超时后的安全重试不能仅靠本地账本宣称解决。

因此首个阶段只把 Shopify 用作**真实订单与 return 契约沙箱**，不把它写成生产架构承诺。

## 外部前置条件

需要一名有权限的用户在 Shopify Dev Dashboard 完成：

1. 创建 Partner/开发组织或获得已有组织的开发权限。
2. 创建 Dev Store，并选择生成测试数据。
3. 创建 API-only/Dev Dashboard app，安装到该 Dev Store。
4. 第一阶段只批准 `read_orders,read_returns`。
5. 只有进入受控写入验证时，创建独立 app version，将 `write_returns` 替代 `read_returns`；
   不申请 `write_orders`、支付、退款或 `read_all_orders`。
6. 把 client ID/secret 放入本机或 CI secret store，绝不粘贴到 issue、PR、日志、截图或 Git。

创建商店、安装应用和批准 scope 是外部持久状态变更，执行前必须由 Dev Store 所有者明确授权。

## 配置契约

当前实现使用以下环境契约：

```dotenv
CUSTOMER_TOOLS_PROVIDER=shopify_sandbox
SHOPIFY_API_VERSION=2026-07
SHOPIFY_CLIENT_ID_FILE=/absolute/private/path/shopify_client_id
SHOPIFY_CLIENT_SECRET_FILE=/absolute/private/path/shopify_client_secret
SHOPIFY_SANDBOX_BINDINGS_FILE=/absolute/private/path/shopify_bindings.json
SHOPIFY_TIMEOUT_SECONDS=8
SHOPIFY_EXTERNAL_WRITES_ENABLED=false
```

- shop domain 位于服务端 bindings 文件中，必须是精确的小写 `*.myshopify.com` host；不接受 URL、
  路径、查询串、近似域名或浏览器提交值。
- 三个文件路径必须是绝对路径；文件必须是普通文件且权限不宽于 `0600`。bindings 格式以
  [`data/fixtures/shopify-sandbox-bindings.example.json`](../../data/fixtures/shopify-sandbox-bindings.example.json)
  为准，必须同时绑定内部 tenant、customer、order 和版本化保修政策。
- token 由服务端使用 client credentials grant 获取并按响应 `expires_in` 刷新，不把 access token
  放进浏览器或长期 `.env`。
- secret 文件和 token 不进入 Pydantic repr、Trace attribute、异常文案和健康检查响应。
- Mock 继续是 Local/CI 默认。真实沙箱只有在 `development/test`、显式 provider、三个私有文件和
  固定 API 版本同时满足时才启用；任何 `SHOPIFY_EXTERNAL_WRITES_ENABLED=true` 都会启动失败。

### 本机只读冒烟

在 `apps/api/.env` 设置上述变量后，从 `apps/api` 运行：

```bash
uv run shopify_sandbox_smoke \
  --tenant-id tenant-northwind-demo \
  --customer-id customer-nw-01 \
  --order-id order-nw-1001 \
  --order-number 1001
```

命令只输出适配器版本、API 版本、绑定数量、所需 scope、订单状态、保修布尔值和政策事实来源；
不会输出 token、GID、订单号、客户字段或响应体。成功返回码为 `0`；provider 不可用为 `2`；
未找到或归属不符为 `3`。

GitHub Actions 使用手动工作流
[`shopify-sandbox.yml`](../../.github/workflows/shopify-sandbox.yml)。仓库管理员先配置
`SHOPIFY_SANDBOX_CLIENT_ID`、`SHOPIFY_SANDBOX_CLIENT_SECRET`、
`SHOPIFY_SANDBOX_BINDINGS_JSON` 三个 Actions secrets，再从 Actions 页面输入四个合成内部 ID/
订单名称运行。该工作流不在 PR 或 main push 自动触发，也没有任何写 scope。

## 租户和客户绑定

新增服务端受控映射：

```text
tenant_id -> shop_domain + installation/app identity
internal_customer_id -> shopify_customer_gid
internal_order_id -> shopify_order_gid
```

这些映射只能由受信任导入/账户绑定流程建立。浏览器、模型、订单号、email 或电话都不能直接声明
Shopify customer GID。查询顺序为：

1. 从 `CurrentPrincipal` 得到 tenant/customer。
2. 读取 tenant 对应的唯一 shop installation。
3. 使用候选 order number 查找最小订单候选。
4. 在 Adapter 内再次校验订单 customer GID 与服务端映射一致。
5. 只投影 UI 需要的状态、时间、允许动作；未找到和不属于当前客户返回同一结果。

不得为了方便把 email、电话、地址放入日志或用它们作为浏览器提交的所有权证明。

## 第一阶段：真实只读契约

### 允许字段

- Order：GID（只在服务端）、name、createdAt、displayFinancialStatus、displayFulfillmentStatus。
- Customer：仅用于服务端比较的 GID。
- Fulfillment/line item：完成订单状态和 RMA 对象选择所需的最小 ID、quantity、SKU/title 摘要。
- Return：ID、name、status、line item/quantity 和必要时间。

地址、电话、email、支付方式、完整交易和营销字段默认不查询。需要新增字段必须附具体任务、数据
分类、保留和 UI 最小化说明。

### Adapter 对应

- `OrderQueryPort`：真实查询和所有权复核后投影 `OrderStatusResult`。
- `WarrantyEligibilityPort`：Shopify 提供订单/SKU 事实；保修期限仍由版本化品牌政策计算，不能
  把 Shopify Order 当作 warranty source of truth。
- `AfterSalesOrderFactsPort`：读取真实订单/履约/已有 return，并生成服务端政策需要的最小事实。

### 失败语义

| 情况 | API 行为 |
|---|---|
| token/scope 缺失 | fail closed，503/provider unavailable；不回退到另一个租户或猜测数据 |
| 订单不存在或不属于客户 | 相同 404，不暴露候选是否存在 |
| 限流 | 映射为 provider unavailable，不在同步请求内长时间重试；客户可重试或转人工 |
| 供应商超时 | 不声称订单不存在；标为 provider unavailable |
| schema/version 变化 | contract test 失败并阻止升级，不在运行时静默丢字段 |

## 第二阶段：沙箱 `returnCreate`

`returnCreate` 需要 `write_returns`，会创建 OPEN return，并假设申请已经由外部工作流批准。它只能
在现有工作流满足以下条件后调用：

- 当前状态为 queued/executing，且客户确认与坐席审批仍有效。
- 外部 order/line item/quantity/reason 全部由服务端存储事实和批准快照派生。
- 政策版本、tenant/shop/customer/order 绑定在调用前重新核验。
- `SHOPIFY_EXTERNAL_WRITES_ENABLED=true` 且运行环境被明确标记为 development sandbox。

当前 `OmsWriteRequest` 只有 `tenant_id/workflow_id/action_type/idempotency_key`，不足以表达真实
return。实现前必须新增版本化命令，例如：

```text
ShopifyReturnCommandV1
  tenant_id
  workflow_id
  shop_domain
  order_gid
  return_line_items[{fulfillment_line_item_gid, quantity, reason_definition_id, note_code}]
  customer_confirmation_version
  agent_approval_version
  policy_snapshot_digest
  external_idempotency_key
```

命令中的说明只能使用枚举/受控 code；不得把未脱敏客户自由文本复制到 Shopify return note。

### 幂等与不确定结果

本地唯一账本只能阻止两个本地 Worker 同时发起调用，不能证明 Shopify 在“已提交但响应丢失”时
没有创建 return。实现必须先完成一个可验证的 reconciliation 设计：

1. 调用前记录 command digest 和 attempt，提交本地事务。
2. 调用后保存 Shopify return GID 和最小 receipt。
3. 超时/断连后禁止盲目再次 `returnCreate`。
4. 通过 order 的 returns 回读并按批准对象、行项目、数量、时间窗和 app-owned correlation 标记
   匹配；若无法唯一匹配，进入 `compensation_required/investigation`。
5. 故障注入证明响应丢失、Worker 崩溃、重复 delivery 和人工重试不会产生第二个 return。

如果 Shopify 契约不能提供可靠 correlation，沙箱可以保留人工调查演示，但生产写路径永久保持
关闭；不得把“开发商店没有撞上重复”当作幂等证明。

退款、取消、修改地址、支付和自动补发不属于本次 sandbox write scope。

## Webhook

只在只读轮询契约通过后增加必要 webhook：

- 验证原始 body 的 HMAC；任何解析后的重新序列化 body 不能用于签名验证。
- 使用 `X-Shopify-Webhook-Id` 去重，并记录 topic/shop/received_at/minimal resource ID。
- shop domain 必须命中当前 installation；不接受 header 自报的新租户映射。
- include_fields 和 filter 尽量缩小 payload；不把完整订单 body持久化到审计或 Trace。
- webhook 只触发回读/同步，不直接越过客户确认和坐席审批执行售后动作。

## 测试矩阵

### 自动化（无 secret）

- GraphQL 请求快照和 schema contract fixture。
- tenant/customer/order 二次归属过滤。
- 未找到/不属于同一错误语义。
- token scope、429/非 2xx、超时和无效响应映射。
- 任何 log/Trace 不含 token、email、phone、address、原始 body。
- write provider 默认关闭，Fake/Mock CI 不访问网络。

上述只读项已由离线测试覆盖，仍不能替代真实供应商运行。

### 当前专用真实只读 Job（有 secret）

- 获取短期 token并验证实际 granted scopes。
- 查询生成订单并映射最小 DTO。
- 外租户/错误客户映射在 Adapter 内拒绝。
- 真实 API 版本和字段 contract。
- 使用绑定的 internal order GID 查询 R2 政策事实，并再次校验 customer GID。

专用 job 不在 fork PR 运行，不向日志输出响应体或 secret，并使用可重置 Dev Store 数据。

写入、响应丢失故障注入和 webhook 测试尚未实现，也不属于当前只读 Job；在真实只读门禁通过且
另行授权前不得增加。

## 验收和回滚

只读 GO：

- 真实 API 查询、归属复核、最小 DTO、错误映射和 secret 泄漏扫描全部通过。
- 用户研究的 A2 订单/保修任务使用该真实沙箱，而不是 fixture。
- 切回 Mock 只需 provider 配置，不迁移或破坏应用数据。

写入 GO（仅开发沙箱）：

- 客户确认、人员审批、政策和 command digest 全部关联。
- 正常、超时、崩溃和重复触发场景都没有第二个 external return。
- 失败/不确定结果可由主管工作台定位和处置。

生产仍 NO-GO，直到真实商户 OAuth/安装、多租户凭据生命周期、protected customer data 审批、
webhook ingress、Outbox/DLQ、告警、数据保留/删除和恢复演练分别完成。

## 官方资料（复核于 2026-08-31）

- [Dev stores](https://shopify.dev/docs/apps/build/stores/development-stores)
- [Generated test data](https://shopify.dev/docs/storefronts/themes/tools/development-stores/generated-data)
- [Client credentials grant](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant)
- [Access scopes](https://shopify.dev/docs/api/usage/access-scopes)
- [`returnCreate` 2026-07](https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/returnCreate)
- [Shopify webhooks](https://shopify.dev/docs/apps/build/webhooks)
