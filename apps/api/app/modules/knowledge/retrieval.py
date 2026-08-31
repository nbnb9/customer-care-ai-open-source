"""ACL-first knowledge retrieval.

The SQL statement filters tenant, role audience, applicability scope and
document lifecycle before any ranked rows leave PostgreSQL.  The current
``hash-v1`` vector is deterministic plumbing only, so lexical matches take
priority and this module makes no semantic-quality claim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from fastapi import HTTPException
from opentelemetry.trace import SpanKind
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.identity import Customer, UserRole
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
)
from app.modules.identity.principal import CurrentPrincipal
from app.shared.observability import (
    bind_knowledge_versions,
    observe_span,
    set_span_attributes,
)

from .schemas import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)
from .worker import hash_embedding

ALLOWED_AUDIENCES: Final[dict[UserRole, tuple[str, ...]]] = {
    UserRole.customer: ("public", "customer"),
    UserRole.agent: ("public", "agent", "internal"),
    UserRole.supervisor: ("public", "supervisor", "internal"),
}
MAX_CANDIDATE_MULTIPLIER: Final = 3
MAX_SNIPPET_CHARS: Final = 300
SNIPPET_CONTEXT_CHARS: Final = 96


def search_knowledge(
    db: Session,
    principal: CurrentPrincipal,
    data: KnowledgeSearchRequest,
    *,
    audiences: tuple[str, ...] | None = None,
) -> KnowledgeSearchResponse:
    """Return only server-authorized, currently applicable chunks."""

    with observe_span(
        "knowledge.retrieval",
        kind=SpanKind.INTERNAL,
        attributes={"gen_ai.operation.name": "retrieval"},
    ):
        role_audiences = _audiences_for(principal)
        if audiences is None:
            audiences = role_audiences
        elif not set(audiences).issubset(role_audiences):
            raise ValueError(
                "Requested knowledge audiences exceed the principal policy"
            )
        region = _resolve_region(db, principal, data.region)
        now = datetime.now(UTC)
        rows = db.execute(
            build_acl_filtered_search_statement(
                principal=principal,
                query=data.query,
                product=data.product,
                region=region,
                language=data.language,
                audiences=audiences,
                now=now,
                limit=data.limit * MAX_CANDIDATE_MULTIPLIER,
            )
        ).all()

        results: list[KnowledgeSearchResult] = []
        seen_chunks: set[tuple[str, str]] = set()
        for chunk, document, _distance, matched_lexically in rows:
            # A duplicate import should already be impossible, but avoid returning
            # byte-identical chunks if data is repaired or imported manually.
            deduplication_key = (chunk.document_id, chunk.text)
            if deduplication_key in seen_chunks:
                continue
            seen_chunks.add(deduplication_key)
            results.append(
                KnowledgeSearchResult(
                    chunk_id=chunk.id,
                    document_key=chunk.document_key,
                    document_version=chunk.document_version,
                    title=document.title.strip()
                    or _title_from_document_key(document.document_key),
                    snippet=_make_snippet(chunk.text, data.query),
                    product=chunk.product,
                    region=chunk.region,
                    language=chunk.language,
                    effective_at=chunk.effective_at,
                    expires_at=chunk.expires_at,
                    matched_lexically=bool(matched_lexically),
                    untrusted_content=(
                        bool(chunk.untrusted_content)
                        or bool(document.untrusted_content)
                    ),
                )
            )
            if len(results) == data.limit:
                break
        bind_knowledge_versions([result.document_version for result in results])
        set_span_attributes(
            {
                "customer_care.retrieval.candidate_count": len(rows),
                "customer_care.retrieval.result_count": len(results),
            }
        )
    return KnowledgeSearchResponse(results=results)


def build_acl_filtered_search_statement(
    *,
    principal: CurrentPrincipal,
    query: str,
    product: str,
    region: str,
    language: str,
    audiences: tuple[str, ...],
    now: datetime,
    limit: int,
):
    """Build the one SQL statement used for ACL filtering and ranking.

    PostgreSQL applies the ``WHERE`` predicates to both duplicated chunk ACL
    metadata and its authoritative document row before ordering by lexical and
    vector distance.  This never reads cross-tenant rows into application code.
    """

    vector_distance = KnowledgeChunk.embedding.l2_distance(hash_embedding(query)).label(
        "vector_distance"
    )
    lexical_match = (
        func.strpos(func.lower(KnowledgeChunk.text), query.lower()) > 0
    ).label("matched_lexically")
    lexical_rank = case((lexical_match, 0), else_=1).label("lexical_rank")
    product_scope = _exact_or_global_scope(product)
    region_scope = _exact_or_global_scope(region)
    language_scope = _exact_or_global_scope(language)

    return (
        select(KnowledgeChunk, KnowledgeDocument, vector_distance, lexical_match)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            *_acl_predicates(
                principal=principal,
                audiences=audiences,
                product_scope=product_scope,
                region_scope=region_scope,
                language_scope=language_scope,
                now=now,
            )
        )
        .order_by(lexical_rank, vector_distance, KnowledgeChunk.id)
        .limit(limit)
    )


def get_authorized_source(
    db: Session,
    principal: CurrentPrincipal,
    *,
    chunk_id: str,
    product: str,
    region: str,
    language: str,
) -> tuple[KnowledgeChunk, KnowledgeDocument]:
    """Re-check ACL, lifecycle and scope before exposing a cited chunk.

    A citation URL is not a capability token. The source must still be visible
    to the caller at click time, and untrusted imports can never be displayed
    through the answer evidence flow.
    """

    audiences = _audiences_for(principal)
    resolved_region = _resolve_region(db, principal, region)
    now = datetime.now(UTC)
    row = db.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.untrusted_content.is_(False),
            KnowledgeDocument.untrusted_content.is_(False),
            *_acl_predicates(
                principal=principal,
                audiences=audiences,
                product_scope=_exact_or_global_scope(product),
                region_scope=_exact_or_global_scope(resolved_region),
                language_scope=_exact_or_global_scope(language),
                now=now,
            ),
        )
    ).one_or_none()
    if row is None:
        # Do not distinguish a non-existent ID from a cross-tenant, expired,
        # or otherwise unauthorized source.
        raise HTTPException(status_code=404, detail="Knowledge source is unavailable")
    return row._t


def _acl_predicates(
    *,
    principal: CurrentPrincipal,
    audiences: tuple[str, ...],
    product_scope: tuple[str, ...],
    region_scope: tuple[str, ...],
    language_scope: tuple[str, ...],
    now: datetime,
) -> tuple[ColumnElement[bool], ...]:
    """Predicates shared by ranked retrieval and source re-authorization."""

    return (
        KnowledgeChunk.tenant_id == principal.tenant_id,
        KnowledgeDocument.tenant_id == principal.tenant_id,
        KnowledgeChunk.audience.in_(audiences),
        KnowledgeDocument.audience.in_(audiences),
        KnowledgeChunk.product.in_(product_scope),
        KnowledgeDocument.product.in_(product_scope),
        KnowledgeChunk.region.in_(region_scope),
        KnowledgeDocument.region.in_(region_scope),
        KnowledgeChunk.language.in_(language_scope),
        KnowledgeDocument.language.in_(language_scope),
        KnowledgeChunk.status == KnowledgeDocumentStatus.published,
        KnowledgeDocument.status == KnowledgeDocumentStatus.published,
        KnowledgeChunk.effective_at <= now,
        KnowledgeDocument.effective_at <= now,
        or_(KnowledgeChunk.expires_at.is_(None), KnowledgeChunk.expires_at > now),
        or_(
            KnowledgeDocument.expires_at.is_(None),
            KnowledgeDocument.expires_at > now,
        ),
    )


def _audiences_for(principal: CurrentPrincipal) -> tuple[str, ...]:
    audiences = ALLOWED_AUDIENCES.get(principal.role)
    if audiences is None:
        raise HTTPException(
            status_code=403,
            detail="Knowledge search is unavailable for this role",
        )
    return audiences


def _resolve_region(
    db: Session, principal: CurrentPrincipal, requested_region: str
) -> str:
    if principal.role is not UserRole.customer:
        return requested_region
    if principal.customer_id is None:
        raise HTTPException(status_code=403, detail="Customer identity is incomplete")
    customer = db.scalar(
        select(Customer).where(
            Customer.id == principal.customer_id,
            Customer.tenant_id == principal.tenant_id,
            Customer.user_id == principal.user_id,
        )
    )
    if customer is None:
        raise HTTPException(status_code=403, detail="Customer identity is invalid")
    if requested_region != customer.region:
        raise HTTPException(
            status_code=403,
            detail="Customer knowledge search region must match the principal region",
        )
    return customer.region


def _exact_or_global_scope(value: str) -> tuple[str, ...]:
    return ("all",) if value == "all" else (value, "all")


def _make_snippet(text: str, query: str) -> str:
    query_position = text.lower().find(query.lower())
    if query_position < 0:
        start = 0
    else:
        start = max(0, query_position - SNIPPET_CONTEXT_CHARS)
    end = min(len(text), start + MAX_SNIPPET_CHARS)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _title_from_document_key(document_key: str) -> str:
    return document_key.replace("-", " ").replace("_", " ").strip()
