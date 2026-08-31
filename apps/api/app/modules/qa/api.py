"""Supervisor-only QA sampling and feedback-loop endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import (
    QAConversationSampleListItemRead,
    QAConversationSampleRead,
    QAIssueCreate,
    QAIssueUpdate,
    QAOwnerRead,
    QARegressionCatalogEntryRead,
    QAReviewIssueDetailRead,
    QAReviewIssueRead,
)

router = APIRouter(prefix="/v1/admin/qa", tags=["supervisor-qa"])

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=255),
]


@router.get("/owners", response_model=list[QAOwnerRead])
def list_qa_owners(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_owners(db, principal)


@router.get("/regression-cases", response_model=list[QARegressionCatalogEntryRead])
def list_qa_regression_cases(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
):
    return service.list_regression_cases(principal)


@router.get("/samples", response_model=list[QAConversationSampleListItemRead])
def list_qa_samples(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return service.list_samples(db, principal, limit=limit)


@router.get("/samples/{conversation_id}", response_model=QAConversationSampleRead)
def get_qa_sample(
    conversation_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_sample(db, principal, conversation_id)


@router.get("/issues", response_model=list[QAReviewIssueRead])
def list_qa_issues(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_issues(db, principal)


@router.post("/issues", response_model=QAReviewIssueDetailRead, status_code=201)
def create_qa_issue(
    data: QAIssueCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: IdempotencyKey,
):
    return service.create_issue(db, principal, data, idempotency_key)


@router.get("/issues/{issue_id}", response_model=QAReviewIssueDetailRead)
def get_qa_issue(
    issue_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_issue(db, principal, issue_id)


@router.patch("/issues/{issue_id}", response_model=QAReviewIssueDetailRead)
def update_qa_issue(
    issue_id: str,
    data: QAIssueUpdate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: IdempotencyKey,
):
    return service.update_issue(db, principal, issue_id, data, idempotency_key)
