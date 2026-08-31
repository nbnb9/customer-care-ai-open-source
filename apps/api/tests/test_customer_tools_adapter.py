from datetime import UTC, datetime

import pytest

from app.modules.customer_tools.mock_adapter import (
    MockCustomerToolsAdapter,
    default_fixture_path,
)

pytestmark = pytest.mark.offline


@pytest.fixture()
def adapter() -> MockCustomerToolsAdapter:
    return MockCustomerToolsAdapter.from_fixture_path(default_fixture_path())


def test_mock_adapter_returns_only_minimal_owned_order_fields(
    adapter: MockCustomerToolsAdapter,
):
    result = adapter.query_order_status(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="nw-demo-order-1001",
    )

    assert result is not None
    assert result.order_number == "NW-DEMO-ORDER-1001"
    assert result.status == "shipped"
    assert result.ordered_at == datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
    assert set(vars(result)) == {"order_number", "status", "ordered_at"}


def test_mock_adapter_rejects_customer_and_tenant_scope_tampering(
    adapter: MockCustomerToolsAdapter,
):
    own_order = "NW-DEMO-ORDER-1001"

    # A caller cannot turn an order number into an ID lookup or substitute a
    # different verified customer/tenant scope.
    assert (
        adapter.query_order_status(
            tenant_id="tenant-northwind-demo",
            verified_customer_id="customer-nw-02",
            candidate_order_number=own_order,
        )
        is None
    )
    assert (
        adapter.query_order_status(
            tenant_id="tenant-contoso-demo",
            verified_customer_id="customer-nw-01",
            candidate_order_number=own_order,
        )
        is None
    )
    assert (
        adapter.query_order_status(
            tenant_id="tenant-northwind-demo",
            verified_customer_id="customer-nw-01",
            candidate_order_number="order-nw-1001",
        )
        is None
    )


def test_mock_adapter_scopes_warranty_and_reports_no_coverage_without_pii(
    adapter: MockCustomerToolsAdapter,
):
    foreign = adapter.query_warranty_eligibility(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="NW-DEMO-ORDER-1002",
    )
    no_coverage = adapter.query_warranty_eligibility(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="NW-DEMO-ORDER-1001",
    )
    covered = adapter.query_warranty_eligibility(
        tenant_id="tenant-contoso-demo",
        verified_customer_id="customer-ct-01",
        candidate_order_number="CT-DEMO-ORDER-2001",
    )

    assert foreign is None
    assert no_coverage is not None
    assert no_coverage.eligible is False
    assert no_coverage.coverage_ends_at is None
    assert set(vars(no_coverage)) == {
        "order_number",
        "eligible",
        "coverage_ends_at",
    }
    assert covered is not None
    assert covered.eligible is True
    assert covered.coverage_ends_at == datetime(2027, 1, 11, 10, 0, tzinfo=UTC)
