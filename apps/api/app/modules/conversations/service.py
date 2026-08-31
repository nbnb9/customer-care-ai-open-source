"""Conversation domain service.

The event log is the source of truth for browser recovery.  Each writer locks
its conversation row before advancing `last_sequence`, so an SSE subscriber
can always request the strict suffix after the last sequence it confirmed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.audit import AuditEvent
from app.models.conversations import (
    Conversation,
    ConversationActor,
    ConversationEvent,
    ConversationEventType,
    ConversationFeedback,
    ConversationHandoff,
    ConversationIdempotencyRecord,
    ConversationMessage,
    ConversationStatus,
    HandoffStatus,
)
from app.models.identity import Customer, User, UserRole
from app.models.tickets import TicketPriority
from app.modules.identity.policies import can_view_queue, has_verified_customer_context
from app.modules.knowledge import answer as knowledge_answer
from app.modules.knowledge.schemas import (
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
)
from app.modules.self_service import service as self_service
from app.shared.config import get_settings
from app.shared.middleware import request_id_var
from app.shared.observability import (
    bind_principal,
    bind_resources,
    observe_background_task,
    set_span_attributes,
)
from app.shared.redaction import redact_direct_identifiers

from ..identity.principal import CurrentPrincipal
from .schemas import (
    ConversationCreate,
    ConversationEventEnvelope,
    ConversationFeedbackCreate,
    ConversationFeedbackRead,
    ConversationHandoffCreate,
    ConversationHandoffRead,
    ConversationMessageAccepted,
    ConversationMessageCreate,
    ConversationRead,
    HandoffClaimCreate,
    HandoffContextFactRead,
    HandoffContextPackageRead,
    HandoffCustomerVisibleCitationRead,
    HandoffDetailRead,
    HandoffQueueItemRead,
    HandoffResolutionCreate,
)

CREATE_CONVERSATION_OPERATION = "POST:/v1/conversations"
SSE_POLL_INTERVAL_SECONDS = 0.15
SSE_CONNECTION_LIFETIME_SECONDS = 20
CONTROLLED_ACKNOWLEDGEMENT = (
    "已收到你的消息。当前低风险自助 FAQ 尚未向此账户开放；你可以随时请求人工客服协助。"
)
GENERATION_FAILURE_MESSAGE = "系统暂时无法完成回复。你可以重试或转人工客服。"
REPEATED_GENERATION_FAILURE_HANDOFF_MESSAGE = (
    "系统连续未能完成回复，已转交人工客服处理。"
)
R0_HANDOFF_MESSAGE = "当前无法基于可验证的公开资料完成回答，已转交人工客服处理。"
IDENTITY_HANDOFF_MESSAGE = "当前验证状态不足，已转交人工客服协助完成核验。"
CONTROLLED_ACK_RESPONSE_KIND = "controlled_ack"
R0_SELF_SERVICE_RESPONSE_KIND = "r0_self_service"
R0_HANDOFF_RESPONSE_KIND = "r0_handoff"


@dataclass(frozen=True)
class MessageSubmission:
    response: ConversationMessageAccepted
    should_generate_reply: bool


@dataclass(frozen=True)
class HandoffRouting:
    priority: TicketPriority
    target_queue: str
    sla_minutes: int


# Routing is a server-side policy.  Neither the customer request body nor a
# model output can select its priority, queue, or SLA.
_HANDOFF_ROUTING: dict[str, HandoffRouting] = {
    "safety_review": HandoffRouting(TicketPriority.urgent, "safety-review", 15),
    "system_generation_repeated_failure": HandoffRouting(
        TicketPriority.urgent, "technical-support", 30
    ),
    "identity_verification_required": HandoffRouting(
        TicketPriority.high, "identity-verification", 60
    ),
    "identity_or_account": HandoffRouting(
        TicketPriority.high, "identity-verification", 60
    ),
    "policy_action": HandoffRouting(TicketPriority.high, "after-sales-review", 60),
    "r0_generation_failed": HandoffRouting(
        TicketPriority.high, "technical-support", 60
    ),
    "order_or_account_action": HandoffRouting(
        TicketPriority.normal, "order-support", 120
    ),
    "r0_evidence_unavailable": HandoffRouting(
        TicketPriority.normal, "knowledge-support", 240
    ),
    "customer_requested": HandoffRouting(TicketPriority.normal, "general-support", 240),
}
_DEFAULT_HANDOFF_ROUTING = HandoffRouting(TicketPriority.normal, "general-support", 240)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _request_id() -> str:
    value = request_id_var.get()
    return value if value != "-" else _new_id("request")


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_audit(
    db: Session,
    *,
    tenant_id: str,
    actor_id: str | None,
    action: str,
    conversation_id: str,
    request_id: str,
    before_summary: str | None,
    after_summary: str | None,
) -> None:
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="conversation",
            resource_id=conversation_id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=request_id,
        )
    )


def _conversation_summary(conversation: Conversation, **extra: object) -> str:
    value: dict[str, object] = {
        "last_sequence": conversation.last_sequence,
        "status": conversation.status.value,
    }
    value.update(extra)
    return _json_summary(value)


def _require_customer(principal: CurrentPrincipal) -> str:
    if principal.role is not UserRole.customer or principal.customer_id is None:
        raise HTTPException(status_code=403, detail="Customer role is required")
    return principal.customer_id


def _get_owned_conversation_or_404(
    db: Session,
    principal: CurrentPrincipal,
    conversation_id: str,
    *,
    for_update: bool = False,
) -> Conversation:
    # Resource endpoints deliberately use 404 for staff and other customers so
    # a conversation identifier never becomes an existence oracle.
    if principal.role is not UserRole.customer or principal.customer_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == principal.tenant_id,
        Conversation.customer_id == principal.customer_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    conversation = db.scalar(stmt)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    bind_resources(conversation_id=conversation.id)
    return conversation


def _get_conversation_for_update(
    db: Session, conversation_id: str
) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )


def _append_event(
    db: Session,
    conversation: Conversation,
    *,
    event_type: ConversationEventType,
    request_id: str,
    payload: dict[str, object],
) -> ConversationEvent:
    conversation.last_sequence += 1
    event = ConversationEvent(
        event_id=_new_id("event"),
        conversation_id=conversation.id,
        sequence=conversation.last_sequence,
        event_type=event_type.value,
        request_id=request_id,
        payload=payload,
    )
    db.add(event)
    db.flush()
    return event


def _normalize_idempotency_key(value: str) -> str:
    key = value.strip()
    if not 8 <= len(key) <= 255 or any(ord(char) < 33 for char in key):
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
    return key


def _fingerprint(data: object) -> str:
    if hasattr(data, "model_dump"):
        payload = data.model_dump(mode="json")  # type: ignore[union-attr]
    else:
        payload = data
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _find_idempotency_record(
    db: Session,
    principal: CurrentPrincipal,
    *,
    operation: str,
    key_hash: str,
) -> ConversationIdempotencyRecord | None:
    return db.scalar(
        select(ConversationIdempotencyRecord).where(
            ConversationIdempotencyRecord.tenant_id == principal.tenant_id,
            ConversationIdempotencyRecord.principal_id == principal.user_id,
            ConversationIdempotencyRecord.operation == operation,
            ConversationIdempotencyRecord.idempotency_key_hash == key_hash,
        )
    )


def _replay_or_conflict(
    record: ConversationIdempotencyRecord, request_fingerprint: str
) -> dict[str, object]:
    if record.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )
    return record.response_body


def _store_idempotency_record(
    db: Session,
    principal: CurrentPrincipal,
    *,
    operation: str,
    key_hash: str,
    request_fingerprint: str,
    conversation_id: str,
    response_body: dict[str, object],
) -> None:
    db.add(
        ConversationIdempotencyRecord(
            id=_new_id("conversation-idempotency"),
            tenant_id=principal.tenant_id,
            principal_id=principal.user_id,
            operation=operation,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            conversation_id=conversation_id,
            response_body=response_body,
        )
    )


def _message_operation(conversation_id: str) -> str:
    return f"POST:/v1/conversations/{conversation_id}/messages"


def _handoff_operation(conversation_id: str) -> str:
    return f"POST:/v1/conversations/{conversation_id}/handoffs"


def _handoff_claim_operation(handoff_id: str) -> str:
    return f"POST:/v1/handoffs/{handoff_id}/claim"


def _handoff_resolution_operation(handoff_id: str) -> str:
    return f"POST:/v1/handoffs/{handoff_id}/resolution"


def _feedback_operation(conversation_id: str) -> str:
    return f"POST:/v1/conversations/{conversation_id}/feedback"


def create_conversation(
    db: Session,
    principal: CurrentPrincipal,
    data: ConversationCreate,
    idempotency_key: str,
) -> ConversationRead:
    customer_id = _require_customer(principal)
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    existing = _find_idempotency_record(
        db,
        principal,
        operation=CREATE_CONVERSATION_OPERATION,
        key_hash=key_hash,
    )
    if existing is not None:
        return ConversationRead.model_validate(
            _replay_or_conflict(existing, request_fingerprint)
        )

    conversation = Conversation(
        id=_new_id("conversation"),
        tenant_id=principal.tenant_id,
        customer_id=customer_id,
        status=ConversationStatus.active,
        last_sequence=0,
    )
    db.add(conversation)
    db.flush()
    bind_resources(conversation_id=conversation.id)
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.conversation_created,
        request_id=request_id,
        payload={"status": conversation.status.value, "channel": data.channel},
    )
    response = ConversationRead.model_validate(conversation)
    _write_audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="conversation.created",
        conversation_id=conversation.id,
        request_id=request_id,
        before_summary=None,
        after_summary=_conversation_summary(conversation),
    )
    _store_idempotency_record(
        db,
        principal,
        operation=CREATE_CONVERSATION_OPERATION,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db,
            principal,
            operation=CREATE_CONVERSATION_OPERATION,
            key_hash=key_hash,
        )
        if winner is None:
            raise
        return ConversationRead.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
    return response


def _enforce_message_rate_limit(db: Session, principal: CurrentPrincipal) -> None:
    customer_id = _require_customer(principal)
    settings = get_settings()
    # A customer-row lock serializes submissions across that customer's open
    # conversations. It makes the DB-backed starting limit deterministic even
    # before a distributed limiter is introduced in the production phase.
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id, Customer.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    now = datetime.now(UTC)
    window_started_at = now - timedelta(
        seconds=settings.conversation_message_rate_window_seconds
    )
    submitted = db.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(
            Conversation.tenant_id == principal.tenant_id,
            Conversation.customer_id == customer_id,
            ConversationMessage.actor == ConversationActor.customer,
            ConversationMessage.created_at >= window_started_at,
        )
    )
    if (submitted or 0) >= settings.conversation_message_rate_limit:
        raise HTTPException(
            status_code=429,
            detail="Too many conversation messages; please try again shortly",
            headers={
                "Retry-After": str(settings.conversation_message_rate_window_seconds)
            },
        )


def _policy_handoff_reason(text: str) -> str | None:
    """Reuse the Step 33 deterministic floor before any model/RAG call."""

    return self_service.r0_handoff_reason(text)


def _handoff_routing(reason_code: str) -> HandoffRouting:
    return _HANDOFF_ROUTING.get(reason_code, _DEFAULT_HANDOFF_ROUTING)


def _verification_level_for_conversation(
    db: Session, conversation: Conversation
) -> str | None:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == conversation.customer_id,
            Customer.tenant_id == conversation.tenant_id,
        )
    )
    if customer is None:
        return None
    user = db.scalar(
        select(User).where(
            User.id == customer.user_id,
            User.tenant_id == conversation.tenant_id,
            User.role == UserRole.customer,
        )
    )
    return user.verification_level if user is not None else None


def _customer_visible_citations(
    db: Session, conversation_id: str
) -> list[HandoffCustomerVisibleCitationRead]:
    """Return only citation fields the customer already saw in an R0 answer."""

    citations: list[HandoffCustomerVisibleCitationRead] = []
    seen: set[tuple[str, str, str]] = set()
    events = db.scalars(
        select(ConversationEvent)
        .where(
            ConversationEvent.conversation_id == conversation_id,
            ConversationEvent.event_type
            == ConversationEventType.answer_completed.value,
        )
        .order_by(ConversationEvent.sequence.asc())
    )
    for event in events:
        raw_citations = event.payload.get("citations")
        if not isinstance(raw_citations, list):
            continue
        for raw_citation in raw_citations:
            if not isinstance(raw_citation, dict):
                continue
            title = raw_citation.get("title")
            document_version = raw_citation.get("document_version")
            excerpt = raw_citation.get("excerpt")
            source_locator = raw_citation.get("source_locator")
            if (
                not isinstance(title, str)
                or not isinstance(document_version, str)
                or not isinstance(excerpt, str)
                or not isinstance(source_locator, str)
                or not source_locator.startswith("/v1/knowledge/chunks/")
            ):
                continue
            key = (title, document_version, source_locator)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                HandoffCustomerVisibleCitationRead(
                    title=redact_direct_identifiers(title, max_chars=160),
                    document_version=document_version[:80],
                    excerpt=redact_direct_identifiers(excerpt, max_chars=500),
                    source_locator=source_locator[:300],
                )
            )
            if len(citations) == 6:
                return citations
    return citations


def _build_handoff_context(
    db: Session,
    conversation: Conversation,
    *,
    reason_code: str,
    verification_level: str | None,
) -> dict[str, object]:
    """Build a bounded staff package without exposing raw conversation history."""

    counts: dict[str, int] = {}
    for actor, count in db.execute(
        select(ConversationMessage.actor, func.count())
        .where(ConversationMessage.conversation_id == conversation.id)
        .group_by(ConversationMessage.actor)
    ):
        actor_name = actor.value if isinstance(actor, ConversationActor) else str(actor)
        counts[actor_name] = int(count)
    latest_customer_message = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.actor == ConversationActor.customer,
        )
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(1)
    )
    unresolved_questions: list[str] = []
    if latest_customer_message is not None:
        unresolved_questions.append(
            redact_direct_identifiers(latest_customer_message.body, max_chars=500)
        )

    package = HandoffContextPackageRead(
        verification_level=verification_level
        if verification_level is not None
        else _verification_level_for_conversation(db, conversation),
        conversation_summary=(
            "会话已记录"
            f" {counts.get(ConversationActor.customer.value, 0)} 条客户消息和"
            f" {counts.get(ConversationActor.assistant.value, 0)} 条助手回复；"
            f"当前自助会话状态为 {conversation.status.value}。"
        ),
        # These are system-observed state facts, not unverified customer claims.
        confirmed_facts=[
            HandoffContextFactRead(label="升级原因", value=reason_code),
            HandoffContextFactRead(
                label="当前会话状态", value=conversation.status.value
            ),
        ],
        # R1 tools are deliberately not yet called from a conversation.  An
        # empty list is more truthful than inferring a tool result from audit
        # records that are not causally linked to this conversation.
        tool_summaries=[],
        customer_visible_citations=_customer_visible_citations(db, conversation.id),
        unresolved_questions=unresolved_questions,
        risk_reasons=[reason_code],
    )
    return package.model_dump(mode="json")


def _handoff_context_read(handoff: ConversationHandoff) -> HandoffContextPackageRead:
    snapshot = handoff.context_snapshot
    return HandoffContextPackageRead.model_validate(
        snapshot if isinstance(snapshot, dict) else {}
    )


def _handoff_queue_read(handoff: ConversationHandoff) -> HandoffQueueItemRead:
    return HandoffQueueItemRead.model_validate(handoff)


def _handoff_detail_read(handoff: ConversationHandoff) -> HandoffDetailRead:
    item = _handoff_queue_read(handoff)
    return HandoffDetailRead(
        **item.model_dump(),
        context=_handoff_context_read(handoff),
    )


def _find_open_handoff(db: Session, conversation_id: str) -> ConversationHandoff | None:
    return db.scalar(
        select(ConversationHandoff)
        .where(
            ConversationHandoff.conversation_id == conversation_id,
            ConversationHandoff.status.in_(
                (HandoffStatus.requested, HandoffStatus.claimed)
            ),
        )
        .order_by(ConversationHandoff.created_at.desc(), ConversationHandoff.id.desc())
        .limit(1)
    )


def _create_handoff(
    db: Session,
    conversation: Conversation,
    *,
    reason_code: str,
    requested_by: str | None,
    request_id: str,
    verification_level: str | None = None,
) -> tuple[ConversationHandoff, bool]:
    existing = _find_open_handoff(db, conversation.id)
    if existing is not None:
        return existing, True

    routing = _handoff_routing(reason_code)
    conversation.status = (
        ConversationStatus.blocked_for_safety
        if reason_code == "safety_review"
        else ConversationStatus.handed_off
    )
    handoff = ConversationHandoff(
        id=_new_id("handoff"),
        conversation_id=conversation.id,
        requested_by=requested_by,
        reason_code=reason_code,
        status=HandoffStatus.requested,
        priority=routing.priority,
        target_queue=routing.target_queue,
        sla_due_at=datetime.now(UTC) + timedelta(minutes=routing.sla_minutes),
        context_snapshot=_build_handoff_context(
            db,
            conversation,
            reason_code=reason_code,
            verification_level=verification_level,
        ),
    )
    db.add(handoff)
    db.flush()
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.handoff_created,
        request_id=request_id,
        payload={
            "handoff_id": handoff.id,
            "reason_code": handoff.reason_code,
            "status": handoff.status.value,
        },
    )
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.workflow_updated,
        request_id=request_id,
        payload={
            "conversation_status": conversation.status.value,
            "workflow_status": "handoff_requested",
        },
    )
    return handoff, False


def _route_identity_insufficient_to_handoff(
    db: Session,
    *,
    principal: CurrentPrincipal,
    conversation: Conversation,
    operation: str,
    key_hash: str,
    request_fingerprint: str,
    request_id: str,
) -> MessageSubmission:
    """Do not persist an unverified input; route the known session safely."""

    handoff, already_requested = _create_handoff(
        db,
        conversation,
        reason_code="identity_verification_required",
        requested_by=None,
        request_id=request_id,
        verification_level=principal.verification_level,
    )
    if already_requested:
        # The conversation lock makes this a defensive data-integrity guard,
        # not a normal request path.
        raise HTTPException(
            status_code=409, detail="Conversation already has a handoff"
        )
    notice = ConversationMessage(
        id=_new_id("message"),
        conversation_id=conversation.id,
        actor=ConversationActor.system,
        body=IDENTITY_HANDOFF_MESSAGE,
        response_kind=R0_HANDOFF_RESPONSE_KIND,
        request_id=request_id,
    )
    db.add(notice)
    db.flush()
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.message_created,
        request_id=request_id,
        payload={
            "message_id": notice.id,
            "actor": ConversationActor.system.value,
            "text": notice.body,
        },
    )
    response = ConversationMessageAccepted(
        conversation_id=conversation.id,
        message_id=notice.id,
        accepted_sequence=conversation.last_sequence,
        request_id=request_id,
    )
    _write_audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=None,
        action="conversation.identity_verification_handoff_created",
        conversation_id=conversation.id,
        request_id=request_id,
        before_summary=None,
        after_summary=_conversation_summary(
            conversation,
            handoff_id=handoff.id,
            reason_code=handoff.reason_code,
        ),
    )
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        replay = ConversationMessageAccepted.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
        return MessageSubmission(replay, should_generate_reply=False)
    return MessageSubmission(response, should_generate_reply=False)


def create_message(
    db: Session,
    principal: CurrentPrincipal,
    conversation_id: str,
    data: ConversationMessageCreate,
    idempotency_key: str,
) -> MessageSubmission:
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = _message_operation(conversation_id)
    existing = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        replay = ConversationMessageAccepted.model_validate(
            _replay_or_conflict(existing, request_fingerprint)
        )
        return MessageSubmission(replay, should_generate_reply=False)

    conversation = _get_owned_conversation_or_404(
        db, principal, conversation_id, for_update=True
    )
    if conversation.status is not ConversationStatus.active:
        raise HTTPException(
            status_code=409,
            detail="Conversation is not accepting automated messages",
        )
    if not has_verified_customer_context(principal):
        return _route_identity_insufficient_to_handoff(
            db,
            principal=principal,
            conversation=conversation,
            operation=operation,
            key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
        )
    _enforce_message_rate_limit(db, principal)
    customer_message = ConversationMessage(
        id=_new_id("message"),
        conversation_id=conversation.id,
        actor=ConversationActor.customer,
        body=data.text,
        generation_status="queued",
        request_id=request_id,
    )
    db.add(customer_message)
    db.flush()
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.message_created,
        request_id=request_id,
        payload={
            "message_id": customer_message.id,
            "actor": ConversationActor.customer.value,
            "text": customer_message.body,
        },
    )
    policy_reason = _policy_handoff_reason(data.text)
    if policy_reason is not None:
        customer_message.generation_status = "routed_to_handoff"
        handoff, _ = _create_handoff(
            db,
            conversation,
            reason_code=policy_reason,
            requested_by=None,
            request_id=request_id,
            verification_level=principal.verification_level,
        )
        _write_audit(
            db,
            tenant_id=principal.tenant_id,
            actor_id=None,
            action="conversation.policy_handoff_created",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation,
                handoff_id=handoff.id,
                reason_code=handoff.reason_code,
            ),
        )
    else:
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.workflow_updated,
            request_id=request_id,
            payload={
                "message_id": customer_message.id,
                "workflow_status": "answer_generation_started",
                "business_action_status": "not_started",
            },
        )
    response = ConversationMessageAccepted(
        conversation_id=conversation.id,
        message_id=customer_message.id,
        accepted_sequence=conversation.last_sequence,
        request_id=request_id,
    )
    _write_audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="conversation.message_received",
        conversation_id=conversation.id,
        request_id=request_id,
        before_summary=None,
        after_summary=_conversation_summary(
            conversation,
            message_id=customer_message.id,
            generation_status=customer_message.generation_status,
        ),
    )
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        replay = ConversationMessageAccepted.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
        return MessageSubmission(replay, should_generate_reply=False)
    return MessageSubmission(response, should_generate_reply=policy_reason is None)


def create_handoff(
    db: Session,
    principal: CurrentPrincipal,
    conversation_id: str,
    data: ConversationHandoffCreate,
    idempotency_key: str,
) -> ConversationHandoffRead:
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = _handoff_operation(conversation_id)
    existing = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return ConversationHandoffRead.model_validate(
            _replay_or_conflict(existing, request_fingerprint)
        )

    conversation = _get_owned_conversation_or_404(
        db, principal, conversation_id, for_update=True
    )
    handoff, already_requested = _create_handoff(
        db,
        conversation,
        reason_code=data.reason_code,
        requested_by=principal.user_id,
        request_id=request_id,
        verification_level=principal.verification_level,
    )
    response = ConversationHandoffRead.model_validate(handoff).model_copy(
        update={"already_requested": already_requested}
    )
    if not already_requested:
        _write_audit(
            db,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="conversation.handoff_requested",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation,
                handoff_id=handoff.id,
                reason_code=handoff.reason_code,
            ),
        )
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        return ConversationHandoffRead.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
    return response


def _require_handoff_staff(principal: CurrentPrincipal) -> None:
    if not can_view_queue(principal):
        raise HTTPException(
            status_code=403, detail="Agent or supervisor role is required"
        )


def _get_handoff_and_conversation_or_404(
    db: Session,
    principal: CurrentPrincipal,
    handoff_id: str,
    *,
    for_update: bool = False,
) -> tuple[ConversationHandoff, Conversation]:
    _require_handoff_staff(principal)
    stmt = (
        select(ConversationHandoff, Conversation)
        .join(Conversation, Conversation.id == ConversationHandoff.conversation_id)
        .where(
            ConversationHandoff.id == handoff_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).one_or_none()
    if row is None:
        # Keep a cross-tenant handoff identifier from becoming an existence
        # oracle for a staff member in another tenant.
        raise HTTPException(status_code=404, detail="Handoff not found")
    return row[0], row[1]


def _staff_can_view_handoff(
    principal: CurrentPrincipal, handoff: ConversationHandoff
) -> bool:
    # The demo has no dedicated safety-reviewer role.  Until one exists, only
    # a supervisor may see or handle this specialist queue; ordinary agents
    # must not claim a security escalation simply because it is tenant-local.
    if handoff.target_queue == "safety-review":
        return principal.role is UserRole.supervisor
    if principal.role is UserRole.supervisor:
        return True
    return handoff.status is HandoffStatus.requested or (
        handoff.assigned_agent_id == principal.user_id
    )


def _can_handle_handoff(
    principal: CurrentPrincipal, handoff: ConversationHandoff
) -> bool:
    if handoff.target_queue == "safety-review":
        return principal.role is UserRole.supervisor
    return principal.role is UserRole.agent


def list_handoffs(
    db: Session, principal: CurrentPrincipal
) -> list[HandoffQueueItemRead]:
    _require_handoff_staff(principal)
    stmt = (
        select(ConversationHandoff)
        .join(Conversation, Conversation.id == ConversationHandoff.conversation_id)
        .where(
            Conversation.tenant_id == principal.tenant_id,
            ConversationHandoff.status.in_(
                (HandoffStatus.requested, HandoffStatus.claimed)
            ),
        )
        .order_by(
            ConversationHandoff.sla_due_at.asc(),
            ConversationHandoff.created_at.asc(),
        )
    )
    if principal.role is UserRole.agent:
        stmt = stmt.where(
            or_(
                ConversationHandoff.status == HandoffStatus.requested,
                ConversationHandoff.assigned_agent_id == principal.user_id,
            ),
            ConversationHandoff.target_queue != "safety-review",
        )
    return [_handoff_queue_read(handoff) for handoff in db.scalars(stmt)]


def get_handoff(
    db: Session, principal: CurrentPrincipal, handoff_id: str
) -> HandoffDetailRead:
    handoff, _ = _get_handoff_and_conversation_or_404(db, principal, handoff_id)
    if not _staff_can_view_handoff(principal, handoff):
        raise HTTPException(status_code=404, detail="Handoff not found")
    return _handoff_detail_read(handoff)


def claim_handoff(
    db: Session,
    principal: CurrentPrincipal,
    handoff_id: str,
    data: HandoffClaimCreate,
    idempotency_key: str,
) -> HandoffDetailRead:
    _require_handoff_staff(principal)
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = _handoff_claim_operation(handoff_id)
    existing = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return HandoffDetailRead.model_validate(
            _replay_or_conflict(existing, request_fingerprint)
        )

    handoff, conversation = _get_handoff_and_conversation_or_404(
        db, principal, handoff_id, for_update=True
    )
    if not _can_handle_handoff(principal, handoff):
        raise HTTPException(status_code=404, detail="Handoff not found")
    if handoff.status is HandoffStatus.claimed:
        if handoff.assigned_agent_id != principal.user_id:
            raise HTTPException(status_code=409, detail="Handoff is already claimed")
    elif handoff.status is not HandoffStatus.requested:
        raise HTTPException(status_code=409, detail="Handoff is not available to claim")
    else:
        handoff.status = HandoffStatus.claimed
        handoff.assigned_agent_id = principal.user_id
        handoff.claimed_at = datetime.now(UTC)
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.handoff_updated,
            request_id=request_id,
            payload={"handoff_id": handoff.id, "status": handoff.status.value},
        )
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.workflow_updated,
            request_id=request_id,
            payload={
                "conversation_status": conversation.status.value,
                "workflow_status": "handoff_claimed",
            },
        )
        _write_audit(
            db,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="conversation.handoff_claimed",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation,
                handoff_id=handoff.id,
                handoff_status=handoff.status.value,
            ),
        )
    response = _handoff_detail_read(handoff)
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        return HandoffDetailRead.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
    return response


def resolve_handoff(
    db: Session,
    principal: CurrentPrincipal,
    handoff_id: str,
    data: HandoffResolutionCreate,
    idempotency_key: str,
) -> HandoffDetailRead:
    _require_handoff_staff(principal)
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = _handoff_resolution_operation(handoff_id)
    existing = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return HandoffDetailRead.model_validate(
            _replay_or_conflict(existing, request_fingerprint)
        )

    handoff, conversation = _get_handoff_and_conversation_or_404(
        db, principal, handoff_id, for_update=True
    )
    if not _can_handle_handoff(principal, handoff):
        raise HTTPException(status_code=404, detail="Handoff not found")
    if handoff.assigned_agent_id != principal.user_id:
        if handoff.status is HandoffStatus.requested:
            raise HTTPException(
                status_code=409, detail="Claim the handoff before resolving it"
            )
        # Do not reveal another agent's active handoff state.
        raise HTTPException(status_code=404, detail="Handoff not found")
    if handoff.status is not HandoffStatus.claimed:
        raise HTTPException(status_code=409, detail="Handoff is not active")

    handoff.completed_at = datetime.now(UTC)
    if data.action == "close_self_service":
        handoff.status = HandoffStatus.closed
        conversation.status = ConversationStatus.closed
        workflow_status = "handoff_closed"
    else:
        # This does not reactivate model automation.  `create_message` accepts
        # only active conversations, so a restored safe flow remains a
        # controlled manual follow-up state until a later human-channel step.
        handoff.status = HandoffStatus.safe_flow_restored
        conversation.status = ConversationStatus.awaiting_customer
        workflow_status = "handoff_safe_flow_restored"
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.handoff_updated,
        request_id=request_id,
        payload={
            "handoff_id": handoff.id,
            "status": handoff.status.value,
            "resolution": data.action,
        },
    )
    _append_event(
        db,
        conversation,
        event_type=ConversationEventType.workflow_updated,
        request_id=request_id,
        payload={
            "conversation_status": conversation.status.value,
            "workflow_status": workflow_status,
        },
    )
    _write_audit(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action=f"conversation.{workflow_status}",
        conversation_id=conversation.id,
        request_id=request_id,
        before_summary=None,
        after_summary=_conversation_summary(
            conversation,
            handoff_id=handoff.id,
            handoff_status=handoff.status.value,
        ),
    )
    response = _handoff_detail_read(handoff)
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        return HandoffDetailRead.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
    return response


def create_feedback(
    db: Session,
    principal: CurrentPrincipal,
    conversation_id: str,
    data: ConversationFeedbackCreate,
    idempotency_key: str,
) -> ConversationFeedbackRead:
    """Store one customer's latest explicit rating for a completed R0 answer."""

    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = _feedback_operation(conversation_id)
    existing_idempotency = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing_idempotency is not None:
        return ConversationFeedbackRead.model_validate(
            _replay_or_conflict(existing_idempotency, request_fingerprint)
        )

    customer_id = _require_customer(principal)
    conversation = _get_owned_conversation_or_404(
        db, principal, conversation_id, for_update=True
    )
    answer_message = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.id == data.answer_message_id,
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.actor == ConversationActor.assistant,
            ConversationMessage.response_kind == R0_SELF_SERVICE_RESPONSE_KIND,
        )
        .with_for_update()
    )
    if answer_message is None:
        # A client cannot attach quality telemetry to acknowledgements, system
        # notices or an answer from another conversation.
        raise HTTPException(status_code=404, detail="R0 answer not found")

    feedback = db.scalar(
        select(ConversationFeedback)
        .where(
            ConversationFeedback.conversation_id == conversation.id,
            ConversationFeedback.answer_message_id == answer_message.id,
            ConversationFeedback.customer_id == customer_id,
        )
        .with_for_update()
    )
    already_recorded = feedback is not None and feedback.feedback == data.feedback
    before_summary = None
    if feedback is None:
        feedback = ConversationFeedback(
            id=_new_id("conversation-feedback"),
            conversation_id=conversation.id,
            answer_message_id=answer_message.id,
            customer_id=customer_id,
            feedback=data.feedback,
            request_id=request_id,
        )
        db.add(feedback)
    elif not already_recorded:
        before_summary = _json_summary(
            {"answer_message_id": answer_message.id, "feedback": feedback.feedback}
        )
        feedback.feedback = data.feedback
        feedback.request_id = request_id

    response = ConversationFeedbackRead.model_validate(feedback).model_copy(
        update={"already_recorded": already_recorded}
    )
    if not already_recorded:
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.feedback_recorded,
            request_id=request_id,
            payload={
                "answer_message_id": answer_message.id,
                "feedback": data.feedback,
            },
        )
        _write_audit(
            db,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="conversation.r0_feedback_recorded",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=before_summary,
            after_summary=_json_summary(
                {"answer_message_id": answer_message.id, "feedback": data.feedback}
            ),
        )
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation.id,
        response_body=response.model_dump(mode="json"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=operation, key_hash=key_hash
        )
        if winner is None:
            raise
        return ConversationFeedbackRead.model_validate(
            _replay_or_conflict(winner, request_fingerprint)
        )
    return response


def _session_factory(bind: Engine | Connection) -> sessionmaker:
    return sessionmaker(bind=bind, autoflush=False, autocommit=False)


def _claim_generation(
    bind: Engine | Connection, conversation_id: str, customer_message_id: str
) -> bool:
    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or message is None
            or conversation.status is not ConversationStatus.active
            or message.generation_status != "queued"
        ):
            return False
        message.generation_status = "processing"
        db.commit()
        return True


def _generate_customer_reply() -> str:
    """Return the feature-disabled, non-policy acknowledgement."""

    return CONTROLLED_ACKNOWLEDGEMENT


def _principal_for_conversation(
    db: Session, conversation: Conversation
) -> tuple[CurrentPrincipal, Customer] | None:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == conversation.customer_id,
            Customer.tenant_id == conversation.tenant_id,
        )
    )
    if customer is None:
        return None
    user = db.scalar(
        select(User).where(
            User.id == customer.user_id,
            User.tenant_id == conversation.tenant_id,
            User.role == UserRole.customer,
        )
    )
    if user is None:
        return None
    return (
        CurrentPrincipal(
            user_id=user.id,
            tenant_id=conversation.tenant_id,
            role=UserRole.customer,
            verification_level=user.verification_level,
            customer_id=customer.id,
        ),
        customer,
    )


def _r0_answer_for_message(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
) -> KnowledgeAnswerResponse | None:
    """Run the public-only R0 path only while the current DB flag permits it."""

    with _session_factory(bind)() as db:
        conversation = db.get(Conversation, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage).where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            return None
        context = _principal_for_conversation(db, conversation)
        if context is None:
            raise RuntimeError("conversation customer principal is unavailable")
        principal, customer = context
        if not self_service.is_r0_self_service_enabled(db, principal):
            return None
        settings = get_settings()
        # Product and language are deployment-owned, not browser/model input;
        # the answer service independently binds region to the customer record.
        data = KnowledgeAnswerRequest(
            query=customer_message.body,
            product=settings.r0_self_service_product,
            region=customer.region,
            language=settings.r0_self_service_language,
        )
        return knowledge_answer.answer_knowledge(
            db,
            principal,
            data,
            audiences=("public",),
        )


def _create_assistant_message(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
    body: str,
    response_kind: str = CONTROLLED_ACK_RESPONSE_KIND,
) -> str | None:
    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            if (
                customer_message is not None
                and customer_message.generation_status == "processing"
            ):
                customer_message.generation_status = "cancelled"
                db.commit()
            return None
        assistant_message = ConversationMessage(
            id=_new_id("message"),
            conversation_id=conversation_id,
            actor=ConversationActor.assistant,
            body=body,
            reply_to_message_id=customer_message_id,
            response_kind=response_kind,
            request_id=request_id,
        )
        db.add(assistant_message)
        db.commit()
        return assistant_message.id


def _answer_deltas(text: str) -> list[str]:
    # Keep chunks human-readable and deterministic for the fake/offline path.
    size = 18
    return [text[index : index + size] for index in range(0, len(text), size)]


def _append_answer_delta(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    assistant_message_id: str,
    request_id: str,
    delta: str,
) -> bool:
    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            if (
                customer_message is not None
                and customer_message.generation_status == "processing"
            ):
                customer_message.generation_status = "cancelled"
                db.commit()
            return False
        # Deliberately only message identity plus text delta. Citations and any
        # executable next step are reserved for answer.completed.
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.answer_delta,
            request_id=request_id,
            payload={"message_id": assistant_message_id, "delta": delta},
        )
        db.commit()
        return True


def _complete_answer(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    assistant_message_id: str,
    request_id: str,
    body: str,
    citations: list[dict[str, object]] | None = None,
    response_kind: str = CONTROLLED_ACK_RESPONSE_KIND,
) -> bool:
    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            if (
                customer_message is not None
                and customer_message.generation_status == "processing"
            ):
                customer_message.generation_status = "cancelled"
                db.commit()
            return False
        customer_message.generation_status = "completed"
        allowed_actions: list[dict[str, str]] = [
            {"type": "handoff.request", "label": "转人工客服"}
        ]
        if response_kind == R0_SELF_SERVICE_RESPONSE_KIND:
            allowed_actions.extend(
                [
                    {"type": "feedback.helpful", "label": "有帮助"},
                    {"type": "feedback.unhelpful", "label": "没有帮助"},
                ]
            )
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.answer_completed,
            request_id=request_id,
            payload={
                "message_id": assistant_message_id,
                "text": body,
                "citations": citations or [],
                "answer_mode": response_kind,
                "allowed_actions": allowed_actions,
            },
        )
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.workflow_updated,
            request_id=request_id,
            payload={
                "message_id": customer_message_id,
                "workflow_status": "answer_generation_completed",
                "business_action_status": "not_started",
            },
        )
        _write_audit(
            db,
            tenant_id=conversation.tenant_id,
            actor_id=None,
            action="conversation.answer_completed",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation, message_id=assistant_message_id
            ),
        )
        db.commit()
        return True


def _route_r0_to_handoff(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
    reason_code: str,
) -> bool:
    """Persist a customer-safe notice and handoff for an unsupported R0 answer."""

    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            if (
                customer_message is not None
                and customer_message.generation_status == "processing"
            ):
                customer_message.generation_status = "cancelled"
                db.commit()
            return False
        customer_message.generation_status = "routed_to_handoff"
        notice = ConversationMessage(
            id=_new_id("message"),
            conversation_id=conversation.id,
            actor=ConversationActor.system,
            body=R0_HANDOFF_MESSAGE,
            reply_to_message_id=customer_message.id,
            response_kind=R0_HANDOFF_RESPONSE_KIND,
            request_id=request_id,
        )
        db.add(notice)
        db.flush()
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.message_created,
            request_id=request_id,
            payload={
                "message_id": notice.id,
                "actor": ConversationActor.system.value,
                "text": notice.body,
            },
        )
        handoff, _ = _create_handoff(
            db,
            conversation,
            reason_code=reason_code,
            requested_by=None,
            request_id=request_id,
        )
        _write_audit(
            db,
            tenant_id=conversation.tenant_id,
            actor_id=None,
            action="conversation.r0_handoff_created",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation,
                handoff_id=handoff.id,
                reason_code=handoff.reason_code,
                message_id=customer_message.id,
            ),
        )
        db.commit()
        return True


def _deliver_reply(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
    body: str,
    response_kind: str,
    citations: list[dict[str, object]] | None = None,
) -> bool:
    assistant_message_id = _create_assistant_message(
        bind,
        conversation_id=conversation_id,
        customer_message_id=customer_message_id,
        request_id=request_id,
        body=body,
        response_kind=response_kind,
    )
    if assistant_message_id is None:
        return False
    for delta in _answer_deltas(body):
        if not _append_answer_delta(
            bind,
            conversation_id=conversation_id,
            customer_message_id=customer_message_id,
            assistant_message_id=assistant_message_id,
            request_id=request_id,
            delta=delta,
        ):
            return False
    return _complete_answer(
        bind,
        conversation_id=conversation_id,
        customer_message_id=customer_message_id,
        assistant_message_id=assistant_message_id,
        request_id=request_id,
        body=body,
        citations=citations,
        response_kind=response_kind,
    )


def _deliver_r0_reply(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
    body: str,
    citations: list[dict[str, object]],
) -> bool:
    """Atomically expose a completed R0 answer while holding the flag row lock.

    R0 intentionally does not emit text deltas: a tenant disable that wins
    before this transaction locks the flag must prevent all model-derived text
    from reaching the browser, not only the final event.
    """

    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status != "processing"
        ):
            if (
                customer_message is not None
                and customer_message.generation_status == "processing"
            ):
                customer_message.generation_status = "cancelled"
                db.commit()
            return False
        context = _principal_for_conversation(db, conversation)
        if context is None or not self_service.is_r0_self_service_enabled(
            db, context[0], for_update=True
        ):
            return False

        assistant_message = ConversationMessage(
            id=_new_id("message"),
            conversation_id=conversation_id,
            actor=ConversationActor.assistant,
            body=body,
            reply_to_message_id=customer_message_id,
            response_kind=R0_SELF_SERVICE_RESPONSE_KIND,
            request_id=request_id,
        )
        db.add(assistant_message)
        db.flush()
        customer_message.generation_status = "completed"
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.answer_completed,
            request_id=request_id,
            payload={
                "message_id": assistant_message.id,
                "text": body,
                "citations": citations,
                "answer_mode": R0_SELF_SERVICE_RESPONSE_KIND,
                "allowed_actions": [
                    {"type": "handoff.request", "label": "转人工客服"},
                    {"type": "feedback.helpful", "label": "有帮助"},
                    {"type": "feedback.unhelpful", "label": "没有帮助"},
                ],
            },
        )
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.workflow_updated,
            request_id=request_id,
            payload={
                "message_id": customer_message_id,
                "workflow_status": "answer_generation_completed",
                "business_action_status": "not_started",
            },
        )
        _write_audit(
            db,
            tenant_id=conversation.tenant_id,
            actor_id=None,
            action="conversation.answer_completed",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation, message_id=assistant_message.id
            ),
        )
        db.commit()
        return True


def _record_generation_failure(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
) -> None:
    with _session_factory(bind)() as db:
        conversation = _get_conversation_for_update(db, conversation_id)
        customer_message = db.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == customer_message_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or customer_message is None
            or conversation.status is not ConversationStatus.active
            or customer_message.generation_status not in {"queued", "processing"}
        ):
            return
        customer_message.generation_status = "failed"
        # Background sessions disable autoflush. Flush the current failure so
        # the deterministic repeat threshold includes this message.
        db.flush()
        failed_count = db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.actor == ConversationActor.customer,
                ConversationMessage.generation_status == "failed",
            )
        )
        should_handoff = (failed_count or 0) >= 2
        _append_event(
            db,
            conversation,
            event_type=ConversationEventType.error,
            request_id=request_id,
            payload={
                "message_id": customer_message_id,
                "code": "answer_generation_failed",
                "message": (
                    REPEATED_GENERATION_FAILURE_HANDOFF_MESSAGE
                    if should_handoff
                    else GENERATION_FAILURE_MESSAGE
                ),
                "retryable": not should_handoff,
                "allowed_actions": (
                    []
                    if should_handoff
                    else [{"type": "handoff.request", "label": "转人工客服"}]
                ),
            },
        )
        handoff: ConversationHandoff | None = None
        if should_handoff:
            handoff, _ = _create_handoff(
                db,
                conversation,
                reason_code="system_generation_repeated_failure",
                requested_by=None,
                request_id=request_id,
            )
        else:
            _append_event(
                db,
                conversation,
                event_type=ConversationEventType.workflow_updated,
                request_id=request_id,
                payload={
                    "message_id": customer_message_id,
                    "workflow_status": "answer_generation_failed",
                    "business_action_status": "not_started",
                },
            )
        _write_audit(
            db,
            tenant_id=conversation.tenant_id,
            actor_id=None,
            action="conversation.answer_generation_failed",
            conversation_id=conversation.id,
            request_id=request_id,
            before_summary=None,
            after_summary=_conversation_summary(
                conversation,
                message_id=customer_message_id,
                handoff_id=handoff.id if handoff is not None else None,
            ),
        )
        db.commit()


def generate_controlled_reply(
    bind: Engine | Connection,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
    traceparent: str | None = None,
) -> None:
    """Resume the accepted message under its originating W3C trace."""

    with observe_background_task(
        "conversation.reply_generation",
        traceparent=traceparent,
        request_id=request_id,
        attributes={"customer_care.worker.name": "fastapi-background-task"},
    ):
        # A BackgroundTask has no request principal. Rehydrate only the tenant
        # from the owned conversation so the local trace viewer remains tenant
        # scoped; never copy a customer/user ID into telemetry.
        with _session_factory(bind)() as db:
            conversation = db.get(Conversation, conversation_id)
            if conversation is None:
                set_span_attributes(
                    {"customer_care.workflow.status": "conversation_not_found"}
                )
                return
            bind_principal(
                tenant_id=conversation.tenant_id,
                role="conversation_worker",
            )
        bind_resources(conversation_id=conversation_id)
        set_span_attributes({"customer_care.workflow.status": "answer_generation"})
        _generate_controlled_reply(
            bind,
            conversation_id,
            customer_message_id,
            request_id,
        )


def _generate_controlled_reply(
    bind: Engine | Connection,
    conversation_id: str,
    customer_message_id: str,
    request_id: str,
) -> None:
    """Background continuation for a 202 message submission.

    Every lifecycle transition is committed separately. A browser can therefore
    reconnect after a transport break without requiring a live in-memory task.
    """

    if not _claim_generation(bind, conversation_id, customer_message_id):
        return
    r0_attempted = False
    try:
        r0_answer = _r0_answer_for_message(
            bind,
            conversation_id=conversation_id,
            customer_message_id=customer_message_id,
        )
        if r0_answer is not None:
            r0_attempted = True
            if r0_answer.needs_handoff:
                _route_r0_to_handoff(
                    bind,
                    conversation_id=conversation_id,
                    customer_message_id=customer_message_id,
                    request_id=request_id,
                    reason_code="r0_evidence_unavailable",
                )
                return
            if _deliver_r0_reply(
                bind,
                conversation_id=conversation_id,
                customer_message_id=customer_message_id,
                request_id=request_id,
                body=r0_answer.answer,
                citations=[
                    citation.model_dump(mode="json") for citation in r0_answer.citations
                ],
            ):
                return
        _deliver_reply(
            bind,
            conversation_id=conversation_id,
            customer_message_id=customer_message_id,
            request_id=request_id,
            body=_generate_customer_reply(),
            response_kind=CONTROLLED_ACK_RESPONSE_KIND,
        )
    except Exception:
        # Do not expose provider/database exception details in the event stream.
        if r0_attempted and _route_r0_to_handoff(
            bind,
            conversation_id=conversation_id,
            customer_message_id=customer_message_id,
            request_id=request_id,
            reason_code="r0_generation_failed",
        ):
            return
        _record_generation_failure(
            bind,
            conversation_id=conversation_id,
            customer_message_id=customer_message_id,
            request_id=request_id,
        )


def resolve_after_sequence(
    *, after_sequence: int | None, last_event_id: str | None
) -> int:
    header_sequence: int | None = None
    if last_event_id is not None:
        value = last_event_id.strip()
        if not value.isascii() or not value.isdecimal():
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
        header_sequence = int(value)
    if (
        after_sequence is not None
        and header_sequence is not None
        and after_sequence != header_sequence
    ):
        raise HTTPException(
            status_code=400,
            detail="after_sequence conflicts with Last-Event-ID",
        )
    return header_sequence if header_sequence is not None else (after_sequence or 0)


def assert_can_stream(
    db: Session, principal: CurrentPrincipal, conversation_id: str
) -> None:
    _get_owned_conversation_or_404(db, principal, conversation_id)


def _events_after(
    bind: Engine | Connection, conversation_id: str, after_sequence: int
) -> list[ConversationEvent]:
    with _session_factory(bind)() as db:
        return list(
            db.scalars(
                select(ConversationEvent)
                .where(
                    ConversationEvent.conversation_id == conversation_id,
                    ConversationEvent.sequence > after_sequence,
                )
                .order_by(ConversationEvent.sequence)
            )
        )


def _sse_frame(event: ConversationEvent) -> str:
    envelope = ConversationEventEnvelope(
        event_id=event.event_id,
        sequence=event.sequence,
        type=event.event_type,
        conversation_id=event.conversation_id,
        request_id=event.request_id,
        payload=event.payload,
    )
    data = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
    # `id` intentionally stores the monotonic sequence, not the opaque UUID.
    # Browsers then send it as Last-Event-ID on a standards-compliant reconnect.
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def stream_events(
    bind: Engine | Connection,
    *,
    conversation_id: str,
    after_sequence: int,
    follow: bool,
) -> AsyncIterator[str]:
    """Yield an ordered durable suffix, optionally waiting for live events.

    The connection intentionally rotates after a bounded idle period. Clients
    preserve the last confirmed sequence and reconnect, which bounds occupied
    HTTP/DB resources while retaining standard SSE recovery semantics.
    """

    cursor = after_sequence
    deadline = time.monotonic() + SSE_CONNECTION_LIFETIME_SECONDS
    while True:
        events = await asyncio.to_thread(_events_after, bind, conversation_id, cursor)
        if events:
            for event in events:
                cursor = event.sequence
                yield _sse_frame(event)
            if not follow:
                return
            continue
        if not follow:
            return
        if time.monotonic() >= deadline:
            yield ": keep-alive\n\n"
            return
        await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
