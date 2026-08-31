from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import TriageDecisionCreate, TriageSuggestionRead

router = APIRouter(prefix="/v1/tickets", tags=["triage"])


@router.post(
    "/{ticket_id}/triage", response_model=TriageSuggestionRead, status_code=201
)
def create_suggestion(
    ticket_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.create_suggestion(db, principal, ticket_id)


@router.get("/{ticket_id}/triage", response_model=TriageSuggestionRead)
def get_suggestion(
    ticket_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_latest_suggestion(db, principal, ticket_id)


@router.get("/{ticket_id}/triage/history", response_model=list[TriageSuggestionRead])
def get_suggestion_history(
    ticket_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_suggestions(db, principal, ticket_id)


@router.post("/{ticket_id}/triage/decision", response_model=TriageSuggestionRead)
def record_decision(
    ticket_id: str,
    data: TriageDecisionCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.record_decision(db, principal, ticket_id, data)
