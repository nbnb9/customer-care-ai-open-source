export type HandoffPriority = "low" | "normal" | "high" | "urgent";
export type HandoffStatus =
  | "requested"
  | "claimed"
  | "closed"
  | "safe_flow_restored";

export type HandoffQueueItem = {
  id: string;
  conversation_id: string;
  reason_code: string;
  status: HandoffStatus;
  priority: HandoffPriority;
  target_queue: string;
  sla_due_at: string;
  assigned_agent_id: string | null;
  claimed_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type HandoffContextFact = {
  label: string;
  value: string;
};

export type HandoffToolSummary = {
  tool_name: string;
  result_status: string;
};

export type HandoffCustomerVisibleCitation = {
  title: string;
  document_version: string;
  excerpt: string;
  source_locator: string;
};

export type HandoffContext = {
  schema_version: string;
  verification_level: string | null;
  conversation_summary: string;
  confirmed_facts: HandoffContextFact[];
  tool_summaries: HandoffToolSummary[];
  customer_visible_citations: HandoffCustomerVisibleCitation[];
  unresolved_questions: string[];
  risk_reasons: string[];
};

export type HandoffDetail = HandoffQueueItem & {
  context: HandoffContext;
};

const PRIORITY_LABELS: Record<HandoffPriority, string> = {
  low: "低",
  normal: "普通",
  high: "高",
  urgent: "紧急",
};

const STATUS_LABELS: Record<HandoffStatus, string> = {
  requested: "待领取",
  claimed: "已接手",
  closed: "已关闭",
  safe_flow_restored: "已恢复安全流程",
};

const REASON_LABELS: Record<string, string> = {
  customer_requested: "客户请求人工",
  safety_review: "安全复核",
  policy_action: "高风险业务请求",
  order_or_account_action: "订单或账户操作",
  identity_or_account: "身份或账户风险",
  identity_verification_required: "身份核验不足",
  r0_evidence_unavailable: "公开证据不足",
  r0_generation_failed: "自助回答系统故障",
  system_generation_repeated_failure: "连续回答失败",
};

export function handoffPriorityLabel(priority: HandoffPriority): string {
  return PRIORITY_LABELS[priority] ?? priority;
}

export function handoffStatusLabel(status: HandoffStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function handoffReasonLabel(reasonCode: string): string {
  return REASON_LABELS[reasonCode] ?? reasonCode;
}

export function formatHandoffDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(date);
}
