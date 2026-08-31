# 客户会话、SSE、R0 自助与人工升级 Runbook

本 Runbook 覆盖步骤 32–35 的本地合成会话实现。它建立可恢复事件协议、受控 R0 FAQ 灰度和人工升级闭环，不构成真实客户、生产 OIDC、跨区域流量或高可用运行的批准。

## 契约与安全边界

- `POST /v1/conversations`、`POST /messages`、`POST /handoffs` 和 `POST /feedback` 均要求 `Idempotency-Key`。同一客户、同一操作与相同请求重放返回首次响应；相同 key 改变请求体返回 `409`。
- 服务端从身份解析 `tenant_id` 和 `customer_id`。其他客户、其他租户及非客户角色访问某个 `conversation_id` 时统一得到 `404`，避免资源枚举。
- 每个事件在事务内分配一个唯一、递增的 `sequence`，并持久化 `event_id`、请求关联 ID 与 JSON payload。不得从浏览器提交 sequence、事件类型、租户或客户归属。
- `answer.delta` 只能包含 `message_id` 和 `delta`；引用、最终文本和允许操作只能放入 `answer.completed`。前端不得将 delta 当作已完成操作。
- R0 默认关闭。只有 `R0_SELF_SERVICE_MASTER_ENABLED=true` 且本租户数据库灰度开关命中当前客户时，普通消息才可进入 R0；客户不能用请求字段开启、扩大产品/语言范围或绕过灰度。
- 订单/物流、退款、退货、支付、投诉、隐私、登录/账户变更、安全及人工请求在 RAG/模型调用前直接创建人工升级，不能被视为退款/订单/安全工作流已经完成。无可信公开证据、版本冲突、无效引用或模型不确定也会升级。

## R0 灰度、回滚与质量信号

R0 只适用于 `public` 受众、可点击重新授权的引用和低风险 FAQ。它不执行订单、退款、账号或隐私动作。部署主开关和租户开关是两层独立保护：前者默认关闭并需要受控部署变更；后者由主管即时读写，适合限定 Demo 租户与小名单灰度。

在本机合成环境中，先在 API 环境文件设置并重启 API：

```bash
R0_SELF_SERVICE_MASTER_ENABLED=true
R0_SELF_SERVICE_PRODUCT=demo-support
R0_SELF_SERVICE_LANGUAGE=zh-CN
```

然后仅用当前租户主管身份为明确名单开启。`allow_all_customers` 只有在人工确认全租户范围后才可设为 `true`：

```bash
curl -X PUT http://localhost:8000/v1/admin/self-service/r0-feature-flag \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-supervisor-01' \
  --data '{"enabled":true,"allow_all_customers":false,"customer_ids":["customer-nw-01"]}'
```

即时停止租户 R0 时，不必等待 API 重启：

```bash
curl -X PUT http://localhost:8000/v1/admin/self-service/r0-feature-flag \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-ID: user-nw-supervisor-01' \
  --data '{"enabled":false,"allow_all_customers":false,"customer_ids":[]}'
```

服务端在检索/模型调用前和模型结果暴露前各读取一次有效开关；R0 不发送文本 delta，最终答复在持有租户开关行锁的事务中才持久化。关闭后新消息返回非政策确认，已生成但尚未提交的 R0 结果不会对客户发送。每次修改会写入 `r0_self_service.feature_flag_updated` 审计事件。主管可用 `GET /v1/admin/self-service/r0-feature-flag` 核实数据库状态和主开关是否共同生效。

客户对完成的 R0 回答可以提交 `helpful` 或 `unhelpful`。这只产生 `feedback.recorded` 事件和审计质量信号；不得把点赞率当作事实正确性、自动发布规则、自动放宽 ACL 或替代人工升级。人工请求和系统证据不足升级都通过 `handoff.created` 留下独立事件。

## 浏览器恢复流程

1. 页面保存已成功处理的最大 `sequence`。
2. 使用 `GET /v1/conversations/{id}/events?after_sequence=<sequence>` 请求事件流；标准 SSE 客户端也可将该 sequence 放在 `Last-Event-ID`。
3. 服务端只发送 `sequence > cursor` 的严格后缀，并把 sequence 写为 SSE 的 `id` 字段。
4. 若网络中断、服务器按空闲上限主动旋转连接，或浏览器刷新，重新以最后确认 sequence 请求；重复事件必须在客户端忽略，出现缺口时不能前移 cursor。
5. Demo 身份需要自定义头，因此 Web 使用 `fetch` 读取 `text/event-stream`；不得把身份头、Bearer Token、模型密钥或 CRM 凭据移到查询参数以强行使用原生 `EventSource`。

## 失败与人工升级

- 后台回复生成失败时，服务端追加 `error` 和 `workflow.updated` 事件，消息状态标记为 `failed`。错误 payload 不包含供应商异常、堆栈或客户内容，只给出“重试或转人工”的可操作提示。
- R0 证据不足、模型不确定或引用校验失败时，不把模型草稿降级为普通回复。服务端把客户消息标记为 `routed_to_handoff`，写入系统通知、`handoff.created` 和 `workflow.updated`；浏览器应等待这些持久事件确认状态。
- 浏览器对未完成回复设置 30 秒体验超时；超时不表示业务失败或状态完成，只提示用户重试或请求人工客服。
- 客户主动人工升级、R2/R3 风险拦截、R0 无证据/R0 故障、身份不足和连续生成失败都会持久化 `handoff.created` / `workflow.updated`。优先级、队列和 SLA 由服务端原因码映射，浏览器和模型不能提交或覆盖它们。

## 人工升级队列与上下文包（步骤 35）

- `GET /v1/handoffs` 只返回当前租户的未结束升级。主管可看全部；普通坐席只看待领取或自己已领取的记录。`GET /v1/handoffs/{id}` 返回同一权限边界内的最小上下文包。跨租户 ID 一律返回 404。
- 上下文包仅包含验证等级、摘要、系统观察到的事实、已因果关联的工具摘要、客户已见引用、直接标识脱敏后的未解决问题与风险原因。它不包含系统 Prompt、模型隐式推理、其他客户资料或完整 Trace。当前 R1 工具未与会话绑定，因此工具摘要为空是正常且诚实的结果。
- `POST /v1/handoffs/{id}/claim` 与 `POST /v1/handoffs/{id}/resolution` 都要求 `Idempotency-Key`。普通坐席领取后可用 `close_self_service` 关闭，或用 `restore_safe_flow` 移到 `awaiting_customer`；后者不会接受新的自动化消息或调用模型。
- `safety-review` 是专用队列。当前 Demo 没有安全专员角色，故只有 supervisor 可查看、领取和结束安全升级；普通 agent 不会在列表或详情中看到它。生产必须改为经过身份提供方验证的队列成员资格和技能/轮班策略。
- SLA 当前只是可审计的截止时间，不会自动通知、重分配或升级。超时、排班、Outbox、队列 worker 和值班报警属于生产门禁，不能以 UI 列表替代。

## 本地排查

```bash
cd apps/api
uv run pytest -q tests/test_conversations_api.py tests/test_r0_self_service.py

cd ../web
pnpm exec playwright test --grep '会话|人工升级'
```

若事件流无法建立，先确认 API 的 `/health`、`ALLOW_DEMO_IDENTITY=true`（仅开发/测试）和浏览器 `X-Demo-User-ID` 是否一致。若出现 `429`，检查 `CONVERSATION_MESSAGE_RATE_LIMIT` 与 `CONVERSATION_MESSAGE_RATE_WINDOW_SECONDS`；不要通过关闭服务端限流来绕过问题。若预期 R0 却收到非政策确认，先检查部署主开关、租户开关、当前客户是否在名单、产品/语言配置及是否存在当前有效的 `public` 词面证据；不要通过把订单、退款或账户资料标为 `public` 来“修复”问题。

## 仍未关闭的生产门禁

当前事件生产使用 FastAPI `BackgroundTasks`，适合 Local/CI 证明 202 + 持久事件语义，不是可跨进程重试的生产队列。步骤 39 的本地脱敏 Trace/版本/成本状态不改变这一判断；进入 Staging 前必须补齐 durable queue/Outbox、worker 崩溃恢复、事件保留和删除策略、静态/传输加密、真实 OIDC/会话认证、全局限流、生产级 Trace/成本/TTFT 指标、负载与断网演练，以及真实客户数据的合规评审。
