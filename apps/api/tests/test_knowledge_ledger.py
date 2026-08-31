import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.knowledge.ledger import (
    KnowledgeDocument,
    KnowledgeLedgerError,
    accessible_documents,
    load_ledger,
)

pytestmark = pytest.mark.offline


def _manifest_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data/fixtures/knowledge/manifest.json"


def test_step27_ledger_has_complete_lifecycle_and_content_hashes():
    ledger = load_ledger(_manifest_path())

    assert len(ledger.documents) == 8
    assert {document.status.value for document in ledger.documents} == {
        "draft",
        "expired",
        "published",
        "withdrawn",
    }
    assert all(document.owner for document in ledger.documents)
    assert all(document.tenant_id for document in ledger.documents)
    assert all(document.product for document in ledger.documents)
    assert all(document.region for document in ledger.documents)
    assert all(document.language for document in ledger.documents)
    assert all(document.version for document in ledger.documents)
    assert all(document.content_sha256 for document in ledger.documents)


def test_access_is_deterministic_without_model_or_database():
    ledger = load_ledger(_manifest_path())

    northwind_customer = accessible_documents(
        ledger, tenant_id="tenant-northwind-demo", role="customer"
    )
    assert {document.id for document in northwind_customer} == {
        "kc-nw-public-faq-v1",
        "kc-nw-returns-cn-v2",
        "kc-nw-untrusted-instruction-v1",
    }
    assert all(
        document.tenant_id == "tenant-northwind-demo" for document in northwind_customer
    )
    assert "kc-nw-returns-cn-v1" not in {document.id for document in northwind_customer}
    assert "kc-nw-draft-faq-v2" not in {document.id for document in northwind_customer}

    northwind_agent = accessible_documents(
        ledger, tenant_id="tenant-northwind-demo", role="agent"
    )
    assert "kc-ct-agent-escalation-v1" not in {
        document.id for document in northwind_agent
    }

    contoso_customer = accessible_documents(
        ledger, tenant_id="tenant-contoso-demo", role="customer"
    )
    assert {document.id for document in contoso_customer} == {
        "kc-ct-other-tenant-policy-v1"
    }

    contoso_agent = accessible_documents(
        ledger, tenant_id="tenant-contoso-demo", role="agent"
    )
    assert {document.id for document in contoso_agent} == {
        "kc-ct-agent-escalation-v1",
        "kc-ct-other-tenant-policy-v1",
    }


def test_hash_tampering_is_rejected(tmp_path):
    source_dir = _manifest_path().parent
    target_dir = tmp_path / "knowledge"
    target_dir.mkdir()
    for path in source_dir.glob("*.md"):
        (target_dir / path.name).write_bytes(path.read_bytes())
    manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
    manifest["documents"][0]["content_sha256"] = "0" * 64
    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(KnowledgeLedgerError, match="content hash mismatch"):
        load_ledger(manifest_path)


def test_metadata_rejects_naive_timestamps_and_path_escape():
    with pytest.raises(ValidationError):
        KnowledgeDocument.model_validate(
            {
                "id": "doc",
                "path": "../secret.md",
                "document_key": "doc",
                "owner": "owner",
                "tenant_id": "tenant",
                "audience": "public",
                "product": "product",
                "region": "all",
                "language": "zh-CN",
                "version": "1.0.0",
                "effective_at": datetime(2026, 1, 1),
                "status": "draft",
                "content_sha256": "0" * 64,
            }
        )


def test_lifecycle_rejects_invalid_publish_windows_and_naive_access_time():
    ledger = load_ledger(_manifest_path())
    published = next(
        document
        for document in ledger.documents
        if document.status.value == "published"
    )
    from app.modules.knowledge.ledger import (
        KnowledgeLedgerError,
        KnowledgeStatus,
        validate_document_lifecycle,
    )

    with pytest.raises(KnowledgeLedgerError, match="include a timezone"):
        validate_document_lifecycle(published, as_of=datetime(2026, 1, 1))
    with pytest.raises(KnowledgeLedgerError, match="not effective"):
        validate_document_lifecycle(published, as_of=datetime(2025, 1, 1, tzinfo=UTC))

    expired = published.model_copy(
        update={
            "id": "expired-check",
            "status": KnowledgeStatus.EXPIRED,
            "expires_at": datetime(2026, 6, 1, tzinfo=UTC),
        }
    )
    with pytest.raises(KnowledgeLedgerError, match="expiry at or before"):
        validate_document_lifecycle(expired, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(KnowledgeLedgerError, match="access check time"):
        accessible_documents(
            ledger,
            tenant_id="tenant-northwind-demo",
            role="customer",
            at=datetime(2026, 1, 1),
        )
