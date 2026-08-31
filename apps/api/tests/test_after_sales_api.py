import json

import pytest
from conftest import auth
from sqlalchemy import func, select

from app.models.after_sales import (
    AfterSalesIdempotencyRecord,
    AfterSalesWorkflow,
    AfterSalesWorkflowEvent,
    MockOmsOperation,
)
from app.models.audit import AuditEvent
from app.modules.llm_gateway import factory as llm_factory

CUSTOMER = "user-nw-customer-01"
OTHER_CUSTOMER = "user-nw-customer-02"
AGENT = "user-nw-agent-01"
CONTOSO_AGENT = "user-ct-agent-01"
KNOWLEDGE_OPERATOR = "user-nw-knowledge-operator-01"
TICKET = "ticket-nw-open-01"


def write_headers(
    user_id: str, key: str, request_id: str | None = None
) -> dict[str, str]:
    headers = {**auth(user_id), "Idempotency-Key": key}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def create_draft(
    client, *, key: str = "r2-draft-create-001", action: str = "complaint"
):
    return client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": TICKET, "action_type": action},
        headers=write_headers(AGENT, key, "r2-draft-create-request-001"),
    )


def confirm_draft(client, workflow_id: str, *, key: str = "r2-confirm-001"):
    return client.post(
        f"/v1/after-sales/workflows/{workflow_id}/customer-confirmation",
        json={"decision": "confirm"},
        headers=write_headers(CUSTOMER, key, "r2-confirm-request-001"),
    )


def approve_draft(client, workflow_id: str, *, key: str = "r2-approve-001"):
    return client.post(
        f"/v1/after-sales/workflows/{workflow_id}/agent-approval",
        json={"decision": "approve"},
        headers=write_headers(AGENT, key, "r2-approve-request-001"),
    )


def test_r2_draft_is_deterministic_customer_visible_and_idempotent(
    client, db, monkeypatch
):
    def unavailable_gateway():
        raise AssertionError("R2 draft creation must not invoke an LLM gateway")

    monkeypatch.setattr(llm_factory, "get_gateway", unavailable_gateway)

    first = create_draft(client)
    assert first.status_code == 201
    body = first.json()
    assert body["status"] == "awaiting_customer_confirmation"
    assert body["action_type"] == "complaint"
    assert body["policy"] == {
        "policy_version": "r2-mock-policy-v1",
        "eligible": True,
        "decision_code": "COMPLAINT_ELIGIBLE",
        "action_type": "complaint",
        "order_number": "NW-DEMO-ORDER-1001",
        "order_status": "shipped",
        "warranty_active": False,
    }
    assert body["customer_summary"]["amount_minor"] is None
    assert body["customer_summary"]["manual_approval_required"] is True
    assert "退款" in " ".join(body["customer_summary"]["risk_notices"])
    assert "customer_id" not in body
    assert "tenant_id" not in body
    assert "external_idempotency_key_hash" not in body
    assert db.scalar(select(func.count()).select_from(AfterSalesWorkflow)) == 1
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 0

    replay = create_draft(client)
    assert replay.status_code == 201
    assert replay.json() == body
    assert db.scalar(select(func.count()).select_from(AfterSalesWorkflow)) == 1
    assert db.scalar(select(func.count()).select_from(AfterSalesIdempotencyRecord)) == 1

    conflict = client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": TICKET, "action_type": "return"},
        headers=write_headers(AGENT, "r2-draft-create-001"),
    )
    assert conflict.status_code == 409

    events = client.get(
        f"/v1/after-sales/workflows/{body['id']}/events", headers=auth(CUSTOMER)
    )
    assert events.status_code == 200
    assert [event["event_type"] for event in events.json()] == [
        "draft_created",
        "policy_checked",
        "customer_confirmation_requested",
    ]
    assert [event["sequence"] for event in events.json()] == [1, 2, 3]

    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == body["id"],
            AuditEvent.action == "after_sales.workflow_draft_created",
        )
    )
    assert audit is not None
    assert audit.request_id == "r2-draft-create-request-001"
    assert "NW-DEMO-ORDER-1001" not in f"{audit.before_summary} {audit.after_summary}"


def test_r2_requires_eligible_server_bound_order_and_rejects_forbidden_action_types(
    client, db
):
    ineligible = create_draft(client, key="ineligible", action="return")
    assert ineligible.status_code == 422
    assert db.scalar(select(func.count()).select_from(AfterSalesWorkflow)) == 0

    forbidden = client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": TICKET, "action_type": "refund"},
        headers=write_headers(AGENT, "r2-forbidden-action-001"),
    )
    assert forbidden.status_code == 422
    assert db.scalar(select(func.count()).select_from(AfterSalesWorkflow)) == 0

    customer_cannot_create = client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": TICKET, "action_type": "complaint"},
        headers=write_headers(CUSTOMER, "r2-customer-draft-001"),
    )
    assert customer_cannot_create.status_code == 403


@pytest.mark.parametrize(
    ("ticket_id", "agent_id", "action", "decision_code"),
    [
        (
            "ticket-nw-closed-01",
            AGENT,
            "replacement",
            "REPLACEMENT_ELIGIBLE",
        ),
        (
            "ticket-ct-open-01",
            CONTOSO_AGENT,
            "return",
            "RETURN_ELIGIBLE",
        ),
    ],
)
def test_r2_deterministic_policy_supports_eligible_action_types(
    client, ticket_id: str, agent_id: str, action: str, decision_code: str
):
    response = client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": ticket_id, "action_type": action},
        headers=write_headers(agent_id, f"r2-policy-{action}-001"),
    )
    assert response.status_code == 201
    assert response.json()["action_type"] == action
    assert response.json()["policy"]["decision_code"] == decision_code


def test_r2_confirmation_and_approval_require_distinct_principals_and_are_idempotent(
    client, db
):
    workflow_id = create_draft(client).json()["id"]

    premature = approve_draft(client, workflow_id, key="premature")
    assert premature.status_code == 409
    assert db.get(AfterSalesWorkflow, workflow_id).status.value == (
        "awaiting_customer_confirmation"
    )

    confirmation = confirm_draft(client, workflow_id)
    assert confirmation.status_code == 200
    assert confirmation.json()["status"] == "awaiting_agent_approval"
    event_count = db.scalar(
        select(func.count())
        .select_from(AfterSalesWorkflowEvent)
        .where(AfterSalesWorkflowEvent.workflow_id == workflow_id)
    )

    duplicate_confirmation = confirm_draft(client, workflow_id)
    assert duplicate_confirmation.status_code == 200
    assert duplicate_confirmation.json() == confirmation.json()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AfterSalesWorkflowEvent)
            .where(AfterSalesWorkflowEvent.workflow_id == workflow_id)
        )
        == event_count
    )

    approved = approve_draft(client, workflow_id)
    assert approved.status_code == 200
    assert approved.json()["status"] == "queued"
    assert approved.json()["external_status"] == "not_started"
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 0

    duplicate_approval = approve_draft(client, workflow_id)
    assert duplicate_approval.status_code == 200
    assert duplicate_approval.json() == approved.json()
    assert db.scalar(select(func.count()).select_from(MockOmsOperation)) == 0

    same_key_different_decision = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/agent-approval",
        json={"decision": "reject"},
        headers=write_headers(AGENT, "r2-approve-001"),
    )
    assert same_key_different_decision.status_code == 409


def test_r2_customer_scope_staff_roles_and_agent_edit_preserve_consent(client, db):
    workflow_id = create_draft(client).json()["id"]

    foreign_customer = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/customer-confirmation",
        json={"decision": "confirm"},
        headers=write_headers(OTHER_CUSTOMER, "r2-foreign-confirm-001"),
    )
    assert foreign_customer.status_code == 404

    cross_tenant = client.get(
        f"/v1/after-sales/workflows/{workflow_id}", headers=auth(CONTOSO_AGENT)
    )
    assert cross_tenant.status_code == 404

    no_staff_scope = client.get(
        "/v1/after-sales/workflows", headers=auth(KNOWLEDGE_OPERATOR)
    )
    assert no_staff_scope.status_code == 403

    assert confirm_draft(client, workflow_id).status_code == 200
    edited = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/agent-approval",
        json={"decision": "edit", "edited_action_type": "complaint"},
        headers=write_headers(AGENT, "r2-edit-draft-001"),
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "awaiting_customer_confirmation"
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    assert workflow.customer_confirmed_by is None
    assert workflow.agent_approved_by is None


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "edit"},
        {"decision": "approve", "edited_action_type": "complaint"},
        {"decision": "reject", "extra": "not allowed"},
    ],
)
def test_r2_approval_payload_is_strict(client, payload):
    workflow_id = create_draft(client).json()["id"]
    assert confirm_draft(client, workflow_id).status_code == 200
    response = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/agent-approval",
        json=payload,
        headers=write_headers(AGENT, "r2-strict-approval-001"),
    )
    assert response.status_code == 422


def test_r2_customer_read_model_keeps_events_and_receipts_minimal(client, db):
    workflow_id = create_draft(client).json()["id"]
    workflow = db.get(AfterSalesWorkflow, workflow_id)
    assert workflow is not None
    workflow.external_receipt = {
        "adapter": "mock-oms-v1",
        "order_number": "NW-DEMO-ORDER-1001",
    }
    db.commit()

    response = client.get(
        f"/v1/after-sales/workflows/{workflow_id}", headers=auth(CUSTOMER)
    )
    assert response.status_code == 200
    # A raw provider receipt must be projected again at the API boundary. This
    # guards against an adapter later adding internal identifiers to its ledger.
    assert "external_idempotency_key_hash" not in response.text
    assert "customer_id" not in response.text
    assert "tenant_id" not in response.text
    assert "order_number" not in json.loads(response.text)["receipt_summary"]
    assert json.loads(response.text)["receipt_summary"] == {"adapter": "mock-oms-v1"}
