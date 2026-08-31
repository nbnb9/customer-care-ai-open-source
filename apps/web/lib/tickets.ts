export type Ticket = {
  id: string;
  tenant_id: string;
  customer_id: string;
  status: string;
  subject: string;
  description: string | null;
  priority: string;
  assignee_id: string | null;
  created_at: string;
  closed_at: string | null;
};

export type TicketEvent = {
  id: string;
  event_type: string;
  payload_digest: string | null;
  created_at: string;
};

export const STATUS_LABELS: Record<string, string> = {
  new: "新建",
  triaged: "已分诊",
  assigned: "已分配",
  in_progress: "处理中",
  waiting_customer: "等待客户",
  resolved: "已解决",
  closed: "已关闭",
  reopened: "已重开",
};

export const PRIORITY_LABELS: Record<string, string> = {
  low: "低",
  normal: "普通",
  high: "高",
  urgent: "紧急",
};

export const NEXT_STATUSES: Record<string, string[]> = {
  new: ["triaged"],
  triaged: ["assigned"],
  assigned: ["in_progress"],
  in_progress: ["waiting_customer", "resolved"],
  waiting_customer: ["in_progress", "resolved"],
  resolved: ["closed", "reopened"],
  reopened: ["in_progress", "resolved"],
  closed: [],
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function priorityLabel(priority: string): string {
  return PRIORITY_LABELS[priority] ?? priority;
}
