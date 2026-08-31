"""Server-controlled R2 draft, confirmation, approval and audit lifecycle."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.after_sales import (
    AfterSalesActionType,
    AfterSalesIdempotencyRecord,
    AfterSalesWorkflow,
    AfterSalesWorkflowEvent,
    AfterSalesWorkflowStatus,
)
from app.models.audit import AuditEvent
from app.models.identity import Customer, User, UserRole
from app.models.tickets import Ticket
from app.modules.customer_tools.errors import CustomerToolsProviderError
from app.modules.identity.policies import (
    can_update_ticket,
    has_verified_customer_context,
)
from app.modules.identity.principal import CurrentPrincipal
from app.shared.middleware import request_id_var
from app.shared.observability import bind_resources, current_traceparent, observe_span

from .factory import get_after_sales_order_facts_adapter
from .policy import PolicyDecision, evaluate_policy
from .ports import AfterSalesOrderFactsPort
from .schemas import (
    AfterSalesCustomerSummaryRead,
    AfterSalesDraftCreate,
    AfterSalesPolicyRead,
    AfterSalesWorkflowEventRead,
    AfterSalesWorkflowRead,
    AgentApprovalRequest,
    CustomerConfirmationRequest,
)
from .state import can_transition

CREATE_WORKFLOW_OPERATION = "POST:/v1/after-sales/workflows"


class ExecutionPreconditionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def request_id() -> str:
    value = request_id_var.get()
    return value if value != "-" else f"request-{uuid.uuid4().hex}"


def external_idempotency_key(workflow_id: str) -> str:
    """Stable internal key, intentionally never represented in an API DTO."""

    return f"after-sales-workflow:{workflow_id}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workflow_summary(workflow: AfterSalesWorkflow) -> str:
    snapshot = workflow.policy_snapshot
    return _json_summary(
        {
            "action_type": workflow.action_type.value,
            "agent_approved": workflow.agent_approved_by is not None,
            "customer_confirmed": workflow.customer_confirmed_by is not None,
            "external_status": workflow.external_status,
            "policy_decision_code": snapshot.get("decision_code"),
            "reconciliation_required": workflow.reconciliation_required,
            "status": workflow.status.value,
        }
    )


def _append_event(
    db: Session,
    workflow: AfterSalesWorkflow,
    *,
    actor_id: str | None,
    event_type: str,
    from_status: AfterSalesWorkflowStatus | None,
    to_status: AfterSalesWorkflowStatus | None,
    summary: dict[str, object],
    event_request_id: str | None = None,
) -> None:
    workflow.last_event_sequence += 1
    db.add(
        AfterSalesWorkflowEvent(
            id=new_id("after-sales-event"),
            workflow_id=workflow.id,
            sequence=workflow.last_event_sequence,
            actor_id=actor_id,
            event_type=event_type,
            from_status=from_status.value if from_status is not None else None,
            to_status=to_status.value if to_status is not None else None,
            summary=summary,
            request_id=event_request_id or request_id(),
        )
    )


def _write_audit(
    db: Session,
    *,
    workflow: AfterSalesWorkflow,
    actor_id: str | None,
    action: str,
    before_summary: str | None,
    after_summary: str | None,
    audit_request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            id=new_id("audit"),
            tenant_id=workflow.tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="after_sales_workflow",
            resource_id=workflow.id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=audit_request_id or request_id(),
        )
    )


def _transition(
    db: Session,
    workflow: AfterSalesWorkflow,
    *,
    actor_id: str | None,
    target: AfterSalesWorkflowStatus,
    event_type: str,
    summary: dict[str, object],
) -> None:
    current = workflow.status
    if not can_transition(current, target):
        raise HTTPException(
            status_code=409, detail="Invalid after-sales workflow transition"
        )
    workflow.status = target
    _append_event(
        db,
        workflow,
        actor_id=actor_id,
        event_type=event_type,
        from_status=current,
        to_status=target,
        summary=summary,
    )


def _normalize_idempotency_key(value: str) -> str:
    key = value.strip()
    if not 8 <= len(key) <= 255 or any(ord(char) < 33 for char in key):
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
    return key


def _fingerprint(data: BaseModel) -> str:
    canonical = json.dumps(
        data.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _hash(canonical)


def _find_idempotency_record(
    db: Session,
    *,
    principal: CurrentPrincipal,
    operation: str,
    key_hash: str,
) -> AfterSalesIdempotencyRecord | None:
    return db.scalar(
        select(AfterSalesIdempotencyRecord).where(
            AfterSalesIdempotencyRecord.tenant_id == principal.tenant_id,
            AfterSalesIdempotencyRecord.principal_id == principal.user_id,
            AfterSalesIdempotencyRecord.operation == operation,
            AfterSalesIdempotencyRecord.idempotency_key_hash == key_hash,
        )
    )


def _replay_or_conflict(
    record: AfterSalesIdempotencyRecord, request_fingerprint: str
) -> dict[str, object]:
    if record.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )
    return record.response_body


def _store_idempotency_record(
    db: Session,
    *,
    principal: CurrentPrincipal,
    operation: str,
    key_hash: str,
    request_fingerprint: str,
    workflow: AfterSalesWorkflow,
    response_body: dict[str, object],
) -> None:
    db.add(
        AfterSalesIdempotencyRecord(
            id=new_id("after-sales-idempotency"),
            tenant_id=principal.tenant_id,
            principal_id=principal.user_id,
            operation=operation,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            workflow_id=workflow.id,
            response_body=response_body,
        )
    )


def _require_staff(principal: CurrentPrincipal) -> None:
    if principal.role not in {UserRole.agent, UserRole.supervisor}:
        raise HTTPException(
            status_code=403, detail="Agent or supervisor role is required"
        )


def _get_ticket_for_draft(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    if ticket is None or not can_update_ticket(
        principal, ticket_tenant_id=ticket.tenant_id
    ):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _get_workflow_or_404(
    db: Session,
    principal: CurrentPrincipal,
    workflow_id: str,
    *,
    for_update: bool = False,
) -> AfterSalesWorkflow:
    stmt = select(AfterSalesWorkflow).where(
        AfterSalesWorkflow.id == workflow_id,
        AfterSalesWorkflow.tenant_id == principal.tenant_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    workflow = db.scalar(stmt)
    if workflow is None:
        raise HTTPException(status_code=404, detail="After-sales workflow not found")
    if principal.role is UserRole.customer:
        if principal.customer_id != workflow.customer_id:
            raise HTTPException(
                status_code=404, detail="After-sales workflow not found"
            )
    elif principal.role not in {UserRole.agent, UserRole.supervisor}:
        raise HTTPException(status_code=404, detail="After-sales workflow not found")
    bind_resources(ticket_id=workflow.ticket_id, workflow_id=workflow.id)
    return workflow


def _policy_for_ticket(
    ticket: Ticket,
    action_type: AfterSalesActionType,
    *,
    adapter: AfterSalesOrderFactsPort | None = None,
) -> PolicyDecision | None:
    if ticket.order_id is None:
        return None
    return evaluate_policy(
        adapter=adapter or get_after_sales_order_facts_adapter(),
        tenant_id=ticket.tenant_id,
        customer_id=ticket.customer_id,
        order_id=ticket.order_id,
        action_type=action_type,
    )


def _require_eligible_policy(
    ticket: Ticket,
    action_type: AfterSalesActionType,
    *,
    adapter: AfterSalesOrderFactsPort | None = None,
) -> PolicyDecision:
    try:
        decision = _policy_for_ticket(ticket, action_type, adapter=adapter)
    except CustomerToolsProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail="Order provider is temporarily unavailable",
        ) from exc
    if decision is None:
        raise HTTPException(
            status_code=422,
            detail="A server-bound owned order is required for an after-sales draft",
        )
    if not decision.eligible:
        raise HTTPException(
            status_code=422,
            detail=(
                "Current deterministic policy does not allow this after-sales action"
            ),
        )
    return decision


def _workflow_read(workflow: AfterSalesWorkflow) -> AfterSalesWorkflowRead:
    return AfterSalesWorkflowRead(
        id=workflow.id,
        ticket_id=workflow.ticket_id,
        action_type=workflow.action_type,
        status=workflow.status,
        order_number=workflow.order_number,
        policy=AfterSalesPolicyRead.model_validate(workflow.policy_snapshot),
        customer_summary=AfterSalesCustomerSummaryRead.model_validate(
            workflow.customer_summary
        ),
        customer_confirmed_at=workflow.customer_confirmed_at,
        agent_approved_at=workflow.agent_approved_at,
        queued_at=workflow.queued_at,
        external_status=workflow.external_status,
        external_reference=workflow.external_reference,
        receipt_summary=_receipt_summary(workflow.external_receipt),
        reconciliation_required=workflow.reconciliation_required,
        failure_code=workflow.failure_code,
        compensation_reason_code=workflow.compensation_reason_code,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _receipt_summary(receipt: dict[str, object]) -> dict[str, object]:
    """Project stored OMS data again so a future adapter cannot widen the DTO."""

    allowed = {"adapter", "effect", "action_type", "status"}
    return {
        key: value
        for key, value in receipt.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }


def _response_body(workflow: AfterSalesWorkflow) -> dict[str, object]:
    return _workflow_read(workflow).model_dump(mode="json")


def serialize_workflow(workflow: AfterSalesWorkflow) -> AfterSalesWorkflowRead:
    """Public projection that never includes IDs/keys used only internally."""

    return _workflow_read(workflow)


def _response_snapshot(db: Session, workflow: AfterSalesWorkflow) -> dict[str, object]:
    db.flush()
    db.refresh(workflow)
    return _response_body(workflow)


def _commit_or_replay(
    db: Session,
    *,
    principal: CurrentPrincipal,
    operation: str,
    key_hash: str,
    request_fingerprint: str,
) -> dict[str, object] | None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db,
            principal=principal,
            operation=operation,
            key_hash=key_hash,
        )
        if winner is None:
            raise
        return _replay_or_conflict(winner, request_fingerprint)
    return None


def create_workflow(
    db: Session,
    principal: CurrentPrincipal,
    data: AfterSalesDraftCreate,
    idempotency_key: str,
) -> AfterSalesWorkflow | dict[str, object]:
    """Create a staff-owned draft; no LLM or external write is invoked here."""

    _require_staff(principal)
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = _hash(normalized_key)
    request_fingerprint = _fingerprint(data)
    existing = _find_idempotency_record(
        db,
        principal=principal,
        operation=CREATE_WORKFLOW_OPERATION,
        key_hash=key_hash,
    )
    if existing is not None:
        return _replay_or_conflict(existing, request_fingerprint)

    ticket = _get_ticket_for_draft(db, principal, data.ticket_id)
    bind_resources(ticket_id=ticket.id)
    decision = _require_eligible_policy(ticket, data.action_type)
    with observe_span(
        "workflow.draft_create",
        attributes={"customer_care.workflow.action_type": data.action_type.value},
    ):
        workflow = AfterSalesWorkflow(
            id=new_id("after-sales-workflow"),
            tenant_id=ticket.tenant_id,
            customer_id=ticket.customer_id,
            ticket_id=ticket.id,
            action_type=data.action_type,
            status=AfterSalesWorkflowStatus.draft,
            order_number=decision.facts.order_number,
            policy_snapshot=decision.policy_snapshot,
            customer_summary=decision.customer_summary,
            created_by=principal.user_id,
            external_idempotency_key_hash=_hash(external_idempotency_key("pending")),
            observability_traceparent=current_traceparent(),
        )
        bind_resources(workflow_id=workflow.id)
    # The workflow ID forms the external key.  Assign it before persistence so
    # a worker can recompute and verify the digest without storing the raw key.
    workflow.external_idempotency_key_hash = _hash(
        external_idempotency_key(workflow.id)
    )
    db.add(workflow)
    db.flush()
    _append_event(
        db,
        workflow,
        actor_id=principal.user_id,
        event_type="draft_created",
        from_status=None,
        to_status=AfterSalesWorkflowStatus.draft,
        summary={"action_type": workflow.action_type.value},
    )
    _transition(
        db,
        workflow,
        actor_id=principal.user_id,
        target=AfterSalesWorkflowStatus.policy_checked,
        event_type="policy_checked",
        summary={"decision_code": decision.decision_code, "eligible": True},
    )
    _transition(
        db,
        workflow,
        actor_id=principal.user_id,
        target=AfterSalesWorkflowStatus.awaiting_customer_confirmation,
        event_type="customer_confirmation_requested",
        summary={"manual_approval_required": True},
    )
    _write_audit(
        db,
        workflow=workflow,
        actor_id=principal.user_id,
        action="after_sales.workflow_draft_created",
        before_summary=None,
        after_summary=_workflow_summary(workflow),
    )
    response_body = _response_snapshot(db, workflow)
    _store_idempotency_record(
        db,
        principal=principal,
        operation=CREATE_WORKFLOW_OPERATION,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        workflow=workflow,
        response_body=response_body,
    )
    replay = _commit_or_replay(
        db,
        principal=principal,
        operation=CREATE_WORKFLOW_OPERATION,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    db.refresh(workflow)
    return workflow


def list_workflows(
    db: Session,
    principal: CurrentPrincipal,
    *,
    ticket_id: str | None = None,
) -> list[AfterSalesWorkflow]:
    if principal.role not in {UserRole.customer, UserRole.agent, UserRole.supervisor}:
        raise HTTPException(
            status_code=403, detail="After-sales workflow access is unavailable"
        )
    stmt = select(AfterSalesWorkflow).where(
        AfterSalesWorkflow.tenant_id == principal.tenant_id
    )
    if principal.role is UserRole.customer:
        if principal.customer_id is None:
            return []
        stmt = stmt.where(AfterSalesWorkflow.customer_id == principal.customer_id)
    if ticket_id is not None:
        stmt = stmt.where(AfterSalesWorkflow.ticket_id == ticket_id)
    return list(db.scalars(stmt.order_by(AfterSalesWorkflow.created_at.desc())))


def get_workflow(
    db: Session, principal: CurrentPrincipal, workflow_id: str
) -> AfterSalesWorkflow:
    return _get_workflow_or_404(db, principal, workflow_id)


def list_workflow_events(
    db: Session, principal: CurrentPrincipal, workflow_id: str
) -> list[AfterSalesWorkflowEvent]:
    workflow = _get_workflow_or_404(db, principal, workflow_id)
    return list(
        db.scalars(
            select(AfterSalesWorkflowEvent)
            .where(AfterSalesWorkflowEvent.workflow_id == workflow.id)
            .order_by(AfterSalesWorkflowEvent.sequence)
        )
    )


def customer_confirmation_operation(workflow_id: str) -> str:
    return f"POST:/v1/after-sales/workflows/{workflow_id}/customer-confirmation"


def confirm_by_customer(
    db: Session,
    principal: CurrentPrincipal,
    workflow_id: str,
    data: CustomerConfirmationRequest,
    idempotency_key: str,
) -> AfterSalesWorkflow | dict[str, object]:
    if not has_verified_customer_context(principal):
        raise HTTPException(
            status_code=403,
            detail="Only verified customers can confirm an after-sales draft",
        )
    assert principal.customer_id is not None
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = _hash(normalized_key)
    request_fingerprint = _fingerprint(data)
    operation = customer_confirmation_operation(workflow_id)
    workflow = _get_workflow_or_404(db, principal, workflow_id, for_update=True)
    existing = _find_idempotency_record(
        db, principal=principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return _replay_or_conflict(existing, request_fingerprint)
    before_summary = _workflow_summary(workflow)

    already_applied = (
        data.decision == "confirm" and workflow.customer_confirmed_by is not None
    ) or (
        data.decision == "cancel"
        and workflow.status is AfterSalesWorkflowStatus.cancelled
    )
    if not already_applied:
        if (
            workflow.status
            is not AfterSalesWorkflowStatus.awaiting_customer_confirmation
        ):
            raise HTTPException(
                status_code=409,
                detail="Workflow is not awaiting customer confirmation",
            )
        if data.decision == "confirm":
            workflow.customer_confirmed_by = principal.user_id
            workflow.customer_confirmed_at = datetime.now(UTC)
            _transition(
                db,
                workflow,
                actor_id=principal.user_id,
                target=AfterSalesWorkflowStatus.awaiting_agent_approval,
                event_type="customer_confirmation_recorded",
                summary={"decision": "confirm", "verification": "server_verified"},
            )
            audit_action = "after_sales.customer_confirmation_recorded"
        else:
            _transition(
                db,
                workflow,
                actor_id=principal.user_id,
                target=AfterSalesWorkflowStatus.cancelled,
                event_type="customer_confirmation_cancelled",
                summary={"decision": "cancel"},
            )
            audit_action = "after_sales.customer_cancelled"
        _write_audit(
            db,
            workflow=workflow,
            actor_id=principal.user_id,
            action=audit_action,
            before_summary=before_summary,
            after_summary=_workflow_summary(workflow),
        )

    response_body = _response_snapshot(db, workflow)
    _store_idempotency_record(
        db,
        principal=principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        workflow=workflow,
        response_body=response_body,
    )
    replay = _commit_or_replay(
        db,
        principal=principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    db.refresh(workflow)
    return workflow


def agent_approval_operation(workflow_id: str) -> str:
    return f"POST:/v1/after-sales/workflows/{workflow_id}/agent-approval"


def _reset_customer_confirmation(workflow: AfterSalesWorkflow) -> None:
    workflow.customer_confirmed_by = None
    workflow.customer_confirmed_at = None
    workflow.agent_approved_by = None
    workflow.agent_approved_at = None
    workflow.queued_at = None


def approve_by_agent(
    db: Session,
    principal: CurrentPrincipal,
    workflow_id: str,
    data: AgentApprovalRequest,
    idempotency_key: str,
) -> AfterSalesWorkflow | dict[str, object]:
    _require_staff(principal)
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = _hash(normalized_key)
    request_fingerprint = _fingerprint(data)
    operation = agent_approval_operation(workflow_id)
    workflow = _get_workflow_or_404(db, principal, workflow_id, for_update=True)
    existing = _find_idempotency_record(
        db, principal=principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return _replay_or_conflict(existing, request_fingerprint)
    before_summary = _workflow_summary(workflow)

    if data.decision == "approve":
        if workflow.agent_approved_by is None:
            if workflow.status is not AfterSalesWorkflowStatus.awaiting_agent_approval:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is not awaiting agent approval",
                )
            workflow.agent_approved_by = principal.user_id
            workflow.agent_approved_at = datetime.now(UTC)
            workflow.queued_at = datetime.now(UTC)
            _transition(
                db,
                workflow,
                actor_id=principal.user_id,
                target=AfterSalesWorkflowStatus.queued,
                event_type="agent_approval_recorded",
                summary={"decision": "approve", "role": principal.role.value},
            )
            _write_audit(
                db,
                workflow=workflow,
                actor_id=principal.user_id,
                action="after_sales.agent_approval_recorded",
                before_summary=before_summary,
                after_summary=_workflow_summary(workflow),
            )
    elif data.decision == "reject":
        if workflow.status is not AfterSalesWorkflowStatus.cancelled:
            if workflow.status is not AfterSalesWorkflowStatus.awaiting_agent_approval:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is not awaiting agent approval",
                )
            _transition(
                db,
                workflow,
                actor_id=principal.user_id,
                target=AfterSalesWorkflowStatus.cancelled,
                event_type="agent_approval_rejected",
                summary={"decision": "reject", "role": principal.role.value},
            )
            _write_audit(
                db,
                workflow=workflow,
                actor_id=principal.user_id,
                action="after_sales.agent_rejected",
                before_summary=before_summary,
                after_summary=_workflow_summary(workflow),
            )
    else:
        if workflow.status is not AfterSalesWorkflowStatus.awaiting_agent_approval:
            raise HTTPException(
                status_code=409,
                detail="Workflow is not awaiting agent approval",
            )
        assert data.edited_action_type is not None
        ticket = _get_ticket_for_draft(db, principal, workflow.ticket_id)
        decision = _require_eligible_policy(ticket, data.edited_action_type)
        workflow.action_type = data.edited_action_type
        workflow.order_number = decision.facts.order_number
        workflow.policy_snapshot = decision.policy_snapshot
        workflow.customer_summary = decision.customer_summary
        _reset_customer_confirmation(workflow)
        _append_event(
            db,
            workflow,
            actor_id=principal.user_id,
            event_type="draft_edited",
            from_status=workflow.status,
            to_status=workflow.status,
            summary={"action_type": data.edited_action_type.value},
        )
        _transition(
            db,
            workflow,
            actor_id=principal.user_id,
            target=AfterSalesWorkflowStatus.awaiting_customer_confirmation,
            event_type="customer_reconfirmation_requested",
            summary={"reason": "material_draft_edit"},
        )
        _write_audit(
            db,
            workflow=workflow,
            actor_id=principal.user_id,
            action="after_sales.draft_edited",
            before_summary=before_summary,
            after_summary=_workflow_summary(workflow),
        )

    response_body = _response_snapshot(db, workflow)
    _store_idempotency_record(
        db,
        principal=principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        workflow=workflow,
        response_body=response_body,
    )
    replay = _commit_or_replay(
        db,
        principal=principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    db.refresh(workflow)
    return workflow


def revalidate_workflow_for_execution(
    db: Session,
    workflow: AfterSalesWorkflow,
    *,
    policy_adapter: AfterSalesOrderFactsPort | None = None,
) -> None:
    """Fail closed immediately before the OMS call.

    The persisted confirmation is not trusted by itself: the current customer
    identity, ticket binding, approved staff role, policy facts, workflow state
    and deterministic external key digest are all checked again.
    """

    if workflow.status is not AfterSalesWorkflowStatus.executing:
        raise ExecutionPreconditionError("WORKFLOW_NOT_EXECUTING")
    ticket = db.get(Ticket, workflow.ticket_id)
    if (
        ticket is None
        or ticket.tenant_id != workflow.tenant_id
        or ticket.customer_id != workflow.customer_id
        or ticket.order_id is None
    ):
        raise ExecutionPreconditionError("TICKET_BINDING_INVALID")
    customer = db.get(Customer, workflow.customer_id)
    if customer is None or customer.tenant_id != workflow.tenant_id:
        raise ExecutionPreconditionError("CUSTOMER_BINDING_INVALID")
    customer_user = db.get(User, customer.user_id)
    if customer_user is None or customer_user.tenant_id != workflow.tenant_id:
        raise ExecutionPreconditionError("CUSTOMER_IDENTITY_INVALID")
    customer_principal = CurrentPrincipal(
        user_id=customer_user.id,
        tenant_id=customer_user.tenant_id,
        role=customer_user.role,
        verification_level=customer_user.verification_level,
        customer_id=customer.id,
    )
    if (
        workflow.customer_confirmed_by != customer_user.id
        or workflow.customer_confirmed_at is None
        or not has_verified_customer_context(customer_principal)
    ):
        raise ExecutionPreconditionError("CUSTOMER_CONFIRMATION_NOT_REVALIDATED")
    approver = (
        db.get(User, workflow.agent_approved_by)
        if workflow.agent_approved_by is not None
        else None
    )
    if (
        approver is None
        or approver.tenant_id != workflow.tenant_id
        or approver.role not in {UserRole.agent, UserRole.supervisor}
        or workflow.agent_approved_at is None
    ):
        raise ExecutionPreconditionError("AGENT_APPROVAL_NOT_REVALIDATED")
    try:
        decision = _policy_for_ticket(
            ticket, workflow.action_type, adapter=policy_adapter
        )
    except CustomerToolsProviderError as exc:
        raise ExecutionPreconditionError("POLICY_PROVIDER_UNAVAILABLE") from exc
    if decision is None or not decision.eligible:
        raise ExecutionPreconditionError("CURRENT_POLICY_NOT_ELIGIBLE")
    if decision.policy_snapshot != workflow.policy_snapshot:
        raise ExecutionPreconditionError("POLICY_SNAPSHOT_CHANGED")
    if workflow.external_idempotency_key_hash != _hash(
        external_idempotency_key(workflow.id)
    ):
        raise ExecutionPreconditionError("EXTERNAL_IDEMPOTENCY_KEY_INVALID")


def serialize_event(event: AfterSalesWorkflowEvent) -> AfterSalesWorkflowEventRead:
    return AfterSalesWorkflowEventRead.model_validate(event)
