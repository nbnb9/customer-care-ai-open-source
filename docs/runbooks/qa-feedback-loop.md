# 主管 QA 反馈闭环 Runbook

本手册只适用于步骤 37 的本机/CI 合成环境。它帮助将一个坏例变成有负责人、可追溯
和可执行的回归资产；不批准处理真实客户聊天、生产政策发布、生产 R0 开关、真实
模型放行或企业级事故响应。

## 权限与接口

只有当前租户的 `supervisor` 可调用以下接口：

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

- `POST` 和 `PATCH` 需要长度合规的 `Idempotency-Key`。同 tenant、principal、操作和
  key 的同请求重放同一问题；同 key 改变请求返回 `409`。
- 客户、坐席和知识运营角色得到 `403`；跨租户 ID 和不属于当前租户的负责人得到
  `404`，不可用作枚举接口。
- 样本仅来自有已完成助手答复的同租户会话。返回文本是直接标识符脱敏后的审阅视图；
  不返回系统 Prompt、模型隐式推理、原始 provider trace 或其他租户资料。

## 记录一个问题

1. 登录主管队列 `/queue`，打开“主管 QA 反馈闭环”，在“最近可复核会话样本”中选择
   一个样本。
2. 核对最终结果、路由、引用、工具摘要和客户反馈。客户的负面反馈只能作为线索，
   不能自动认定政策或模型错误。
3. 选择问题分类：事实错误、引用无依据、过期政策、路由错误、缺少升级、权限/隐私、
   工具参数、UI 可用性或坐席误用。
4. 指定严重程度、根因、同租户负责人和修复方式。自由文本只写必要的技术事实；即使
   API 有最小脱敏，也不要粘贴真实客户内容、订单、地址、证件、密钥或供应商 trace。
5. 选择一个 `data/evals/qa/regression-catalog.json` 中的回归用例。系统会拒绝不存在的
   Case，或与分类/修复类型不匹配的 Case。
6. 提交后保存问题 ID、事件序列、根因、负责人、修复说明和回归映射。问题的源证据是
   哈希/版本摘要；要重新查看会话，请通过受控样本端点，而不是把会话复制到外部系统。

## 高严重度处置与 R0 停用

高或严重问题可勾选“暂停此租户 R0 自助服务”。提交会在同一事务中把租户 R0 flag
设为 `enabled=false`，写入 `qa.r0_feature_flag_paused` 审计事件，并记录停用前后的
有效性摘要。低/中严重度请求该操作会被拒绝。

- 此接口只能关闭，不能重新开启，也不会删除灰度名单。
- 重新开启必须在修复、评测、人工复核和发布批准后，由主管使用既有
  `/v1/admin/self-service/r0-feature-flag` 独立完成。重新开启前应核对租户、灰度范围、
  修复 commit、评测报告与待解决 QA 问题；当前 Demo 没有生产变更批准或告警联动。
- 暂停 R0 不会取消 R2 工作流、工单或人工升级，也不等于完成真实事故响应。

## 把修复变成回归

先修改真实控制面，再新增或更新公开合成评测 Case，最后登记目录映射。不要只改
Prompt 或只写 QA 备注。

### 知识修复

1. 更新受控知识版本、生命周期和适用范围；过期/撤回的版本不能保持 `published`。
2. 在 `data/evals/rag/corpus.json` 表达历史与当前版本，在 `data/evals/rag/cases.json`
   增加当前版本、关键结论、禁止历史结论及 ACL 约束。
3. 在 `data/evals/qa/regression-catalog.json` 增加 `remediation_type: knowledge` 映射。
4. 运行 RAG 评测并在问题中记录报告/CI 证据引用。

### 规则修复

1. 修复所有会影响安全结论的确定性入口，例如 R0 前置门禁、Fake/真实网关输出约束和
   服务端后置强制策略；只改模型提示词不能证明入口不会绕过。
2. 在 `data/evals/triage/cases.json` 加入公开回归，并在目录中添加
   `remediation_type: rule` 映射。
3. 运行 triage 评测，并记录报告/CI 证据引用。

## 验证与关闭

状态只能按下列路径流转：

```text
open -> in_remediation -> verification_pending -> verified -> closed
                  ^              |
                  |--------------+
```

- `verified` 必须同时提供 `regression_status: passed` 和非空的
  `regression_evidence_ref`；否则 API 返回 `422`。
- 已验证问题的回归状态和证据引用不可再改写；关闭会保留这份通过证据。若发现新的
  回归失败，应创建新的问题或按后续治理流程重新评审，而不是把已验证记录改成失败。
- 失败评测应保留为 `failed`，问题回到 `in_remediation`，不要关闭或用文字覆盖失败。
- `closed` 是终态。若发现新问题，创建新的问题而不是修改不可变源证据或复用旧的
  幂等键。

本机合成质量门禁：

```bash
cd apps/api
uv run pytest -q tests/test_qa_feedback_loop.py
uv run eval_triage
uv run eval_rag --use-default-test-database
uv run alembic check

cd ../web
pnpm lint
pnpm typecheck
pnpm exec playwright test --grep 'QA 闭环'
```

`eval_rag --use-default-test-database` 会清空专用本机 `*_test` 数据库；不要指向共享或
生产库。Fake 评测、公开合成 Case 和浏览器演示只证明本地回归路径，不能作为真实
模型质量、真实政策正确性、真实 OMS/支付、OIDC、Outbox/DLQ、告警、值班或企业生产
放行的证据。
