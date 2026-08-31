"""Durable local Mock OMS with a separate idempotency ledger.

The adapter uses its own database session on purpose.  It models the important
failure boundary: a worker may crash after the OMS committed but before the
workflow database records the receipt.  Recovery must then *look up* the same
external key rather than submit a second write.
"""

from __future__ import annotations

import hashlib
import uuid
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.after_sales import AfterSalesActionType, MockOmsOperation

from .ports import OmsOperationResult, OmsOperationState, OmsWriteRequest


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _result(operation: MockOmsOperation) -> OmsOperationResult:
    return OmsOperationResult(
        state=OmsOperationState(operation.status),
        external_reference=operation.external_reference,
        receipt=dict(operation.receipt),
    )


class MockOmsAdapter:
    """The Local/CI OMS only records approved request creation, never money."""

    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal):
        self._session_factory = session_factory

    def execute(self, request: OmsWriteRequest) -> OmsOperationResult:
        key_hash = _key_hash(request.idempotency_key)
        with self._session_factory() as db:
            existing = db.scalar(
                select(MockOmsOperation).where(
                    MockOmsOperation.tenant_id == request.tenant_id,
                    MockOmsOperation.external_idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                self._validate_existing(existing, request)
                return _result(existing)

            operation = MockOmsOperation(
                id=f"mock-oms-operation-{uuid.uuid4().hex}",
                tenant_id=request.tenant_id,
                workflow_id=request.workflow_id,
                action_type=AfterSalesActionType(request.action_type),
                external_idempotency_key_hash=key_hash,
                status=OmsOperationState.completed.value,
                external_reference=f"mock-oms-{uuid.uuid4().hex}",
                receipt={
                    "adapter": "mock-oms-v1",
                    "effect": "after_sales_request_created",
                    "action_type": request.action_type,
                },
            )
            db.add(operation)
            try:
                db.commit()
            except IntegrityError:
                # Another independent worker won the external idempotency race.
                db.rollback()
                existing = db.scalar(
                    select(MockOmsOperation).where(
                        MockOmsOperation.tenant_id == request.tenant_id,
                        MockOmsOperation.external_idempotency_key_hash == key_hash,
                    )
                )
                if existing is None:
                    raise
                self._validate_existing(existing, request)
                return _result(existing)
            db.refresh(operation)
            return _result(operation)

    def lookup(self, request: OmsWriteRequest) -> OmsOperationResult:
        key_hash = _key_hash(request.idempotency_key)
        with self._session_factory() as db:
            operation = db.scalar(
                select(MockOmsOperation).where(
                    MockOmsOperation.tenant_id == request.tenant_id,
                    MockOmsOperation.external_idempotency_key_hash == key_hash,
                )
            )
            if operation is None:
                return OmsOperationResult(state=OmsOperationState.not_found)
            self._validate_existing(operation, request)
            return _result(operation)

    @staticmethod
    def _validate_existing(
        operation: MockOmsOperation, request: OmsWriteRequest
    ) -> None:
        if (
            operation.workflow_id != request.workflow_id
            or operation.action_type.value != request.action_type
        ):
            raise RuntimeError(
                "Mock OMS idempotency key conflicts with another request"
            )


@lru_cache(maxsize=1)
def get_mock_oms_adapter() -> MockOmsAdapter:
    return MockOmsAdapter()
