from datetime import UTC, datetime, timedelta

import pytest
from conftest import TestingSessionLocal
from sqlalchemy import func, select
from test_after_sales_api import approve_draft, confirm_draft, create_draft

from app.models.after_sales import AfterSalesWorkflow, MockOmsOperation
from app.models.identity import User
from app.modules.after_sales import worker
from app.modules.after_sales.mock_oms import MockOmsAdapter
from app.modules.after_sales.ports import (
    OmsOperationResult,
    OmsOperationState,
    OmsWriteRequest,
)
from app.modules.after_sales.worker import process_pending_workflows


class ScriptedOms:
    def __init__(
        self,
        *,
        execute_result: OmsOperationResult,
        lookup_result: OmsOperationResult | None = None,
    ):
        self.execute_result = execute_result
        self.lookup_result = lookup_result or execute_result
        self.executed: list[OmsWriteRequest] = []
        self.looked_up: list[OmsWriteRequest] = []

    def execute(self, request: OmsWriteRequest) -> OmsOperationResult:
        self.executed.append(request)
        return self.execute_result

    def lookup(self, request: OmsWriteRequest) -> OmsOperationResult:
        self.looked_up.append(request)
        return self.lookup_result


def queued_workflow(client) -> str:
    workflow_id = create_draft(client).json()["id"]
    assert confirm_draft(client, workflow_id).status_code == 200
    assert approve_draft(client, workflow_id).status_code == 200
    return workflow_id


def test_worker_cli_keeps_polling_after_an_empty_queue(monkeypatch):
    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    calls = iter([[], []])
    sleeps: list[float] = []

    class StopPolling(RuntimeError):
        pass

    def stop_after_second_poll(seconds: float):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopPolling

    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        worker, "process_pending_workflows", lambda db, limit: next(calls)
    )
    monkeypatch.setattr(worker.time, "sleep", stop_after_second_poll)

    with pytest.raises(StopPolling):
        worker.main(["--poll-seconds", "0"])
    assert sleeps == [0, 0]


def test_worker_executes_approved_workflow_once_with_mock_oms(client, db):
    workflow_id = queued_workflow(client)
    mock_oms = MockOmsAdapter(session_factory=TestingSessionLocal)

    processed = process_pending_workflows(db, adapter=mock_oms, limit=5)
    assert processed == [workflow_id]
    db.expire_all()
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    assert workflow.status.value == "completed"
    assert workflow.external_status == "completed"
    assert workflow.external_reference is not None
    assert workflow.external_receipt == {
        "adapter": "mock-oms-v1",
        "effect": "after_sales_request_created",
        "action_type": "complaint",
    }
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 1

    second = process_pending_workflows(db, adapter=mock_oms, limit=5)
    assert second == []
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 1


def test_mock_oms_deduplicates_an_independent_replayed_write(client, db):
    workflow_id = queued_workflow(client)
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    request = OmsWriteRequest(
        tenant_id=workflow.tenant_id,
        workflow_id=workflow.id,
        action_type=workflow.action_type.value,
        idempotency_key=f"after-sales-workflow:{workflow.id}",
    )
    mock_oms = MockOmsAdapter(session_factory=TestingSessionLocal)

    first = mock_oms.execute(request)
    replay = mock_oms.execute(request)
    assert first.state is OmsOperationState.completed
    assert replay.state is OmsOperationState.completed
    assert replay.external_reference == first.external_reference
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 1


def test_timeout_reconciles_by_lookup_without_resubmitting_external_write(client, db):
    workflow_id = queued_workflow(client)
    oms = ScriptedOms(
        execute_result=OmsOperationResult(
            state=OmsOperationState.pending,
            failure_code="OMS_TIMEOUT_PENDING",
        ),
        lookup_result=OmsOperationResult(
            state=OmsOperationState.completed,
            external_reference="mock-timeout-recovered-001",
            receipt={"adapter": "scripted", "effect": "reconciled"},
        ),
    )
    now = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)

    assert process_pending_workflows(db, adapter=oms, now=now) == [workflow_id]
    db.expire_all()
    waiting = db.get(AfterSalesWorkflow, workflow_id)
    assert waiting is not None
    assert waiting.status.value == "executing"
    assert waiting.external_status == "pending_reconciliation"
    assert waiting.reconciliation_required is True
    assert len(oms.executed) == 1
    assert len(oms.looked_up) == 0

    assert process_pending_workflows(
        db, adapter=oms, now=now + timedelta(seconds=1)
    ) == [workflow_id]
    db.expire_all()
    completed = db.get(AfterSalesWorkflow, workflow_id)
    assert completed is not None
    assert completed.status.value == "completed"
    assert completed.external_reference == "mock-timeout-recovered-001"
    assert len(oms.executed) == 1
    assert len(oms.looked_up) == 1


def test_worker_crash_recovery_uses_lookup_and_marks_missing_operation_failed(
    client, db
):
    workflow_id = queued_workflow(client)
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    # Simulate a dead worker after it claimed the workflow. It is uncertain
    # whether the remote request was sent, so recovery must lookup, not submit.
    workflow.status = workflow.status.__class__.executing
    workflow.reconciliation_required = True
    workflow.execution_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    oms = ScriptedOms(
        execute_result=OmsOperationResult(state=OmsOperationState.completed),
        lookup_result=OmsOperationResult(state=OmsOperationState.not_found),
    )

    assert process_pending_workflows(db, adapter=oms) == [workflow_id]
    db.expire_all()
    failed = db.get(AfterSalesWorkflow, workflow_id)
    assert failed is not None
    assert failed.status.value == "failed"
    assert failed.failure_code == "EXTERNAL_OPERATION_NOT_FOUND"
    assert oms.executed == []
    assert len(oms.looked_up) == 1


def test_compensation_path_is_explicit_and_never_retried(client, db):
    workflow_id = queued_workflow(client)
    oms = ScriptedOms(
        execute_result=OmsOperationResult(
            state=OmsOperationState.compensation_required,
            compensation_reason_code="OMS_PARTIAL_EFFECT",
        )
    )

    assert process_pending_workflows(db, adapter=oms) == [workflow_id]
    db.expire_all()
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    assert workflow.status.value == "compensation_required"
    assert workflow.compensation_reason_code == "OMS_PARTIAL_EFFECT"
    assert process_pending_workflows(db, adapter=oms) == []
    assert len(oms.executed) == 1


def test_worker_revalidates_customer_identity_before_external_write(client, db):
    workflow_id = queued_workflow(client)
    customer = db.get(User, "user-nw-customer-01")
    assert customer is not None
    customer.verification_level = "unverified"
    db.commit()
    oms = ScriptedOms(
        execute_result=OmsOperationResult(state=OmsOperationState.completed)
    )

    assert process_pending_workflows(db, adapter=oms) == [workflow_id]
    db.expire_all()
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    assert workflow.status.value == "failed"
    assert workflow.failure_code == "CUSTOMER_CONFIRMATION_NOT_REVALIDATED"
    assert oms.executed == []
