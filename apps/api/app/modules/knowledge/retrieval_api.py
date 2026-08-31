from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.identity import UserRole
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal
from app.modules.self_service import service as self_service
from app.shared.config import get_settings

from . import answer, retrieval
from .schemas import (
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
    KnowledgeCitationSource,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSourceScope,
)

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    data: KnowledgeSearchRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return retrieval.search_knowledge(db, principal, data)


@router.post("/answer", response_model=KnowledgeAnswerResponse)
def answer_with_citations(
    data: KnowledgeAnswerRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    if principal.role is UserRole.customer:
        reason = self_service.r0_handoff_reason(data.query)
        if reason is not None:
            return answer.safe_handoff_response(principal, reason)
        if not self_service.is_r0_self_service_enabled(db, principal):
            return answer.safe_handoff_response(principal, "r0_feature_disabled")
        settings = get_settings()
        # Customer R0 scope is deployment-owned. The client cannot expand it
        # by selecting another product/language, while region remains bound to
        # the authenticated customer in retrieval._resolve_region.
        r0_data = data.model_copy(
            update={
                "product": settings.r0_self_service_product,
                "language": settings.r0_self_service_language,
            }
        )
        return answer.answer_knowledge(
            db,
            principal,
            r0_data,
            audiences=("public",),
        )
    return answer.answer_knowledge(db, principal, data)


@router.get("/chunks/{chunk_id}", response_model=KnowledgeCitationSource)
def get_citation_source(
    chunk_id: str,
    scope: Annotated[KnowledgeSourceScope, Depends()],
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    chunk, document = retrieval.get_authorized_source(
        db,
        principal,
        chunk_id=chunk_id,
        product=scope.product,
        region=scope.region,
        language=scope.language,
    )
    return KnowledgeCitationSource(
        chunk_id=chunk.id,
        document_key=chunk.document_key,
        document_version=chunk.document_version,
        title=document.title.strip() or document.document_key,
        text=chunk.text,
        product=chunk.product,
        region=chunk.region,
        language=chunk.language,
        effective_at=chunk.effective_at,
        expires_at=chunk.expires_at,
    )
