from app.models.after_sales import AfterSalesWorkflowStatus

ALLOWED_TRANSITIONS: dict[AfterSalesWorkflowStatus, set[AfterSalesWorkflowStatus]] = {
    AfterSalesWorkflowStatus.draft: {AfterSalesWorkflowStatus.policy_checked},
    AfterSalesWorkflowStatus.policy_checked: {
        AfterSalesWorkflowStatus.awaiting_customer_confirmation,
        AfterSalesWorkflowStatus.cancelled,
    },
    AfterSalesWorkflowStatus.awaiting_customer_confirmation: {
        AfterSalesWorkflowStatus.awaiting_agent_approval,
        AfterSalesWorkflowStatus.cancelled,
    },
    # A material staff edit returns to customer confirmation; this is the one
    # controlled backwards edge needed to preserve informed consent.
    AfterSalesWorkflowStatus.awaiting_agent_approval: {
        AfterSalesWorkflowStatus.awaiting_customer_confirmation,
        AfterSalesWorkflowStatus.queued,
        AfterSalesWorkflowStatus.cancelled,
    },
    AfterSalesWorkflowStatus.queued: {AfterSalesWorkflowStatus.executing},
    AfterSalesWorkflowStatus.executing: {
        AfterSalesWorkflowStatus.completed,
        AfterSalesWorkflowStatus.failed,
        AfterSalesWorkflowStatus.compensation_required,
    },
    AfterSalesWorkflowStatus.completed: set(),
    AfterSalesWorkflowStatus.failed: set(),
    AfterSalesWorkflowStatus.compensation_required: set(),
    AfterSalesWorkflowStatus.cancelled: set(),
}


def can_transition(
    current: AfterSalesWorkflowStatus, target: AfterSalesWorkflowStatus
) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
