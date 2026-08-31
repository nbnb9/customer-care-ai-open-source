import json

import pytest
from conftest import auth
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.conversations import (
    ConversationFeedback,
    ConversationMessage,
)
from app.models.knowledge import KnowledgeImport, KnowledgeImportStatus
from app.modules.conversations import service as conversation_service
from app.modules.knowledge import answer as answer_service
from app.modules.knowledge.worker import process_pending_imports
from app.shared.config import get_settings

CUSTOMER = "user-nw-customer-01"
OTHER_CUSTOMER = "user-nw-customer-02"
SUPERVISOR = "user-nw-supervisor-01"


def _idempotent_headers(user_id: str, key: str) -> dict[str, str]:
    return {**auth(user_id), "Idempotency-Key": key}


def _create_conversation(client, *, user_id: str = CUSTOMER, key: str) -> dict:
    response = client.post(
        "/v1/conversations",
        json={},
        headers=_idempotent_headers(user_id, key),
    )
    assert response.status_code == 201
    return response.json()


def _read_events(
    client, conversation_id: str, *, user_id: str = CUSTOMER
) -> list[dict]:
    response = client.get(
        f"/v1/conversations/{conversation_id}/events?follow=false",
        headers=auth(user_id),
    )
    assert response.status_code == 200
    events: list[dict] = []
    for frame in response.text.split("\n\n"):
        if not frame or frame.startswith(":"):
            continue
        fields = dict(
            line.split(": ", 1) for line in frame.splitlines() if ": " in line
        )
        events.append(json.loads(fields["data"]))
    return events


def _set_r0_canary(
    client,
    monkeypatch,
    *,
    enabled: bool = True,
    customer_ids: list[str] | None = None,
    allow_all_customers: bool = False,
) -> dict:
    monkeypatch.setattr(get_settings(), "r0_self_service_master_enabled", True)
    response = client.put(
        "/v1/admin/self-service/r0-feature-flag",
        json={
            "enabled": enabled,
            "allow_all_customers": allow_all_customers,
            "customer_ids": customer_ids
            if customer_ids is not None
            else ["customer-nw-01"],
        },
        headers=auth(SUPERVISOR),
    )
    assert response.status_code == 200
    return response.json()


def _import_document(
    client,
    db,
    *,
    document_key: str,
    title: str,
    content: str,
    audience: str = "public",
) -> None:
    response = client.post(
        "/v1/admin/knowledge/imports",
        json={
            "filename": f"{document_key}.md",
            "title": title,
            "content": content,
            "document_key": document_key,
            "owner": "demo-knowledge-operations",
            "audience": audience,
            "product": "demo-support",
            "region": "cn-north",
            "language": "zh-CN",
            "version": "1.0.0",
            "effective_at": "2026-01-01T00:00:00Z",
            "requested_status": "published",
            "untrusted_content": False,
        },
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 202
    process_pending_imports(db, limit=10)
    item = db.get(KnowledgeImport, response.json()["id"])
    assert item is not None
    assert item.status is KnowledgeImportStatus.published


def test_r0_flag_defaults_closed_is_supervisor_scoped_and_audited(
    client, db, monkeypatch
):
    default = client.get(
        "/v1/admin/self-service/r0-feature-flag", headers=auth(SUPERVISOR)
    )
    assert default.status_code == 200
    assert default.json() == {
        "tenant_id": "tenant-northwind-demo",
        "enabled": False,
        "allow_all_customers": False,
        "customer_ids": [],
        "master_enabled": False,
        "effective_for_tenant": False,
    }
    assert (
        client.get(
            "/v1/admin/self-service/r0-feature-flag", headers=auth(CUSTOMER)
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v1/admin/self-service/r0-feature-flag",
            json={"enabled": True, "customer_ids": ["customer-nw-01"]},
            headers=auth("user-nw-agent-01"),
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v1/admin/self-service/r0-feature-flag",
            json={"enabled": True, "customer_ids": []},
            headers=auth(SUPERVISOR),
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/v1/admin/self-service/r0-feature-flag",
            json={"enabled": True, "customer_ids": ["customer-ct-01"]},
            headers=auth(SUPERVISOR),
        ).status_code
        == 400
    )

    updated = _set_r0_canary(client, monkeypatch)
    assert updated["enabled"] is True
    assert updated["customer_ids"] == ["customer-nw-01"]
    assert updated["master_enabled"] is True
    assert updated["effective_for_tenant"] is True
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "r0_self_service.feature_flag_updated",
            AuditEvent.resource_id == "tenant-northwind-demo",
        )
    )
    assert audit is not None
    assert audit.actor_id == SUPERVISOR


def test_r0_canary_emits_cited_public_answer_feedback_and_honors_kill_switch(
    client, db, monkeypatch
):
    _set_r0_canary(client, monkeypatch)
    _import_document(
        client,
        db,
        document_key="public-setup-faq",
        title="公开安装 FAQ",
        content="公开安装说明：请按应用内引导完成首次设置。",
    )
    _import_document(
        client,
        db,
        document_key="customer-only-setup-faq",
        title="客户专属安装 FAQ",
        content="客户专属安装说明：这条资料不属于公开 R0 范围。",
        audience="customer",
    )

    conversation = _create_conversation(client, key="r0-conversation-create-001")
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "公开安装说明"},
        headers=_idempotent_headers(CUSTOMER, "r0-conversation-message-001"),
    )
    assert sent.status_code == 202
    events = _read_events(client, conversation["id"])
    assert not any(event["type"] == "answer.delta" for event in events)
    completed = next(event for event in events if event["type"] == "answer.completed")
    assert completed["payload"]["answer_mode"] == "r0_self_service"
    assert (
        completed["payload"]["text"]
        == "根据「公开安装 FAQ」：公开安装说明：请按应用内引导完成首次设置。"
    )
    assert [citation["title"] for citation in completed["payload"]["citations"]] == [
        "公开安装 FAQ"
    ]
    assert completed["payload"]["allowed_actions"] == [
        {"type": "handoff.request", "label": "转人工客服"},
        {"type": "feedback.helpful", "label": "有帮助"},
        {"type": "feedback.unhelpful", "label": "没有帮助"},
    ]
    answer_message_id = completed["payload"]["message_id"]
    assert (
        db.scalar(
            select(ConversationMessage.response_kind).where(
                ConversationMessage.id == answer_message_id
            )
        )
        == "r0_self_service"
    )

    helpful_headers = _idempotent_headers(CUSTOMER, "r0-feedback-helpful-001")
    helpful = client.post(
        f"/v1/conversations/{conversation['id']}/feedback",
        json={"answer_message_id": answer_message_id, "feedback": "helpful"},
        headers=helpful_headers,
    )
    replay = client.post(
        f"/v1/conversations/{conversation['id']}/feedback",
        json={"answer_message_id": answer_message_id, "feedback": "helpful"},
        headers=helpful_headers,
    )
    assert helpful.status_code == replay.status_code == 201
    assert helpful.json() == replay.json()
    changed = client.post(
        f"/v1/conversations/{conversation['id']}/feedback",
        json={"answer_message_id": answer_message_id, "feedback": "unhelpful"},
        headers=_idempotent_headers(CUSTOMER, "r0-feedback-unhelpful-001"),
    )
    assert changed.status_code == 201
    assert changed.json()["feedback"] == "unhelpful"
    assert (
        db.scalar(
            select(ConversationFeedback.feedback).where(
                ConversationFeedback.answer_message_id == answer_message_id
            )
        )
        == "unhelpful"
    )
    events_after_feedback = _read_events(client, conversation["id"])
    feedback_events = [
        event for event in events_after_feedback if event["type"] == "feedback.recorded"
    ]
    assert [event["payload"]["feedback"] for event in feedback_events] == [
        "helpful",
        "unhelpful",
    ]

    handoff = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=_idempotent_headers(CUSTOMER, "r0-context-handoff-001"),
    )
    assert handoff.status_code == 201
    staff_detail = client.get(
        f"/v1/handoffs/{handoff.json()['id']}", headers=auth(SUPERVISOR)
    )
    assert staff_detail.status_code == 200
    customer_visible = completed["payload"]["citations"][0]
    assert staff_detail.json()["context"]["customer_visible_citations"] == [
        {
            "title": customer_visible["title"],
            "document_version": customer_visible["document_version"],
            "excerpt": customer_visible["excerpt"],
            "source_locator": customer_visible["source_locator"],
        }
    ]

    non_canary = _create_conversation(
        client,
        user_id=OTHER_CUSTOMER,
        key="r0-other-customer-conversation-create-001",
    )
    other_sent = client.post(
        f"/v1/conversations/{non_canary['id']}/messages",
        json={"text": "公开安装说明"},
        headers=_idempotent_headers(OTHER_CUSTOMER, "r0-other-customer-message-001"),
    )
    assert other_sent.status_code == 202
    other_events = _read_events(client, non_canary["id"], user_id=OTHER_CUSTOMER)
    other_completed = next(
        event for event in other_events if event["type"] == "answer.completed"
    )
    assert other_completed["payload"]["answer_mode"] == "controlled_ack"

    called = False

    def unexpected_gateway():
        nonlocal called
        called = True
        raise AssertionError("blocked customer answer must not reach the model gateway")

    monkeypatch.setattr(answer_service, "get_gateway", unexpected_gateway)
    direct_high_risk = client.post(
        "/v1/knowledge/answer",
        json={
            "query": "请退款",
            "product": "another-client-selected-product",
            "region": "cn-north",
            "language": "en-US",
        },
        headers=auth(CUSTOMER),
    )
    assert direct_high_risk.status_code == 200
    assert direct_high_risk.json()["needs_handoff"] is True
    assert direct_high_risk.json()["citations"] == []

    direct_customer_only = client.post(
        "/v1/knowledge/answer",
        json={
            "query": "客户专属安装说明",
            "product": "another-client-selected-product",
            "region": "cn-north",
            "language": "en-US",
        },
        headers=auth(CUSTOMER),
    )
    assert direct_customer_only.status_code == 200
    assert direct_customer_only.json()["needs_handoff"] is True
    assert direct_customer_only.json()["citations"] == []
    assert called is False

    disabled = _set_r0_canary(
        client,
        monkeypatch,
        enabled=False,
        customer_ids=[],
    )
    assert disabled["effective_for_tenant"] is False
    disabled_conversation = _create_conversation(
        client, key="r0-conversation-create-disabled-001"
    )
    disabled_send = client.post(
        f"/v1/conversations/{disabled_conversation['id']}/messages",
        json={"text": "公开安装说明"},
        headers=_idempotent_headers(CUSTOMER, "r0-conversation-message-disabled-001"),
    )
    assert disabled_send.status_code == 202
    disabled_events = _read_events(client, disabled_conversation["id"])
    disabled_completed = next(
        event for event in disabled_events if event["type"] == "answer.completed"
    )
    assert disabled_completed["payload"]["answer_mode"] == "controlled_ack"
    assert disabled_completed["payload"]["citations"] == []


@pytest.mark.parametrize(
    ("text", "reason_code"),
    [
        ("请退款", "policy_action"),
        ("商品损坏了，请补发换货", "policy_action"),
        ("我无法登录", "identity_or_account"),
        ("请修改邮箱", "identity_or_account"),
        ("我的订单物流在哪里", "order_or_account_action"),
        ("我要人工客服", "customer_requested"),
        ("请删除我的数据", "policy_action"),
    ],
)
def test_r0_sensitive_intents_are_handed_off_before_knowledge_or_model_calls(
    client, monkeypatch, text, reason_code
):
    _set_r0_canary(client, monkeypatch)

    def unexpected_answer(*args, **kwargs):
        del args, kwargs
        raise AssertionError("sensitive R0 input must not reach knowledge answering")

    monkeypatch.setattr(
        conversation_service.knowledge_answer, "answer_knowledge", unexpected_answer
    )
    conversation = _create_conversation(
        client, key=f"r0-sensitive-conversation-{reason_code}-{len(text)}"
    )
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": text},
        headers=_idempotent_headers(
            CUSTOMER, f"r0-sensitive-message-{reason_code}-{len(text)}"
        ),
    )
    assert response.status_code == 202
    events = _read_events(client, conversation["id"])
    assert not any(event["type"] == "answer.completed" for event in events)
    handoff = next(event for event in events if event["type"] == "handoff.created")
    assert handoff["payload"]["reason_code"] == reason_code


def test_r0_without_public_evidence_persists_a_notice_and_handoff(
    client, db, monkeypatch
):
    _set_r0_canary(client, monkeypatch)
    _import_document(
        client,
        db,
        document_key="customer-only-r0-evidence",
        title="客户专属资料",
        content="仅客户资料：这条资料绝不能作为公开 R0 证据。",
        audience="customer",
    )
    conversation = _create_conversation(client, key="r0-no-evidence-create-001")
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "仅客户资料"},
        headers=_idempotent_headers(CUSTOMER, "r0-no-evidence-message-001"),
    )
    assert sent.status_code == 202
    events = _read_events(client, conversation["id"])
    assert not any(event["type"] == "answer.delta" for event in events)
    assert not any(event["type"] == "answer.completed" for event in events)
    notice = next(
        event
        for event in events
        if event["type"] == "message.created"
        and event["payload"].get("actor") == "system"
    )
    assert notice["payload"]["text"] == (
        "当前无法基于可验证的公开资料完成回答，已转交人工客服处理。"
    )
    handoff = next(event for event in events if event["type"] == "handoff.created")
    assert handoff["payload"]["reason_code"] == "r0_evidence_unavailable"
    assert (
        db.scalar(
            select(ConversationMessage.generation_status).where(
                ConversationMessage.id == sent.json()["message_id"]
            )
        )
        == "routed_to_handoff"
    )


def test_feedback_cannot_target_an_acknowledgement_or_another_customers_answer(
    client, db
):
    conversation = _create_conversation(client, key="r0-feedback-reject-create-001")
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "普通问题"},
        headers=_idempotent_headers(CUSTOMER, "r0-feedback-reject-message-001"),
    )
    assert sent.status_code == 202
    completed = next(
        event
        for event in _read_events(client, conversation["id"])
        if event["type"] == "answer.completed"
    )
    answer_message_id = completed["payload"]["message_id"]
    payload = {"answer_message_id": answer_message_id, "feedback": "helpful"}
    assert (
        client.post(
            f"/v1/conversations/{conversation['id']}/feedback",
            json=payload,
            headers=_idempotent_headers(CUSTOMER, "r0-feedback-reject-own-001"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/conversations/{conversation['id']}/feedback",
            json=payload,
            headers=_idempotent_headers(OTHER_CUSTOMER, "r0-feedback-reject-other-001"),
        ).status_code
        == 404
    )
    assert db.scalar(select(ConversationFeedback.id)) is None


def test_r0_rechecks_its_kill_switch_before_exposing_a_model_answer(
    client, db, monkeypatch
):
    _set_r0_canary(client, monkeypatch)
    _import_document(
        client,
        db,
        document_key="r0-kill-switch-faq",
        title="R0 kill switch FAQ",
        content="开关验证说明：这个回答在二次检查前不得暴露。",
    )
    enabled_checks = 0
    lock_checks: list[bool] = []
    original_is_enabled = conversation_service.self_service.is_r0_self_service_enabled

    def disable_after_model(*args, **kwargs):
        nonlocal enabled_checks
        enabled_checks += 1
        lock_checks.append(bool(kwargs.get("for_update", False)))
        if enabled_checks == 1:
            return original_is_enabled(*args, **kwargs)
        return False

    monkeypatch.setattr(
        conversation_service.self_service,
        "is_r0_self_service_enabled",
        disable_after_model,
    )
    conversation = _create_conversation(client, key="r0-kill-switch-create-001")
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "开关验证说明"},
        headers=_idempotent_headers(CUSTOMER, "r0-kill-switch-message-001"),
    )
    assert response.status_code == 202
    completed = next(
        event
        for event in _read_events(client, conversation["id"])
        if event["type"] == "answer.completed"
    )
    assert completed["payload"]["answer_mode"] == "controlled_ack"
    assert completed["payload"]["citations"] == []
    assert "R0 kill switch FAQ" not in completed["payload"]["text"]
    assert enabled_checks == 2
    assert lock_checks == [False, True]
