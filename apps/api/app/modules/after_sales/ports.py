"""Ports for deterministic policy lookup and explicitly idempotent OMS writes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class OwnedOrderFacts:
    """Internal-only facts returned after tenant/customer ownership filtering."""

    order_id: str
    order_number: str
    status: str
    warranty_active: bool
    policy_version: str = "r2-mock-policy-v1"
    source_system: str = "mock"


class AfterSalesOrderFactsPort(Protocol):
    def lookup_owned_order_facts(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> OwnedOrderFacts | None: ...


class OmsOperationState(StrEnum):
    completed = "completed"
    failed = "failed"
    pending = "pending"
    compensation_required = "compensation_required"
    not_found = "not_found"


@dataclass(frozen=True)
class OmsWriteRequest:
    tenant_id: str
    workflow_id: str
    action_type: str
    idempotency_key: str


@dataclass(frozen=True)
class OmsOperationResult:
    state: OmsOperationState
    external_reference: str | None = None
    receipt: dict[str, object] | None = None
    failure_code: str | None = None
    compensation_reason_code: str | None = None


class AfterSalesOmsPort(Protocol):
    """OMS must expose a lookup by the same write idempotency key."""

    def execute(self, request: OmsWriteRequest) -> OmsOperationResult: ...

    def lookup(self, request: OmsWriteRequest) -> OmsOperationResult: ...
