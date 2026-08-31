"""Controlled R2 execution worker with timeout reconciliation, never blind retry."""

from __future__ import annotations

import argparse
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.after_sales import (
    AfterSalesWorkflow,
    AfterSalesWorkflowEvent,
    AfterSalesWorkflowStatus,
)
from app.models.audit import AuditEvent
from app.shared.observability import (
    bind_principal,
    bind_resources,
    observe_background_task,
    set_span_attributes,
)

from . import service
from .mock_oms import get_mock_oms_adapter
from .ports import (
    AfterSalesOmsPort,
    OmsOperationResult,
    OmsOperationState,
    OmsWriteRequest,
)
from .state import can_transition

EXECUTION_LEASE = timedelta(minutes=5)
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")


def _worker_request_id() -> str:
    return f"after-sales-worker-{uuid.uuid4().hex}"


def _summary(workflow: AfterSalesWorkflow) -> str:
    return (
        "{"
        f'"action_type":"{workflow.action_type.value}",'
        f'"external_status":"{workflow.external_status}",'
        f'"reconciliation_required":{str(workflow.reconciliation_required).lower()},'
        f'"status":"{workflow.status.value}"'
        "}"
    )


def _event(
    db: Session,
    workflow: AfterSalesWorkflow,
    *,
    event_type: str,
    from_status: AfterSalesWorkflowStatus | None,
    to_status: AfterSalesWorkflowStatus | None,
    summary: dict[str, object],
    worker_request_id: str,
) -> None:
    workflow.last_event_sequence += 1
    db.add(
        AfterSalesWorkflowEvent(
            id=service.new_id("after-sales-event"),
            workflow_id=workflow.id,
            sequence=workflow.last_event_sequence,
            actor_id=None,
            event_type=event_type,
            from_status=from_status.value if from_status is not None else None,
            to_status=to_status.value if to_status is not None else None,
            summary=summary,
            request_id=worker_request_id,
        )
    )


def _audit(
    db: Session,
    workflow: AfterSalesWorkflow,
    *,
    action: str,
    before_summary: str | None,
    after_summary: str | None,
    worker_request_id: str,
) -> None:
    db.add(
        AuditEvent(
            id=service.new_id("audit"),
            tenant_id=workflow.tenant_id,
            actor_id=None,
            action=action,
            resource_type="after_sales_workflow",
            resource_id=workflow.id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=worker_request_id,
        )
    )


def _safe_code(value: str | None, fallback: str) -> str:
    if value and _SAFE_CODE.fullmatch(value):
        return value
    return fallback


def _safe_receipt(value: dict[str, object] | None) -> dict[str, object]:
    """Do not persist arbitrary external payloads in this demo's visible DTO."""

    if not value:
        return {}
    allowed = {"adapter", "effect", "action_type", "status"}
    return {
        key: item
        for key, item in value.items()
        if key in allowed and isinstance(item, (str, int, float, bool))
    }


def _request_for(workflow: AfterSalesWorkflow) -> OmsWriteRequest:
    return OmsWriteRequest(
        tenant_id=workflow.tenant_id,
        workflow_id=workflow.id,
        action_type=workflow.action_type.value,
        idempotency_key=service.external_idempotency_key(workflow.id),
    )


def _claim_next_queued(db: Session, *, now: datetime) -> str | None:
    workflow = db.scalars(
        select(AfterSalesWorkflow)
        .where(AfterSalesWorkflow.status == AfterSalesWorkflowStatus.queued)
        .order_by(AfterSalesWorkflow.queued_at, AfterSalesWorkflow.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if workflow is None:
        return None
    before_summary = _summary(workflow)
    if not can_transition(workflow.status, AfterSalesWorkflowStatus.executing):
        raise RuntimeError("queued workflow cannot enter executing")
    workflow.status = AfterSalesWorkflowStatus.executing
    workflow.execution_started_at = now
    workflow.execution_lease_expires_at = now + EXECUTION_LEASE
    workflow.execution_attempts += 1
    # This is intentionally set before the external call: a crash at any point
    # after claiming is an uncertainty to reconcile, not a permission to resend.
    workflow.reconciliation_required = True
    workflow.external_status = "execution_claimed"
    worker_request_id = _worker_request_id()
    _event(
        db,
        workflow,
        event_type="execution_started",
        from_status=AfterSalesWorkflowStatus.queued,
        to_status=AfterSalesWorkflowStatus.executing,
        summary={"attempt": workflow.execution_attempts},
        worker_request_id=worker_request_id,
    )
    _audit(
        db,
        workflow,
        action="after_sales.execution_started",
        before_summary=before_summary,
        after_summary=_summary(workflow),
        worker_request_id=worker_request_id,
    )
    db.commit()
    return workflow.id


def _claim_next_reconciliation(db: Session, *, now: datetime) -> str | None:
    workflow = db.scalars(
        select(AfterSalesWorkflow)
        .where(
            AfterSalesWorkflow.status == AfterSalesWorkflowStatus.executing,
            AfterSalesWorkflow.reconciliation_required.is_(True),
            or_(
                AfterSalesWorkflow.execution_lease_expires_at.is_(None),
                AfterSalesWorkflow.execution_lease_expires_at <= now,
            ),
        )
        .order_by(AfterSalesWorkflow.execution_started_at, AfterSalesWorkflow.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if workflow is None:
        return None
    before_summary = _summary(workflow)
    worker_request_id = _worker_request_id()
    workflow.execution_lease_expires_at = now + EXECUTION_LEASE
    workflow.reconciliation_attempts += 1
    _event(
        db,
        workflow,
        event_type="execution_reconciliation_started",
        from_status=AfterSalesWorkflowStatus.executing,
        to_status=AfterSalesWorkflowStatus.executing,
        summary={"attempt": workflow.reconciliation_attempts},
        worker_request_id=worker_request_id,
    )
    _audit(
        db,
        workflow,
        action="after_sales.execution_reconciliation_started",
        before_summary=before_summary,
        after_summary=_summary(workflow),
        worker_request_id=worker_request_id,
    )
    db.commit()
    return workflow.id


def _finish_with_result(
    db: Session,
    *,
    workflow_id: str,
    result: OmsOperationResult,
    now: datetime,
    source: str,
) -> None:
    workflow = db.scalar(
        select(AfterSalesWorkflow)
        .where(AfterSalesWorkflow.id == workflow_id)
        .with_for_update()
    )
    if workflow is None or workflow.status is not AfterSalesWorkflowStatus.executing:
        db.rollback()
        return
    before_summary = _summary(workflow)
    worker_request_id = _worker_request_id()
    workflow.external_reference = (
        result.external_reference or workflow.external_reference
    )
    receipt = _safe_receipt(result.receipt)
    if receipt:
        workflow.external_receipt = receipt

    if result.state is OmsOperationState.completed:
        target = AfterSalesWorkflowStatus.completed
        event_type = "execution_completed"
        audit_action = "after_sales.execution_completed"
        workflow.external_status = OmsOperationState.completed.value
        workflow.reconciliation_required = False
        workflow.execution_lease_expires_at = None
        workflow.completed_at = now
        workflow.failure_code = None
        workflow.compensation_reason_code = None
    elif result.state is OmsOperationState.failed:
        target = AfterSalesWorkflowStatus.failed
        event_type = "execution_failed"
        audit_action = "after_sales.execution_failed"
        workflow.external_status = OmsOperationState.failed.value
        workflow.reconciliation_required = False
        workflow.execution_lease_expires_at = None
        workflow.failed_at = now
        workflow.failure_code = _safe_code(result.failure_code, "OMS_REJECTED")
    elif result.state is OmsOperationState.compensation_required:
        target = AfterSalesWorkflowStatus.compensation_required
        event_type = "compensation_required"
        audit_action = "after_sales.compensation_required"
        workflow.external_status = OmsOperationState.compensation_required.value
        workflow.reconciliation_required = False
        workflow.execution_lease_expires_at = None
        workflow.failed_at = now
        workflow.compensation_reason_code = _safe_code(
            result.compensation_reason_code, "OMS_COMPENSATION_REQUIRED"
        )
    elif result.state is OmsOperationState.not_found:
        target = AfterSalesWorkflowStatus.failed
        event_type = "execution_failed"
        audit_action = "after_sales.execution_failed"
        workflow.external_status = OmsOperationState.not_found.value
        workflow.reconciliation_required = False
        workflow.execution_lease_expires_at = None
        workflow.failed_at = now
        workflow.failure_code = "EXTERNAL_OPERATION_NOT_FOUND"
    else:
        # A timeout/pending result is not a failure assertion.  Release the
        # lease for a later lookup, but never send a second write automatically.
        workflow.external_status = "pending_reconciliation"
        workflow.reconciliation_required = True
        workflow.execution_lease_expires_at = None
        workflow.failure_code = _safe_code(result.failure_code, "OMS_TIMEOUT_PENDING")
        _event(
            db,
            workflow,
            event_type="execution_reconciliation_pending",
            from_status=AfterSalesWorkflowStatus.executing,
            to_status=AfterSalesWorkflowStatus.executing,
            summary={"source": source, "external_status": workflow.external_status},
            worker_request_id=worker_request_id,
        )
        _audit(
            db,
            workflow,
            action="after_sales.execution_reconciliation_pending",
            before_summary=before_summary,
            after_summary=_summary(workflow),
            worker_request_id=worker_request_id,
        )
        set_span_attributes(
            {
                "customer_care.workflow.status": workflow.status.value,
                "customer_care.workflow.external_status": workflow.external_status,
            }
        )
        db.commit()
        return

    if not can_transition(workflow.status, target):
        db.rollback()
        raise RuntimeError("invalid terminal after-sales transition")
    current = workflow.status
    workflow.status = target
    set_span_attributes(
        {
            "customer_care.workflow.status": workflow.status.value,
            "customer_care.workflow.external_status": workflow.external_status,
        }
    )
    _event(
        db,
        workflow,
        event_type=event_type,
        from_status=current,
        to_status=target,
        summary={"source": source, "external_status": workflow.external_status},
        worker_request_id=worker_request_id,
    )
    _audit(
        db,
        workflow,
        action=audit_action,
        before_summary=before_summary,
        after_summary=_summary(workflow),
        worker_request_id=worker_request_id,
    )
    db.commit()


def _mark_uncertain_transport(db: Session, *, workflow_id: str, now: datetime) -> None:
    _finish_with_result(
        db,
        workflow_id=workflow_id,
        result=OmsOperationResult(
            state=OmsOperationState.pending,
            failure_code="OMS_TRANSPORT_UNCERTAIN",
        ),
        now=now,
        source="transport_exception",
    )


def _mark_precondition_failed(
    db: Session, *, workflow_id: str, code: str, now: datetime
) -> None:
    _finish_with_result(
        db,
        workflow_id=workflow_id,
        result=OmsOperationResult(state=OmsOperationState.failed, failure_code=code),
        now=now,
        source="execution_revalidation",
    )


def _execute_claimed(
    db: Session,
    *,
    workflow_id: str,
    adapter: AfterSalesOmsPort,
    now: datetime,
) -> None:
    workflow = db.scalar(
        select(AfterSalesWorkflow)
        .where(AfterSalesWorkflow.id == workflow_id)
        .with_for_update()
    )
    if workflow is None or workflow.status is not AfterSalesWorkflowStatus.executing:
        db.rollback()
        return
    with observe_background_task(
        "workflow.execution",
        traceparent=workflow.observability_traceparent,
        request_id=_worker_request_id(),
        attributes={"customer_care.worker.name": "after-sales-worker"},
    ):
        bind_principal(tenant_id=workflow.tenant_id, role="after_sales_worker")
        bind_resources(ticket_id=workflow.ticket_id, workflow_id=workflow.id)
        set_span_attributes(
            {
                "customer_care.workflow.action_type": workflow.action_type.value,
                "customer_care.workflow.status": workflow.status.value,
                "customer_care.tool.name": "mock_oms.execute",
            }
        )
        _execute_claimed_inner(db, workflow=workflow, adapter=adapter, now=now)


def _execute_claimed_inner(
    db: Session,
    *,
    workflow: AfterSalesWorkflow,
    adapter: AfterSalesOmsPort,
    now: datetime,
) -> None:
    try:
        service.revalidate_workflow_for_execution(db, workflow)
    except service.ExecutionPreconditionError as exc:
        db.rollback()
        set_span_attributes({"customer_care.tool.outcome": "precondition_failed"})
        _mark_precondition_failed(db, workflow_id=workflow.id, code=exc.code, now=now)
        return
    request = _request_for(workflow)
    # Release the row lock before the side-effect boundary.  The unexpired lease
    # stops another worker from reconciling this operation mid-call.
    db.commit()
    try:
        result = adapter.execute(request)
    except Exception:
        set_span_attributes({"customer_care.tool.outcome": "transport_uncertain"})
        _mark_uncertain_transport(db, workflow_id=workflow.id, now=now)
        return
    set_span_attributes({"customer_care.tool.outcome": result.state.value})
    _finish_with_result(
        db,
        workflow_id=workflow.id,
        result=result,
        now=now,
        source="submit",
    )


def _reconcile_claimed(
    db: Session,
    *,
    workflow_id: str,
    adapter: AfterSalesOmsPort,
    now: datetime,
) -> None:
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    if workflow is None or workflow.status is not AfterSalesWorkflowStatus.executing:
        return
    with observe_background_task(
        "workflow.reconciliation",
        traceparent=workflow.observability_traceparent,
        request_id=_worker_request_id(),
        attributes={"customer_care.worker.name": "after-sales-worker"},
    ):
        bind_principal(tenant_id=workflow.tenant_id, role="after_sales_worker")
        bind_resources(ticket_id=workflow.ticket_id, workflow_id=workflow.id)
        set_span_attributes(
            {
                "customer_care.workflow.action_type": workflow.action_type.value,
                "customer_care.workflow.status": workflow.status.value,
                "customer_care.tool.name": "mock_oms.lookup",
            }
        )
        request = _request_for(workflow)
        try:
            result = adapter.lookup(request)
        except Exception:
            set_span_attributes({"customer_care.tool.outcome": "transport_uncertain"})
            _mark_uncertain_transport(db, workflow_id=workflow_id, now=now)
            return
        set_span_attributes({"customer_care.tool.outcome": result.state.value})
        _finish_with_result(
            db,
            workflow_id=workflow_id,
            result=result,
            now=now,
            source="reconciliation_lookup",
        )


def process_pending_workflows(
    db: Session,
    *,
    adapter: AfterSalesOmsPort | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[str]:
    """Process at most `limit` records, reconciling uncertainty before new work."""

    if limit < 1:
        return []
    current = now or datetime.now(UTC)
    oms = adapter or get_mock_oms_adapter()
    processed: list[str] = []
    while len(processed) < limit:
        workflow_id = _claim_next_reconciliation(db, now=current)
        if workflow_id is None:
            break
        _reconcile_claimed(db, workflow_id=workflow_id, adapter=oms, now=current)
        processed.append(workflow_id)
    while len(processed) < limit:
        workflow_id = _claim_next_queued(db, now=current)
        if workflow_id is None:
            break
        _execute_claimed(db, workflow_id=workflow_id, adapter=oms, now=current)
        processed.append(workflow_id)
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process CustomerCare R2 after-sales Mock OMS workflows"
    )
    parser.add_argument(
        "--once", action="store_true", help="process one batch and exit"
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.poll_seconds < 0:
        parser.error("limit must be positive and poll-seconds must be non-negative")

    while True:
        with SessionLocal() as db:
            processed = process_pending_workflows(db, limit=args.limit)
            if processed:
                print("processed", " ".join(processed))
        if args.once:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
