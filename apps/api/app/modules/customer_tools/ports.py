from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class OrderStatusResult:
    """Minimal order state that a customer-facing UI is allowed to render."""

    order_number: str
    status: str
    ordered_at: datetime


@dataclass(frozen=True)
class WarrantyEligibilityResult:
    """Minimal warranty state that a customer-facing UI is allowed to render."""

    order_number: str
    eligible: bool
    coverage_ends_at: datetime | None


class OrderQueryPort(Protocol):
    def query_order_status(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> OrderStatusResult | None: ...


class WarrantyEligibilityPort(Protocol):
    def query_warranty_eligibility(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> WarrantyEligibilityResult | None: ...


class CustomerToolsPort(OrderQueryPort, WarrantyEligibilityPort, Protocol):
    """Combined read boundary selected by the server-side provider factory."""
