"""Synthetic ownership facts for the deterministic R2 policy engine.

This deliberately re-reads the local fixture rather than the browser's R1
response.  The internal `order_id` is used only to bind a draft to its ticket;
it is never returned by an API schema.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache

from app.modules.customer_tools.mock_adapter import default_fixture_path

from .ports import OwnedOrderFacts


def _records(
    world: Mapping[str, object], field: str
) -> tuple[Mapping[str, object], ...]:
    value = world.get(field)
    if not isinstance(value, list):
        raise ValueError(f"Mock fixture world is missing list {field!r}")
    records: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Mock fixture {field!r} contains a non-object record")
        records.append(item)
    return tuple(records)


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Mock fixture record is missing non-empty {field!r}")
    return value


class MockAfterSalesPolicyAdapter:
    def __init__(
        self,
        *,
        orders: tuple[Mapping[str, object], ...],
        warranties: tuple[Mapping[str, object], ...],
    ):
        self._orders = orders
        self._warranties = warranties

    @classmethod
    def from_default_fixture(cls) -> MockAfterSalesPolicyAdapter:
        raw = json.loads(default_fixture_path().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Mock fixture world must be an object")
        return cls(
            orders=_records(raw, "orders"),
            warranties=_records(raw, "warranties"),
        )

    def lookup_owned_order_facts(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> OwnedOrderFacts | None:
        order = next(
            (
                item
                for item in self._orders
                if _text(item, "tenant_id") == tenant_id
                and _text(item, "customer_id") == customer_id
                and _text(item, "id") == order_id
            ),
            None,
        )
        if order is None:
            return None
        order_id = _text(order, "id")
        warranty_active = any(
            _text(item, "tenant_id") == tenant_id
            and _text(item, "order_id") == order_id
            and _text(item, "status") == "active"
            for item in self._warranties
        )
        return OwnedOrderFacts(
            order_id=order_id,
            order_number=_text(order, "order_number"),
            status=_text(order, "status"),
            warranty_active=warranty_active,
        )


@lru_cache(maxsize=1)
def get_mock_after_sales_policy_adapter() -> MockAfterSalesPolicyAdapter:
    return MockAfterSalesPolicyAdapter.from_default_fixture()
