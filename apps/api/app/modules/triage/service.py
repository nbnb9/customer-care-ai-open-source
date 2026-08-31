import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.identity import UserRole
from app.models.tickets import Ticket
from app.models.triage import TriageSuggestion
from app.modules.identity.policies import can_view_ticket
from app.modules.identity.principal import CurrentPrincipal
from app.modules.llm_gateway.base import (
    TRIAGE_SCHEMA_VERSION,
    enforce_triage_policy,
    generate_triage_safe,
)
from app.modules.llm_gateway.factory import get_gateway
from app.modules.llm_gateway.schemas import TriageResult
from app.shared.middleware import request_id_var
from app.shared.observability import bind_resources
from app.shared.redaction import sanitize_for_model

from .schemas import TriageDecisionCreate

MAX_INPUT_CHARS = 2000


def _require_triage_role(principal: CurrentPrincipal) -> None:
    if principal.role not in (UserRole.agent, UserRole.supervisor):
        raise HTTPException(status_code=403, detail="Triage requires staff role")


def _suggestion_summary(suggestion: TriageSuggestion) -> str:
    return json.dumps(
        {
            "decision": suggestion.decision,
            "decision_reason_code": suggestion.decision_reason_code,
            "decided_by": suggestion.decided_by,
            "decided_at": (
                suggestion.decided_at.isoformat() if suggestion.decided_at else None
            ),
            "edited_summary_present": suggestion.edited_summary is not None,
            "intent": suggestion.intent,
            "generation_latency_ms": suggestion.generation_latency_ms,
            "model": suggestion.model,
            "prompt_version": suggestion.prompt_version,
            "provider": suggestion.provider,
            "risk_level": suggestion.risk_level,
            "route": suggestion.route,
            "schema_version": suggestion.schema_version,
            "suggested_priority": suggestion.suggested_priority,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_audit(
    db: Session,
    principal: CurrentPrincipal,
    action: str,
    *,
    suggestion: TriageSuggestion,
    before_summary: str | None,
) -> None:
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action=action,
            resource_type="triage_suggestion",
            resource_id=suggestion.id,
            before_summary=before_summary,
            after_summary=_suggestion_summary(suggestion),
            request_id=request_id_var.get(),
        )
    )


def _get_ticket_or_404(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> Ticket:
    _require_triage_role(principal)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not can_view_ticket(
        principal,
        ticket_tenant_id=ticket.tenant_id,
        ticket_customer_id=ticket.customer_id,
    ):
        raise HTTPException(status_code=404, detail="Ticket not found")
    bind_resources(ticket_id=ticket.id)
    return ticket


def _build_input(ticket: Ticket) -> str:
    return f"主题：{ticket.subject}\n描述：{ticket.description or ''}"


def create_suggestion(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> TriageSuggestion:
    ticket = _get_ticket_or_404(db, principal, ticket_id)
    text = sanitize_for_model(_build_input(ticket), max_chars=MAX_INPUT_CHARS)

    gateway = get_gateway()
    started = time.monotonic()
    result: TriageResult = generate_triage_safe(gateway, text=text)
    result = enforce_triage_policy(result, text=text)
    generation_latency_ms = max(0, round((time.monotonic() - started) * 1000))

    suggestion = TriageSuggestion(
        id=_new_id("triage"),
        ticket_id=ticket_id,
        intent=result.intent.value,
        risk_level=result.risk_level.value,
        route=result.route.value,
        suggested_priority=result.suggested_priority.value,
        summary=result.summary,
        missing_information=result.missing_information,
        needs_human_review=result.needs_human_review,
        reason_codes=result.reason_codes,
        provider=gateway.provider_name,
        model=gateway.model_name,
        prompt_version=gateway.prompt_version,
        schema_version=TRIAGE_SCHEMA_VERSION,
        generation_latency_ms=generation_latency_ms,
    )
    db.add(suggestion)
    _write_audit(
        db,
        principal,
        "triage.suggestion_created",
        suggestion=suggestion,
        before_summary=None,
    )
    db.commit()
    db.refresh(suggestion)
    return suggestion


def get_latest_suggestion(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> TriageSuggestion:
    _get_ticket_or_404(db, principal, ticket_id)
    stmt = (
        select(TriageSuggestion)
        .where(TriageSuggestion.ticket_id == ticket_id)
        .order_by(TriageSuggestion.created_at.desc(), TriageSuggestion.id.desc())
        .limit(1)
    )
    suggestion = db.scalars(stmt).first()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="尚无分诊建议")
    return suggestion


def list_suggestions(
    db: Session, principal: CurrentPrincipal, ticket_id: str
) -> list[TriageSuggestion]:
    _get_ticket_or_404(db, principal, ticket_id)
    stmt = (
        select(TriageSuggestion)
        .where(TriageSuggestion.ticket_id == ticket_id)
        .order_by(TriageSuggestion.created_at.desc(), TriageSuggestion.id.desc())
    )
    return list(db.scalars(stmt))


def record_decision(
    db: Session,
    principal: CurrentPrincipal,
    ticket_id: str,
    data: TriageDecisionCreate,
) -> TriageSuggestion:
    _get_ticket_or_404(db, principal, ticket_id)
    stmt = (
        select(TriageSuggestion)
        .where(
            TriageSuggestion.id == data.suggestion_id,
            TriageSuggestion.ticket_id == ticket_id,
        )
        .with_for_update()
    )
    suggestion = db.scalars(stmt).first()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Triage suggestion not found")
    if suggestion.decision is not None:
        raise HTTPException(status_code=409, detail="Triage decision is immutable")
    before_summary = _suggestion_summary(suggestion)
    suggestion.decision = data.decision
    suggestion.decision_reason_code = data.reason_code
    suggestion.edited_summary = data.edited_summary
    suggestion.decided_by = principal.user_id
    suggestion.decided_at = datetime.now(UTC)
    _write_audit(
        db,
        principal,
        "triage.decision_recorded",
        suggestion=suggestion,
        before_summary=before_summary,
    )
    db.commit()
    db.refresh(suggestion)
    return suggestion


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"
