export type QAIssueCategory =
  | "factual_error"
  | "citation_unsupported"
  | "outdated_policy"
  | "incorrect_routing"
  | "escalation_missing"
  | "authorization_privacy"
  | "tool_parameter"
  | "ui_usability"
  | "agent_misuse";

export type QASeverity = "low" | "medium" | "high" | "critical";
export type QAReviewStatus =
  | "open"
  | "in_remediation"
  | "verification_pending"
  | "verified"
  | "closed";
export type QARootCause =
  | "knowledge_data"
  | "retrieval"
  | "routing_rule"
  | "authorization"
  | "tool_contract"
  | "ui"
  | "workflow"
  | "model_behavior"
  | "agent_process";
export type QARemediationType =
  | "knowledge"
  | "rule"
  | "code"
  | "ui"
  | "workflow"
  | "process";
export type QARegressionStatus = "not_run" | "passed" | "failed";

export type QAOwner = {
  id: string;
  display_name: string;
  role: "agent" | "supervisor" | "knowledge_operator";
};

export type QARegressionCatalogEntry = {
  id: string;
  evaluation_suite: "triage" | "rag";
  evaluation_case_id: string;
  category: QAIssueCategory;
  remediation_type: QARemediationType;
  description: string;
};

export type QAConversationSampleListItem = {
  conversation_id: string;
  created_at: string;
  last_answer_message_id: string;
  answer_mode: string | null;
  final_result_preview: string;
  citation_count: number;
  handoff_reason_code: string | null;
  handoff_status: string | null;
};

export type QAConversationSample = {
  conversation_id: string;
  messages: Array<{
    id: string;
    actor: "customer" | "assistant" | "system";
    text: string;
    response_kind: string | null;
    created_at: string;
  }>;
  route: {
    conversation_status: string;
    answer_mode: string | null;
    handoff_reason_code: string | null;
    handoff_status: string | null;
  };
  citations: Array<{
    document_key: string;
    document_version: string;
    title: string;
    excerpt: string;
    source_locator: string;
  }>;
  tool_summaries: Array<{ tool_name: string; result_status: string }>;
  final_result: {
    message_id: string;
    text: string;
    answer_mode: string | null;
    created_at: string;
  };
  source_versions: Array<{ document_key: string; document_version: string }>;
  feedback: { helpful_count: number; unhelpful_count: number };
};

export type QAReviewIssue = {
  id: string;
  source_conversation_id: string;
  category: QAIssueCategory;
  severity: QASeverity;
  status: QAReviewStatus;
  owner_id: string;
  root_cause: QARootCause;
  problem_summary: string;
  remediation_type: QARemediationType;
  remediation_summary: string;
  feature_flag: { paused: boolean; snapshot: Record<string, unknown> };
  regression: {
    catalog_case_id: string;
    evaluation_suite: "triage" | "rag";
    evaluation_case_id: string;
    status: QARegressionStatus;
    evidence_ref: string | null;
  };
  reported_by: string;
  verified_by: string | null;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
};

export type QAReviewIssueDetail = QAReviewIssue & {
  source: QAConversationSample;
  source_evidence: Record<string, unknown>;
  events: Array<{
    id: string;
    sequence: number;
    event_type: string;
    actor_id: string | null;
    summary: Record<string, unknown>;
    request_id: string;
    created_at: string;
  }>;
};

export const QA_CATEGORY_LABELS: Record<QAIssueCategory, string> = {
  factual_error: "事实错误",
  citation_unsupported: "引用无依据",
  outdated_policy: "政策过期",
  incorrect_routing: "路由错误",
  escalation_missing: "缺少升级",
  authorization_privacy: "权限或隐私",
  tool_parameter: "工具参数",
  ui_usability: "界面可用性",
  agent_misuse: "坐席误用",
};

export const QA_SEVERITY_LABELS: Record<QASeverity, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "严重",
};

export const QA_ROOT_CAUSE_LABELS: Record<QARootCause, string> = {
  knowledge_data: "知识数据",
  retrieval: "检索",
  routing_rule: "路由规则",
  authorization: "权限",
  tool_contract: "工具契约",
  ui: "界面",
  workflow: "流程",
  model_behavior: "模型行为",
  agent_process: "坐席流程",
};

export const QA_REMEDIATION_LABELS: Record<QARemediationType, string> = {
  knowledge: "知识修复",
  rule: "规则修复",
  code: "代码修复",
  ui: "界面修复",
  workflow: "流程修复",
  process: "流程治理",
};

export const QA_STATUS_LABELS: Record<QAReviewStatus, string> = {
  open: "待修复",
  in_remediation: "修复中",
  verification_pending: "待验证",
  verified: "已验证",
  closed: "已关闭",
};

export const QA_REGRESSION_STATUS_LABELS: Record<QARegressionStatus, string> = {
  not_run: "未运行",
  passed: "通过",
  failed: "失败",
};

export const QA_STATUS_TRANSITIONS: Record<QAReviewStatus, QAReviewStatus[]> = {
  open: ["in_remediation", "closed"],
  in_remediation: ["verification_pending", "open"],
  verification_pending: ["verified", "in_remediation"],
  verified: ["closed"],
  closed: [],
};
