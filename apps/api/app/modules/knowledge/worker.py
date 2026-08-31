"""Durable knowledge import worker and deterministic local embedding provider."""

from __future__ import annotations

import argparse
import hashlib
import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocumentStatus,
    KnowledgeImport,
    KnowledgeImportStatus,
)
from app.models.knowledge import (
    KnowledgeDocument as DBKnowledgeDocument,
)
from app.modules.knowledge.ledger import (
    KnowledgeAudience,
    KnowledgeDocument,
    KnowledgeLedgerError,
    KnowledgeStatus,
    validate_document_lifecycle,
)
from app.shared.config import get_settings
from app.shared.observability import (
    bind_knowledge_versions,
    bind_principal,
    observe_background_task,
    set_span_attributes,
)

MAX_ATTEMPTS: Final = 3
MAX_CHUNK_CHARS: Final = 480
CHUNK_OVERLAP_CHARS: Final = 60
EMBEDDING_DIMENSIONS: Final = 32
EMBEDDING_MODEL: Final = "hash-v1"
STALE_PROCESSING_AFTER: Final = timedelta(minutes=5)


class KnowledgeWorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def process_pending_imports(db: Session, *, limit: int = 10) -> list[KnowledgeImport]:
    """Claim and process queued/retryable imports; safe to call after restart."""

    if limit < 1:
        return []
    recover_stale_imports(db)
    processed: list[KnowledgeImport] = []
    for _ in range(limit):
        item = _claim_next(db)
        if item is None:
            break
        _process_claimed(db, item)
        processed.append(item)
    return processed


def recover_stale_imports(db: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    cutoff = current - STALE_PROCESSING_AFTER
    stale = db.scalars(
        select(KnowledgeImport).where(
            KnowledgeImport.status == KnowledgeImportStatus.processing,
            KnowledgeImport.started_at < cutoff,
        )
    ).all()
    for item in stale:
        item.status = KnowledgeImportStatus.failed
        item.error_code = "WORKER_INTERRUPTED"
        item.error_message = "worker lease expired; queued for retry"
        item.finished_at = current
    if stale:
        db.commit()
    return len(stale)


def _claim_next(db: Session) -> KnowledgeImport | None:
    stmt = (
        select(KnowledgeImport)
        .where(
            KnowledgeImport.attempts < MAX_ATTEMPTS,
            KnowledgeImport.status.in_(
                [KnowledgeImportStatus.queued, KnowledgeImportStatus.failed]
            ),
        )
        .order_by(KnowledgeImport.created_at, KnowledgeImport.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    item = db.scalars(stmt).first()
    if item is None:
        return None
    item.status = KnowledgeImportStatus.processing
    item.attempts += 1
    item.started_at = datetime.now(UTC)
    item.finished_at = None
    item.error_code = None
    item.error_message = None
    db.commit()
    return item


def _process_claimed(db: Session, item: KnowledgeImport) -> None:
    with observe_background_task(
        "knowledge.import_worker",
        traceparent=item.observability_traceparent,
        request_id=f"knowledge-worker-{uuid.uuid4().hex}",
        attributes={"customer_care.worker.name": "knowledge-worker"},
    ):
        bind_principal(tenant_id=item.tenant_id, role="knowledge_worker")
        bind_knowledge_versions([item.version])
        _process_claimed_inner(db, item)
        set_span_attributes(
            {"customer_care.knowledge.import_status": item.status.value}
        )


def _process_claimed_inner(db: Session, item: KnowledgeImport) -> None:
    try:
        document = _build_document(item)
        _ensure_version_available(db, item, document)
        actual_sha256 = hashlib.sha256(item.source_content.encode()).hexdigest()
        if actual_sha256 != item.source_sha256:
            raise KnowledgeWorkerError(
                "CONTENT_HASH_MISMATCH",
                "staged content does not match the import fingerprint",
            )
        settings = get_settings()
        chunks = chunk_text(
            item.source_content,
            max_chars=settings.knowledge_chunk_max_chars,
            overlap_chars=settings.knowledge_chunk_overlap_chars,
        )
        if not chunks:
            raise KnowledgeWorkerError("EMPTY_CONTENT", "document has no text chunks")

        db_document = DBKnowledgeDocument(
            id=f"knowledge-document-{item.id.removeprefix('knowledge-import-')}",
            import_id=item.id,
            tenant_id=item.tenant_id,
            document_key=item.document_key,
            title=item.title,
            owner=item.owner,
            audience=item.audience,
            product=item.product,
            region=item.region,
            language=item.language,
            version=item.version,
            effective_at=item.effective_at,
            expires_at=item.expires_at,
            status=KnowledgeDocumentStatus(item.requested_status.value),
            untrusted_content=item.untrusted_content,
            content_sha256=item.source_sha256,
            published_at=(
                datetime.now(UTC)
                if document.status is KnowledgeStatus.PUBLISHED
                else None
            ),
        )
        db.add(db_document)
        db.flush()
        for index, text in enumerate(chunks):
            db.add(
                KnowledgeChunk(
                    id=f"knowledge-chunk-{uuid.uuid4().hex}",
                    document_id=db_document.id,
                    tenant_id=item.tenant_id,
                    document_key=item.document_key,
                    document_version=item.version,
                    audience=item.audience,
                    product=item.product,
                    region=item.region,
                    language=item.language,
                    effective_at=item.effective_at,
                    expires_at=item.expires_at,
                    status=KnowledgeDocumentStatus(item.requested_status.value),
                    untrusted_content=item.untrusted_content,
                    chunk_index=index,
                    text=text,
                    embedding=hash_embedding(text),
                    chunk_metadata={
                        "document_id": db_document.id,
                        "document_key": item.document_key,
                        "document_version": item.version,
                        "title": item.title,
                        "owner": item.owner,
                        "tenant_id": item.tenant_id,
                        "audience": item.audience,
                        "product": item.product,
                        "region": item.region,
                        "language": item.language,
                        "effective_at": item.effective_at.isoformat(),
                        "expires_at": (
                            item.expires_at.isoformat()
                            if item.expires_at is not None
                            else None
                        ),
                        "status": item.requested_status.value,
                        "untrusted_content": item.untrusted_content,
                        "embedding_model": EMBEDDING_MODEL,
                        "embedding_dimensions": EMBEDDING_DIMENSIONS,
                        "chunker": "paragraph-v1",
                        "chunk_max_chars": settings.knowledge_chunk_max_chars,
                        "chunk_overlap_chars": settings.knowledge_chunk_overlap_chars,
                    },
                )
            )
        item.status = KnowledgeImportStatus.published
        item.finished_at = datetime.now(UTC)
        db.commit()
    except KnowledgeWorkerError as exc:
        _mark_failed(db, item, exc.code, str(exc))
    except (KnowledgeLedgerError, ValueError) as exc:
        _mark_failed(db, item, "INVALID_METADATA", str(exc))
    except IntegrityError as exc:
        _mark_failed(db, item, "DUPLICATE_DOCUMENT_VERSION", str(exc))
    except Exception as exc:
        db.rollback()
        _mark_failed(db, item, "PROCESSING_ERROR", str(exc)[:500])


def _build_document(item: KnowledgeImport) -> KnowledgeDocument:
    try:
        document = KnowledgeDocument(
            id=f"import-{item.id}",
            path=item.filename,
            document_key=item.document_key,
            owner=item.owner,
            tenant_id=item.tenant_id,
            audience=cast(KnowledgeAudience, item.audience),
            product=item.product,
            region=item.region,
            language=item.language,
            version=item.version,
            effective_at=item.effective_at,
            expires_at=item.expires_at,
            status=KnowledgeStatus(item.requested_status.value),
            content_sha256=item.source_sha256,
        )
        validate_document_lifecycle(document, as_of=datetime.now(UTC))
        return document
    except KnowledgeLedgerError:
        raise
    except ValueError as exc:
        raise KnowledgeWorkerError("INVALID_METADATA", str(exc)) from exc


def _ensure_version_available(
    db: Session, item: KnowledgeImport, document: KnowledgeDocument
) -> None:
    existing = db.scalars(
        select(DBKnowledgeDocument).where(
            DBKnowledgeDocument.tenant_id == item.tenant_id,
            DBKnowledgeDocument.document_key == item.document_key,
            DBKnowledgeDocument.product == item.product,
            DBKnowledgeDocument.region == item.region,
            DBKnowledgeDocument.language == item.language,
            DBKnowledgeDocument.version == item.version,
        )
    ).first()
    if existing is not None:
        raise KnowledgeWorkerError(
            "DUPLICATE_DOCUMENT_VERSION",
            "document version already exists; create a new version instead "
            "of duplicating it",
        )
    if document.status is not KnowledgeStatus.PUBLISHED:
        return
    conflict = db.scalars(
        select(DBKnowledgeDocument).where(
            DBKnowledgeDocument.tenant_id == item.tenant_id,
            DBKnowledgeDocument.document_key == item.document_key,
            DBKnowledgeDocument.product == item.product,
            DBKnowledgeDocument.region == item.region,
            DBKnowledgeDocument.language == item.language,
            DBKnowledgeDocument.status == KnowledgeDocumentStatus.published,
        )
    ).first()
    if conflict is not None:
        raise KnowledgeWorkerError(
            "PUBLISHED_SCOPE_CONFLICT",
            "withdraw or expire the existing published version before "
            "publishing a new one",
        )


def _mark_failed(db: Session, item: KnowledgeImport, code: str, message: str) -> None:
    item_id = item.id
    db.rollback()
    failed_item = db.get(KnowledgeImport, item_id)
    if failed_item is None:
        return
    failed_item.status = KnowledgeImportStatus.failed
    failed_item.error_code = code
    failed_item.error_message = message[:500]
    failed_item.finished_at = datetime.now(UTC)
    db.commit()


def chunk_text(
    content: str,
    *,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Paragraph-aware deterministic chunker with bounded overlap."""

    if max_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("invalid chunking parameters")
    paragraphs = [part.strip() for part in content.replace("\r\n", "\n").split("\n\n")]
    paragraphs = [part for part in paragraphs if part]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        start = 0
        while start < len(paragraph):
            end = min(start + max_chars, len(paragraph))
            chunks.append(paragraph[start:end])
            if end == len(paragraph):
                current = ""
                break
            start = end - overlap_chars
    if current:
        chunks.append(current)
    return chunks


def hash_embedding(text: str) -> list[float]:
    """Deterministic offline vector; not a semantic production embedding."""

    digest = hashlib.sha256(f"{EMBEDDING_MODEL}\0{text}".encode()).digest()
    values = [(byte / 127.5) - 1.0 for byte in digest[:EMBEDDING_DIMENSIONS]]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process CustomerCare knowledge imports"
    )
    parser.add_argument(
        "--once", action="store_true", help="process one batch and exit"
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.poll_seconds < 0:
        parser.error("limit must be positive and poll-seconds must be non-negative")

    while True:
        with SessionLocal() as db:
            processed = process_pending_imports(db, limit=args.limit)
            if processed:
                print("processed", " ".join(item.id for item in processed))
        if args.once:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
