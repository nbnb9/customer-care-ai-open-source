import json

from conftest import auth
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.conversations import ConversationActor, ConversationMessage
from app.models.qa import QAIdempotencyRecord, QAReviewIssue, QAReviewIssueEvent
from app.models.self_service import R0SelfServiceFeatureFlag
from app.models.tickets import TicketPriority
from app.modules.llm_gateway.base import FakeLLMGateway, enforce_triage_policy
from app.modules.llm_gateway.schemas import Intent, RiskLevel, Route, TriageResult
from app.modules.qa.catalog import load_regression_catalog
from app.modules.self_service.service import r0_handoff_reason

CUSTOMER = "user-nw-customer-01"
AGENT = "user-nw-agent-01"
SUPERVISOR = "user-nw-supervisor-01"
KNOWLEDGE_OPERATOR = "user-nw-knowledge-operator-01"
CONTOSO_CUSTOMER = "user-ct-customer-01"
CONTOSO_SUPERVISOR = "user-ct-supervisor-01"


def _headers(user_id: str, key: str | None = None) -> dict[str, str]:
    headers = auth(user_id)
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _completed_conversation(client, *, user_id: str = CUSTOMER) -> str:
    created = client.post(
        "/v1/conversations",
        json={},
        headers=_headers(user_id, f"qa-conversation-create-{user_id}"),
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    sent = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "请联系 qa.case@example.com，手机号 13900000000；产品怎么使用？"},
        headers=_headers(user_id, f"qa-conversation-message-{user_id}"),
    )
    assert sent.status_code == 202
    return conversation_id


def _knowledge_issue_payload(conversation_id: str) -> dict[str, object]:
    return {
        "source_conversation_id": conversation_id,
        "category": "outdated_policy",
        "severity": "high",
        "owner_id": KNOWLEDGE_OPERATOR,
        "root_cause": "knowledge_data",
        "problem_summary": "答案错误，请回呼 qa.case@example.com。",
        "remediation_type": "knowledge",
        "remediation_summary": "发布当前政策并将 13900000000 样例加入回归。",
        "regression_case_id": "qa-rag-current-return-policy-v2",
        "pause_r0_feature_flag": True,
    }


def test_supervisor_qa_closes_the_loop_without_copying_raw_transcripts(client, db):
    conversation_id = _completed_conversation(client)
    answer = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.actor == ConversationActor.assistant,
        )
    )
    assert answer is not None
    assert answer.response_kind == "controlled_ack"

    samples = client.get("/v1/admin/qa/samples", headers=_headers(SUPERVISOR))
    assert samples.status_code == 200
    assert conversation_id in {item["conversation_id"] for item in samples.json()}

    sample = client.get(
        f"/v1/admin/qa/samples/{conversation_id}", headers=_headers(SUPERVISOR)
    )
    assert sample.status_code == 200
    sample_body = sample.json()
    assert sample_body["route"]["answer_mode"] == "controlled_ack"
    assert sample_body["citations"] == []
    assert sample_body["tool_summaries"] == []
    assert sample_body["feedback"] == {"helpful_count": 0, "unhelpful_count": 0}
    assert "qa.case@example.com" not in sample.text
    assert "13900000000" not in sample.text
    assert "[REDACTED_EMAIL]" in sample.text
    assert "[REDACTED_PHONE]" in sample.text

    db.add(
        R0SelfServiceFeatureFlag(
            tenant_id="tenant-northwind-demo",
            enabled=True,
            allow_all_customers=False,
            customer_ids=["customer-nw-01"],
            updated_by=SUPERVISOR,
        )
    )
    db.commit()

    created = client.post(
        "/v1/admin/qa/issues",
        json=_knowledge_issue_payload(conversation_id),
        headers=_headers(SUPERVISOR, "qa-issue-create-001"),
    )
    assert created.status_code == 201
    body = created.json()
    issue_id = body["id"]
    assert body["status"] == "open"
    assert body["feature_flag"]["paused"] is True
    assert body["feature_flag"]["snapshot"]["enabled_before"] is True
    assert body["feature_flag"]["snapshot"]["enabled_after"] is False
    assert body["regression"] == {
        "catalog_case_id": "qa-rag-current-return-policy-v2",
        "evaluation_suite": "rag",
        "evaluation_case_id": "rag-qa-current-return-policy-v2",
        "status": "not_run",
        "evidence_ref": None,
    }
    assert "qa.case@example.com" not in created.text
    assert "13900000000" not in created.text
    assert "[REDACTED_EMAIL]" in body["problem_summary"]
    assert "[REDACTED_PHONE]" in body["remediation_summary"]
    assert db.get(R0SelfServiceFeatureFlag, "tenant-northwind-demo").enabled is False

    issue = db.get(QAReviewIssue, issue_id)
    assert issue is not None
    evidence = json.dumps(issue.source_snapshot, ensure_ascii=False)
    assert "qa.case@example.com" not in evidence
    assert "13900000000" not in evidence
    assert "body_sha256" in evidence
    idempotency = db.scalar(select(QAIdempotencyRecord))
    assert idempotency is not None
    assert idempotency.response_body == {"issue_id": issue_id}

    replay = client.post(
        "/v1/admin/qa/issues",
        json=_knowledge_issue_payload(conversation_id),
        headers=_headers(SUPERVISOR, "qa-issue-create-001"),
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == issue_id
    assert db.scalar(select(func.count()).select_from(QAReviewIssue)) == 1
    changed = _knowledge_issue_payload(conversation_id)
    changed["problem_summary"] = "不同的故障描述"
    conflict = client.post(
        "/v1/admin/qa/issues",
        json=changed,
        headers=_headers(SUPERVISOR, "qa-issue-create-001"),
    )
    assert conflict.status_code == 409

    in_remediation = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={"status": "in_remediation"},
        headers=_headers(SUPERVISOR, "qa-issue-remediation-001"),
    )
    assert in_remediation.status_code == 200
    verifying = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={"status": "verification_pending"},
        headers=_headers(SUPERVISOR, "qa-issue-verification-001"),
    )
    assert verifying.status_code == 200
    premature = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={"status": "verified"},
        headers=_headers(SUPERVISOR, "qa-issue-verified-premature-001"),
    )
    assert premature.status_code == 422
    verified = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={
            "status": "verified",
            "regression_status": "passed",
            "regression_evidence_ref": "pytest:rag-qa-current-return-policy-v2",
        },
        headers=_headers(SUPERVISOR, "qa-issue-verified-001"),
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    assert verified.json()["regression"]["status"] == "passed"
    assert verified.json()["verified_by"] == SUPERVISOR
    assert verified.json()["verified_at"] is not None
    invalid_close = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={"status": "closed", "regression_status": "failed"},
        headers=_headers(SUPERVISOR, "qa-issue-close-with-failed-regression-001"),
    )
    assert invalid_close.status_code == 409
    replay_verified = client.patch(
        f"/v1/admin/qa/issues/{issue_id}",
        json={
            "status": "verified",
            "regression_status": "passed",
            "regression_evidence_ref": "pytest:rag-qa-current-return-policy-v2",
        },
        headers=_headers(SUPERVISOR, "qa-issue-verified-001"),
    )
    assert replay_verified.status_code == 200
    assert replay_verified.json()["id"] == issue_id
    assert [
        event.sequence
        for event in db.scalars(
            select(QAReviewIssueEvent)
            .where(QAReviewIssueEvent.issue_id == issue_id)
            .order_by(QAReviewIssueEvent.sequence)
        )
    ] == [1, 2, 3, 4]
    audit_actions = {
        event.action
        for event in db.scalars(
            select(AuditEvent).where(AuditEvent.resource_id == issue_id)
        )
    }
    assert {"qa.issue_created", "qa.issue_updated"}.issubset(audit_actions)
    paused_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "qa.r0_feature_flag_paused",
            AuditEvent.resource_id == "tenant-northwind-demo",
        )
    )
    assert paused_audit is not None
    assert "qa.case@example.com" not in (
        f"{paused_audit.before_summary} {paused_audit.after_summary}"
    )


def test_supervisor_qa_permissions_catalog_and_tenant_scope(client):
    for user_id in (CUSTOMER, AGENT, KNOWLEDGE_OPERATOR):
        assert (
            client.get("/v1/admin/qa/samples", headers=_headers(user_id)).status_code
            == 403
        )

    catalog = client.get("/v1/admin/qa/regression-cases", headers=_headers(SUPERVISOR))
    assert catalog.status_code == 200
    assert {item["id"] for item in catalog.json()} == {
        "qa-rag-current-return-policy-v2",
        "qa-triage-r2-replacement-routing",
    }
    assert load_regression_catalog().source == "synthetic"

    northwind_conversation = _completed_conversation(client)
    contoso_conversation = _completed_conversation(client, user_id=CONTOSO_CUSTOMER)
    assert (
        client.get(
            f"/v1/admin/qa/samples/{contoso_conversation}",
            headers=_headers(SUPERVISOR),
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/v1/admin/qa/samples/{northwind_conversation}",
            headers=_headers(CONTOSO_SUPERVISOR),
        ).status_code
        == 404
    )

    payload = _knowledge_issue_payload(northwind_conversation)
    payload["owner_id"] = "user-ct-agent-01"
    assert (
        client.post(
            "/v1/admin/qa/issues",
            json=payload,
            headers=_headers(SUPERVISOR, "qa-issue-cross-tenant-owner-001"),
        ).status_code
        == 404
    )
    payload = _knowledge_issue_payload(northwind_conversation)
    payload["severity"] = "low"
    assert (
        client.post(
            "/v1/admin/qa/issues",
            json=payload,
            headers=_headers(SUPERVISOR, "qa-issue-low-pause-001"),
        ).status_code
        == 422
    )


def test_replacement_rule_is_r2_before_and_after_model_output_policy():
    text = "我的商品损坏了，请帮我补发换货"
    assert r0_handoff_reason(text) == "policy_action"
    generated = FakeLLMGateway().generate_structured(text=text)
    assert generated.model_dump(mode="json") == {
        "intent": "return_request",
        "risk_level": "R2",
        "route": "human_handoff",
        "suggested_priority": "high",
        "summary": "退货/退款/补换货请求",
        "missing_information": [],
        "needs_human_review": True,
        "reason_codes": ["policy_action"],
    }
    downgraded = TriageResult(
        intent=Intent.product_faq,
        risk_level=RiskLevel.R0,
        route=Route.self_service,
        suggested_priority=TicketPriority.low,
        summary="错误的 R0 输出",
    )
    enforced = enforce_triage_policy(downgraded, text=text)
    assert enforced.intent is Intent.return_request
    assert enforced.risk_level is RiskLevel.R2
    assert enforced.route is Route.human_handoff
    assert enforced.suggested_priority is TicketPriority.high
    assert enforced.needs_human_review is True
