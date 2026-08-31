# CustomerCare AI

[![Backend](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/backend.yml/badge.svg)](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/backend.yml)
[![Frontend](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/frontend.yml/badge.svg)](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/frontend.yml)
[![E2E](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/e2e.yml/badge.svg)](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/e2e.yml)
[![Eval and Security](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/eval-security.yml/badge.svg)](https://github.com/nbnb9/customer-care-ai-open-source/actions/workflows/eval-security.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

这是一个可追溯、可测试、逐步引入 AI 能力的智能客服项目。当前代码覆盖开发计划步骤 1–41 的本地/CI 合成基线；步骤 39 提供脱敏本地 Trace、版本和明确成本状态，步骤 40 提供可复核 GitHub Actions Checks，步骤 41 提供完整本地 Compose、应用镜像、健康依赖和持久化 Runbook。2026-08-30 在原步骤 42 前插入步骤 41.5“产品化重构阶段”：先验证目标行业、购买者、角色信息架构、关键原型、设计系统、真实 Shopify OMS 沙箱和真实坐席任务效率；门禁未通过前不进入 Staging/CD。公开仓库的 `main` 要求 Pull Request 和四项 CI Checks；生产认证、CD、Collector/应用指标/告警、备份恢复、高可用和企业级安全演练仍不在当前交付范围内，因此不能把本仓库直接视为企业级生产系统。

本仓库是从已验收 `main` 导出的干净开源快照，不包含私有工程仓库的提交历史、PR、Actions 日志、构建产物或凭据。

## 已有能力

- 客户创建和查看工单、公开回复；坐席领取、回复并按状态机流转工单。
- 租户隔离、角色授权、主管队列及同租户坐席分配。
- 带幂等键的建单、并发状态保护、结构化审计元数据。
- AI 分诊建议、确定性业务规则、常见直接标识规则脱敏、坐席接受/编辑/拒绝闭环。
- 知识资产台账、知识运营角色、异步导入队列、版本状态、分块、pgvector 本地索引，以及服务端 ACL 优先的检索。
- 受证据约束的知识回答：服务端验证引用、无证据和版本冲突安全升级，客户/坐席 UI 展示来源并在点击时重新校验原文访问权限。
- RAG 分层评测：分别量化 ACL 检索、引用、关键结论与拒答/升级；对错误政策执行发布、发现、审计撤回和回归演练。
- 客户在线会话：服务端绑定客户/租户、持久化消息与追加式事件序列、SSE 断线恢复、客户级起步限流、人工升级和失败降级。
- 人工升级闭环：服务端按原因码生成优先级、目标队列和 SLA；主管可查看队列，坐席可领取或结束自助会话，且只读取最小化、脱敏的上下文包。安全复核队列在当前 Demo 中仅由主管处理。
- 受控 R0 客户自助：仅在部署主开关和租户灰度名单同时开启时，用 `public`、可验证且带引用的知识回答低风险 FAQ；无证据、模型不确定和订单/退款/退货/补发/换货/账户/隐私/投诉/安全/人工请求一律升级。客户可记录“有帮助/没有帮助”，但该信号不自动改变知识或路由。
- R1 只读订单/保修：仅已验证客户可用，订单号只作为浏览器提交的候选值；服务端从身份注入客户/租户范围并再次校验归属。Local/CI 默认使用 Mock；可显式启用只读 Shopify Dev Store 适配器，后者仅查询最小订单字段并验证短期 token scope。
- R2 受控售后：坐席创建经确定性规则校验的退货/补发/投诉草稿，客户确认与坐席审批后才进入 Worker；Local/CI 的 Mock OMS 使用独立外部幂等账本，失败和补偿路径可审计。Shopify 可提供只读订单/保修政策事实，但外部写适配器未启用；退款、取消订单和对外发送不在动作白名单中。
- 主管 QA 反馈闭环：主管在租户范围内抽样已完成会话，查看脱敏的路由、引用、最小工具摘要和最终结果；问题必须有分类、严重度、根因、负责人、修复类型和可执行合成回归映射。高/严重问题可审计地关闭本租户 R0，但不能通过该接口重新开启。
- 安全回归与最小威胁演练：自动验证跨租户资源、浏览器伪造身份字段、明确 Prompt Injection、恶意知识导入、异常请求体、CORS、重放/限流/SSE 游标冲突，以及模型 HTML/恶意来源 URL 的前端安全渲染。
- 可观测性、成本和版本关联：浏览器/API/Worker 使用 W3C `traceparent` 关联 `request_id`、Trace、会话、工单和 Workflow；主管可在本地 Trace Viewer 复核脱敏的 HTTP/DB/检索/模型/工具/Worker 摘要、知识与部署版本。Fake 成本明确为 `$0`，未配置真实价格合同的 Provider 成本明确为 `unavailable`，不估算金额。
- CI 质量门禁：四个独立 GitHub Actions 工作流在 PR 和 main push 上运行后端、前端单测/构建、Playwright E2E、Fake Eval/RAG ACL/安全回归；只使用锁文件、合成数据、临时测试库和 Fake Gateway，不读取个人模型 Key。
- 完整本地 Compose：多阶段 API/Worker/Web 镜像、一次性 Alembic migration 和非破坏空库 bootstrap、服务健康依赖、文件型本机 secrets、命名卷、持续 Worker、显式数据重置，以及可选 Prometheus/Grafana 验收 profile。
- FastAPI、Next.js、Alembic、API 测试及 Playwright 端到端测试。

## 产品化重构（步骤 41.5，执行中）

步骤 41.5 不是视觉换肤，而是原步骤 42 的前置产品门禁。当前定位假设收窄为：面向已有帮助台和
OMS 的高客单消费电子/智能硬件品牌，提供审批优先、可审计、支持保修/RMA 的 AI 售后协同层。
这个定位仍必须通过真实买家和坐席研究验证，不能当作已经获得产品市场匹配。

阶段资产：

- [产品化阶段章程、购买者与 GO/NO-GO](docs/product/productization-phase.md)
- [产品发现与真实用户研究计划](docs/product/product-discovery-research-plan.md)
- [角色信息架构与六条关键任务流](docs/product/product-information-architecture.md)
- [CareOps 设计系统基础](docs/product/design-system-foundations.md)
- [真实坐席任务完成率与处理效率验收](docs/product/agent-usability-acceptance.md)
- [产品化 GO 证据登记表](docs/product/productization-go-register.md)
- [可点击静态原型](docs/product/prototypes/careops-console.html)
- [Shopify Dev Store 真实 OMS 沙箱方案](docs/runbooks/shopify-dev-store-sandbox.md)
- [ADR-0014：在 Staging 前插入产品化门禁](docs/adr/ADR-0014-step41-5-productization-gate.md)

仓库内已经完成角色信息架构、设计系统、客户/坐席/主管页面迁移、WCAG 自动扫描与回流门禁、
Shopify 只读适配器、服务端租户/客户/订单绑定、R2 政策事实接入、离线契约测试和手动沙箱 Job。
仍未完成的是仓库外证据：真实企业访谈、Dev Store 所有者提供的凭据与一次真实只读 Job、真实坐席
对照验收，以及产品/运营/安全三方授权。因此步骤 41.5 和企业生产仍为 **NO-GO**；详细责任人和
解锁证据见产品化 GO 证据登记表。

## 完整本地 Compose（步骤 41）

最快的完整验收方式要求 Docker Compose v2 和 OpenSSL。所有业务数据都是合成数据，容器
默认强制使用 Fake Gateway，不会读取个人 `.env` 中的 DeepSeek/API Key：

```bash
./scripts/init-compose-secrets.sh
docker compose config --quiet
docker compose up -d --build --wait
./scripts/compose-smoke.sh
docker compose ps -a
```

默认地址：Web <http://127.0.0.1:3000>、API <http://127.0.0.1:8000>、MinIO Console
<http://127.0.0.1:19001>。`migrate` 和 `bootstrap` 成功后显示 `Exited (0)` 是正常的一次性
任务状态。普通 `docker compose down` 保留命名卷；删除全部本地数据必须使用带 project
确认口令的 `./scripts/reset-compose-data.sh`。

Prometheus/Grafana 仅在验收时启用：

```bash
docker compose --profile observability up -d --wait
```

当前 Prometheus 只自抓取，尚无应用 `/metrics`、Dashboard、SLO 或告警，不应把 profile
健康外推为生产可观测性。完整停启、持久化、重置、端口冲突和故障处理见
[`docs/runbooks/complete-local-compose.md`](docs/runbooks/complete-local-compose.md)，架构取舍见
[`ADR-0013`](docs/adr/ADR-0013-step41-complete-local-compose.md)。

## 宿主机开发

要求 Python 3.12、`uv`、Node.js、`pnpm` 和 Docker。所有示例数据都是合成数据。

需要 FastAPI/Next 热更新时，先停止完整 Compose，随后只启动本地 PostgreSQL、Redis 和
MinIO。详细边界见 [`infra/compose/README.md`](infra/compose/README.md)：

```bash
cp .env.example .env
# 为 PostgreSQL 和 MinIO 生成不同的本机密码，并同步更新 DATABASE_URL
docker compose --env-file .env -f infra/compose/compose.dev.yaml up -d --wait
```

启动 API。Demo Header 身份可以被调用者伪造，只能在本机开发或隔离 E2E 的
`development/test` 环境显式开启：

```bash
cd apps/api
cp .env.example .env
# 将 DATABASE_URL 等本机值写入 apps/api/.env
uv sync
uv run alembic upgrade head
ALLOW_DEMO_IDENTITY=true uv run fastapi dev app/main.py
```

知识运营角色提交导入后，API 只创建队列记录并返回 `import_id`；另开终端
运行 Worker：

```bash
cd apps/api
uv run knowledge_worker --once
```

导入接口为 `POST /v1/admin/knowledge/imports`，状态接口为
`GET /v1/admin/knowledge/imports/{import_id}`，已发布版本的受控撤回接口为
`POST /v1/admin/knowledge/documents/withdrawals`，检索接口为
`POST /v1/knowledge/search`，受证据约束回答接口为
`POST /v1/knowledge/answer`。检索服务从服务端身份生成租户与角色受众 ACL，
并在 SQL 中同时过滤产品、地区、语言、生效窗口和发布状态，再做词面优先、
向量距离兜底的候选排序；结果只包含允许的 chunk、版本、标题和片段。客户的
地区还会与数据库中的客户身份记录核对，不能由请求改写。

回答接口只把本次检索中“词面命中且非 `untrusted_content`”的片段交给 Gateway。
服务端要求至少一个无重复、属于当前证据集的 `citation_id`；无词面证据、同一
`document_key` 出现多个可用版本、模型报告不确定/要求升级、或引用无效时，都会
返回固定的非确定性人工升级答复，不透传模型草稿或虚构引用。客户响应不含内部
不确定性码；坐席响应才显示服务端生成的证据状态。回答中的 `source_locator` 是
`GET /v1/knowledge/chunks/{chunk_id}` 的相对定位，点击时会再次检查租户、角色、
适用范围、发布状态、生效时间和可信标记；它不是可绕过 ACL 的 URL，也不是外部
原始文件链接。引用归属校验只能证明“引用来自本次允许的证据集”，不能单独证明
模型对片段的蕴含关系或最终政策结论一定正确；RAG Eval 仅增加合成语料上的回归
信号，真实政策仍需要人工验收。

步骤 38 还会在任何 RAG Gateway 调用前将已知的明确指令覆盖模式降级为人工处理；知识
导入即使被运营者标为可信，命中同一类模式也会自动存为 `untrusted_content`，因此不会成为
模型证据。它是确定性防御纵深，不是“能够识别所有 Prompt Injection”的宣称；未知攻击仍须
依赖最小权限、ACL、证据约束、工具白名单、人工复核与后续生产安全控制。

撤回必须指定精确的 `document_key + version + product + region + language`，只允许
知识运营角色撤回当前 `published` 文档。操作会在一个事务中将权威 document、所有
chunk 及其复制 metadata 标记为 `withdrawn`，但不会改写原始 import（发布证据），
并写入 `knowledge.document_withdrawn` 审计事件。检索与来源端点已对双重状态列做
过滤，因此撤回提交后不会继续返回或回答该版本；当前没有应用层回答缓存，故无可
清除的缓存键，也不需要重建未改变的向量。详见
[`docs/runbooks/knowledge-policy-withdrawal.md`](docs/runbooks/knowledge-policy-withdrawal.md)。

当前仅接受 Markdown/TXT、最大 1 MB，并使用固定 32 维 `hash-v1` embedding
验证 pgvector 链路；它不代表生产语义检索质量。通用检索与坐席回答的产品和语言目前仍是调用方提交的
适用范围，不是已由身份或订单事实派生的授权条件；客户 R0 例外地由部署配置固定产品/语言，且地区由客户身份绑定。
正式接入前仍需要补齐可信的工单/订单上下文。当前没有检索缓存；未来若加入缓存，键必须至少包含租户、
角色、产品、地区、文档版本和查询范围。标记为 `untrusted_content` 的文档可以
作为受 ACL 约束的检索数据返回，但绝不会进入回答模型或通过来源端点展示，且不能
改变服务端 ACL 或工具权限。

如需重置并重新导入合成数据，seed 会清空应用表，必须在本机开发数据库显式授权；不要对共享或生产数据库执行：

```bash
cd apps/api
ALLOW_DESTRUCTIVE_SEED=true uv run python -m app.seed
```

另开终端启动 Web：

```bash
cd apps/web
cp .env.example .env.local
pnpm install --frozen-lockfile
pnpm dev
```

默认地址：Web <http://localhost:3000>、API <http://localhost:8000>、存活检查 <http://localhost:8000/health>、就绪检查 <http://localhost:8000/ready>。

## 客户会话、受控 R0 与人工升级（步骤 32–35）

所有会话写操作都要求 `Idempotency-Key`；客户和租户由服务端身份推导，浏览器提交的归属字段不会被接受。当前接口为：

```text
POST /v1/conversations
POST /v1/conversations/{conversation_id}/messages
GET  /v1/conversations/{conversation_id}/events
POST /v1/conversations/{conversation_id}/handoffs
POST /v1/conversations/{conversation_id}/feedback
GET  /v1/handoffs
GET  /v1/handoffs/{handoff_id}
POST /v1/handoffs/{handoff_id}/claim
POST /v1/handoffs/{handoff_id}/resolution
GET  /v1/admin/self-service/r0-feature-flag
PUT  /v1/admin/self-service/r0-feature-flag
```

事件是持久化的 SSE JSON 信封，包含 `event_id`、递增 `sequence`、`type`、`conversation_id`、`request_id` 与 `payload`。客户端可使用 `after_sequence`，或标准 `Last-Event-ID`（服务端的 SSE `id` 即 sequence）获取严格的后缀事件。`answer.delta` 只含消息 ID 与文本增量；引用和允许操作只能出现在 `answer.completed`。当且仅当 R0 已生效并且公开证据通过校验，完成事件才携带 `answer_mode: r0_self_service`、服务端构造的引用以及反馈动作；其他正常消息是 `controlled_ack`，不含引用。

R0 的部署主开关 `R0_SELF_SERVICE_MASTER_ENABLED` 默认 `false`，用于阻止整个发布面；它不能由浏览器或租户 API 打开。即使主开关为 `true`，主管仍须通过租户开关显式启用，并指定 `customer_ids` 灰度名单，或明确设置 `allow_all_customers: true`。租户开关由数据库逐消息读取，因此主管提交 `enabled: false` 后，后续消息会立即回到非政策确认；R0 不发送模型文本 delta，最终答复会在持有租户开关行锁的事务中再次验证开关后才持久化，避免在关闭竞态中暴露已生成的 R0 文本。只有当前租户主管可读取或修改该开关，修改会产生审计事件。

客户会话在任何 RAG/模型调用前会确定性识别订单/物流、退款、退货、补发、换货、支付、投诉、隐私、登录/账户变更、安全及人工请求，并创建人工升级，而不是把它们当作 R0 FAQ。通过灰度的普通问题只检索 `public` 受众，并沿用 ACL、地区、生命周期、可信标记、词面证据、版本冲突和引用校验；无证据或不确定时会写入系统升级通知与 `handoff.created`，不输出半完成答案。为兼容既有 API，客户直接调用 `POST /v1/knowledge/answer` 也受到相同的主开关、风险拦截、部署拥有的产品/语言范围和 `public` 受众限制；客户页面不再提供该绕过会话审计的入口。

客户只能对已完成的 `r0_self_service` 助手消息提交 `helpful` 或 `unhelpful` 反馈。服务端以会话、回答消息和客户为唯一范围保存最新评价，使用幂等键防止重复写入，并追加 `feedback.recorded` 事件和审计记录；评价不是证据质量结论，不能自动发布知识、改变 ACL 或关闭人工升级。

人工升级的优先级、目标队列和 SLA 只能由服务端根据原因码决定。staff API 仅向当前租户获授权角色交付 `handoff-context-v1`：验证等级、摘要、受控事实、因果关联的工具摘要、客户已见引用、脱敏问题和风险原因；它不返回系统 Prompt、模型隐式推理、其他客户数据或完整 Trace。普通坐席只可见待领取或自己已领取的升级；`safety-review` 在当前 Demo 中只由主管处理。领取和结束操作均要求 `Idempotency-Key` 并写入持久事件和审计；“恢复安全流程”不会重新开启模型自动回复。详见 [`ADR-0007`](docs/adr/ADR-0007-human-handoff-context-package.md) 与 [`会话 Runbook`](docs/runbooks/customer-conversation-sse.md)。

当前 Demo 身份使用 `X-Demo-User-ID` 请求头。原生 `EventSource` 无法携带该头，前端因此用 `fetch` 消费标准 `text/event-stream`，并在连接关闭或网络失败时按最后确认的序号恢复。不要把身份、模型或 CRM Token 放进查询参数。客户可随时请求人工；高风险关键词会走确定性人工升级，绝不把订单、退款、安全或投诉输入误写成已完成的业务操作。

`CONVERSATION_MESSAGE_RATE_LIMIT`（默认 `20`）和 `CONVERSATION_MESSAGE_RATE_WINDOW_SECONDS`（默认 `60`）提供一个数据库锁保护的客户级起步限流。它不是生产容量方案：多区域全局限流、队列/Outbox、事件保留、加密、真实 OIDC、生产级 Trace/成本/TTFT 指标与故障演练仍属于后续发布门禁。详细恢复语义见 [`docs/runbooks/customer-conversation-sse.md`](docs/runbooks/customer-conversation-sse.md)。

## R1 只读订单/保修 Mock（步骤 34）

传统客户页面为 `/support/order-lookup`，并由客户支持页链接进入。它不使用会话模型或工具编排，因而在模型不可用时仍可完成受控查询；需要写操作、无法匹配订单或人工协助时，页面会引导回人工客服入口。

```text
POST /v1/customer-tools/order-status
POST /v1/customer-tools/warranty-eligibility

{"order_number":"NW-DEMO-ORDER-1001"}
```

请求体严格拒绝 `tenant_id`、`customer_id` 和 `verified_customer_id` 等归属字段。只有身份提供方已构造为验证通过的 customer `CurrentPrincipal` 才能调用；服务端将 `tenant_id + verified_customer_id` 传入 `OrderQueryPort` / `WarrantyEligibilityPort`，而 Mock Adapter 还会按同一范围再次过滤步骤 12 的 `data/fixtures/world.json`。因此，同租户其他客户、跨租户和不存在的订单都返回相同的 `404 Order not found`，不构成订单枚举接口。

订单响应只含 `order_number`、`status`、`ordered_at`；保修响应只含 `order_number`、`eligible`、`coverage_ends_at`。每一次真正触发的读取都写入 `customer_read_tool.<tool>.read`：工具名、`candidate_order_number: provided`、`verified_customer_id: server_derived`、结果状态和 `request_id`，但不复制候选订单号、地址或电话。当前保修资格完全依据固定合成 fixture 状态，不随运行当天变化。

这是接口/授权/审计边界演练，不是对真实 OMS、CRM、保修政策或生产 OIDC 的验收。真实接入前需要把 identity assurance 映射为可信的 `verified` 状态，完成外部契约、超时/限流/可用性、数据最小化、保留期和供应商安全评审。决策与操作细节见 [`ADR-0006`](docs/adr/ADR-0006-r1-read-only-customer-tools.md) 和 [`R1 Runbook`](docs/runbooks/r1-read-tools.md)。

## R2 售后草稿、确认、审批与幂等（步骤 36）

R2 是一个受控的 Local/CI Mock 工作流，而不是退款或真实 OMS 集成：

```text
draft -> policy_checked -> awaiting_customer_confirmation
      -> awaiting_agent_approval -> queued -> executing
      -> completed | failed | compensation_required | cancelled
```

坐席/主管只能对已有 Ticket 创建 `return`、`replacement` 或 `complaint` 草稿；服务端从 Ticket 绑定 tenant、customer 和内部订单后才运行固定合成规则。客户只能以已验证身份确认自己的草稿，坐席审批后才进入 Worker。任何实质编辑都会清除旧的客户确认并要求重新确认。`refund`、订单取消和外部消息不在动作白名单、API 路由或浏览器 UI 中，模型也没有执行权限；如未来引入模型，它只能给坐席提供非执行性建议。

所有写 API 都要求 `Idempotency-Key`。API 重放记录和 Mock OMS 的外部幂等账本分离：后者以稳定的 `workflow_id` 派生外部键，并只在 Worker 中使用。Worker 在外部写前重新核验当前身份、资源归属、确认/审批、政策快照、状态和外部键 digest；超时或 Worker 崩溃后只按同一键查询最终态，绝不盲目重发。客户可见 DTO 只显示最小摘要、状态、安全回执投影以及失败/补偿码。

```text
POST /v1/after-sales/workflows
GET  /v1/after-sales/workflows?ticket_id=...
GET  /v1/after-sales/workflows/{workflow_id}
GET  /v1/after-sales/workflows/{workflow_id}/events
POST /v1/after-sales/workflows/{workflow_id}/customer-confirmation
POST /v1/after-sales/workflows/{workflow_id}/agent-approval
```

本机 Worker 只处理合成 Mock OMS：

```bash
cd apps/api
uv run after_sales_worker --once
```

当前 fixture 没有价格、退款、库存、物流或真实外部系统事实，因此客户摘要会明确显示未计算金额/权益。真实接入前仍需要业务批准的规则、可信 OIDC、外部幂等+查询契约、Outbox/队列/死信/告警、数据分类与保留、补偿 SOP 和故障演练。详见 [`ADR-0008`](docs/adr/ADR-0008-r2-after-sales-controlled-execution.md) 与 [`R2 Runbook`](docs/runbooks/r2-after-sales-workflows.md)。

## 主管 QA 反馈闭环（步骤 37）

主管页 `/queue` 里的“主管 QA 反馈闭环”将一个会话坏例变成可追溯的修复记录。API
只接受当前租户主管：

```text
GET   /v1/admin/qa/owners
GET   /v1/admin/qa/regression-cases
GET   /v1/admin/qa/samples?limit=20
GET   /v1/admin/qa/samples/{conversation_id}
GET   /v1/admin/qa/issues
POST  /v1/admin/qa/issues
GET   /v1/admin/qa/issues/{issue_id}
PATCH /v1/admin/qa/issues/{issue_id}
```

样本展示来自当前会话的脱敏消息、路由、客户可见引用、最小工具摘要、最终结果与反馈
计数；问题的持久源证据只含 ID、哈希、路由、引用版本和工具摘要，不保存第二份原始
会话、系统 Prompt、模型隐式推理或 provider 原始 trace。所有问题必须选择分类、严重度、
根因、负责人、修复方式和 `data/evals/qa/regression-catalog.json` 中的回归条目；目录在
加载和创建时都验证目标 triage/RAG Case 存在且映射类型一致。

问题状态受服务端控制：`open -> in_remediation -> verification_pending -> verified -> closed`。
`verified` 必须同时有 `passed` 回归状态和非空的运行证据引用；事件序列和审计记录会保留
该过程。高/严重问题可以在创建时关闭当前租户的 R0 开关，写入审计和安全快照；QA API
没有重新开启能力，恢复必须由既有开关 API 独立完成并经人工发布复核。

首批目录包含一条知识修复和一条规则修复：RAG 语料将历史 45 天退款政策标记为过期并
要求当前 30 天 v2；补发/换货在 R0 门禁、Fake Gateway 与模型输出强制策略三层均提升为
R2 人工审核。它们是本地合成回归资产，不是生产 CI、真实政策签字或真实模型质量证明。
操作细节见 [`ADR-0009`](docs/adr/ADR-0009-step37-supervisor-qa-feedback-loop.md) 与
[`QA Runbook`](docs/runbooks/qa-feedback-loop.md)。

## 质量门禁

```bash
cd apps/api
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
uv run pytest -q tests/test_qa_feedback_loop.py
uv run pytest -q tests/security
uv run eval_triage
# 仅限专用本机 *_test 数据库；会清空并灌入合成测试世界
uv run eval_rag --use-default-test-database

cd ../web
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
pnpm test:e2e
```

E2E 默认会为 Playwright 启动一个独立 API，并将 `DATABASE_URL` 安全推导为本机
`*_e2e_test` 数据库；它会迁移、清空并灌入合成世界，再运行浏览器用例。默认不复用
已有服务，因此不会把验收误连到旧的开发 API。更详细的 Web 配置见
[`apps/web/README.md`](apps/web/README.md)。

`eval_triage` 默认使用确定性 Fake 网关，连续运行 3 次公开集和流程隔离 holdout，并把带输入/阈值/报告指纹的 JSON/Markdown 报告写到 `artifacts/evals/triage/`。如显式使用 `--provider configured`，必须用 `--expected-model` 固定实际模型、提供匹配 provider 的独立阈值 profile，并自行控制外部模型费用和数据出境。该 holdout 保存在仓库内，不是真正保密的盲测集；当前报告不会虚构未采集的 Token 或成本。

`eval_rag` 将版本化的合成语料经生产导入 worker 写入隔离数据库，再分开检查
`Recall@k`、允许/禁止文档、过期命中、当前检索引用、关键结论和拒答/升级。默认
Fake 网关离线且确定；如使用 `--provider configured`，必须使用 `--expected-model` 固定
评测模型、匹配实际 provider 的阈值文件，并由操作者承担外部调用成本与数据出境
审查。每次默认还执行错误政策撤回演练；`data/evals/rag/` 与运行手册说明了语料、
保留集和边界。此命令只接受 loopback 主机且数据库名以 `_test` 结尾的目标，拒绝
非测试库；不要将其指向共享或生产数据库。

## 安全测试与最小威胁演练（步骤 38）

安全回归位于 `apps/api/tests/security/`，浏览器对抗用例位于
`apps/web/e2e/security.spec.ts`。它们验证以下当前代码级边界：

- 当前 tenant/身份不能用 ID、请求 JSON、`X-Demo-Tenant-ID`、`X-Demo-Role` 或
  `X-Demo-Customer-ID` 跨越工单、会话、订单、知识或主管接口；Demo Header 本身仍只能
  在 development/test 使用，不能替代真实 OIDC。
- 明确的“忽略先前指令 / 泄漏 system prompt / 绕过安全”等模式在 R0、分诊和 RAG 中
  降级，不允许进入自动回答或工具写路径；恶意知识至少会被自动标为不可信，不能成为模型
  证据或来源原文。
- `MAX_REQUEST_BODY_BYTES` 默认是 4 MiB，超过限制的声明或流式请求体返回 `413`；
  非法 `X-Request-ID` 不会被反射。CORS 只允许配置的 origin、当前使用的 GET/POST/PATCH/
  OPTIONS 方法和必要请求头。
- React 将服务端/模型文本作为文本渲染；前端只接受带产品、地区、语言范围的相对
  `/v1/knowledge/chunks/{id}` 来源定位，拒绝 `javascript:`、跨域和路径穿越形式。Web 同时
  返回 `nosniff`、拒绝嵌入、Referrer 与 Permissions Policy 基础响应头。

运行细节、演练处置与限制见
[`ADR-0010`](docs/adr/ADR-0010-step38-security-threat-drill.md) 和
[`安全演练 Runbook`](docs/runbooks/security-threat-drill.md)。这些合成测试已接入步骤 40
GitHub Actions，并在公开仓库中配置为 `main` 的必需检查。它们只能证明当前合成回归通过，
不能替代真实身份、真实供应商和生产威胁演练。

## 可观测性、成本和版本关联（步骤 39）

每个浏览器 API 调用生成 W3C `traceparent`；API 返回 `X-Request-ID`、`X-Trace-ID` 与
`traceparent`。服务端以手工 OpenTelemetry span 关联允许的 HTTP、DB、检索、模型、R1 工具、
会话 BackgroundTask 和 R2 Worker。主管在 `/queue` 可使用本地 Trace Viewer，或调用：

```text
GET /v1/admin/observability/overview
GET /v1/admin/observability/traces/by-request/{request_id}
GET /v1/admin/observability/traces/conversations/{conversation_id}
GET /v1/admin/observability/traces/workflows/{workflow_id}
```

读取严格按主管 tenant 限制；非主管为 `403`，跨 tenant 的请求 Trace 为 `[]`，会话/Workflow
资源为 `404`。本地 span 只允许保存关联 ID、角色、低基数路由/耗时、DB 操作类别/行数、检索
计数、工具结果、模型/Prompt/Schema/知识/部署版本、Provider token 与成本状态；不会采集
客户正文、HTTP body/header、Prompt、模型输出、SQL/参数、API Key、Cookie、密码或原始异常。

`DEPLOYMENT_VERSION` 默认 `local-dev`，`OBSERVABILITY_TRACE_RETENTION_HOURS` 默认 `72`
（允许 `1..720`）。概览会区分本地实际保留量与最近 500 条分析窗口，避免把局部 p95、token
或错误计数误解为全量指标。Fake Gateway 的已测金额固定为 `$0 USD`；真实 Provider 即使返回
token，在没有批准的、带版本/地区/币种/折扣的价格合同前也只显示 `unavailable`，绝不估算。

本地验证：

```bash
cd apps/api
uv run pytest -q tests/test_observability.py
uv run alembic check

cd ../web
pnpm exec playwright test e2e/observability.spec.ts
```

此实现是 Local/CI 可复核基线，不会把 span 导出到 OTel Collector 或步骤 41 的可选
Prometheus/Grafana profile，也不是集中日志、应用指标、告警/SLO、生产成本结算、服务间
可信传播或高可用的替代品。运行和故障处置见
[`ADR-0011`](docs/adr/ADR-0011-step39-local-observability-version-cost-correlation.md) 与
[`步骤 39 Runbook`](docs/runbooks/observability-local-trace.md)。

## CI 质量门禁（步骤 40）

仓库包含四个短工作流：`backend.yml`、`frontend.yml`、`e2e.yml` 与
`eval-security.yml`。它们在每个 Pull Request、main push 和手动触发时运行，外部 Actions
固定到完整提交 SHA，PostgreSQL/pgvector 镜像固定到 digest：

| Check | 内容 |
|---|---|
| Backend / backend | 完整 Compose 静态配置、Ruff/format、mypy、Alembic upgrade/check、pytest |
| Frontend / frontend | pnpm frozen install、lint、typecheck、Vitest、生产构建 |
| E2E / e2e | 临时 pgvector 测试库、生产 Web、隔离 API、Chromium Playwright |
| Eval and Security / eval-security | Fake 分诊 Eval、RAG ACL/撤回 Eval、安全回归 |

CI 明确设置 `LLM_PROVIDER=fake`，不提供可用模型 Key，也不连接本机数据库、真实 CRM/OMS
或生产端点。失败的浏览器证据保留 7 天，合成 Eval 报告保留 14 天。详细决策和故障处置见
[`ADR-0012`](docs/adr/ADR-0012-step40-ci-quality-gates.md) 与
[`CI Runbook`](docs/runbooks/ci-quality-gates.md)。

事实边界：公开仓库要求通过 Pull Request 合并，并把上述四个精确 Check 配置为 required；
同时禁止 force push、删除受保护分支和未解决讨论。仓库管理员仍需定期复核规则与 GitHub 权限，
而这些代码托管门禁不能把项目自动提升为企业生产 GO。

## 安全与数据规则

- 只使用完全合成的数据，不提交真实客户、订单、工单和聊天数据。
- 不提交 `.env`、API Key、密码、Token 或私钥；仓库可见性不能替代密钥管理。
- `ALLOW_DEMO_IDENTITY` 和 `ALLOW_DESTRUCTIVE_SEED` 默认关闭。
- 步骤 38 的规则只覆盖明确、已知的攻击模式；它不替代真实 IdP、WAF/DDoS 防护、SAST/SCA、
  CSP 策略、DLP、密钥轮换、渗透测试、SIEM、不可篡改审计、告警值班或事故演练。
- 外部模型调用前仍需按组织政策复核数据出境、供应商合规、保留期和密钥管理。
- 根 Compose 与 `infra/compose` 仅供 Local/CI，没有生产级 TLS、备份、高可用或公网防护。

## 开源许可与贡献

本项目由 nbnb9 以 [Apache License 2.0](LICENSE) 开源。提交改动前请阅读
[贡献指南](CONTRIBUTING.md)；安全问题必须通过 [安全策略](SECURITY.md) 中的私密渠道报告，
不得公开提交漏洞细节、凭据或真实客户数据。
