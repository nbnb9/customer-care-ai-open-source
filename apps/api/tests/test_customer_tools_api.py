import json

import pytest
from conftest import auth
from fastapi import HTTPException
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.identity import UserRole
from app.modules.customer_tools import service
from app.modules.customer_tools.errors import CustomerToolsUnavailableError
from app.modules.customer_tools.schemas import OrderLookupRequest
from app.modules.identity.principal import CurrentPrincipal
from app.modules.llm_gateway import factory as llm_factory
from app.shared.config import get_settings

CUSTOMER = "user-nw-customer-01"
CONTOSO_CUSTOMER = "user-ct-customer-01"


def _audits_for_request(db, request_id: str) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.request_id == request_id)
            .order_by(AuditEvent.created_at)
        )
    )


def test_verified_customer_can_read_own_order_with_minimal_response_and_audit(
    client, db
):
    request_id = "r1-owned-order-read-001"
    response = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": "NW-DEMO-ORDER-1001"},
        headers={**auth(CUSTOMER), "X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert response.json() == {
        "order_number": "NW-DEMO-ORDER-1001",
        "status": "shipped",
        "ordered_at": "2026-01-10T08:00:00Z",
    }
    audit = _audits_for_request(db, request_id)
    assert len(audit) == 1
    assert audit[0].action == "customer_read_tool.order_status.read"
    assert audit[0].resource_type == "customer_read_tool"
    assert audit[0].resource_id == "order_status"
    assert json.loads(audit[0].before_summary or "{}") == {
        "parameter_summary": {
            "candidate_order_number": "provided",
            "verified_customer_id": "server_derived",
        },
        "tool_name": "order_status",
    }
    assert json.loads(audit[0].after_summary or "{}") == {"result_status": "found"}
    assert audit[0].request_id == request_id
    trace_text = f"{audit[0].before_summary} {audit[0].after_summary}"
    assert "NW-DEMO-ORDER-1001" not in trace_text
    assert "address" not in trace_text.lower()
    assert "phone" not in trace_text.lower()


@pytest.mark.parametrize(
    ("order_number", "request_id"),
    [
        ("NW-DEMO-ORDER-1002", "r1-other-customer-order-001"),
        ("CT-DEMO-ORDER-2001", "r1-cross-tenant-order-001"),
        ("NW-DEMO-ORDER-9999", "r1-unknown-order-001"),
    ],
)
def test_customer_cannot_read_foreign_or_unknown_order(
    client, db, order_number: str, request_id: str
):
    response = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": order_number},
        headers={**auth(CUSTOMER), "X-Request-ID": request_id},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Order not found"
    assert order_number not in response.text
    audit = _audits_for_request(db, request_id)
    assert len(audit) == 1
    assert audit[0].action == "customer_read_tool.order_status.read"
    assert json.loads(audit[0].after_summary or "{}") == {
        "result_status": "not_found_or_not_owned"
    }
    assert order_number not in f"{audit[0].before_summary} {audit[0].after_summary}"


def test_browser_cannot_tamper_with_server_derived_customer_scope(client, db):
    request_id = "r1-customer-scope-tamper-001"
    response = client.post(
        "/v1/customer-tools/order-status",
        json={
            "order_number": "NW-DEMO-ORDER-1002",
            "verified_customer_id": "customer-nw-02",
            "tenant_id": "tenant-northwind-demo",
        },
        headers={**auth(CUSTOMER), "X-Request-ID": request_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert _audits_for_request(db, request_id) == []


def test_warranty_lookup_returns_eligibility_only_after_order_ownership_check(
    client, db
):
    no_coverage = client.post(
        "/v1/customer-tools/warranty-eligibility",
        json={"order_number": "NW-DEMO-ORDER-1001"},
        headers={**auth(CUSTOMER), "X-Request-ID": "r1-no-warranty-001"},
    )
    covered = client.post(
        "/v1/customer-tools/warranty-eligibility",
        json={"order_number": "CT-DEMO-ORDER-2001"},
        headers={
            **auth(CONTOSO_CUSTOMER),
            "X-Request-ID": "r1-covered-warranty-001",
        },
    )
    foreign = client.post(
        "/v1/customer-tools/warranty-eligibility",
        json={"order_number": "NW-DEMO-ORDER-1002"},
        headers={**auth(CUSTOMER), "X-Request-ID": "r1-foreign-warranty-001"},
    )

    assert no_coverage.status_code == 200
    assert no_coverage.json() == {
        "order_number": "NW-DEMO-ORDER-1001",
        "eligible": False,
        "coverage_ends_at": None,
    }
    assert covered.status_code == 200
    assert covered.json() == {
        "order_number": "CT-DEMO-ORDER-2001",
        "eligible": True,
        "coverage_ends_at": "2027-01-11T10:00:00Z",
    }
    assert foreign.status_code == 404
    foreign_audit = _audits_for_request(db, "r1-foreign-warranty-001")
    assert len(foreign_audit) == 1
    assert foreign_audit[0].action == "customer_read_tool.warranty_eligibility.read"
    assert json.loads(foreign_audit[0].after_summary or "{}") == {
        "result_status": "not_found_or_not_owned"
    }


def test_read_tools_reject_staff_and_unverified_customer_context(
    client, db, monkeypatch
):
    staff = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": "NW-DEMO-ORDER-1001"},
        headers=auth("user-nw-agent-01"),
    )
    assert staff.status_code == 403

    unverified = CurrentPrincipal(
        user_id=CUSTOMER,
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="unverified",
        customer_id="customer-nw-01",
    )
    with pytest.raises(HTTPException) as exc_info:
        service.query_order_status(
            db,
            unverified,
            OrderLookupRequest(order_number="NW-DEMO-ORDER-1001"),
        )
    assert exc_info.value.status_code == 403

    demo_principal = unverified.model_copy(
        update={"verification_level": "demo_verified"}
    )
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(HTTPException) as demo_exc_info:
        service.query_order_status(
            db,
            demo_principal,
            OrderLookupRequest(order_number="NW-DEMO-ORDER-1001"),
        )
    assert demo_exc_info.value.status_code == 403


def test_traditional_read_tool_does_not_depend_on_the_llm_gateway(client, monkeypatch):
    def unavailable_gateway():
        raise AssertionError("R1 traditional read tool must not invoke an LLM")

    monkeypatch.setattr(llm_factory, "get_gateway", unavailable_gateway)

    response = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": "NW-DEMO-ORDER-1001"},
        headers=auth(CUSTOMER),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "shipped"


def test_provider_failure_is_audited_and_does_not_leak_provider_details(
    client, db, monkeypatch
):
    class UnavailableAdapter:
        def query_order_status(self, **_kwargs):
            raise CustomerToolsUnavailableError(
                "provider response contained secret-token-and-order"
            )

        def query_warranty_eligibility(self, **_kwargs):
            raise CustomerToolsUnavailableError("provider unavailable")

    request_id = "shopify-provider-unavailable-001"
    monkeypatch.setattr(
        service, "get_customer_tools_adapter", lambda: UnavailableAdapter()
    )

    response = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": "NW-DEMO-ORDER-1001"},
        headers={**auth(CUSTOMER), "X-Request-ID": request_id},
    )

    assert response.status_code == 503
    assert response.json()["message"] == ("Order provider is temporarily unavailable")
    assert "secret-token" not in response.text
    assert "NW-DEMO-ORDER-1001" not in response.text
    audit = _audits_for_request(db, request_id)
    assert len(audit) == 1
    assert json.loads(audit[0].after_summary or "{}") == {
        "result_status": "provider_unavailable"
    }
