# CareOps 设计系统基础

- 阶段：步骤 41.5 / PZ-2
- 状态：候选基线，待原型和可访问性验收
- 适用：客户嵌入入口、坐席协同台、主管运营台、管理配置台

## 设计原则

1. **任务优先**：每个视图只有一个明确主任务；管理和技术信息按需下钻。
2. **证据邻近**：AI 结论、政策版本、外部事实和引用必须在用户做决定的位置附近。
3. **状态诚实**：建议、草稿、确认、审批、队列中、外部处理中和完成使用不同语义。
4. **风险渐进**：普通回复轻量；售后、身份、安全和不可逆动作逐步增加确认与解释。
5. **角色隔离**：导航和组件按角色生成，但最终权限始终由服务端执行。
6. **可恢复**：错误状态解释发生了什么、是否已产生副作用、下一步和责任人。
7. **密度可控**：坐席桌面允许紧凑密度；客户入口保持舒适密度，语义和顺序不因密度改变。

## 视觉方向

产品默认采用明亮、低噪声的企业工作台，而不是跟随操作系统自动切换未验收的暗色模式。暗色主题
只有在所有语义 token、图表、编辑器、第三方嵌入和可访问性状态完成独立验收后才发布。

- 品牌基调：可信、克制、行动导向。
- 主色：深蓝，表示主导航和确定性主动作。
- 辅色：青绿色，表示已验证来源、连接和可恢复成功。
- 风险色：琥珀用于等待/需复核，红色只用于危险、失败和阻断。
- 大面积背景保持中性；不用颜色区分客户身份、租户或唯一状态。

## Foundation tokens

以下 token 是设计契约；实现可以映射到 CSS variables/Tailwind theme，不允许页面直接复制 hex。

```css
:root {
  color-scheme: light;

  --cc-color-canvas: #f6f8fb;
  --cc-color-surface: #ffffff;
  --cc-color-surface-subtle: #f1f5f9;
  --cc-color-border: #cbd5e1;
  --cc-color-border-strong: #94a3b8;

  --cc-color-text: #0f172a;
  --cc-color-text-muted: #475569;
  --cc-color-text-subtle: #64748b;
  --cc-color-text-inverse: #ffffff;

  --cc-color-primary: #1d4ed8;
  --cc-color-primary-hover: #1e40af;
  --cc-color-primary-soft: #dbeafe;
  --cc-color-focus: #2563eb;

  --cc-color-success: #047857;
  --cc-color-success-soft: #d1fae5;
  --cc-color-warning: #92400e;
  --cc-color-warning-soft: #fef3c7;
  --cc-color-danger: #b91c1c;
  --cc-color-danger-soft: #fee2e2;
  --cc-color-info: #0369a1;
  --cc-color-info-soft: #e0f2fe;

  --cc-space-1: 0.25rem;
  --cc-space-2: 0.5rem;
  --cc-space-3: 0.75rem;
  --cc-space-4: 1rem;
  --cc-space-5: 1.25rem;
  --cc-space-6: 1.5rem;
  --cc-space-8: 2rem;
  --cc-space-10: 2.5rem;

  --cc-radius-sm: 0.375rem;
  --cc-radius-md: 0.625rem;
  --cc-radius-lg: 0.875rem;
  --cc-radius-pill: 999px;

  --cc-shadow-panel: 0 1px 2px rgb(15 23 42 / 0.05),
    0 8px 24px rgb(15 23 42 / 0.06);

  --cc-font-sans: Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei",
    system-ui, sans-serif;
  --cc-font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}
```

颜色是否达标必须由自动和人工对比度检查确认，不能因为 token 表中指定了颜色就跳过真实组件、
hover、disabled、图表和聚焦状态测试。

## 字体与层级

| Token | 尺寸/行高 | 用途 |
|---|---|---|
| `display-sm` | 30/38，600 | 客户入口关键状态，极少使用 |
| `heading-lg` | 24/32，650 | 页面标题 |
| `heading-md` | 18/26，650 | 面板标题 |
| `heading-sm` | 14/20，650 | 卡片/分组标题 |
| `body-md` | 14/22，400 | 工作台正文和消息 |
| `body-sm` | 12/18，400 | 辅助元数据；不得承载关键操作说明 |
| `label` | 12/16，600 | 字段、状态和筛选标签 |

中文正文不使用全大写或过宽字距。英文 ID、Trace 和外部引用使用 mono，但只在技术证据视图出现。

## 密度和布局

| 模式 | 控件最小高度 | 行/卡间距 | 使用范围 |
|---|---:|---:|---|
| Comfortable | 44 px | 12–16 px | 客户入口、设置、低频审批 |
| Compact | 36 px | 8–12 px | 坐席队列和主管表格 |

高风险、主 CTA 和触屏场景保持至少 44 × 44 CSS px。紧凑表格中的较小目标必须有足够间距或等价
大目标；不能用“坐席效率”绕过可访问性。

桌面坐席台建议：

- 左栏 280–340 px，可折叠但保留筛选与选中项。
- 中栏 minmax(560 px, 1fr)，始终承载会话和主动作。
- 右栏 320–400 px，可按任务固定/折叠。
- 低于三栏可用宽度时变为带状态保持的分视图，不把右栏压缩到不可读。

## 核心组件契约

### App Shell

- 角色专属主导航；当前租户和环境清晰但不抢占主任务。
- 全局搜索/命令入口只能搜索当前授权范围。
- Demo 身份切换仅在开发环境的独立工具栏显示，并带“合成环境”标识。

### Queue Item

- 主文本：客户问题/工单主题。
- 次文本：渠道、等待时长、客户/品牌摘要。
- 状态：SLA、风险、技能、AI/人工处理状态；不能只靠颜色。
- 解释当前排序原因，避免“神秘 AI 优先级”。

### Conversation Timeline

- 客户、坐席、AI 草稿、内部备注和系统事件有不同结构，不只靠背景色。
- AI 流式文本不显示为已完成；引用和允许动作只在最终状态出现。
- 内部备注在编辑器和已发送状态都明确标记“客户不可见”。

### Evidence Card

- 显示结论、来源标题、版本、生效范围和重新授权后的定位。
- `verified` 表示来源/范围验证，不表示最终政策结论绝对正确。
- 证据不足、冲突或撤回是阻断状态，不能降级成一个弱提示后继续发送。

### Controlled Action Card

- 显示对象、动作、理由、政策结果、影响、确认者、审批者和外部状态。
- 主按钮文案描述当前一步，例如“请求客户确认”，不能提前写“提交退货”。
- 编辑关键字段后显式清除旧确认，并解释原因。

### Status Badge / Stepper

- badge 用于可扫视状态；stepper 用于有责任人和顺序的多步流程。
- badge 必须包含文字/图标；颜色不是唯一信号。
- 外部状态至少区分 `queued`、`processing`、`confirmed`、`uncertain`、`failed`。

### Empty / Loading / Error

- 加载使用骨架或局部进度，不让整个工作台失去上下文。
- 空状态说明为什么为空和用户可做什么。
- 错误状态显示请求是否到达外部系统、是否可能已产生副作用和安全下一步。
- 不向一线用户暴露原始异常、SQL、token、供应商响应体或内部堆栈。

## 内容设计

- 面向用户使用“订单查询、保修、退货申请、补发申请、人工客服”等业务词。
- `R0/R1/R2`、`Mock OMS`、`Trace Viewer`、`Step 37` 等只存在于开发/技术文档。
- 避免虚假完成：“已创建草稿”不能写成“退货已提交”；“已入队”不能写成“OMS 已完成”。
- CTA 使用动词和对象：`查看来源`、`请求客户确认`、`批准并排队`、`转安全专员`。
- 错误文案包含恢复动作；归属隐藏场景保持相同 404，不用 UX 文案泄露存在性。

## 可访问性基线

以 [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/) 为发布目标，并增加以下项目约束：

- 所有交互可由键盘完成，焦点顺序跟随任务顺序，焦点不被 sticky 区域遮挡。
- 文本最小对比度、非文本控件对比度和焦点可见性通过自动与人工检查。
- 200% 缩放不丢失内容或功能；窄屏可回流，不强迫双向滚动完成主任务。
- 表单错误与字段关联；状态更新使用适当 live region，避免重复播报流式 token。
- 风险、状态、图表和优先级不用颜色作为唯一信息。
- 动画尊重 `prefers-reduced-motion`；自动更新队列不得抢夺键盘焦点。
- 身份认证最终遵循可访问认证要求；Demo 下拉框不能成为生产登录方案。

## 工程迁移规则

1. 先交付 token 和基础组件，再迁移页面；禁止边迁移边继续复制硬编码颜色。
2. 组件 API 使用业务语义（如 `risk="warning"`），不让调用方传任意颜色。
3. 每个组件包含 default/loading/empty/error/disabled/read-only/permission-denied 测试状态。
4. 浏览器 E2E 覆盖关键键盘路径、风险确认和窄屏主任务；截图只作为辅助，不替代语义断言。
5. 生产页面不得依赖 localStorage Demo 身份；迁移期间通过开发环境专用 provider 隔离。
6. 设计系统版本与部署版本、用户研究批次一起记录，便于定位体验回归。
