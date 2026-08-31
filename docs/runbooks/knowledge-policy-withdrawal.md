# 知识政策错误发布与撤回 Runbook

## 适用范围与边界

本 Runbook 适用于已经发布、且确认错误的知识政策版本。当前实现是本机合成基线：
它没有生产认证、缓存集群、异步向量重建服务或已持久化的客户回答审计，因此不能
替代后续 Staging/生产事故流程。

不要直接删除 import、document 或 chunk，也不要把已发布版本原地改写。删除会破坏
可追溯性；原地改写会让历史引用无法解释。应撤回精确版本，再按正常发布流程导入
经过审批的新版本。

## 触发条件

- RAG Eval、人工复核、业务负责人或安全事件确认已发布政策有误。
- 出现租户、受众、过期或不可信内容可能被当作答案证据的迹象。
- 任何错误结论被返回给用户，即使尚未确认是否影响真实客户。

## 处置步骤

1. 记录事件：文档键、版本、产品、地区、语言、发现时间、发现人、影响范围和撤回
   原因。不要把客户个人信息复制进原因字段。
2. 用知识运营身份调用受控撤回端点，指定完整范围，不能只按文档键猜测目标：

   ```bash
   curl -X POST http://127.0.0.1:8000/v1/admin/knowledge/documents/withdrawals \
     -H 'Content-Type: application/json' \
     -H 'X-Demo-User-ID: user-nw-knowledge-operator-01' \
     -d '{
       "document_key":"return-policy",
       "version":"2.1.0",
       "product":"demo-support",
       "region":"cn-north",
       "language":"zh-CN",
       "reason":"Verified policy error; incident INC-123"
     }'
   ```

   Demo Header 只允许本机演示；真实环境应由受控身份提供者替代。成功响应必须为
   `status: withdrawn`，并返回 `audit_event_id`。重复同一请求是幂等的，会返回
   `already_withdrawn: true`。
3. 确认版本记录：原始 `knowledge_imports.requested_status` 仍保留为发布时的状态；
   对应 `knowledge_documents.status`、每个 `knowledge_chunks.status` 和 chunk metadata
   的 `status` 均为 `withdrawn`；审计事件动作应为
   `knowledge.document_withdrawn`，资源为该 document。
4. 重新验证读路径。用受影响身份、相同范围分别调用
   `POST /v1/knowledge/search` 和 `POST /v1/knowledge/answer`。撤回版本不得出现在
   检索结果、引用或最终回答；无其他证据时回答必须升级且 `citations` 为空。
5. 缓存与索引处置：当前没有应用层回答/检索缓存，因此记录“无缓存键可清除”。当前
   pgvector 向量没有变化，检索 SQL 在排序前同时过滤 chunk/document 的
   `published` 状态，因此无需重建向量索引。若未来新增缓存、异步索引或物化视图，
   必须在这里增加精确的失效键、队列状态和完成验证，不能把本说明当作已完成清缓存。
6. 运行回归门禁：

   ```bash
   cd apps/api
   uv run eval_rag --use-default-test-database
   ```

   该命令只允许本机 `_test` 数据库，且会清空并灌入合成世界。报告中的错误政策演练
   应显示：发布门槛发现错误、撤回后检索结果为 0、回答升级且引用数为 0、常规 Case
   回归通过。保存生成的 JSON/Markdown 报告路径和 `audit_event_id` 到事件记录。
7. 经业务负责人复核后，以新版本发布修正政策；重新运行评测，并在后续系统补齐
   已受影响会话的识别、通知、补救和人工回访。

## 失败分支

- 返回 403：调用者不是知识运营角色；不要改用坐席/客户身份绕过。
- 返回 404：检查租户和完整范围；系统不会透露其他租户的文档是否存在。
- 返回 409：目标不是 `published`；确认是否已撤回、过期或发布参数错误，不要强行
  修改数据库状态。
- 撤回后仍能检索或回答：立即停止相关知识回答入口，保存请求 ID、`audit_event_id`、
  查询范围和报告，再调查缓存/副本/部署版本。当前代码路径应同步排除该状态，这种
  现象是阻断级回归。

## 证据与关闭条件

- 精确范围、原因、操作者和 `audit_event_id` 可查。
- document 与全部 chunk 的撤回状态一致，原始 import 未被改写。
- 受影响身份下检索、来源读取和回答均不再暴露错误版本。
- RAG Eval 与错误政策演练通过；若使用固定外部模型，报告中模型名称与批准的模型
  标识匹配，且使用了该 provider 的阈值文件。
- 真实客户影响、通知、法律合规和恢复时限仍需生产事件流程；本仓库不宣称已经覆盖。
