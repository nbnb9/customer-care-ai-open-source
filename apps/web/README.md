# CustomerCare AI Web

Next.js 客户、坐席与主管工作区。浏览器只调用 FastAPI；模型密钥、数据库地址等服务端配置不得放入本项目。

## 本地运行

```bash
cp .env.example .env.local
pnpm install
pnpm dev
```

默认 API 地址为 `http://localhost:8000`，可在构建前通过 `NEXT_PUBLIC_API_BASE_URL` 修改。该变量会公开并固化到浏览器产物中，不能用于密钥。

## 客户会话事件流

客户支持页可以创建 `/support/conversations/[id]` 会话。当前 Demo 身份依赖
`X-Demo-User-ID`，而原生 `EventSource` 无法安全地附带这个头；页面因此使用 `fetch` 读取
标准 SSE，并用服务端的递增 `sequence` / `after_sequence` 重连。不要为了使用
`EventSource` 把身份放进 URL。

页面只乐观显示本地客户消息；人工升级、最终回答、引用或任何业务动作都必须等待服务端事件
或写接口确认。`answer.delta` 不是最终结果，只有 `answer.completed` 能提供最终文本、引用和
允许的下一步。生成失败会显示“重试或转人工”提示，不应保持永久 loading。

步骤 33 将客户自助统一到会话路径：只有服务端部署主开关和租户灰度名单均生效，且存在可验证
`public` 资料时，页面才渲染带来源的 R0 FAQ 回答。点击来源会重新请求已授权原文片段；客户可对
完成的 R0 回答提交“有帮助/没有帮助”。订单/物流、退款、账户变更、隐私、投诉、安全、人工请求以及无证据情形由
服务端升级，浏览器不提供直接知识回答入口，也不决定开关、ACL 或风险类别。

## R1 传统订单与保修查询

`/support/order-lookup` 是步骤 34 的非模型兜底页面。它只在 JSON body 中发送候选 `order_number`，不把订单号放进 URL 或浏览器存储，也不提交客户、租户或验证等级；FastAPI 从 Demo/真实身份上下文绑定范围。页面只显示订单状态或保修资格的最小字段，查询不到或需要写操作时引导用户回人工客服入口。它当前连接的是版本化合成 fixture 的 Mock Adapter，不是生产 OMS/CRM。

## 质量检查

```bash
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

`test:unit` 使用 Vitest 检查纯前端安全/状态辅助逻辑，不启动浏览器或 API；Playwright
仍是独立 E2E 门禁，不能用其中一类测试替代另一类。

## E2E

默认 E2E 会启动独立 API，基于 `../api/.env` 中的 `DATABASE_URL` 推导本机
`*_e2e_test` 数据库，并在每次运行前创建（如不存在）、迁移、清空和灌入
`data/fixtures/world.json`。它只接受 loopback 地址和以 `_e2e_test` 结尾的库，绝不会
清空默认开发库。浏览器 E2E 强制使用 Fake 网关；真实模型另由评测命令验证，避免把
网络抖动、费用和模型变化混入 UI 回归结果。

```bash
pnpm test:e2e
```

Playwright 默认使用 `http://127.0.0.1:3100`（Web）和
`http://127.0.0.1:8100`（API），并且不会复用已有服务。可用以下变量调整：

- `E2E_BASE_URL`：Web 地址，默认 `http://127.0.0.1:3100`。
- `E2E_API_BASE_URL`：API 地址，默认 `http://127.0.0.1:8100`。
- `E2E_START_WEB_SERVER=false`：不由 Playwright 启动 Web。
- `E2E_START_API_SERVER=false`：不由 Playwright 启动 API。
- `E2E_WEB_COMMAND` / `E2E_API_COMMAND`：覆盖启动命令。
- `E2E_DATABASE_URL`：覆盖推导出的数据库；必须是本机且名称以 `_e2e_test` 结尾。
- `E2E_REUSE_EXISTING_SERVER=true`：仅用于本地调试；不得作为验收证据，因为它会跳过
  本次启动的隔离保证。

`E2E_START_API_SERVER=false` 同样是显式调试/外部环境例外；此时由操作者负责其数据库
隔离。不要把 E2E 指向生产或共享数据库；测试使用 Demo 身份并写入真实 API 目标。
