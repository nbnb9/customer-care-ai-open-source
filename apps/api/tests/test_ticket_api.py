from concurrent.futures import ThreadPoolExecutor

from conftest import auth, create_headers
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.tickets import IdempotencyRecord, Ticket, TicketEvent, TicketEventType


def test_create_and_list_ticket(client):
    res = client.post(
        "/v1/tickets",
        json={"subject": "测试工单", "description": "desc", "priority": "normal"},
        headers=create_headers("user-nw-customer-01", "ticket-create-test-001"),
    )
    assert res.status_code == 201
    ticket = res.json()
    assert ticket["status"] == "new"
    assert ticket["tenant_id"] == "tenant-northwind-demo"

    res = client.get("/v1/tickets", headers=auth("user-nw-customer-01"))
    ids = [t["id"] for t in res.json()]
    assert ticket["id"] in ids


def test_cross_tenant_read_returns_404(client):
    res = client.get("/v1/tickets/ticket-nw-open-01", headers=auth("user-ct-agent-01"))
    assert res.status_code == 404


def test_cross_tenant_list_isolation(client):
    res = client.get("/v1/tickets", headers=auth("user-ct-agent-01"))
    tenants = {t["tenant_id"] for t in res.json()}
    assert tenants == {"tenant-contoso-demo"}


def test_illegal_state_transition_rejected(client):
    res = client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "resolved"},
        headers=auth("user-nw-agent-01"),
    )
    assert res.status_code == 409


def test_legal_state_transition(client):
    res = client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "triaged"},
        headers=auth("user-nw-agent-01"),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "triaged"


def test_customer_cannot_update(client):
    res = client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "triaged"},
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 404


def test_customer_cannot_assign(client):
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-customer-01"},
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 404


def test_customer_cannot_create_internal_note(client):
    res = client.post(
        "/v1/tickets/ticket-nw-open-01/comments",
        json={"body": "试图写内部备注", "is_internal": True},
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 403


def test_agent_can_only_claim_triaged_ticket_for_self(client):
    transitioned = client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "triaged"},
        headers=auth("user-nw-agent-01"),
    )
    assert transitioned.status_code == 200

    other = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-agent-02"},
        headers=auth("user-nw-agent-01"),
    )
    assert other.status_code == 403

    claimed = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-agent-01"},
        headers=auth("user-nw-agent-01"),
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "assigned"
    assert claimed.json()["assignee_id"] == "user-nw-agent-01"


def test_supervisor_reassignment_validates_tenant_and_role(client):
    client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "triaged"},
        headers=auth("user-nw-supervisor-01"),
    )

    cross_tenant = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-ct-agent-01"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert cross_tenant.status_code == 404

    customer = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-customer-01"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert customer.status_code == 422

    supervisor = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-supervisor-01"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert supervisor.status_code == 422

    assigned = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-agent-01"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert assigned.status_code == 200
    reassigned = client.post(
        "/v1/tickets/ticket-nw-open-01/assignments",
        json={"assignee_id": "user-nw-agent-02"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["assignee_id"] == "user-nw-agent-02"


def test_closed_ticket_cannot_be_assigned(client):
    res = client.post(
        "/v1/tickets/ticket-nw-closed-01/assignments",
        json={"assignee_id": "user-nw-agent-01"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert res.status_code == 409


def test_internal_note_filtered_for_customer(client):
    client.post(
        "/v1/tickets/ticket-nw-open-01/comments",
        json={"body": "内部排查", "is_internal": True},
        headers=auth("user-nw-agent-01"),
    )
    client.post(
        "/v1/tickets/ticket-nw-open-01/comments",
        json={"body": "已回复", "is_internal": False},
        headers=auth("user-nw-agent-01"),
    )
    customer_types = [
        e["event_type"]
        for e in client.get(
            "/v1/tickets/ticket-nw-open-01/events", headers=auth("user-nw-customer-01")
        ).json()
    ]
    agent_types = [
        e["event_type"]
        for e in client.get(
            "/v1/tickets/ticket-nw-open-01/events", headers=auth("user-nw-agent-01")
        ).json()
    ]
    assert "internal_note" not in customer_types
    assert "comment_added" in customer_types
    assert "internal_note" in agent_types


def test_validation_error_unified_format(client):
    res = client.post(
        "/v1/tickets",
        json={},
        headers=create_headers("user-nw-customer-01", "invalid-create-001"),
    )
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "request_id" in body


def test_create_requires_idempotency_key(client):
    res = client.post(
        "/v1/tickets",
        json={"subject": "缺少幂等键"},
        headers=auth("user-nw-customer-01"),
    )
    assert res.status_code == 422


def test_idempotent_replay_has_one_ticket_and_one_side_effect_set(client, db):
    headers = create_headers("user-nw-customer-01", "idempotent-create-001")
    payload = {
        "order_id": "order-nw-1001",
        "subject": "重复提交测试",
        "description": "完全相同的请求",
    }
    first = client.post("/v1/tickets", json=payload, headers=headers)
    second = client.post("/v1/tickets", json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    ticket_id = first.json()["id"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.ticket_id == ticket_id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(TicketEvent)
            .where(
                TicketEvent.ticket_id == ticket_id,
                TicketEvent.event_type == TicketEventType.created,
            )
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.resource_id == ticket_id,
                AuditEvent.action == "ticket.created",
            )
        )
        == 1
    )


def test_same_idempotency_scope_with_different_body_conflicts(client):
    headers = create_headers("user-nw-customer-01", "idempotent-conflict-001")
    first = client.post("/v1/tickets", json={"subject": "第一个请求"}, headers=headers)
    second = client.post("/v1/tickets", json={"subject": "不同请求"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409


def test_idempotent_replay_returns_original_response_after_ticket_changes(client):
    headers = create_headers("user-nw-customer-01", "stable-response-create-001")
    first = client.post(
        "/v1/tickets",
        json={"subject": "响应快照", "priority": "normal"},
        headers=headers,
    )
    assert first.status_code == 201

    changed = client.patch(
        f"/v1/tickets/{first.json()['id']}",
        json={"priority": "urgent"},
        headers=auth("user-nw-supervisor-01"),
    )
    assert changed.status_code == 200
    assert changed.json()["priority"] == "urgent"

    replay = client.post(
        "/v1/tickets",
        json={"subject": "响应快照", "priority": "normal"},
        headers=headers,
    )
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.json()["priority"] == "normal"


def test_concurrent_idempotent_requests_return_one_ticket(client, db):
    headers = create_headers("user-nw-customer-01", "concurrent-create-001")
    payload = {"subject": "并发幂等请求", "description": "只能创建一张工单"}

    def create():
        return client.post("/v1/tickets", json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(), range(2)))

    assert [response.status_code for response in responses] == [201, 201]
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    ticket_id = ids.pop()
    assert (
        db.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.ticket_id == ticket_id)
        )
        == 1
    )


def test_idempotency_key_is_scoped_by_principal(client):
    key = "shared-but-scoped-key-001"
    first = client.post(
        "/v1/tickets",
        json={"subject": "客户一"},
        headers=create_headers("user-nw-customer-01", key),
    )
    second = client.post(
        "/v1/tickets",
        json={"subject": "客户二"},
        headers=create_headers("user-nw-customer-02", key),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_non_empty_and_length_validation(client):
    empty_subject = client.post(
        "/v1/tickets",
        json={"subject": "   "},
        headers=create_headers("user-nw-customer-01", "empty-subject-001"),
    )
    assert empty_subject.status_code == 422

    empty_comment = client.post(
        "/v1/tickets/ticket-nw-open-01/comments",
        json={"body": "   "},
        headers=auth("user-nw-customer-01"),
    )
    assert empty_comment.status_code == 422

    too_long_subject = client.post(
        "/v1/tickets",
        json={"subject": "x" * 201},
        headers=create_headers("user-nw-customer-01", "long-subject-001"),
    )
    assert too_long_subject.status_code == 422


def test_status_update_sets_closed_at_and_traceable_audit(client, db):
    ticket = db.get(Ticket, "ticket-nw-open-01")
    assert ticket is not None
    ticket.status = "resolved"
    ticket.closed_at = None
    db.commit()

    res = client.patch(
        "/v1/tickets/ticket-nw-open-01",
        json={"status": "closed"},
        headers=auth("user-nw-agent-01"),
    )
    assert res.status_code == 200
    assert res.json()["closed_at"] is not None

    audit = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.resource_id == "ticket-nw-open-01",
            AuditEvent.action == "ticket.updated",
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert audit is not None
    assert audit.resource_type == "ticket"
    assert '"status":"resolved"' in (audit.before_summary or "")
    assert '"status":"closed"' in (audit.after_summary or "")


def test_concurrent_status_updates_create_only_one_transition(client, db):
    def transition():
        return client.patch(
            "/v1/tickets/ticket-nw-open-01",
            json={"status": "triaged"},
            headers=auth("user-nw-agent-01"),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: transition(), range(2)))

    assert statuses == [200, 409]
    assert (
        db.scalar(
            select(func.count())
            .select_from(TicketEvent)
            .where(
                TicketEvent.ticket_id == "ticket-nw-open-01",
                TicketEvent.event_type == TicketEventType.status_changed,
                TicketEvent.payload_digest == "triaged",
            )
        )
        == 1
    )
