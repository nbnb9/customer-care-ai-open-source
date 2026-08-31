import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from conftest import auth
from fastapi import HTTPException
from sqlalchemy import func, select

from app.main import app
from app.models.audit import AuditEvent
from app.models.conversations import (
    Conversation,
    ConversationActor,
    ConversationEvent,
    ConversationHandoff,
    ConversationIdempotencyRecord,
    ConversationMessage,
    ConversationStatus,
)
from app.models.identity import UserRole
from app.modules.conversations import service
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal
from app.shared.config import get_settings

CUSTOMER = "user-nw-customer-01"
OTHER_CUSTOMER = "user-nw-customer-02"
OTHER_TENANT_CUSTOMER = "user-ct-customer-01"
AGENT = "user-nw-agent-01"
OTHER_AGENT = "user-nw-agent-02"
SUPERVISOR = "user-nw-supervisor-01"
OTHER_TENANT_AGENT = "user-ct-agent-01"


def idempotent_headers(user_id: str, key: str) -> dict[str, str]:
    return {**auth(user_id), "Idempotency-Key": key}


def create_conversation(
    client, *, user_id: str = CUSTOMER, key: str = "conversation-create-001"
):
    response = client.post(
        "/v1/conversations",
        json={},
        headers=idempotent_headers(user_id, key),
    )
    assert response.status_code == 201
    return response.json()


def read_sse_events(client, conversation_id: str, *, user_id: str = CUSTOMER, **params):
    response = client.get(
        f"/v1/conversations/{conversation_id}/events",
        params={"follow": "false", **params},
        headers=auth(user_id),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    events = []
    for frame in response.text.split("\n\n"):
        if not frame or frame.startswith(":"):
            continue
        fields = dict(
            line.split(": ", 1) for line in frame.splitlines() if ": " in line
        )
        assert fields["id"].isdigit()
        events.append(json.loads(fields["data"]))
    return events


def test_customer_can_create_idempotent_conversation_with_durable_first_event(
    client, db
):
    first = create_conversation(client, key="conversation-create-stable-001")
    replay = client.post(
        "/v1/conversations",
        json={},
        headers=idempotent_headers(CUSTOMER, "conversation-create-stable-001"),
    )

    assert replay.status_code == 201
    assert replay.json() == first
    assert first["status"] == "active"
    assert first["last_sequence"] == 1
    assert db.scalar(select(func.count()).select_from(Conversation)) == 1
    assert db.scalar(select(func.count()).select_from(ConversationEvent)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.conversation_id == first["id"],
            )
        )
        == 1
    )
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == first["id"],
            AuditEvent.action == "conversation.created",
        )
    )
    assert audit is not None
    assert audit.request_id


def test_conversation_resources_are_invisible_to_other_customers_and_staff(client):
    conversation = create_conversation(client)
    conversation_id = conversation["id"]

    for user_id, endpoint, method in (
        (
            OTHER_CUSTOMER,
            f"/v1/conversations/{conversation_id}/events?follow=false",
            "get",
        ),
        (
            OTHER_TENANT_CUSTOMER,
            f"/v1/conversations/{conversation_id}/events?follow=false",
            "get",
        ),
        (AGENT, f"/v1/conversations/{conversation_id}/events?follow=false", "get"),
        (OTHER_CUSTOMER, f"/v1/conversations/{conversation_id}/messages", "post"),
        (AGENT, f"/v1/conversations/{conversation_id}/messages", "post"),
        (AGENT, f"/v1/conversations/{conversation_id}/handoffs", "post"),
    ):
        if method == "get":
            response = client.get(endpoint, headers=auth(user_id))
        else:
            payload = (
                {"reason_code": "customer_requested"}
                if endpoint.endswith("/handoffs")
                else {"text": "越权消息"}
            )
            response = client.post(
                endpoint,
                json=payload,
                headers=idempotent_headers(user_id, "cross-owner-message-001"),
            )
        assert response.status_code == 404


def test_message_generation_emits_ordered_controlled_sse_events(client, db):
    conversation = create_conversation(client)
    request_id = "conversation-message-request-001"
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "产品怎么使用？"},
        headers={
            **idempotent_headers(CUSTOMER, "conversation-message-001"),
            "X-Request-ID": request_id,
        },
    )

    assert response.status_code == 202
    accepted = response.json()
    events = read_sse_events(client, conversation["id"])
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "conversation.created"
    assert any(event["type"] == "message.created" for event in events)
    deltas = [event for event in events if event["type"] == "answer.delta"]
    completed = [event for event in events if event["type"] == "answer.completed"]
    assert deltas
    assert len(completed) == 1
    assert all(set(event["payload"]) == {"message_id", "delta"} for event in deltas)
    assert all(event["request_id"] == request_id for event in events[1:])
    assert completed[0]["payload"]["citations"] == []
    assert completed[0]["payload"]["allowed_actions"] == [
        {"type": "handoff.request", "label": "转人工客服"}
    ]
    assert completed[0]["payload"]["message_id"] != accepted["message_id"]
    assert (
        db.scalar(
            select(ConversationMessage.generation_status).where(
                ConversationMessage.id == accepted["message_id"]
            )
        )
        == "completed"
    )


def test_sse_recovery_uses_sequence_cursor_or_standard_last_event_id(client):
    conversation = create_conversation(client)
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "需要帮助"},
        headers=idempotent_headers(CUSTOMER, "conversation-recovery-message-001"),
    )
    assert sent.status_code == 202
    all_events = read_sse_events(client, conversation["id"])
    cursor = all_events[2]["sequence"]

    by_query = read_sse_events(client, conversation["id"], after_sequence=str(cursor))
    by_header_response = client.get(
        f"/v1/conversations/{conversation['id']}/events?follow=false",
        headers={**auth(CUSTOMER), "Last-Event-ID": str(cursor)},
    )
    assert by_header_response.status_code == 200
    by_header = [
        json.loads(
            dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)[
                "data"
            ]
        )
        for frame in by_header_response.text.split("\n\n")
        if frame and not frame.startswith(":")
    ]
    expected = [event for event in all_events if event["sequence"] > cursor]
    assert by_query == by_header == expected

    conflict = client.get(
        f"/v1/conversations/{conversation['id']}/events?follow=false&after_sequence={cursor}",
        headers={**auth(CUSTOMER), "Last-Event-ID": str(cursor + 1)},
    )
    assert conflict.status_code == 400

    malformed = client.get(
        f"/v1/conversations/{conversation['id']}/events?follow=false",
        headers={**auth(CUSTOMER), "Last-Event-ID": "not-a-sequence"},
    )
    assert malformed.status_code == 400


def test_message_idempotency_key_with_a_changed_body_conflicts(client):
    conversation = create_conversation(client, key="conversation-conflict-create-001")
    headers = idempotent_headers(CUSTOMER, "conversation-message-conflict-001")
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "第一个问题"},
        headers=headers,
    )
    conflict = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "不同的问题"},
        headers=headers,
    )
    assert first.status_code == 202
    assert conflict.status_code == 409
    assert "different request" in conflict.json()["message"]


def test_invalid_normalized_idempotency_key_is_rejected():
    try:
        service._normalize_idempotency_key("     short")
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError("expected an invalid idempotency key to be rejected")


def test_replaying_message_does_not_duplicate_events_or_reply(client, db):
    conversation = create_conversation(client)
    headers = idempotent_headers(CUSTOMER, "conversation-message-replay-001")
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "我要了解产品"},
        headers=headers,
    )
    replay = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "我要了解产品"},
        headers=headers,
    )

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    events = read_sse_events(client, conversation["id"])
    assert len([event for event in events if event["type"] == "message.created"]) == 1
    assert len([event for event in events if event["type"] == "answer.completed"]) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation["id"])
        )
        == 2
    )


def test_concurrent_message_replay_has_one_ordered_side_effect_set(client, db):
    conversation = create_conversation(client, key="conversation-concurrent-create-001")
    headers = idempotent_headers(CUSTOMER, "conversation-concurrent-message-001")

    def submit():
        return client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            json={"text": "并发重试不能重复创建回复"},
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))

    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json() == responses[1].json()
    events = read_sse_events(client, conversation["id"])
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert len([event for event in events if event["type"] == "message.created"]) == 1
    assert len([event for event in events if event["type"] == "answer.completed"]) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(ConversationIdempotencyRecord)
            .where(
                ConversationIdempotencyRecord.conversation_id == conversation["id"],
            )
        )
        == 2
    )


def test_manual_and_policy_handoffs_are_deduplicated_and_stop_auto_reply(client, db):
    conversation = create_conversation(client, key="conversation-handoff-create-001")
    handoff_headers = idempotent_headers(CUSTOMER, "conversation-handoff-001")
    first = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=handoff_headers,
    )
    replay = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=handoff_headers,
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert (
        db.scalar(
            select(Conversation.status).where(Conversation.id == conversation["id"])
        )
        == "handed_off"
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(ConversationHandoff)
            .where(ConversationHandoff.conversation_id == conversation["id"])
        )
        == 1
    )
    cannot_reply = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "还能继续自动回复吗？"},
        headers=idempotent_headers(CUSTOMER, "conversation-after-handoff-001"),
    )
    assert cannot_reply.status_code == 409

    policy_conversation = create_conversation(
        client, key="conversation-policy-create-001"
    )
    policy = client.post(
        f"/v1/conversations/{policy_conversation['id']}/messages",
        json={"text": "我要退款"},
        headers=idempotent_headers(CUSTOMER, "conversation-policy-message-001"),
    )
    assert policy.status_code == 202
    policy_events = read_sse_events(client, policy_conversation["id"])
    assert not any(event["type"] == "answer.completed" for event in policy_events)
    handoff_event = next(
        event for event in policy_events if event["type"] == "handoff.created"
    )
    assert handoff_event["payload"]["reason_code"] == "policy_action"
    policy_queue_item = next(
        item
        for item in client.get("/v1/handoffs", headers=auth(SUPERVISOR)).json()
        if item["id"] == handoff_event["payload"]["handoff_id"]
    )
    assert policy_queue_item["priority"] == "high"
    assert policy_queue_item["target_queue"] == "after-sales-review"
    assert (
        db.scalar(
            select(ConversationMessage.generation_status).where(
                ConversationMessage.id == policy.json()["message_id"]
            )
        )
        == "routed_to_handoff"
    )


def test_new_handoff_key_returns_existing_handoff_without_a_second_event(client):
    conversation = create_conversation(
        client, key="conversation-handoff-existing-create"
    )
    first = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=idempotent_headers(CUSTOMER, "conversation-handoff-existing-001"),
    )
    second = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=idempotent_headers(CUSTOMER, "conversation-handoff-existing-002"),
    )
    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["already_requested"] is True
    events = read_sse_events(client, conversation["id"])
    assert len([event for event in events if event["type"] == "handoff.created"]) == 1


def test_safety_message_is_blocked_and_escalated_without_an_answer(client, db):
    conversation = create_conversation(client, key="conversation-safety-create-001")
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "有人试图绕过安全限制"},
        headers=idempotent_headers(CUSTOMER, "conversation-safety-message-001"),
    )
    assert response.status_code == 202
    assert (
        db.scalar(
            select(Conversation.status).where(Conversation.id == conversation["id"])
        )
        == "blocked_for_safety"
    )
    events = read_sse_events(client, conversation["id"])
    assert not any(event["type"] == "answer.completed" for event in events)
    handoff = next(event for event in events if event["type"] == "handoff.created")
    assert handoff["payload"]["reason_code"] == "safety_review"


def test_safety_review_queue_is_reserved_for_supervisor_handling(client):
    conversation = create_conversation(
        client, key="conversation-safety-queue-create-001"
    )
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "请检查安全风险"},
        headers=idempotent_headers(CUSTOMER, "conversation-safety-queue-message-001"),
    )
    assert sent.status_code == 202
    handoff_id = next(
        event
        for event in read_sse_events(client, conversation["id"])
        if event["type"] == "handoff.created"
    )["payload"]["handoff_id"]

    assert client.get("/v1/handoffs", headers=auth(AGENT)).json() == []
    assert (
        client.get(f"/v1/handoffs/{handoff_id}", headers=auth(AGENT)).status_code == 404
    )
    assert (
        client.post(
            f"/v1/handoffs/{handoff_id}/claim",
            json={},
            headers=idempotent_headers(AGENT, "conversation-safety-agent-claim-001"),
        ).status_code
        == 404
    )

    claimed = client.post(
        f"/v1/handoffs/{handoff_id}/claim",
        json={},
        headers=idempotent_headers(
            SUPERVISOR, "conversation-safety-supervisor-claim-001"
        ),
    )
    assert claimed.status_code == 200
    assert claimed.json()["assigned_agent_id"] == SUPERVISOR
    closed = client.post(
        f"/v1/handoffs/{handoff_id}/resolution",
        json={"action": "close_self_service"},
        headers=idempotent_headers(
            SUPERVISOR, "conversation-safety-supervisor-close-001"
        ),
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_generation_failure_is_a_durable_actionable_event_not_a_stuck_reply(
    client, db, monkeypatch
):
    conversation = create_conversation(client, key="conversation-failure-create-001")

    def fail_generation() -> str:
        raise RuntimeError("synthetic generator outage")

    monkeypatch.setattr(service, "_generate_customer_reply", fail_generation)
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "模拟回答失败"},
        headers=idempotent_headers(CUSTOMER, "conversation-failure-message-001"),
    )
    assert response.status_code == 202
    events = read_sse_events(client, conversation["id"])
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["payload"] == {
        "message_id": response.json()["message_id"],
        "code": "answer_generation_failed",
        "message": "系统暂时无法完成回复。你可以重试或转人工客服。",
        "retryable": True,
        "allowed_actions": [{"type": "handoff.request", "label": "转人工客服"}],
    }
    assert not any(event["type"] == "answer.completed" for event in events)
    assert (
        db.scalar(
            select(ConversationMessage.generation_status).where(
                ConversationMessage.id == response.json()["message_id"]
            )
        )
        == "failed"
    )


def test_handoff_queue_exposes_only_a_minimized_redacted_staff_context(client, db):
    conversation = create_conversation(client, key="handoff-context-create-001")
    sent = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={
            "text": "我的邮箱是 customer@example.test，电话是 13800138000，请帮忙查看。"
        },
        headers=idempotent_headers(CUSTOMER, "handoff-context-message-001"),
    )
    assert sent.status_code == 202
    requested = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=idempotent_headers(CUSTOMER, "handoff-context-request-001"),
    )
    assert requested.status_code == 201
    handoff = requested.json()
    # The customer response intentionally omits staff-only routing and context.
    assert set(handoff) == {
        "id",
        "conversation_id",
        "reason_code",
        "status",
        "created_at",
        "already_requested",
    }

    queue = client.get("/v1/handoffs", headers=auth(SUPERVISOR))
    assert queue.status_code == 200
    item = next(row for row in queue.json() if row["id"] == handoff["id"])
    assert item["priority"] == "normal"
    assert item["target_queue"] == "general-support"
    assert item["sla_due_at"]
    assert "context" not in item

    detail_response = client.get(
        f"/v1/handoffs/{handoff['id']}", headers=auth(SUPERVISOR)
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    context = detail["context"]
    assert set(context) == {
        "schema_version",
        "verification_level",
        "conversation_summary",
        "confirmed_facts",
        "tool_summaries",
        "customer_visible_citations",
        "unresolved_questions",
        "risk_reasons",
    }
    assert context["verification_level"] == "demo_verified"
    assert context["tool_summaries"] == []
    assert context["risk_reasons"] == ["customer_requested"]
    rendered_context = json.dumps(context, ensure_ascii=False)
    assert "customer@example.test" not in rendered_context
    assert "13800138000" not in rendered_context
    assert "[REDACTED_EMAIL]" in rendered_context
    assert "[REDACTED_PHONE]" in rendered_context
    assert "system prompt" not in rendered_context.lower()
    assert "trace" not in rendered_context.lower()

    assert (
        client.get(f"/v1/handoffs/{handoff['id']}", headers=auth(CUSTOMER)).status_code
        == 403
    )
    assert (
        client.get(
            f"/v1/handoffs/{handoff['id']}", headers=auth(OTHER_TENANT_AGENT)
        ).status_code
        == 404
    )
    snapshot = db.scalar(
        select(ConversationHandoff.context_snapshot).where(
            ConversationHandoff.id == handoff["id"]
        )
    )
    assert isinstance(snapshot, dict)


def test_agent_can_claim_and_close_handoff_with_idempotent_audited_events(client, db):
    conversation = create_conversation(client, key="handoff-claim-create-001")
    requested = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=idempotent_headers(CUSTOMER, "handoff-claim-request-001"),
    )
    assert requested.status_code == 201
    handoff_id = requested.json()["id"]

    claim_headers = idempotent_headers(AGENT, "handoff-claim-action-001")
    claimed = client.post(
        f"/v1/handoffs/{handoff_id}/claim",
        json={},
        headers=claim_headers,
    )
    replay = client.post(
        f"/v1/handoffs/{handoff_id}/claim",
        json={},
        headers=claim_headers,
    )
    assert claimed.status_code == replay.status_code == 200
    assert claimed.json() == replay.json()
    assert claimed.json()["status"] == "claimed"
    assert claimed.json()["assigned_agent_id"] == AGENT
    assert claimed.json()["claimed_at"]

    claimed_by_other = client.post(
        f"/v1/handoffs/{handoff_id}/claim",
        json={},
        headers=idempotent_headers(OTHER_AGENT, "handoff-claim-other-agent-001"),
    )
    assert claimed_by_other.status_code == 409
    assert (
        client.get(f"/v1/handoffs/{handoff_id}", headers=auth(OTHER_AGENT)).status_code
        == 404
    )

    own_queue = client.get("/v1/handoffs", headers=auth(AGENT))
    assert own_queue.status_code == 200
    assert [item["id"] for item in own_queue.json()] == [handoff_id]

    resolution_headers = idempotent_headers(AGENT, "handoff-close-action-001")
    closed = client.post(
        f"/v1/handoffs/{handoff_id}/resolution",
        json={"action": "close_self_service"},
        headers=resolution_headers,
    )
    closed_replay = client.post(
        f"/v1/handoffs/{handoff_id}/resolution",
        json={"action": "close_self_service"},
        headers=resolution_headers,
    )
    assert closed.status_code == closed_replay.status_code == 200
    assert closed.json() == closed_replay.json()
    assert closed.json()["status"] == "closed"
    assert closed.json()["completed_at"]
    assert (
        db.scalar(
            select(Conversation.status).where(Conversation.id == conversation["id"])
        )
        == "closed"
    )
    events = read_sse_events(client, conversation["id"])
    assert [
        event["payload"]["status"]
        for event in events
        if event["type"] == "handoff.updated"
    ] == ["claimed", "closed"]
    assert (
        db.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_id == conversation["id"],
                AuditEvent.action == "conversation.handoff_closed",
            )
        )
        is not None
    )
    assert client.get("/v1/handoffs", headers=auth(AGENT)).json() == []


def test_agent_can_restore_a_safe_manual_follow_up_without_reactivating_bot(client, db):
    conversation = create_conversation(client, key="handoff-safe-flow-create-001")
    requested = client.post(
        f"/v1/conversations/{conversation['id']}/handoffs",
        json={"reason_code": "customer_requested"},
        headers=idempotent_headers(CUSTOMER, "handoff-safe-flow-request-001"),
    )
    assert requested.status_code == 201
    handoff_id = requested.json()["id"]
    assert (
        client.post(
            f"/v1/handoffs/{handoff_id}/claim",
            json={},
            headers=idempotent_headers(AGENT, "handoff-safe-flow-claim-001"),
        ).status_code
        == 200
    )
    restored = client.post(
        f"/v1/handoffs/{handoff_id}/resolution",
        json={"action": "restore_safe_flow"},
        headers=idempotent_headers(AGENT, "handoff-safe-flow-restore-001"),
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "safe_flow_restored"
    assert (
        db.scalar(
            select(Conversation.status).where(Conversation.id == conversation["id"])
        )
        == "awaiting_customer"
    )
    cannot_restart_automation = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "请重新启动机器人"},
        headers=idempotent_headers(CUSTOMER, "handoff-safe-flow-message-001"),
    )
    assert cannot_restart_automation.status_code == 409
    events = read_sse_events(client, conversation["id"])
    workflow = [event for event in events if event["type"] == "workflow.updated"][-1]
    assert workflow["payload"] == {
        "conversation_status": "awaiting_customer",
        "workflow_status": "handoff_safe_flow_restored",
    }


def test_insufficient_identity_routes_known_conversation_without_storing_input(
    client, db, monkeypatch
):
    conversation = create_conversation(client, key="handoff-identity-create-001")
    unverified = CurrentPrincipal(
        user_id=CUSTOMER,
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="unverified",
        customer_id="customer-nw-01",
    )
    monkeypatch.setitem(
        app.dependency_overrides, get_current_principal, lambda: unverified
    )
    routed = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "请保存 unverified@example.test 的新诉求"},
        headers=idempotent_headers(CUSTOMER, "handoff-identity-message-001"),
    )
    assert routed.status_code == 202
    events = read_sse_events(client, conversation["id"])
    assert not any(
        event["type"] == "message.created"
        and event["payload"].get("actor") == "customer"
        for event in events
    )
    assert "unverified@example.test" not in json.dumps(events, ensure_ascii=False)
    handoff_event = next(
        event for event in events if event["type"] == "handoff.created"
    )
    assert handoff_event["payload"]["reason_code"] == "identity_verification_required"
    handoff = db.scalar(
        select(ConversationHandoff).where(
            ConversationHandoff.id == handoff_event["payload"]["handoff_id"]
        )
    )
    assert handoff is not None
    assert handoff.context_snapshot["verification_level"] == "unverified"
    assert (
        db.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_id == conversation["id"],
                AuditEvent.action
                == "conversation.identity_verification_handoff_created",
            )
        )
        is not None
    )


def test_repeated_generation_failures_escalate_to_an_urgent_technical_handoff(
    client, db, monkeypatch
):
    conversation = create_conversation(client, key="handoff-repeat-failure-create-001")

    def fail_generation() -> str:
        raise RuntimeError("synthetic generator outage")

    monkeypatch.setattr(service, "_generate_customer_reply", fail_generation)
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "第一次失败"},
        headers=idempotent_headers(CUSTOMER, "handoff-repeat-failure-message-001"),
    )
    second = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"text": "第二次失败"},
        headers=idempotent_headers(CUSTOMER, "handoff-repeat-failure-message-002"),
    )
    assert first.status_code == second.status_code == 202
    events = read_sse_events(client, conversation["id"])
    errors = [event for event in events if event["type"] == "error"]
    assert errors[-1]["payload"] == {
        "message_id": second.json()["message_id"],
        "code": "answer_generation_failed",
        "message": "系统连续未能完成回复，已转交人工客服处理。",
        "retryable": False,
        "allowed_actions": [],
    }
    handoff = next(event for event in events if event["type"] == "handoff.created")
    assert handoff["payload"]["reason_code"] == "system_generation_repeated_failure"
    queue_item = next(
        item
        for item in client.get("/v1/handoffs", headers=auth(SUPERVISOR)).json()
        if item["id"] == handoff["payload"]["handoff_id"]
    )
    assert queue_item["priority"] == "urgent"
    assert queue_item["target_queue"] == "technical-support"
    assert (
        db.scalar(
            select(Conversation.status).where(Conversation.id == conversation["id"])
        )
        == "handed_off"
    )


def test_message_rate_limit_is_customer_scoped_across_conversations(
    client, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_message_rate_limit", 1)
    first = create_conversation(client, key="conversation-rate-create-001")
    second = create_conversation(client, key="conversation-rate-create-002")
    sent = client.post(
        f"/v1/conversations/{first['id']}/messages",
        json={"text": "第一条"},
        headers=idempotent_headers(CUSTOMER, "conversation-rate-message-001"),
    )
    assert sent.status_code == 202
    limited = client.post(
        f"/v1/conversations/{second['id']}/messages",
        json={"text": "第二条"},
        headers=idempotent_headers(CUSTOMER, "conversation-rate-message-002"),
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(
        settings.conversation_message_rate_window_seconds
    )


def test_generation_stops_cleanly_when_handoff_wins_the_race(client, db):
    conversation = create_conversation(client, key="conversation-cancel-create-001")
    customer_message = ConversationMessage(
        id="message-cancel-race",
        conversation_id=conversation["id"],
        actor=ConversationActor.customer,
        body="等待生成",
        generation_status="queued",
        request_id="request-cancel-race",
    )
    db.add(customer_message)
    db.commit()

    bind = db.get_bind()
    assert service._claim_generation(bind, conversation["id"], customer_message.id)
    current = db.get(Conversation, conversation["id"])
    assert current is not None
    current.status = ConversationStatus.handed_off
    db.commit()

    assert (
        service._create_assistant_message(
            bind,
            conversation_id=conversation["id"],
            customer_message_id=customer_message.id,
            request_id="request-cancel-race",
            body="不应发送",
        )
        is None
    )
    db.expire_all()
    message = db.get(ConversationMessage, customer_message.id)
    assert message is not None
    assert message.generation_status == "cancelled"
    assert not service._claim_generation(bind, conversation["id"], customer_message.id)


def test_idle_sse_connection_emits_a_heartbeat_then_rotates(client, db, monkeypatch):
    conversation = create_conversation(client, key="conversation-heartbeat-create-001")
    monkeypatch.setattr(service, "SSE_CONNECTION_LIFETIME_SECONDS", 0)
    monkeypatch.setattr(service, "SSE_POLL_INTERVAL_SECONDS", 0)

    async def collect() -> list[str]:
        return [
            frame
            async for frame in service.stream_events(
                db.get_bind(),
                conversation_id=conversation["id"],
                after_sequence=conversation["last_sequence"],
                follow=True,
            )
        ]

    assert asyncio.run(collect()) == [": keep-alive\n\n"]
