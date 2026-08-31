import hashlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.identity import User, UserRole
from app.models.tickets import (
    IdempotencyRecord,
    Ticket,
    TicketEvent,
    TicketEventType,
    TicketStatus,
)
from app.shared.middleware import request_id_var

from ..identity.policies import can_update_ticket, can_view_ticket
from ..identity.principal import CurrentPrincipal
from .schemas import (
    AssignmentCreate,
    CommentCreate,
    TicketCreate,
    TicketRead,
    TicketUpdate,
)
from .state import can_transition

CREATE_TICKET_OPERATION = "POST:/v1/tickets"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ticket_summary(ticket: Ticket, **extra: object) -> str:
    value: dict[str, object] = {
        "assignee_id": ticket.assignee_id,
        "priority": ticket.priority.value,
        "status": ticket.status.value,
    }
    value.update(extra)
    return _json_summary(value)


def _write_audit(
    db: Session,
    principal: CurrentPrincipal,
    action: str,
    *,
    ticket_id: str,
    before_summary: str | None,
    after_summary: str | None,
) -> None:
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action=action,
            resource_type="ticket",
            resource_id=ticket_id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=request_id_var.get(),
        )
    )


def _add_ticket_event(
    db: Session,
    ticket: Ticket,
    principal: CurrentPrincipal,
    event_type: TicketEventType,
    payload: str | None = None,
) -> None:
    db.add(
        TicketEvent(
            id=_new_id("ticket-event"),
            ticket_id=ticket.id,
            actor_id=principal.user_id,
            event_type=event_type,
            payload_digest=payload,
        )
    )


def _get_ticket_or_404(db: Session, ticket_id: str) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _get_ticket_for_update_or_404(db: Session, ticket_id: str) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _normalize_idempotency_key(value: str) -> str:
    key = value.strip()
    if not 8 <= len(key) <= 255 or any(ord(char) < 33 for char in key):
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
    return key


def _ticket_request_fingerprint(data: TicketCreate) -> str:
    canonical = json.dumps(
        data.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _find_idempotency_record(
    db: Session,
    principal: CurrentPrincipal,
    key_hash: str,
) -> IdempotencyRecord | None:
    return db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == principal.tenant_id,
            IdempotencyRecord.principal_id == principal.user_id,
            IdempotencyRecord.operation == CREATE_TICKET_OPERATION,
            IdempotencyRecord.idempotency_key_hash == key_hash,
        )
    )


def _replay_or_conflict(
    record: IdempotencyRecord,
    request_fingerprint: str,
) -> dict[str, object]:
    if record.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )
    return record.response_body


def create_ticket(
    db: Session,
    principal: CurrentPrincipal,
    data: TicketCreate,
    idempotency_key: str,
) -> Ticket | dict[str, object]:
    if principal.customer_id is None:
        raise HTTPException(status_code=403, detail="Only customers can create tickets")
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _ticket_request_fingerprint(data)
    existing = _find_idempotency_record(db, principal, key_hash)
    if existing is not None:
        return _replay_or_conflict(existing, request_fingerprint)

    ticket = Ticket(
        id=_new_id("ticket"),
        tenant_id=principal.tenant_id,
        customer_id=principal.customer_id,
        order_id=data.order_id,
        status=TicketStatus.new,
        subject=data.subject,
        description=data.description,
        priority=data.priority,
    )
    db.add(ticket)
    db.flush()
    response_body: dict[str, object] = TicketRead.model_validate(ticket).model_dump(
        mode="json"
    )
    _add_ticket_event(db, ticket, principal, TicketEventType.created)
    _write_audit(
        db,
        principal,
        "ticket.created",
        ticket_id=ticket.id,
        before_summary=None,
        after_summary=_ticket_summary(ticket),
    )
    db.add(
        IdempotencyRecord(
            id=_new_id("idempotency"),
            tenant_id=principal.tenant_id,
            principal_id=principal.user_id,
            operation=CREATE_TICKET_OPERATION,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            ticket_id=ticket.id,
            response_body=response_body,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request can win the unique scope race. Its transaction is
        # now committed, so re-read it and apply the same replay/conflict rule.
        db.rollback()
        winner = _find_idempotency_record(db, principal, key_hash)
        if winner is None:
            raise
        return _replay_or_conflict(winner, request_fingerprint)
    db.refresh(ticket)
    return ticket


def list_tickets(db: Session, principal: CurrentPrincipal) -> list[Ticket]:
    stmt = select(Ticket).where(Ticket.tenant_id == principal.tenant_id)
    if principal.customer_id is not None:  # customer 只看自己的
        stmt = stmt.where(Ticket.customer_id == principal.customer_id)
    return list(db.scalars(stmt))


def get_ticket(db: Session, principal: CurrentPrincipal, ticket_id: str) -> Ticket:
    ticket = _get_ticket_or_404(db, ticket_id)
    if not can_view_ticket(
        principal,
        ticket_tenant_id=ticket.tenant_id,
        ticket_customer_id=ticket.customer_id,
    ):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def update_ticket(
    db: Session, principal: CurrentPrincipal, ticket_id: str, data: TicketUpdate
) -> Ticket:
    ticket = _get_ticket_for_update_or_404(db, ticket_id)
    if not can_update_ticket(principal, ticket_tenant_id=ticket.tenant_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    before_summary = _ticket_summary(ticket)
    if data.status is not None:
        if not can_transition(ticket.status, data.status):
            raise HTTPException(status_code=409, detail="Invalid state transition")
        _add_ticket_event(
            db,
            ticket,
            principal,
            TicketEventType.status_changed,
            payload=str(data.status.value),
        )
        ticket.status = data.status
        ticket.closed_at = (
            datetime.now(UTC) if data.status is TicketStatus.closed else None
        )
    if data.priority is not None:
        ticket.priority = data.priority
    _write_audit(
        db,
        principal,
        "ticket.updated",
        ticket_id=ticket.id,
        before_summary=before_summary,
        after_summary=_ticket_summary(ticket),
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def add_comment(
    db: Session, principal: CurrentPrincipal, ticket_id: str, data: CommentCreate
) -> Ticket:
    ticket = _get_ticket_or_404(db, ticket_id)
    if not can_view_ticket(
        principal,
        ticket_tenant_id=ticket.tenant_id,
        ticket_customer_id=ticket.customer_id,
    ):
        raise HTTPException(status_code=404, detail="Ticket not found")
    if data.is_internal and principal.role is UserRole.customer:
        raise HTTPException(status_code=403, detail="Internal notes require staff role")
    before_summary = _ticket_summary(ticket)
    event_type = (
        TicketEventType.internal_note
        if data.is_internal
        else TicketEventType.comment_added
    )
    _add_ticket_event(db, ticket, principal, event_type, payload=data.body)
    _write_audit(
        db,
        principal,
        "ticket.commented",
        ticket_id=ticket.id,
        before_summary=before_summary,
        after_summary=_ticket_summary(ticket, event_type=event_type.value),
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def assign_ticket(
    db: Session, principal: CurrentPrincipal, ticket_id: str, data: AssignmentCreate
) -> Ticket:
    ticket = _get_ticket_for_update_or_404(db, ticket_id)
    if not can_update_ticket(principal, ticket_tenant_id=ticket.tenant_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status not in {
        TicketStatus.triaged,
        TicketStatus.assigned,
        TicketStatus.in_progress,
        TicketStatus.waiting_customer,
        TicketStatus.reopened,
    }:
        raise HTTPException(
            status_code=409, detail="Ticket cannot be assigned in its current state"
        )
    assignee = db.get(User, data.assignee_id)
    if assignee is None or assignee.tenant_id != ticket.tenant_id:
        raise HTTPException(status_code=404, detail="Assignee not found")
    if assignee.role is not UserRole.agent:
        raise HTTPException(status_code=422, detail="Assignee must be an agent")
    if principal.role is UserRole.agent:
        if assignee.id != principal.user_id:
            raise HTTPException(
                status_code=403, detail="Agents can only claim tickets for themselves"
            )
        if ticket.status is not TicketStatus.triaged or ticket.assignee_id is not None:
            raise HTTPException(
                status_code=409, detail="Ticket is not available for self-claim"
            )
    if ticket.assignee_id == assignee.id:
        raise HTTPException(status_code=409, detail="Ticket already has this assignee")

    before_summary = _ticket_summary(ticket)
    ticket.assignee_id = assignee.id
    if ticket.status is TicketStatus.triaged:
        ticket.status = TicketStatus.assigned
        _add_ticket_event(
            db,
            ticket,
            principal,
            TicketEventType.status_changed,
            payload=TicketStatus.assigned.value,
        )
    _add_ticket_event(
        db, ticket, principal, TicketEventType.assigned, payload=data.assignee_id
    )
    _write_audit(
        db,
        principal,
        "ticket.assigned",
        ticket_id=ticket.id,
        before_summary=before_summary,
        after_summary=_ticket_summary(ticket),
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def list_events(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> list[TicketEvent]:
    ticket = _get_ticket_or_404(db, ticket_id)
    if not can_view_ticket(
        principal,
        ticket_tenant_id=ticket.tenant_id,
        ticket_customer_id=ticket.customer_id,
    ):
        raise HTTPException(status_code=404, detail="Ticket not found")
    stmt = (
        select(TicketEvent)
        .where(TicketEvent.ticket_id == ticket_id)
        .order_by(TicketEvent.created_at)
    )
    events = list(db.scalars(stmt))
    if principal.role is UserRole.customer:
        # 客户看不到内部备注
        events = [
            e for e in events if e.event_type is not TicketEventType.internal_note
        ]
    return events
