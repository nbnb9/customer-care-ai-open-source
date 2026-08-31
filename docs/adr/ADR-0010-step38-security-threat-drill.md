# ADR-0010：以应用层安全回归和最小威胁演练保护当前受控路径

- 状态：已采纳
- 日期：2026-08-25

## 背景

步骤 32–37 已有租户 ACL、角色检查、R0/R2 状态门、最小上下文和 QA 闭环，但这些控制
分散在各模块的单元/集成测试中。它们不能自动证明以下攻击路径不会回归：跨租户 ID、
浏览器伪造身份字段、明确的指令覆盖、恶意知识、异常大的请求、重复提交、事件游标冲突，
以及模型文本在浏览器中的 HTML/URL 注入。

“模型提示词写着不要执行指令”不是充分控制。智能客服的安全结论应主要来自服务端身份、
ACL、工具/状态机和输出投影；提示词只能是补充。反过来，以关键词声称能够检测全部
Prompt Injection 也是错误前提，因为攻击可改写表达、混合语言或利用数据/工作流漏洞。

## 候选方案

1. 只依赖模型 system prompt 与人工代码评审。
2. 建立一个通用的关键词拒绝器，并把所有含“提示词”等内容拒绝掉。
3. 保持已有最小权限和证据控制；为明确的指令覆盖模式增加窄范围、可测试的降级；把
   恶意知识降为不可信；将完整威胁矩阵固化为 API 与浏览器测试。
4. 直接引入生产 WAF、真实 OIDC、DLP、SIEM、SAST/SCA、渗透测试和全球限流。

## 决策

采用方案 3；方案 4 是后续生产化门禁，不能在本地合成 Demo 中伪装为已完成。

- 新增 `app.shared.security.contains_prompt_injection`。它仅匹配明确的“忽略此前规则”、
  “展示/泄漏 system/developer prompt”、jailbreak、绕过安全等模式，并会归一化空白。
  R0、Fake/后置分诊策略和 RAG 共用此判断；RAG 在检索和 Gateway 调用前返回受控人工
  升级，不转发模型草稿。
- 知识导入将运营者提供的 `untrusted_content` 与该明确模式的检测结果做逻辑或。命中后
  保持导入可追溯、可作为 ACL 受限的原始检索数据，但它不能进入回答模型或引用来源端点。
  这阻止“攻击者把恶意文档标为 trusted”直接变成模型证据；它不替代内容审查或签名发布。
- API 在 JSON 解析前限制请求体：`MAX_REQUEST_BODY_BYTES` 默认 4 MiB，既检查
  `Content-Length`，也统计 ASGI 流式片段。超限返回不回显输入的 `413`。外来
  `X-Request-ID` 只接受 128 字符以内的安全 ASCII token，不合法就重新生成，避免日志与
  响应头反射异常值。
- CORS 不再使用 `*` 方法/头，而是仅允许配置 origin、GET/POST/PATCH/OPTIONS 和当前 API
  需要的 `Content-Type`、`Idempotency-Key`、`Last-Event-ID`、`X-Demo-User-ID`、
  `X-Request-ID`。这不把 Demo Header 变成真实认证；生产 OIDC 引入后须重新评审允许头。
- 前端把 API 响应视为不可信：React 默认文本渲染之外，知识 `source_locator` 还必须是带
  product/region/language 的相对 `/v1/knowledge/chunks/{id}` 路径。跨域、`javascript:`、
  fragment、控制字符及路径穿越形式均不呈现可点击入口。Next 响应增加基础 `nosniff`、
  anti-frame、Referrer 和 Permissions Policy 头。
- `tests/security/` 和 `e2e/security.spec.ts` 成为可复现的步骤 38 门禁，覆盖跨租户资源、
  身份篡改、注入/恶意知识、请求体/CORS、限流/重放/SSE 游标，以及 HTML/恶意 URL 渲染。
  这些命令尚未接入 GitHub Actions；步骤 40 负责把它们变成 PR 必经门禁。

## 后果

正面结果：

- 明确攻击样本在进入模型、自动 R0 回答或浏览器外部请求之前被降级，且结果可由本地测试
  重复验证。
- 防护规则集中，避免 R0、分诊、RAG 和知识导入对相同攻击文本给出相互矛盾的结果。
- 源定位不是能力 URL；即使未来服务端或模型返回了恶意 locator，浏览器也不会将它当作
  外部链接或执行上下文。

代价与限制：

- 明确模式会有误报，例如一份正常讨论 system prompt 的知识文档会被标为不可信；这是当前
  合成环境中“宁可人工审阅、不可自动当作模型证据”的选择。运营者不能通过请求体覆盖该
  安全标记，需在受控发布流程中重新审阅内容。
- 规则不识别所有变体，也不防御已经获得合法知识运营权限后的所有数据滥用。ACL、证据约束
  和人工 review 是必要的独立层，不能被检测器替代。
- 当前没有生产 OIDC assurance、WAF/全局限流、CSP、DLP、加密/保留/删除、秘密扫描、
  依赖扫描、签名 CI、SIEM、渗透测试或真实事故响应。请求体上限也不是 DDoS 方案。
- Demo 身份和 Mock OMS 继续使真实客户数据、真实外部写入和不可信网络暴露保持 **NO-GO**。

## 演练发现、修复与复核

本轮记录的是可从代码路径确认的控制面差距，不声称已经在生产环境复现了真实攻击：

| 发现 | 风险判断 | 修复 | 复核 |
|---|---|---|---|
| RAG 在检索前没有针对明确指令覆盖的统一短路；只要查询同时命中知识词，理论上可能继续进入 Gateway。 | 推断为 prompt-injection 防御纵深缺口；并非已证实的模型越权。 | 在检索与 Gateway 前返回受控人工升级，R0/分诊共用同一窄规则。 | `test_explicit_prompt_injection_is_handed_off_before_rag_or_reply_generation` 断言 Gateway 不会被调用。 |
| 知识运营请求中的 `untrusted_content=false` 曾可完全代表信任结论。 | 持有运营权限的恶意/误标文档可能变成模型证据。 | 导入时将明确指令覆盖自动标为不可信。 | `test_explicitly_malicious_knowledge_is_automatically_tainted` 验证导入、worker 和回答路径。 |
| 前端将 API 的来源定位视为可调用字符串，缺少对未来异常/被污染响应的额外约束。 | 推断为浏览器跨域请求和不安全 URL 展示风险；React 文本渲染本身不是漏洞。 | 仅允许带完整范围参数的同源相对 chunk 路径，并加基础安全响应头。 | `e2e/security.spec.ts` 注入 HTML、`javascript:` 和跨域 locator，断言不执行、不发起外部请求。 |

这些修复通过当前合成路径回归，不证明未知绕过、真实 Provider 行为或生产边缘层安全。

## 验证

```bash
cd apps/api
uv run pytest -q tests/security
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run alembic check
uv run eval_triage
uv run eval_rag --use-default-test-database

cd ../web
pnpm lint
pnpm typecheck
pnpm build
pnpm test:e2e
```

步骤 38 定向安全套件验证的期望不是“攻击检测率”，而是已列出的攻击样本不能读取禁止
资源、不能调用模型/自动路径或执行前端代码，并且高风险输入会明确降级到人工路径。
