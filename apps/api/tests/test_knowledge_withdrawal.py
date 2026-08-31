from conftest import auth
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeImport,
    KnowledgeImportStatus,
)
from app.modules.knowledge.worker import process_pending_imports


def _import_payload(**overrides):
    payload = {
        "filename": "withdrawal-policy.md",
        "title": "Withdrawal policy",
        "content": "撤回演练政策：客户可在 30 天内申请退款。",
        "document_key": "withdrawal-policy",
        "owner": "northwind-demo-knowledge-operations",
        "audience": "public",
        "product": "demo-support",
        "region": "cn-north",
        "language": "zh-CN",
        "version": "1.0.0",
        "effective_at": "2025-01-01T00:00:00Z",
        "requested_status": "published",
    }
    payload.update(overrides)
    return payload


def _withdraw_payload(**overrides):
    payload = {
        "document_key": "withdrawal-policy",
        "version": "1.0.0",
        "product": "demo-support",
        "region": "cn-north",
        "language": "zh-CN",
        "reason": "Published policy contains a verified error",
    }
    payload.update(overrides)
    return payload


def _publish(client, db, **overrides):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_import_payload(**overrides),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 202
    processed = process_pending_imports(db, limit=10)
    assert len(processed) == 1
    item = db.get(KnowledgeImport, response.json()["id"])
    assert item is not None
    assert item.status is KnowledgeImportStatus.published
    return item


def _answer(client, *, user_id: str = "user-nw-agent-01"):
    return client.post(
        "/v1/knowledge/answer",
        json={
            "query": "撤回演练政策",
            "product": "demo-support",
            "region": "cn-north",
            "language": "zh-CN",
        },
        headers=auth(user_id),
    )


def test_withdrawal_is_audited_idempotent_and_immediately_stops_answers(client, db):
    item = _publish(client, db)
    before = _answer(client)
    assert before.status_code == 200
    assert before.json()["needs_handoff"] is False
    assert len(before.json()["citations"]) == 1

    withdrawn = client.post(
        "/v1/admin/knowledge/documents/withdrawals",
        json=_withdraw_payload(),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert withdrawn.status_code == 200
    payload = withdrawn.json()
    assert payload["status"] == "withdrawn"
    assert payload["already_withdrawn"] is False
    assert payload["audit_event_id"].startswith("audit-")

    after = _answer(client)
    assert after.status_code == 200
    assert after.json()["needs_handoff"] is True
    assert after.json()["citations"] == []

    document = db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.import_id == item.id)
    )
    assert document is not None
    assert document.status is KnowledgeDocumentStatus.withdrawn
    chunks = db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    ).all()
    assert chunks
    assert all(chunk.status is KnowledgeDocumentStatus.withdrawn for chunk in chunks)
    assert all(chunk.chunk_metadata["status"] == "withdrawn" for chunk in chunks)
    # The original import record is immutable release evidence, not rewritten.
    assert item.requested_status is KnowledgeDocumentStatus.published

    audit = db.get(AuditEvent, payload["audit_event_id"])
    assert audit is not None
    assert audit.action == "knowledge.document_withdrawn"
    assert audit.resource_id == document.id
    assert audit.request_id != "-"
    assert '"status":"published"' in (audit.before_summary or "")
    assert '"status":"withdrawn"' in (audit.after_summary or "")

    repeated = client.post(
        "/v1/admin/knowledge/documents/withdrawals",
        json=_withdraw_payload(),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert repeated.status_code == 200
    assert repeated.json()["already_withdrawn"] is True
    assert repeated.json()["audit_event_id"] == payload["audit_event_id"]
    assert (
        db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "knowledge.document_withdrawn",
                AuditEvent.resource_id == document.id,
            )
        )
        is audit
    )


def test_withdrawal_requires_operator_and_hides_cross_tenant_document(client, db):
    _publish(client, db)

    denied = client.post(
        "/v1/admin/knowledge/documents/withdrawals",
        json=_withdraw_payload(),
        headers=auth("user-nw-agent-01"),
    )
    assert denied.status_code == 403

    cross_tenant = client.post(
        "/v1/admin/knowledge/documents/withdrawals",
        json=_withdraw_payload(),
        headers=auth("user-ct-knowledge-operator-01"),
    )
    assert cross_tenant.status_code == 404


def test_only_published_document_can_be_withdrawn(client, db):
    _publish(
        client,
        db,
        document_key="review-policy",
        version="2.0.0",
        requested_status="review",
    )

    response = client.post(
        "/v1/admin/knowledge/documents/withdrawals",
        json=_withdraw_payload(document_key="review-policy", version="2.0.0"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 409
