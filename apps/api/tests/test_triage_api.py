from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import auth
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.tickets import Ticket, TicketPriority
from app.modules.llm_gateway.schemas import Intent, RiskLevel, Route, TriageResult
from app.modules.triage import service as triage_service


def test_agent_can_create_suggestion(client):
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    assert res.status_code == 201
    body = res.json()
    assert body["intent"] in {"order_status", "product_faq"}  # Fake 网关按关键词
    assert body["suggested_priority"] in {"low", "normal"}
    assert body["provider"] == "fake"
    assert body["model"] == "fake-deterministic-v1"
    assert body["prompt_version"] == "triage-v2"
    assert body["schema_version"] == "triage-result-v1"
    assert body["generation_latency_ms"] >= 0
    assert body["decision"] is None  # 还没决定


def test_customer_cannot_create_suggestion(client):
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage",
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 403


def test_cross_tenant_staff_cannot_access_triage(client):
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-ct-agent-01")
    )
    assert res.status_code == 404


def test_decision_recorded(client):
    created = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={
            "suggestion_id": created.json()["id"],
            "decision": "rejected",
            "reason_code": "incorrect_category",
        },
        headers=auth("user-nw-agent-01"),
    )
    assert res.status_code == 200
    assert res.json()["decision"] == "rejected"
    assert res.json()["decided_by"] == "user-nw-agent-01"
    assert res.json()["decided_at"] is not None


def test_edited_decision_persists_summary_and_is_immutable(client, db):
    created = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    first = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={
            "suggestion_id": created.json()["id"],
            "decision": "edited",
            "reason_code": "summary_correction",
            "edited_summary": "客户查询合成物流记录。",
        },
        headers=auth("user-nw-agent-01"),
    )
    assert first.status_code == 200
    assert first.json()["edited_summary"] == "客户查询合成物流记录。"
    assert first.json()["decided_at"] is not None

    overwritten = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"suggestion_id": created.json()["id"], "decision": "accepted"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert overwritten.status_code == 409

    audits = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.resource_type == "triage_suggestion",
                AuditEvent.resource_id == first.json()["id"],
            )
            .order_by(AuditEvent.created_at)
        )
    )
    assert [audit.action for audit in audits] == [
        "triage.suggestion_created",
        "triage.decision_recorded",
    ]
    assert audits[1].before_summary is not None
    assert audits[1].after_summary is not None


def test_triage_history_keeps_prior_immutable_decision(client):
    created = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    decided = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"suggestion_id": created.json()["id"], "decision": "accepted"},
        headers=auth("user-nw-agent-01"),
    )
    assert decided.status_code == 200
    newer = client.post(
        "/v1/tickets/ticket-nw-open-01/triage",
        headers=auth("user-nw-supervisor-01"),
    )
    assert newer.status_code == 201

    history = client.get(
        "/v1/tickets/ticket-nw-open-01/triage/history",
        headers=auth("user-nw-agent-01"),
    )
    assert history.status_code == 200
    by_id = {item["id"]: item for item in history.json()}
    assert by_id[decided.json()["id"]]["decision"] == "accepted"
    assert by_id[newer.json()["id"]]["decision"] is None


def test_stale_page_decides_exact_suggestion_not_newer_one(client):
    suggestion_a = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    suggestion_b = client.post(
        "/v1/tickets/ticket-nw-open-01/triage",
        headers=auth("user-nw-supervisor-01"),
    )
    assert suggestion_a.status_code == suggestion_b.status_code == 201

    decided = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"suggestion_id": suggestion_a.json()["id"], "decision": "accepted"},
        headers=auth("user-nw-agent-01"),
    )
    assert decided.status_code == 200
    assert decided.json()["id"] == suggestion_a.json()["id"]

    history = client.get(
        "/v1/tickets/ticket-nw-open-01/triage/history",
        headers=auth("user-nw-agent-01"),
    )
    by_id = {item["id"]: item for item in history.json()}
    assert by_id[suggestion_a.json()["id"]]["decision"] == "accepted"
    assert by_id[suggestion_b.json()["id"]]["decision"] is None


def test_suggestion_id_from_another_ticket_returns_404(client):
    suggestion = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    res = client.post(
        "/v1/tickets/ticket-nw-closed-01/triage/decision",
        json={"suggestion_id": suggestion.json()["id"], "decision": "accepted"},
        headers=auth("user-nw-agent-01"),
    )
    assert res.status_code == 404

    history = client.get(
        "/v1/tickets/ticket-nw-open-01/triage/history",
        headers=auth("user-nw-agent-01"),
    )
    assert history.json()[0]["decision"] is None


def test_concurrent_triage_decisions_commit_only_once(client, db):
    created = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    assert created.status_code == 201

    decisions = [
        {"suggestion_id": created.json()["id"], "decision": "accepted"},
        {
            "suggestion_id": created.json()["id"],
            "decision": "rejected",
            "reason_code": "incorrect_category",
        },
    ]

    def decide(payload):
        return client.post(
            "/v1/tickets/ticket-nw-open-01/triage/decision",
            json=payload,
            headers=auth("user-nw-agent-01"),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(decide, decisions))

    assert statuses == [200, 409]
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == created.json()["id"],
                AuditEvent.action == "triage.decision_recorded",
            )
        )
        == 1
    )


def test_decision_edit_contract_validation(client):
    created = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    suggestion_id = created.json()["id"]
    missing_suggestion_id = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"decision": "accepted"},
        headers=auth("user-nw-agent-01"),
    )
    assert missing_suggestion_id.status_code == 422

    missing_summary = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"suggestion_id": suggestion_id, "decision": "edited"},
        headers=auth("user-nw-agent-01"),
    )
    assert missing_summary.status_code == 422

    unexpected_summary = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={
            "suggestion_id": suggestion_id,
            "decision": "accepted",
            "edited_summary": "不应接受",
        },
        headers=auth("user-nw-agent-01"),
    )
    assert unexpected_summary.status_code == 422

    missing_rejection_reason = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={"suggestion_id": suggestion_id, "decision": "rejected"},
        headers=auth("user-nw-agent-01"),
    )
    assert missing_rejection_reason.status_code == 422

    non_standard_reason = client.post(
        "/v1/tickets/ticket-nw-open-01/triage/decision",
        json={
            "suggestion_id": suggestion_id,
            "decision": "rejected",
            "reason_code": "free_form_reason",
        },
        headers=auth("user-nw-agent-01"),
    )
    assert non_standard_reason.status_code == 422


class RateLimitFault(RuntimeError):
    pass


class FailingTriageGateway:
    provider_name = "failure-test"
    model_name = "failure-test-v1"
    prompt_version = "triage-v1"

    def __init__(self, failure: str):
        self.failure = failure

    def generate_structured(self, *, text: str) -> TriageResult:
        del text
        if self.failure == "timeout":
            raise TimeoutError("synthetic provider timeout")
        if self.failure == "rate_limit":
            raise RateLimitFault("synthetic provider rate limit")
        return {"invalid": "structured output"}  # type: ignore[return-value]


@pytest.mark.parametrize("failure", ["invalid", "timeout", "rate_limit"], ids=str)
def test_model_does_not_modify_ticket(client, monkeypatch, failure):
    before_response = client.get(
        "/v1/tickets/ticket-nw-open-01", headers=auth("user-nw-agent-01")
    )
    assert before_response.status_code == 200
    before = before_response.json()

    gateway = FailingTriageGateway(failure)
    monkeypatch.setattr(triage_service, "get_gateway", lambda: gateway)
    triage_response = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    assert triage_response.status_code == 201
    suggestion = triage_response.json()
    assert suggestion["risk_level"] == "R3"
    assert suggestion["suggested_priority"] == "urgent"
    assert suggestion["route"] == "human_handoff"
    assert suggestion["needs_human_review"] is True

    after_response = client.get(
        "/v1/tickets/ticket-nw-open-01", headers=auth("user-nw-agent-01")
    )
    assert after_response.status_code == 200
    after = after_response.json()
    core_fields = {
        "id",
        "tenant_id",
        "customer_id",
        "order_id",
        "status",
        "subject",
        "description",
        "priority",
        "assignee_id",
        "created_at",
        "closed_at",
    }
    if "version" in before:
        core_fields.add("version")
    assert {field: before[field] for field in core_fields} == {
        field: after[field] for field in core_fields
    }


def test_customer_cannot_read_triage_result(client):
    client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    res = client.get(
        "/v1/tickets/ticket-nw-open-01/triage",
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 403


class CapturingGateway:
    provider_name = "capture-provider"
    model_name = "capture-model"
    prompt_version = "capture-v1"

    def __init__(self):
        self.text = ""

    def generate_structured(self, *, text: str) -> TriageResult:
        self.text = text
        return TriageResult(
            intent=Intent.product_faq,
            risk_level=RiskLevel.R0,
            route=Route.self_service,
            suggested_priority=TicketPriority.low,
            summary="普通咨询",
        )


def test_pii_is_redacted_before_gateway_call(client, db, monkeypatch):
    ticket = db.get(Ticket, "ticket-nw-open-01")
    assert ticket is not None
    ticket.subject = "联系 alice@example.com 或 13812345678"
    ticket.description = "身份证 11010519491231002X，卡号 4111 1111 1111 1111"
    db.commit()

    gateway = CapturingGateway()
    monkeypatch.setattr(triage_service, "get_gateway", lambda: gateway)
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    assert res.status_code == 201
    assert "alice@example.com" not in gateway.text
    assert "13812345678" not in gateway.text
    assert "11010519491231002X" not in gateway.text
    assert "4111 1111 1111 1111" not in gateway.text
    assert "[REDACTED_EMAIL]" in gateway.text
    assert "[REDACTED_PHONE]" in gateway.text
    assert "[REDACTED_CN_ID]" in gateway.text
    assert "[REDACTED_PAYMENT_CARD]" in gateway.text


def test_deterministic_policy_prevents_model_risk_downgrade(client, db, monkeypatch):
    ticket = db.get(Ticket, "ticket-nw-open-01")
    assert ticket is not None
    ticket.subject = "我要退款"
    ticket.description = "模型即使判断普通咨询也必须进入人工复核"
    db.commit()

    gateway = CapturingGateway()
    monkeypatch.setattr(triage_service, "get_gateway", lambda: gateway)
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/triage", headers=auth("user-nw-agent-01")
    )
    assert res.status_code == 201
    assert res.json()["intent"] == "return_request"
    assert res.json()["risk_level"] == "R2"
    assert res.json()["route"] == "human_handoff"
    assert res.json()["suggested_priority"] == "high"
    assert res.json()["needs_human_review"] is True
    assert "policy_floor_applied" in res.json()["reason_codes"]
