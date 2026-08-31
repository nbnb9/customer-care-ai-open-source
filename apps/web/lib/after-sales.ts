export type AfterSalesActionType = "return" | "replacement" | "complaint";

export type AfterSalesWorkflowStatus =
  | "draft"
  | "policy_checked"
  | "awaiting_customer_confirmation"
  | "awaiting_agent_approval"
  | "queued"
  | "executing"
  | "completed"
  | "failed"
  | "compensation_required"
  | "cancelled";

export type AfterSalesWorkflow = {
  id: string;
  ticket_id: string;
  action_type: AfterSalesActionType;
  status: AfterSalesWorkflowStatus;
  order_number: string;
  policy: {
    policy_version: string;
    eligible: boolean;
    decision_code: string;
    action_type: AfterSalesActionType;
    order_number: string;
    order_status: string;
    warranty_active: boolean;
  };
  customer_summary: {
    action_label: string;
    impact_summary: string;
    amount_minor: number | null;
    currency: string | null;
    manual_approval_required: boolean;
    risk_notices: string[];
  };
  customer_confirmed_at: string | null;
  agent_approved_at: string | null;
  queued_at: string | null;
  external_status: string;
  external_reference: string | null;
  receipt_summary: Record<string, unknown>;
  reconciliation_required: boolean;
  failure_code: string | null;
  compensation_reason_code: string | null;
  created_at: string;
  updated_at: string;
};

export const AFTER_SALES_ACTION_LABELS: Record<AfterSalesActionType, string> = {
  return: "退货申请",
  replacement: "补发申请",
  complaint: "投诉案例",
};

export const AFTER_SALES_STATUS_LABELS: Record<AfterSalesWorkflowStatus, string> = {
  draft: "草稿",
  policy_checked: "已完成规则校验",
  awaiting_customer_confirmation: "等待客户确认",
  awaiting_agent_approval: "等待坐席审批",
  queued: "已进入受控执行队列",
  executing: "正在核对外部执行结果",
  completed: "已完成",
  failed: "执行失败",
  compensation_required: "需要人工补偿",
  cancelled: "已取消",
};

export function afterSalesStatusLabel(status: AfterSalesWorkflowStatus): string {
  return AFTER_SALES_STATUS_LABELS[status] ?? status;
}

export function afterSalesExecutionMessage(workflow: AfterSalesWorkflow): string | null {
  if (workflow.status === "queued") {
    return "已通过双重确认，等待受控 Worker 调用 Mock OMS；尚未产生外部动作。";
  }
  if (workflow.status === "executing" && workflow.reconciliation_required) {
    return "外部系统状态正在核对。超时不会自动重复提交。";
  }
  if (workflow.status === "failed") {
    return `执行未完成（${workflow.failure_code ?? "UNKNOWN"}）。系统不会静默重试外部写入。`;
  }
  if (workflow.status === "compensation_required") {
    return `需要人工补偿（${workflow.compensation_reason_code ?? "UNKNOWN"}）。`;
  }
  if (workflow.status === "completed") {
    return "Mock OMS 已返回完成回执；没有退款、取消订单或对外消息动作。";
  }
  return null;
}
