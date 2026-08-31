import hashlib
import json
import uuid
from pathlib import PurePath

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.identity import UserRole
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeImport,
)
from app.modules.identity.principal import CurrentPrincipal
from app.shared.middleware import request_id_var
from app.shared.observability import current_traceparent
from app.shared.security import contains_prompt_injection

from .schemas import KnowledgeDocumentWithdrawalCreate, KnowledgeImportCreate

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt"}


def create_import(
    db: Session, principal: CurrentPrincipal, data: KnowledgeImportCreate
) -> tuple[KnowledgeImport, bool]:
    _require_knowledge_operator(principal)
    filename = _safe_filename(data.filename)
    source_sha256 = hashlib.sha256(data.content.encode("utf-8")).hexdigest()

    existing = db.scalars(
        select(KnowledgeImport).where(
            KnowledgeImport.tenant_id == principal.tenant_id,
            KnowledgeImport.document_key == data.document_key,
            KnowledgeImport.version == data.version,
            KnowledgeImport.source_sha256 == source_sha256,
        )
    ).first()
    if existing is not None:
        return existing, True

    item = KnowledgeImport(
        id=f"knowledge-import-{uuid.uuid4().hex}",
        tenant_id=principal.tenant_id,
        requested_by=principal.user_id,
        filename=filename,
        title=_display_title(data.title, filename),
        document_key=data.document_key,
        owner=data.owner,
        audience=data.audience,
        product=data.product,
        region=data.region,
        language=data.language,
        version=data.version,
        effective_at=data.effective_at,
        expires_at=data.expires_at,
        requested_status=data.requested_status,
        # A publisher-controlled boolean is useful metadata but cannot be the
        # only safety boundary.  Explicit instruction-override content is
        # automatically tainted so it cannot become model evidence.
        untrusted_content=(
            data.untrusted_content or contains_prompt_injection(data.content)
        ),
        source_sha256=source_sha256,
        source_content=data.content,
        observability_traceparent=current_traceparent(),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # 两个运营请求可能同时通过上面的读检查；唯一约束是最终裁判。
        db.rollback()
        existing = db.scalars(
            select(KnowledgeImport).where(
                KnowledgeImport.tenant_id == principal.tenant_id,
                KnowledgeImport.document_key == data.document_key,
                KnowledgeImport.version == data.version,
                KnowledgeImport.source_sha256 == source_sha256,
            )
        ).first()
        if existing is not None:
            return existing, True
        raise
    db.refresh(item)
    return item, False


def get_import(
    db: Session, principal: CurrentPrincipal, import_id: str
) -> KnowledgeImport:
    _require_knowledge_operator(principal)
    item = db.get(KnowledgeImport, import_id)
    if item is None or item.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge import not found")
    return item


def withdraw_document(
    db: Session,
    principal: CurrentPrincipal,
    data: KnowledgeDocumentWithdrawalCreate,
) -> tuple[KnowledgeDocument, AuditEvent | None, bool]:
    """Withdraw one exact published version and its retrieval copies atomically.

    The source import remains immutable so it continues to establish who
    published which bytes.  A withdrawal changes only the authoritative
    document/chunk lifecycle state and emits an audit event.
    """

    _require_knowledge_operator(principal)
    document = db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.tenant_id == principal.tenant_id,
            KnowledgeDocument.document_key == data.document_key,
            KnowledgeDocument.version == data.version,
            KnowledgeDocument.product == data.product,
            KnowledgeDocument.region == data.region,
            KnowledgeDocument.language == data.language,
        )
        .with_for_update()
    )
    if document is None:
        # Keep an unknown version indistinguishable from another tenant's one.
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    if document.status is KnowledgeDocumentStatus.withdrawn:
        audit_event = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == principal.tenant_id,
                AuditEvent.action == "knowledge.document_withdrawn",
                AuditEvent.resource_type == "knowledge_document",
                AuditEvent.resource_id == document.id,
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        )
        return document, audit_event, True
    if document.status is not KnowledgeDocumentStatus.published:
        raise HTTPException(
            status_code=409,
            detail="Only a published knowledge document can be withdrawn",
        )

    before_summary = _document_summary(document)
    chunks = db.scalars(
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.tenant_id == principal.tenant_id,
            KnowledgeChunk.document_id == document.id,
        )
        .with_for_update()
    ).all()
    document.status = KnowledgeDocumentStatus.withdrawn
    for chunk in chunks:
        chunk.status = KnowledgeDocumentStatus.withdrawn
        # The retrieval query uses the typed status column, but this copied
        # metadata is retained for traceability and must not claim publication.
        chunk.chunk_metadata = {
            **chunk.chunk_metadata,
            "status": KnowledgeDocumentStatus.withdrawn.value,
        }

    audit_event = AuditEvent(
        id=f"audit-{uuid.uuid4().hex}",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="knowledge.document_withdrawn",
        resource_type="knowledge_document",
        resource_id=document.id,
        before_summary=before_summary,
        after_summary=_json_summary(
            {
                **_document_summary_data(document),
                "reason": data.reason,
            }
        ),
        request_id=request_id_var.get(),
    )
    db.add(audit_event)
    db.commit()
    db.refresh(document)
    db.refresh(audit_event)
    return document, audit_event, False


def _require_knowledge_operator(principal: CurrentPrincipal) -> None:
    if principal.role is not UserRole.knowledge_operator:
        raise HTTPException(
            status_code=403,
            detail="Knowledge administration requires knowledge operator role",
        )


def _safe_filename(filename: str) -> str:
    path = PurePath(filename)
    if path.name != filename or filename in {".", ".."}:
        raise HTTPException(
            status_code=422, detail="filename must be a single file name"
        )
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail="only Markdown and TXT imports are supported in this worker",
        )
    return filename


def _display_title(title: str | None, filename: str) -> str:
    if title is not None:
        return title
    generated = PurePath(filename).stem.replace("_", " ").replace("-", " ").strip()
    return generated[:255] or "Knowledge document"


def _document_summary(document: KnowledgeDocument) -> str:
    return _json_summary(_document_summary_data(document))


def _document_summary_data(document: KnowledgeDocument) -> dict[str, object]:
    return {
        "document_key": document.document_key,
        "language": document.language,
        "product": document.product,
        "region": document.region,
        "status": document.status.value,
        "untrusted_content": document.untrusted_content,
        "version": document.version,
    }


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
