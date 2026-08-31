# 步骤 41.5 产品化 GO 证据登记表

- 结论日期：2026-08-31
- 当前结论：**NO-GO；步骤 42 继续暂停**
- 判定规则：工程实现、真实外部证据和有权负责人授权必须同时成立；三者不能互相替代

## 当前门禁

| 门禁 | 已核实事实 | 状态 | 解锁证据与责任人 |
|---|---|---|---|
| PZ-0 问题与购买验证 | 访谈脚本、招募矩阵、反证规则和记录模板已冻结；仓库没有真实访谈结果 | 阻断 | 产品负责人：完成至少 3 家目标企业、规定角色数量的研究，并取得至少 1 个有负责人/任务/沙箱/时间窗的试点下一步 |
| PZ-1 信息架构与关键任务 | 客户、坐席、主管外壳和六条关键任务已迁移到真实 API 页面；合成身份只在非生产环境显示 | 仓库基线完成 | PZ-4 真实坐席测试中不得出现身份混淆、主任务不可发现或草稿/执行状态混淆 |
| PZ-2 设计系统与可访问性 | 语义 token、角色壳层、状态组件已落地；Playwright 覆盖 WCAG 2.2 A/AA 自动扫描、跳过导航、390px/640px 回流；浏览器人工复核无控制台错误 | 仓库基线完成 | 产品/可访问性负责人复核自动结果，并把任何真实测试中发现的严重可用性问题关闭 |
| PZ-3 Shopify 真实只读 | 只读适配器、最小字段、短期 token、scope 校验、服务端 tenant/customer/order 绑定、R2 政策事实、失败关闭、离线测试和手动 Actions Job 已实现；本机没有 Shopify 配置，仓库没有真实 Job 结果 | 阻断 | Dev Store 所有者：创建/授权只读 app，安全提供三个 secret；工程负责人：运行手动 Job 并保存成功 Check URL 与无泄漏复核。外部写入保持关闭 |
| PZ-4 真实坐席效率 | 任务、正确答案、严重错误、计时和阈值协议已定义；仓库没有 12 名真实坐席对照数据 | 阻断 | 研究负责人：按冻结协议完成至少 12 名真实坐席和至少 2 名主管测试，提交去标识结果；所有严重错误为 0 且完成率/效率达到阈值 |
| 进入 Staging 授权 | 没有产品、售后运营、安全三方签署记录 | 阻断 | 三名有权负责人分别签署明确版本/范围/风险接受；工程通过不能代签 |

## 仓库证据

- 页面实现：`apps/web/app/(customer)`、`apps/web/app/(agent)`、`apps/web/app/(supervisor)`、
  `apps/web/components/product`。
- 可访问性门禁：`apps/web/e2e/accessibility.spec.ts`；任务 E2E：`apps/web/e2e/p0-journey.spec.ts`。
- Shopify 实现：`apps/api/app/modules/customer_tools/shopify_adapter.py`、
  `apps/api/app/modules/after_sales/factory.py`、`.github/workflows/shopify-sandbox.yml`。
- 研究与验收协议：`product-discovery-research-plan.md`、`agent-usability-acceptance.md`。

## 证据纪律

- “实现完成”不等于“真实供应商已验证”；真实 Job 必须能链接到对应 commit 的 Actions Check。
- “参与者喜欢”不等于购买证据；必须记录当前行为、替代方案、实施条件和明确下一步。
- 12 人门禁是本项目的最低发布阈值，不支持对整个行业作统计外推。
- 不把真实企业名称、客户数据、录音、访问链接、Shopify token/GID 或 secrets 提交到 Git。
- 任何跨租户暴露、错误客户订单、越过确认/审批、重复外部动作或安全事件降级立即维持 NO-GO。

## 解锁记录模板

```text
evidence_version:
commit_sha:
pz0_research_report_location:
shopify_actions_check_url:
pz4_usability_report_location:
open_severity_1_or_2_issues:
product_owner_approval_and_date:
after_sales_operations_approval_and_date:
security_owner_approval_and_date:
decision: GO | NO-GO
decision_rationale:
```
