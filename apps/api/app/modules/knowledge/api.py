from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import (
    KnowledgeDocumentWithdrawalCreate,
    KnowledgeDocumentWithdrawalResponse,
    KnowledgeImportCreate,
    KnowledgeImportResponse,
)

router = APIRouter(prefix="/v1/admin/knowledge", tags=["knowledge-admin"])


@router.post(
    "/imports",
    response_model=KnowledgeImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_knowledge_import(
    data: KnowledgeImportCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    item, deduplicated = service.create_import(db, principal, data)
    response = KnowledgeImportResponse.model_validate(item)
    response.deduplicated = deduplicated
    return response


@router.get(
    "/imports/{import_id}",
    response_model=KnowledgeImportResponse,
)
def get_knowledge_import(
    import_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    item = service.get_import(db, principal, import_id)
    return KnowledgeImportResponse.model_validate(item)


@router.post(
    "/documents/withdrawals",
    response_model=KnowledgeDocumentWithdrawalResponse,
)
def withdraw_knowledge_document(
    data: KnowledgeDocumentWithdrawalCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    document, audit_event, already_withdrawn = service.withdraw_document(
        db, principal, data
    )
    return KnowledgeDocumentWithdrawalResponse(
        id=document.id,
        document_key=document.document_key,
        version=document.version,
        product=document.product,
        region=document.region,
        language=document.language,
        status=document.status.value,
        already_withdrawn=already_withdrawn,
        audit_event_id=audit_event.id if audit_event is not None else None,
    )
