from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.db import get_db

from ..identity.deps import get_current_principal
from ..identity.principal import CurrentPrincipal
from . import service
from .schemas import (
    AssignmentCreate,
    CommentCreate,
    TicketCreate,
    TicketEventRead,
    TicketRead,
    TicketUpdate,
)

router = APIRouter(prefix="/v1/tickets", tags=["tickets"])


@router.post("", response_model=TicketRead, status_code=201)
def create_ticket(
    data: TicketCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.create_ticket(db, principal, data, idempotency_key)


@router.get("", response_model=list[TicketRead])
def list_tickets(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_tickets(db, principal)


@router.get("/{ticket_id}", response_model=TicketRead)
def get_ticket(
    ticket_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_ticket(db, principal, ticket_id)


@router.patch("/{ticket_id}", response_model=TicketRead)
def update_ticket(
    ticket_id: str,
    data: TicketUpdate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.update_ticket(db, principal, ticket_id, data)


@router.post("/{ticket_id}/comments", response_model=TicketRead)
def add_comment(
    ticket_id: str,
    data: CommentCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.add_comment(db, principal, ticket_id, data)


@router.post("/{ticket_id}/assignments", response_model=TicketRead)
def assign_ticket(
    ticket_id: str,
    data: AssignmentCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.assign_ticket(db, principal, ticket_id, data)


@router.get("/{ticket_id}/events", response_model=list[TicketEventRead])
def list_events(
    ticket_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_events(db, principal, ticket_id)
