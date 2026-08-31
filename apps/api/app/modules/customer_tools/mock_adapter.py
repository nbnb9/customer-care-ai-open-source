"""Deterministic R1 Mock CRM/OMS adapter backed by the synthetic world fixture.

This module deliberately has no database, model gateway, or browser dependency.
The caller must supply a server-derived tenant/customer scope; the adapter applies
that scope again before it returns any record.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .ports import (
    OrderQueryPort,
    OrderStatusResult,
    WarrantyEligibilityPort,
    WarrantyEligibilityResult,
)


def default_fixture_path() -> Path:
    # mock_adapter.py -> customer_tools -> modules -> app -> api -> apps -> repo
    return Path(__file__).resolve().parents[5] / "data" / "fixtures" / "world.json"


def _require_text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Mock fixture record is missing non-empty {field!r}")
    return value


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Mock fixture record has invalid {field!r}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Mock fixture record has invalid {field!r}") from exc


def _records(
    world: Mapping[str, object], field: str
) -> tuple[Mapping[str, object], ...]:
    value = world.get(field)
    if not isinstance(value, list):
        raise ValueError(f"Mock fixture world is missing list {field!r}")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Mock fixture {field!r} contains a non-object record")
        result.append(item)
    return tuple(result)


class MockCustomerToolsAdapter(OrderQueryPort, WarrantyEligibilityPort):
    """Fixture-backed adapter that fails closed for foreign or unknown orders."""

    def __init__(
        self,
        *,
        orders: tuple[Mapping[str, object], ...],
        warranties: tuple[Mapping[str, object], ...],
    ):
        self._orders = orders
        self._warranties = warranties

    @classmethod
    def from_fixture_path(cls, path: Path) -> MockCustomerToolsAdapter:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Mock fixture world must be an object")
        return cls(
            orders=_records(raw, "orders"),
            warranties=_records(raw, "warranties"),
        )

    def _find_owned_order(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> Mapping[str, object] | None:
        candidate = candidate_order_number.strip().casefold()
        for order in self._orders:
            if (
                _require_text(order, "tenant_id") == tenant_id
                and _require_text(order, "customer_id") == verified_customer_id
                and _require_text(order, "order_number").casefold() == candidate
            ):
                return order
        return None

    def query_order_status(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> OrderStatusResult | None:
        order = self._find_owned_order(
            tenant_id=tenant_id,
            verified_customer_id=verified_customer_id,
            candidate_order_number=candidate_order_number,
        )
        if order is None:
            return None
        return OrderStatusResult(
            order_number=_require_text(order, "order_number"),
            status=_require_text(order, "status"),
            ordered_at=_parse_datetime(order.get("ordered_at"), "ordered_at"),
        )

    def query_warranty_eligibility(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> WarrantyEligibilityResult | None:
        order = self._find_owned_order(
            tenant_id=tenant_id,
            verified_customer_id=verified_customer_id,
            candidate_order_number=candidate_order_number,
        )
        if order is None:
            return None

        order_id = _require_text(order, "id")
        for warranty in self._warranties:
            if (
                _require_text(warranty, "tenant_id") == tenant_id
                and _require_text(warranty, "order_id") == order_id
            ):
                status = _require_text(warranty, "status")
                return WarrantyEligibilityResult(
                    order_number=_require_text(order, "order_number"),
                    eligible=status == "active",
                    coverage_ends_at=_parse_datetime(
                        warranty.get("ends_at"), "ends_at"
                    ),
                )

        # The caller already proved ownership of the order. It is therefore
        # safe and useful to distinguish "no warranty" from an unknown order.
        return WarrantyEligibilityResult(
            order_number=_require_text(order, "order_number"),
            eligible=False,
            coverage_ends_at=None,
        )


@lru_cache(maxsize=1)
def get_mock_customer_tools_adapter() -> MockCustomerToolsAdapter:
    return MockCustomerToolsAdapter.from_fixture_path(default_fixture_path())
