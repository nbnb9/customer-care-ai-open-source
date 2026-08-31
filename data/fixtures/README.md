# Synthetic fixture world

本目录只包含为 CustomerCare AI 的 Local、CI 和演示环境人工创建的完全虚构数据，不来自生产导出、真实聊天记录或真实客户。`source: synthetic` 是来源声明，不是自动证明；提交前仍需人工审阅。

## Fixture 版本与固定时钟

- `schema_version`: `1`
- `fixture_version`: `1.0.0`
- `as_of`: `2026-01-15T12:00:00Z`

所有与有效期有关的测试都使用固定的 `as_of`，不读取运行测试当天的时间。

## 文件职责

| 文件 | 职责 | 是否可作为正常种子数据 |
|---|---|---|
| `world.json` | 两租户的合法基础实体与同租户关系；步骤 34 R1 Mock 订单/保修读取源 | 是 |
| `scenarios.json` | 测试主体、请求和预期结果 | 否 |
| `knowledge/manifest.json` + `knowledge/*.md` | 步骤 27 的知识资产台账、内容与发布生命周期样本 | P2 按台账规则加载 |
| `knowledge.json` | P0/P1 兼容 fixture；字段较旧，不得作为新知识导入契约 | 仅兼容场景 |

`scenarios.json` 中的缺字段、跨租户访问、重复提交和非法状态转换是测试动作，不应作为实体写入数据库。

## 租户

- `northwind-demo`
- `contoso-demo`

ID 中的 `nw` 和 `ct` 前缀只用于人类阅读；授权逻辑不得依赖 ID 前缀，必须使用服务端身份上下文和 `tenant_id` 判断。

## 数据不变量

- 每条可独立寻址的 fixture 记录（有 `id` 的实体、场景和文档）都带 `source: synthetic`；订单 `items` 是继承父订单来源与租户的嵌套值对象，不是独立记录。
- 每条业务记录都带 `tenant_id`。
- `world.json` 中的外键必须存在且保持同租户。
- 正常基础世界不包含缺字段、断外键或跨租户外键。
- 跨租户关系只通过 `scenarios.json` 中“租户 A 主体请求租户 B 合法资源”表达。
- 邮箱只使用 `example.test`；客户、订单和物流编号带 `DEMO`。
- 所有 ID 和时间固定，使 Local 与 CI 可重复运行。
- 每个场景执行前都重置为同一份 `world.json`，场景不依赖执行顺序。

## 场景与预期

| 场景 | 类型 | 预期 |
|---|---|---|
| 正常创建工单 | 成功路径 | 创建一张 `new` 工单并产生审计事件 |
| 缺少 `subject` | 失败路径 | `422 VALIDATION_ERROR`，不创建工单 |
| Northwind 坐席读取 Contoso 工单 | 失败路径 | `404 RESOURCE_NOT_FOUND`，不泄露资源内容且状态不变 |
| 相同请求与幂等键提交两次 | 失败防护 | 只创建一张工单，不产生重复副作用 |
| `closed -> reopened` | 失败路径 | `409 INVALID_STATE_TRANSITION`，状态保持 `closed` |

项目当前状态机只允许 `resolved -> reopened`。如未来需要允许 `closed -> reopened`，应先修改领域规则并记录决策，再修改 fixture。

幂等范围为：

```text
tenant_id + server-derived principal_id + canonical operation + idempotency_key
```

`principal_id` 必须来自服务端身份上下文；`canonical operation` 由 HTTP method 和路由模板推导，不能相信请求体自报字段。系统还要保存请求 fingerprint：同一作用域内，相同 key 加相同 fingerprint 返回原结果；相同 key 加不同 fingerprint 必须冲突。相同幂等键不得让不同租户或同租户不同客户相互命中响应。

## R1 订单与保修 Mock 样本

步骤 34 的 `MockCustomerToolsAdapter` 直接读取本文件的 `orders` 和 `warranties`，不把它们复制到应用数据库，也不连接真实 CRM/OMS。适配器的输入必须由服务端传入 `tenant_id + verified_customer_id + candidate_order_number`；`candidate_order_number` 只是候选值，不能用订单内部 `id` 替代。适配器按前两个服务端范围再次过滤，再返回最小 DTO：订单为编号、状态和下单时间，保修为编号、资格布尔值和截止时间。

同租户其他客户、跨租户和不存在订单均返回空结果；只有已证明归属的订单才会把“没有保修记录”表示为 `eligible=false`。`warranties.status` 由固定 fixture 表示，本步骤不根据测试运行当天重新计算有效性；任何真实保修时钟、产品规则和例外流程都属于后续外部系统契约。

## 知识样本

步骤 27 的 `knowledge/manifest.json` 覆盖：

- 租户内公开 FAQ；
- 中国地区退换货政策的当前版本；
- 同一逻辑资产的已过期旧版本；
- 只允许坐席和主管访问的内部处理流程；
- 另一个租户的同类资料；
- 含恶意指令文字的不可信测试文档；
- 草稿与撤回版本，覆盖完整生命周期。

知识授权以 `tenant_id + audience + 当前时间窗口` 为准；`audience` 表示内容分类。`public` 文档允许客户、坐席和主管，`internal` 文档只允许坐席和主管。服务端必须在召回前执行这些过滤，不能把权限交给模型。

每份知识文档都有租户内的 `owner`，负责后续版本更新、过期和撤回；当前只是稳定团队标识，不要求在 `world.json` 中创建可登录用户。台账用 `document_key + tenant_id + product + region + language` 保证同一范围最多一个 published 版本，并用 `content_sha256` 防止发布后无记录地改文件。

恶意指令只是待检索的测试内容，不能改变系统权限、系统指令或触发工具。

## 当前能力边界

这些 fixtures 只是后续自动化测试的输入，不能单独证明租户隔离、幂等或状态机已经实现正确。步骤 27 的完成证据是：每个文件有完整台账、有固定有效期和所有者，且无需模型或数据库即可由 `app.modules.knowledge.ledger.load_ledger` 判定访问资格。异步导入、向量索引、检索和带引用回答仍属于步骤 28–30，不应在本步骤提前宣称完成。

## 验证

```bash
python3 -m json.tool data/fixtures/world.json >/dev/null
python3 -m json.tool data/fixtures/scenarios.json >/dev/null
python3 -m json.tool data/fixtures/knowledge.json >/dev/null
python3 -m json.tool data/fixtures/knowledge/manifest.json >/dev/null
cd apps/api && uv run pytest -q tests/test_knowledge_ledger.py
cd apps/api && uv run pytest -q tests/test_customer_tools_adapter.py tests/test_customer_tools_api.py
```
