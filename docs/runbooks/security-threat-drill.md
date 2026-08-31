# 步骤 38：安全测试与最小威胁演练 Runbook

本手册只覆盖 Local/CI 合成环境的应用层安全回归。它不授权将真实攻击负载、真实客户
聊天、凭据、支付数据或第三方系统接入本项目，也不构成生产渗透测试、合规评估或事件响应
批准。

## 运行前边界

- 使用默认 Fake Gateway 和 `*_test` / `*_e2e_test` 数据库。`eval_rag` 会清空自己的
  测试库，绝不可指向共享或生产数据库。
- Demo Header 身份只能在 development/test 开启。不要将 `X-Demo-User-ID`、任何
  `X-Demo-*` 头或浏览器 localStorage 误当作生产身份设计。
- 测试样本使用合成固定 token，例如 `step38-*`。不得把真实订单号、工单、邮箱、电话、
  API Key、密码、Cookie 或 Provider trace 填入用例、日志、截图或 QA 记录。

## 安全回归门禁

```bash
cd apps/api
uv run pytest -q tests/security

cd ../web
pnpm exec playwright test e2e/security.spec.ts
```

完整回归还应运行：

```bash
cd apps/api
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
uv run alembic check
uv run eval_triage
uv run eval_rag --use-default-test-database

cd ../web
pnpm lint
pnpm typecheck
pnpm build
pnpm test:e2e
```

GitHub Actions 尚未建立，因此上述命令当前是可供 CI 调用的本地门禁，而不是已经生效的
PR 发布保护。步骤 40 必须将其接入合成环境 CI，并保护分支与发布审批。

## 威胁矩阵与预期结果

| 场景 | 入口 | 预期安全结果 |
|---|---|---|
| 跨租户工单、会话、订单、知识 | 资源 ID / 检索范围 | `404` 或受 ACL 过滤；不泄漏目标资料、标题、片段或存在性 |
| 篡改 `tenant_id`、`role`、`customer_id` | JSON / 非认证 Demo Header | 严格 DTO 返回 `422`，无服务端角色提升；主管接口仍 `403` |
| 明确 Prompt Injection | R0 会话、分诊、RAG | 进入 `safety_review` 或受控人工升级；不调用自动回答/工具写路径 |
| 恶意知识文档 | 运营导入 | 自动保留 `untrusted_content=true`；可追溯但不进入模型证据或来源端点 |
| 超长/异常输入 | HTTP body / `X-Request-ID` | 超过 `MAX_REQUEST_BODY_BYTES` 返回 `413`；非法 request ID 不反射 |
| 频率、重放、事件乱序 | 会话消息 / `Idempotency-Key` / SSE cursor | 同请求安全重放、不同请求受限，`429` 限流，冲突 cursor `400` |
| HTML、JavaScript、恶意 URL | 模型/API 响应 | React 文本渲染不执行；非受信任的来源 locator 不显示可点击入口、不发起跨域请求 |

## 当演练失败时的处置

1. 立即保存最小复现：测试名、commit、运行命令、请求 ID、合成 fixture ID 和失败输出。
   不保存原始真实客户内容或密钥。
2. 判断风险面：是否跨租户泄漏、是否产生写副作用、是否触发模型/外部工具、是否仅是
   展示或可用性问题。不要把测试失败自动归类为可忽略。
3. 对 R0 高/严重问题，主管可通过步骤 37 QA 闭环提交同租户 `pause_r0_feature_flag=true`；
   该接口只能关闭 R0，不能重新开启，且须记录问题、负责人、修复和回归证据。
4. 不要直接修改数据库、删除审计记录或重用旧幂等键来“恢复”。R2 当前没有生产级全局
   kill switch、Outbox 或真实外部适配器；若发现 R2 写路径问题，应停止本地 worker/部署并
   保持真实外部接入 **NO-GO**，而不是声称已完成补偿。
5. 修复实际控制面后，先新增或更新最小安全回归，再跑本页完整门禁。只有通过、复核
   影响范围并保留证据后，才能按独立发布流程考虑恢复功能。
6. 若发现真实凭据或真实数据，按组织的秘密轮换/隐私事件流程处理；本 Runbook 不提供
   生产事件响应替代方案。

## 当前控制与非结论

- `contains_prompt_injection` 只匹配明确、已知的指令覆盖语句。它会产生误报，也会漏掉
  未知绕过；不要把它用作安全评分、客户封禁或生产自动决策。
- 自动标记的恶意知识不是“已清除”：它仍可能作为受 ACL 限制的数据检索结果出现，供
  人工审查；它只是不能成为模型证据、引用来源或权限扩大机制。
- 4 MiB 请求体上限是应用前的资源保护，不能阻挡分布式流量、慢连接、压缩炸弹或网络层
  DDoS。生产需要网关/WAF、全局限流、容量监控和演练。
- 前端有基础安全响应头和来源路径检查，但当前未配置生产 CSP，也没有真实 OIDC、DLP、
  数据保留/删除、密钥轮换、SAST/SCA、签名 CI、SIEM、告警值班或渗透测试。

因此，本步骤的结论仅为“步骤 38 列出的合成攻击样本在当前本地代码路径被拒绝或降级”。
真实客户数据、真实 OMS/支付、真实生产发布和企业级安全认证仍为 **NO-GO**。
