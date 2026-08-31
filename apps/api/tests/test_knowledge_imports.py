from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import auth
from sqlalchemy import func, select, update

from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeImport,
    KnowledgeImportStatus,
)
from app.modules.knowledge import worker
from app.modules.knowledge.worker import process_pending_imports


def _payload(**overrides):
    payload = {
        "filename": "returns.md",
        "content": "# Returns\n\nThe current demo policy is valid for 30 days.",
        "document_key": "returns-policy-cn",
        "owner": "northwind-demo-knowledge-operations",
        "audience": "public",
        "product": "demo-support",
        "region": "cn-north",
        "language": "zh-CN",
        "version": "2.0.0",
        "effective_at": "2026-01-01T00:00:00Z",
        "requested_status": "published",
    }
    payload.update(overrides)
    return payload


def test_only_knowledge_operator_can_queue_import(client):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(),
        headers=auth("user-nw-agent-01"),
    )

    assert response.status_code == 403


def test_import_is_deduplicated_and_worker_publishes_pgvector_chunks(client, db):
    first = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    second = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(),
        headers=auth("user-nw-knowledge-operator-01"),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["status"] == "queued"
    assert first_payload["title"] == "returns"
    assert first_payload["deduplicated"] is False
    assert second_payload["id"] == first_payload["id"]
    assert second_payload["deduplicated"] is True
    assert db.scalar(select(func.count()).select_from(KnowledgeImport)) == 1

    processed = process_pending_imports(db, limit=1)
    assert [item.id for item in processed] == [first_payload["id"]]

    db.expire_all()
    status = client.get(
        f"/v1/admin/knowledge/imports/{first_payload['id']}",
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert status.status_code == 200
    assert status.json()["status"] == "published"

    document = db.scalar(select(KnowledgeDocument))
    assert document is not None
    assert document.status is KnowledgeDocumentStatus.published
    assert document.title == "returns"
    assert document.untrusted_content is False
    chunks = db.scalars(select(KnowledgeChunk)).all()
    assert len(chunks) == 1
    assert len(chunks[0].embedding) == 32
    assert chunks[0].chunk_metadata["embedding_model"] == "hash-v1"
    assert chunks[0].chunk_metadata["title"] == "returns"
    assert chunks[0].chunk_metadata["untrusted_content"] is False


def test_import_failure_is_visible_and_retryable_until_attempt_limit(client, db):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(content="   ", version="3.0.0"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 202
    import_id = response.json()["id"]

    for _ in range(3):
        process_pending_imports(db, limit=1)

    db.expire_all()
    item = db.get(KnowledgeImport, import_id)
    assert item is not None
    assert item.status is KnowledgeImportStatus.failed
    assert item.error_code == "EMPTY_CONTENT"
    assert item.attempts == 3
    assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0


def test_worker_marks_future_published_document_invalid(client, db):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(
            version="3.1.0",
            effective_at="2099-01-01T00:00:00Z",
            requested_status="published",
        ),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 202
    process_pending_imports(db, limit=1)
    item = db.get(KnowledgeImport, response.json()["id"])
    assert item is not None
    assert item.error_code == "INVALID_METADATA"


def test_import_status_is_tenant_scoped(client, db):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="4.0.0"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    assert response.status_code == 202
    import_id = response.json()["id"]

    cross_tenant = client.get(
        f"/v1/admin/knowledge/imports/{import_id}",
        headers=auth("user-ct-knowledge-operator-01"),
    )
    assert cross_tenant.status_code == 404


def test_worker_chunking_and_embedding_are_deterministic():
    content = "第一段。\n\n第二段。"
    assert worker.chunk_text(content) == [content]
    assert worker.hash_embedding(content) == worker.hash_embedding(content)
    assert len(worker.hash_embedding(content)) == 32
    with pytest.raises(ValueError, match="invalid chunking"):
        worker.chunk_text(content, max_chars=0)
    chunks = worker.chunk_text("abcdefghij", max_chars=4, overlap_chars=1)
    assert chunks == ["abcd", "defg", "ghij"]


def test_stale_processing_is_recovered_and_retried(client, db):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="5.0.0", requested_status="review"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    import_id = response.json()["id"]
    stale_at = datetime.now(UTC) - timedelta(minutes=6)
    db.execute(
        update(KnowledgeImport)
        .where(KnowledgeImport.id == import_id)
        .values(status=KnowledgeImportStatus.processing, started_at=stale_at)
    )
    db.commit()

    assert worker.recover_stale_imports(db) == 1
    item = db.get(KnowledgeImport, import_id)
    assert item is not None
    assert item.error_code == "WORKER_INTERRUPTED"
    process_pending_imports(db, limit=10)
    db.expire_all()
    assert db.get(KnowledgeImport, import_id).status is KnowledgeImportStatus.published


def test_worker_rejects_tampered_staged_content(client, db):
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="6.0.0"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    import_id = response.json()["id"]
    db.execute(
        update(KnowledgeImport)
        .where(KnowledgeImport.id == import_id)
        .values(source_content="tampered")
    )
    db.commit()

    process_pending_imports(db, limit=10)
    db.expire_all()
    item = db.get(KnowledgeImport, import_id)
    assert item is not None
    assert item.error_code == "CONTENT_HASH_MISMATCH"


def test_worker_rejects_duplicate_versions_and_published_scope_conflicts(client, db):
    first = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="7.0.0", requested_status="review"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    process_pending_imports(db, limit=10)
    duplicate = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="7.0.0", content="different"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    process_pending_imports(db, limit=10)
    duplicate_item = db.get(KnowledgeImport, duplicate.json()["id"])
    assert duplicate_item is not None
    assert duplicate_item.error_code == "DUPLICATE_DOCUMENT_VERSION"
    assert first.json()["id"] != duplicate.json()["id"]

    published = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="8.0.0", requested_status="published"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    process_pending_imports(db, limit=10)
    conflict = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(version="9.0.0", requested_status="published"),
        headers=auth("user-nw-knowledge-operator-01"),
    )
    process_pending_imports(db, limit=10)
    assert (
        db.get(KnowledgeImport, published.json()["id"]).status
        is KnowledgeImportStatus.published
    )
    assert (
        db.get(KnowledgeImport, conflict.json()["id"]).error_code
        == "PUBLISHED_SCOPE_CONFLICT"
    )


def test_import_rejects_unsafe_filename_and_naive_timestamps(client):
    operator = auth("user-nw-knowledge-operator-01")
    unsafe = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(filename="../secret.md", version="10.0.0"),
        headers=operator,
    )
    unsupported = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(filename="policy.pdf", version="11.0.0"),
        headers=operator,
    )
    naive = client.post(
        "/v1/admin/knowledge/imports",
        json=_payload(effective_at="2026-01-01T00:00:00", version="12.0.0"),
        headers=operator,
    )
    assert unsafe.status_code == 422
    assert unsupported.status_code == 422
    assert naive.status_code == 422


def test_worker_cli_can_run_once_without_work(monkeypatch):
    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(worker, "process_pending_imports", lambda db, limit: [])
    assert worker.main(["--once"]) == 0
    with pytest.raises(SystemExit):
        worker.main(["--once", "--limit", "0"])


def test_worker_cli_waits_after_processing(monkeypatch):
    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    calls = iter([[SimpleNamespace(id="import-1")], []])
    sleeps: list[float] = []

    class StopPolling(RuntimeError):
        pass

    def stop_after_empty_queue(seconds: float):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopPolling

    monkeypatch.setattr(worker, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        worker, "process_pending_imports", lambda db, limit: next(calls)
    )
    monkeypatch.setattr(worker.time, "sleep", stop_after_empty_queue)
    with pytest.raises(StopPolling):
        worker.main(["--poll-seconds", "0"])
    assert sleeps == [0, 0]
