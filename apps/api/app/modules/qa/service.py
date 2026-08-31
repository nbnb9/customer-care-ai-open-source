"""Supervisor QA sampling, remediation records and regression traceability."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.conversations import (
    Conversation,
    ConversationActor,
    ConversationEvent,
    ConversationEventType,
    ConversationFeedback,
    ConversationHandoff,
    ConversationMessage,
)
from app.models.identity import User, UserRole
from app.models.qa import (
    QAIdempotencyRecord,
    QARegressionStatus,
    QAReviewIssue,
    QAReviewIssueEvent,
    QAReviewStatus,
    QASeverity,
)
from app.models.self_service import R0SelfServiceFeatureFlag
from app.modules.identity.principal import CurrentPrincipal
from app.shared.config import get_settings
from app.shared.middleware import request_id_var
from app.shared.redaction import redact_direct_identifiers

from .catalog import (
    QARegressionCatalogEntry,
    QARegressionCatalogError,
    get_regression_case,
    load_regression_catalog,
)
from .schemas import (
    QACitationRead,
    QAConversationFeedbackSummaryRead,
    QAConversationFinalResultRead,
    QAConversationRouteRead,
    QAConversationSampleListItemRead,
    QAConversationSampleMessageRead,
    QAConversationSampleRead,
    QADocumentVersionRead,
    QAFeatureFlagDecisionRead,
    QAIssueCreate,
    QAIssueEventRead,
    QAIssueUpdate,
    QAOwnerRead,
    QARegressionCatalogEntryRead,
    QARegressionRead,
    QAReviewIssueDetailRead,
    QAReviewIssueRead,
    QAToolSummaryRead,
)

CREATE_ISSUE_OPERATION = "POST:/v1/admin/qa/issues"

_ALLOWED_STATUS_TRANSITIONS: dict[QAReviewStatus, frozenset[QAReviewStatus]] = {
    QAReviewStatus.open: frozenset(
        {QAReviewStatus.in_remediation, QAReviewStatus.closed}
    ),
    QAReviewStatus.in_remediation: frozenset(
        {QAReviewStatus.open, QAReviewStatus.verification_pending}
    ),
    QAReviewStatus.verification_pending: frozenset(
        {QAReviewStatus.in_remediation, QAReviewStatus.verified}
    ),
    QAReviewStatus.verified: frozenset({QAReviewStatus.closed}),
    QAReviewStatus.closed: frozenset(),
}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _request_id() -> str:
    value = request_id_var.get()
    return value if value != "-" else _new_id("request")


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_supervisor(principal: CurrentPrincipal) -> None:
    if principal.role is not UserRole.supervisor:
        raise HTTPException(status_code=403, detail="Supervisor role is required")


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Idempotency-Key must not be blank")
    return normalized


def _fingerprint(data: object) -> str:
    if hasattr(data, "model_dump"):
        value = data.model_dump(mode="json")  # type: ignore[union-attr]
    else:
        value = data
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_idempotency_record(
    db: Session,
    principal: CurrentPrincipal,
    *,
    operation: str,
    key_hash: str,
) -> QAIdempotencyRecord | None:
    return db.scalar(
        select(QAIdempotencyRecord).where(
            QAIdempotencyRecord.tenant_id == principal.tenant_id,
            QAIdempotencyRecord.principal_id == principal.user_id,
            QAIdempotencyRecord.operation == operation,
            QAIdempotencyRecord.idempotency_key_hash == key_hash,
        )
    )


def _assert_replayable(record: QAIdempotencyRecord, request_fingerprint: str) -> None:
    if record.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )


def _store_idempotency_record(
    db: Session,
    principal: CurrentPrincipal,
    *,
    operation: str,
    key_hash: str,
    request_fingerprint: str,
    issue_id: str,
) -> None:
    db.add(
        QAIdempotencyRecord(
            id=_new_id("qa-idempotency"),
            tenant_id=principal.tenant_id,
            principal_id=principal.user_id,
            operation=operation,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            issue_id=issue_id,
            # Do not duplicate a redacted conversation sample in the
            # idempotency store.  The durable issue reference is sufficient to
            # rebuild the current, tenant-scoped response on a retry.
            response_body={"issue_id": issue_id},
        )
    )


def _issue_summary(issue: QAReviewIssue) -> str:
    """Audit-safe summary with no free-form diagnosis or source transcript."""

    return _json_summary(
        {
            "category": issue.category.value,
            "severity": issue.severity.value,
            "status": issue.status.value,
            "owner_id": issue.owner_id,
            "root_cause": issue.root_cause.value,
            "remediation_type": issue.remediation_type.value,
            "regression_case_id": issue.regression_case_id,
            "evaluation_suite": issue.evaluation_suite,
            "evaluation_case_id": issue.evaluation_case_id,
            "regression_status": issue.regression_status.value,
            "regression_evidence_present": issue.regression_evidence_ref is not None,
            "feature_flag_paused": issue.feature_flag_paused,
        }
    )


def _append_event(
    db: Session,
    issue: QAReviewIssue,
    *,
    actor_id: str,
    event_type: str,
    request_id: str,
    summary: dict[str, object],
) -> QAReviewIssueEvent:
    issue.last_event_sequence += 1
    event = QAReviewIssueEvent(
        id=_new_id("qa-event"),
        issue_id=issue.id,
        sequence=issue.last_event_sequence,
        actor_id=actor_id,
        event_type=event_type,
        summary=summary,
        request_id=request_id,
    )
    db.add(event)
    return event


def _write_audit(
    db: Session,
    *,
    principal: CurrentPrincipal,
    action: str,
    issue: QAReviewIssue,
    request_id: str,
    before_summary: str | None,
) -> None:
    db.add(
        AuditEvent(
            id=_new_id("audit"),
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action=action,
            resource_type="qa_review_issue",
            resource_id=issue.id,
            before_summary=before_summary,
            after_summary=_issue_summary(issue),
            request_id=request_id,
        )
    )


def _get_conversation_or_404(
    db: Session, principal: CurrentPrincipal, conversation_id: str
) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    )
    if conversation is None:
        # Keep a cross-tenant identifier from becoming an oracle.
        raise HTTPException(status_code=404, detail="QA sample not found")
    return conversation


def _latest_answer(db: Session, conversation_id: str) -> ConversationMessage | None:
    return db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.actor == ConversationActor.assistant,
        )
        .order_by(
            ConversationMessage.created_at.desc(),
            ConversationMessage.id.desc(),
        )
        .limit(1)
    )


def _sample_messages(db: Session, conversation_id: str) -> list[ConversationMessage]:
    """Return the latest bounded window in chronological order for QA review."""

    messages = list(
        db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(
                ConversationMessage.created_at.desc(), ConversationMessage.id.desc()
            )
            .limit(20)
        )
    )
    messages.reverse()
    return messages


def _answer_completion_event(
    db: Session, conversation_id: str, answer_message_id: str
) -> ConversationEvent | None:
    events = db.scalars(
        select(ConversationEvent)
        .where(
            ConversationEvent.conversation_id == conversation_id,
            ConversationEvent.event_type
            == ConversationEventType.answer_completed.value,
        )
        .order_by(ConversationEvent.sequence.desc())
    )
    for event in events:
        if event.payload.get("message_id") == answer_message_id:
            return event
    return None


def _safe_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    return redact_direct_identifiers(value, max_chars=max_chars)


def _safe_citations(payload: dict[str, object]) -> list[QACitationRead]:
    raw = payload.get("citations")
    if not isinstance(raw, list):
        return []
    citations: list[QACitationRead] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        document_key = _safe_text(item.get("document_key"), max_chars=255)
        document_version = _safe_text(item.get("document_version"), max_chars=32)
        title = _safe_text(item.get("title"), max_chars=255)
        excerpt = _safe_text(item.get("excerpt"), max_chars=500)
        source_locator = _safe_text(item.get("source_locator"), max_chars=500)
        if (
            document_key is None
            or document_version is None
            or title is None
            or excerpt is None
            or source_locator is None
        ):
            continue
        citations.append(
            QACitationRead(
                document_key=document_key,
                document_version=document_version,
                title=title,
                excerpt=excerpt,
                source_locator=source_locator,
            )
        )
    return citations


def _tool_summaries(
    db: Session, conversation_id: str
) -> tuple[list[QAToolSummaryRead], ConversationHandoff | None]:
    handoff = db.scalar(
        select(ConversationHandoff)
        .where(ConversationHandoff.conversation_id == conversation_id)
        .order_by(
            ConversationHandoff.created_at.desc(),
            ConversationHandoff.id.desc(),
        )
        .limit(1)
    )
    if handoff is None:
        return [], None
    raw = handoff.context_snapshot.get("tool_summaries")
    if not isinstance(raw, list):
        return [], handoff
    summaries: list[QAToolSummaryRead] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        tool_name = _safe_text(item.get("tool_name"), max_chars=100)
        result_status = _safe_text(item.get("result_status"), max_chars=100)
        if tool_name is not None and result_status is not None:
            summaries.append(
                QAToolSummaryRead(tool_name=tool_name, result_status=result_status)
            )
    return summaries, handoff


def _feedback_summary(
    db: Session, answer_message_id: str
) -> QAConversationFeedbackSummaryRead:
    values = list(
        db.scalars(
            select(ConversationFeedback.feedback).where(
                ConversationFeedback.answer_message_id == answer_message_id
            )
        )
    )
    return QAConversationFeedbackSummaryRead(
        helpful_count=sum(value == "helpful" for value in values),
        unhelpful_count=sum(value == "unhelpful" for value in values),
    )


def _build_sample(
    db: Session, conversation: Conversation, answer: ConversationMessage
) -> QAConversationSampleRead:
    completion = _answer_completion_event(db, conversation.id, answer.id)
    payload = completion.payload if completion is not None else {}
    citations = _safe_citations(payload)
    answer_mode = _safe_text(payload.get("answer_mode"), max_chars=64)
    answer_mode = answer_mode or answer.response_kind
    tool_summaries, handoff = _tool_summaries(db, conversation.id)

    messages = [
        QAConversationSampleMessageRead(
            id=message.id,
            actor=message.actor,
            text=redact_direct_identifiers(message.body, max_chars=500),
            response_kind=message.response_kind,
            created_at=message.created_at,
        )
        for message in _sample_messages(db, conversation.id)
    ]
    document_versions = [
        QADocumentVersionRead(
            document_key=document_key,
            document_version=document_version,
        )
        for document_key, document_version in dict.fromkeys(
            (item.document_key, item.document_version) for item in citations
        )
    ]
    return QAConversationSampleRead(
        conversation_id=conversation.id,
        messages=messages,
        route=QAConversationRouteRead(
            conversation_status=conversation.status,
            answer_mode=answer_mode,
            handoff_reason_code=handoff.reason_code if handoff is not None else None,
            handoff_status=handoff.status if handoff is not None else None,
        ),
        citations=citations,
        tool_summaries=tool_summaries,
        final_result=QAConversationFinalResultRead(
            message_id=answer.id,
            text=redact_direct_identifiers(answer.body, max_chars=1_000),
            answer_mode=answer_mode,
            created_at=answer.created_at,
        ),
        source_versions=document_versions,
        feedback=_feedback_summary(db, answer.id),
    )


def list_samples(
    db: Session, principal: CurrentPrincipal, *, limit: int
) -> list[QAConversationSampleListItemRead]:
    _require_supervisor(principal)
    candidates = db.scalars(
        select(Conversation)
        .where(Conversation.tenant_id == principal.tenant_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(min(limit * 4, 100))
    )
    samples: list[QAConversationSampleListItemRead] = []
    for conversation in candidates:
        answer = _latest_answer(db, conversation.id)
        if answer is None:
            continue
        completion = _answer_completion_event(db, conversation.id, answer.id)
        payload = completion.payload if completion is not None else {}
        citations = _safe_citations(payload)
        _, handoff = _tool_summaries(db, conversation.id)
        samples.append(
            QAConversationSampleListItemRead(
                conversation_id=conversation.id,
                created_at=conversation.created_at,
                last_answer_message_id=answer.id,
                answer_mode=_safe_text(payload.get("answer_mode"), max_chars=64)
                or answer.response_kind,
                final_result_preview=redact_direct_identifiers(
                    answer.body, max_chars=240
                ),
                citation_count=len(citations),
                handoff_reason_code=handoff.reason_code
                if handoff is not None
                else None,
                handoff_status=handoff.status if handoff is not None else None,
            )
        )
        if len(samples) == limit:
            break
    return samples


def get_sample(
    db: Session, principal: CurrentPrincipal, conversation_id: str
) -> QAConversationSampleRead:
    _require_supervisor(principal)
    conversation = _get_conversation_or_404(db, principal, conversation_id)
    answer = _latest_answer(db, conversation.id)
    if answer is None:
        raise HTTPException(status_code=404, detail="QA sample not found")
    return _build_sample(db, conversation, answer)


def _source_snapshot(
    db: Session,
    conversation: Conversation,
    answer: ConversationMessage,
    sample: QAConversationSampleRead,
) -> dict[str, object]:
    """Store a stable, non-transcript pointer to the sampled source evidence."""

    message_digests = [
        {
            "message_id": message.id,
            "actor": message.actor.value,
            "response_kind": message.response_kind,
            "body_sha256": hashlib.sha256(message.body.encode("utf-8")).hexdigest(),
        }
        for message in _sample_messages(db, conversation.id)
    ]
    return {
        "schema_version": "qa-source-snapshot-v1",
        "conversation_id": conversation.id,
        "message_digests": message_digests,
        "final_result": {
            "message_id": sample.final_result.message_id,
            "answer_mode": sample.final_result.answer_mode,
            "body_sha256": hashlib.sha256(answer.body.encode("utf-8")).hexdigest(),
        },
        "route": sample.route.model_dump(mode="json"),
        "citations": [
            {
                "document_key": citation.document_key,
                "document_version": citation.document_version,
                "source_locator": citation.source_locator,
                "excerpt_sha256": hashlib.sha256(
                    citation.excerpt.encode("utf-8")
                ).hexdigest(),
            }
            for citation in sample.citations
        ],
        "tool_summaries": [
            item.model_dump(mode="json") for item in sample.tool_summaries
        ],
        "feedback": sample.feedback.model_dump(mode="json"),
    }


def list_owners(db: Session, principal: CurrentPrincipal) -> list[QAOwnerRead]:
    _require_supervisor(principal)
    rows = db.scalars(
        select(User)
        .where(
            User.tenant_id == principal.tenant_id,
            User.role.in_(
                (UserRole.agent, UserRole.supervisor, UserRole.knowledge_operator)
            ),
        )
        .order_by(User.display_name.asc(), User.id.asc())
    )
    return [
        QAOwnerRead(
            id=user.id,
            display_name=user.display_name,
            role=cast(
                Literal["agent", "supervisor", "knowledge_operator"], user.role.value
            ),
        )
        for user in rows
    ]


def list_regression_cases(
    principal: CurrentPrincipal,
) -> list[QARegressionCatalogEntryRead]:
    _require_supervisor(principal)
    try:
        catalog = load_regression_catalog()
    except QARegressionCatalogError as exc:
        raise HTTPException(
            status_code=500, detail="QA regression catalog unavailable"
        ) from exc
    return [
        QARegressionCatalogEntryRead.model_validate(item.model_dump(mode="json"))
        for item in catalog.cases
    ]


def _get_owner_or_404(db: Session, principal: CurrentPrincipal, owner_id: str) -> User:
    owner = db.scalar(
        select(User).where(
            User.id == owner_id,
            User.tenant_id == principal.tenant_id,
            User.role.in_(
                (UserRole.agent, UserRole.supervisor, UserRole.knowledge_operator)
            ),
        )
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="QA owner not found")
    return owner


def _resolve_regression_case(
    data: QAIssueCreate,
) -> QARegressionCatalogEntry:
    try:
        regression = get_regression_case(data.regression_case_id)
    except QARegressionCatalogError as exc:
        raise HTTPException(
            status_code=422, detail="Unknown QA regression case"
        ) from exc
    if regression.category is not data.category:
        raise HTTPException(
            status_code=422,
            detail="QA regression case category does not match the issue",
        )
    if regression.remediation_type is not data.remediation_type:
        raise HTTPException(
            status_code=422,
            detail="QA regression case remediation type does not match the issue",
        )
    return regression


def _feature_flag_snapshot(
    db: Session,
    principal: CurrentPrincipal,
    *,
    pause_requested: bool,
    request_id: str,
) -> dict[str, object]:
    """Record and, when explicitly requested, close the tenant R0 path."""

    flag = db.scalar(
        select(R0SelfServiceFeatureFlag)
        .where(R0SelfServiceFeatureFlag.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    master_enabled = get_settings().r0_self_service_master_enabled
    before_enabled = flag.enabled if flag is not None else False
    before_has_targets = (
        (flag.allow_all_customers or bool(flag.customer_ids))
        if flag is not None
        else False
    )
    if pause_requested:
        if flag is None:
            flag = R0SelfServiceFeatureFlag(
                tenant_id=principal.tenant_id,
                enabled=False,
                allow_all_customers=False,
                customer_ids=[],
                updated_by=principal.user_id,
            )
            db.add(flag)
        else:
            flag.enabled = False
            flag.updated_by = principal.user_id
    after_enabled = flag.enabled if flag is not None else False
    after_has_targets = (
        (flag.allow_all_customers or bool(flag.customer_ids))
        if flag is not None
        else False
    )
    if pause_requested:
        db.add(
            AuditEvent(
                id=_new_id("audit"),
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                action="qa.r0_feature_flag_paused",
                resource_type="r0_self_service_feature_flag",
                resource_id=principal.tenant_id,
                before_summary=_json_summary(
                    {
                        "enabled": before_enabled,
                        "has_targets": before_has_targets,
                    }
                ),
                after_summary=_json_summary(
                    {
                        "enabled": after_enabled,
                        "has_targets": after_has_targets,
                    }
                ),
                request_id=request_id,
            )
        )
    return {
        "r0_feature_flag_exists": flag is not None,
        "master_enabled": master_enabled,
        "enabled_before": before_enabled,
        "enabled_after": after_enabled,
        "effective_before": bool(
            master_enabled and before_enabled and before_has_targets
        ),
        "effective_after": bool(master_enabled and after_enabled and after_has_targets),
    }


def _issue_read(issue: QAReviewIssue) -> QAReviewIssueRead:
    return QAReviewIssueRead(
        id=issue.id,
        source_conversation_id=issue.source_conversation_id,
        category=issue.category,
        severity=issue.severity,
        status=issue.status,
        owner_id=issue.owner_id,
        root_cause=issue.root_cause,
        problem_summary=issue.problem_summary,
        remediation_type=issue.remediation_type,
        remediation_summary=issue.remediation_summary,
        feature_flag=QAFeatureFlagDecisionRead(
            paused=issue.feature_flag_paused,
            snapshot=dict(issue.feature_flag_snapshot),
        ),
        regression=QARegressionRead(
            catalog_case_id=issue.regression_case_id,
            evaluation_suite=issue.evaluation_suite,  # type: ignore[arg-type]
            evaluation_case_id=issue.evaluation_case_id,
            status=issue.regression_status,
            evidence_ref=issue.regression_evidence_ref,
        ),
        reported_by=issue.reported_by,
        verified_by=issue.verified_by,
        verified_at=issue.verified_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def _issue_detail_read(
    db: Session, principal: CurrentPrincipal, issue: QAReviewIssue
) -> QAReviewIssueDetailRead:
    base = _issue_read(issue)
    source = get_sample(db, principal, issue.source_conversation_id)
    events = [
        QAIssueEventRead(
            id=event.id,
            sequence=event.sequence,
            event_type=event.event_type,
            actor_id=event.actor_id,
            summary=dict(event.summary),
            request_id=event.request_id,
            created_at=event.created_at,
        )
        for event in db.scalars(
            select(QAReviewIssueEvent)
            .where(QAReviewIssueEvent.issue_id == issue.id)
            .order_by(QAReviewIssueEvent.sequence.asc())
        )
    ]
    return QAReviewIssueDetailRead(
        **base.model_dump(),
        source=source,
        source_evidence=dict(issue.source_snapshot),
        events=events,
    )


def _get_issue_or_404(
    db: Session,
    principal: CurrentPrincipal,
    issue_id: str,
    *,
    for_update: bool = False,
) -> QAReviewIssue:
    _require_supervisor(principal)
    stmt = select(QAReviewIssue).where(
        QAReviewIssue.id == issue_id,
        QAReviewIssue.tenant_id == principal.tenant_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    issue = db.scalar(stmt)
    if issue is None:
        raise HTTPException(status_code=404, detail="QA issue not found")
    return issue


def _replay_issue_detail(
    db: Session,
    principal: CurrentPrincipal,
    record: QAIdempotencyRecord,
    request_fingerprint: str,
) -> QAReviewIssueDetailRead:
    _assert_replayable(record, request_fingerprint)
    issue = _get_issue_or_404(db, principal, record.issue_id)
    return _issue_detail_read(db, principal, issue)


def list_issues(db: Session, principal: CurrentPrincipal) -> list[QAReviewIssueRead]:
    _require_supervisor(principal)
    issues: Iterable[QAReviewIssue] = db.scalars(
        select(QAReviewIssue)
        .where(QAReviewIssue.tenant_id == principal.tenant_id)
        .order_by(QAReviewIssue.created_at.desc(), QAReviewIssue.id.desc())
    )
    return [_issue_read(issue) for issue in issues]


def get_issue(
    db: Session, principal: CurrentPrincipal, issue_id: str
) -> QAReviewIssueDetailRead:
    issue = _get_issue_or_404(db, principal, issue_id)
    return _issue_detail_read(db, principal, issue)


def create_issue(
    db: Session,
    principal: CurrentPrincipal,
    data: QAIssueCreate,
    idempotency_key: str,
) -> QAReviewIssueDetailRead:
    _require_supervisor(principal)
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    existing = _find_idempotency_record(
        db, principal, operation=CREATE_ISSUE_OPERATION, key_hash=key_hash
    )
    if existing is not None:
        return _replay_issue_detail(db, principal, existing, request_fingerprint)

    if data.pause_r0_feature_flag and data.severity not in {
        QASeverity.high,
        QASeverity.critical,
    }:
        raise HTTPException(
            status_code=422,
            detail="Only high or critical QA issues may pause the R0 feature flag",
        )

    conversation = _get_conversation_or_404(db, principal, data.source_conversation_id)
    answer = _latest_answer(db, conversation.id)
    if answer is None:
        raise HTTPException(
            status_code=422,
            detail="QA issue source needs a completed assistant result",
        )
    _get_owner_or_404(db, principal, data.owner_id)
    regression = _resolve_regression_case(data)
    sample = _build_sample(db, conversation, answer)
    issue = QAReviewIssue(
        id=_new_id("qa-issue"),
        tenant_id=principal.tenant_id,
        source_conversation_id=conversation.id,
        category=data.category,
        severity=data.severity,
        status=QAReviewStatus.open,
        owner_id=data.owner_id,
        root_cause=data.root_cause,
        problem_summary=redact_direct_identifiers(
            data.problem_summary, max_chars=1_000
        ),
        remediation_type=data.remediation_type,
        remediation_summary=redact_direct_identifiers(
            data.remediation_summary, max_chars=1_000
        ),
        regression_case_id=regression.id,
        evaluation_suite=regression.evaluation_suite,
        evaluation_case_id=regression.evaluation_case_id,
        feature_flag_paused=data.pause_r0_feature_flag,
        feature_flag_snapshot=_feature_flag_snapshot(
            db,
            principal,
            pause_requested=data.pause_r0_feature_flag,
            request_id=request_id,
        ),
        source_snapshot=_source_snapshot(db, conversation, answer, sample),
        regression_status=QARegressionStatus.not_run,
        reported_by=principal.user_id,
        last_event_sequence=0,
    )
    db.add(issue)
    db.flush()
    _append_event(
        db,
        issue,
        actor_id=principal.user_id,
        event_type="qa.issue_created",
        request_id=request_id,
        summary={
            "category": issue.category.value,
            "severity": issue.severity.value,
            "status": issue.status.value,
            "root_cause": issue.root_cause.value,
            "remediation_type": issue.remediation_type.value,
            "regression_case_id": issue.regression_case_id,
            "feature_flag_paused": issue.feature_flag_paused,
        },
    )
    _write_audit(
        db,
        principal=principal,
        action="qa.issue_created",
        issue=issue,
        request_id=request_id,
        before_summary=None,
    )
    db.flush()
    response = _issue_detail_read(db, principal, issue)
    _store_idempotency_record(
        db,
        principal,
        operation=CREATE_ISSUE_OPERATION,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        issue_id=issue.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = _find_idempotency_record(
            db, principal, operation=CREATE_ISSUE_OPERATION, key_hash=key_hash
        )
        if winner is None:
            raise
        return _replay_issue_detail(db, principal, winner, request_fingerprint)
    return response


def update_issue(
    db: Session,
    principal: CurrentPrincipal,
    issue_id: str,
    data: QAIssueUpdate,
    idempotency_key: str,
) -> QAReviewIssueDetailRead:
    _require_supervisor(principal)
    request_id = _request_id()
    normalized_key = _normalize_idempotency_key(idempotency_key)
    key_hash = hashlib.sha256(normalized_key.encode()).hexdigest()
    request_fingerprint = _fingerprint(data)
    operation = f"PATCH:/v1/admin/qa/issues/{issue_id}"
    existing = _find_idempotency_record(
        db, principal, operation=operation, key_hash=key_hash
    )
    if existing is not None:
        return _replay_issue_detail(db, principal, existing, request_fingerprint)

    issue = _get_issue_or_404(db, principal, issue_id, for_update=True)
    before_summary = _issue_summary(issue)
    status_changed = data.status is not issue.status
    if status_changed and data.status not in _ALLOWED_STATUS_TRANSITIONS[issue.status]:
        raise HTTPException(
            status_code=409, detail="Invalid QA issue status transition"
        )

    next_regression_status = data.regression_status or issue.regression_status
    next_evidence_ref = issue.regression_evidence_ref
    if "regression_evidence_ref" in data.model_fields_set:
        next_evidence_ref = (
            redact_direct_identifiers(data.regression_evidence_ref, max_chars=255)
            if data.regression_evidence_ref is not None
            else None
        )
    regression_changed = (
        next_regression_status is not issue.regression_status
        or next_evidence_ref != issue.regression_evidence_ref
    )
    if not status_changed and not regression_changed:
        raise HTTPException(status_code=409, detail="QA issue update has no changes")
    if issue.status is QAReviewStatus.verified and regression_changed:
        raise HTTPException(
            status_code=409,
            detail="Verified QA issue regression evidence is immutable",
        )
    if data.status is QAReviewStatus.verified and (
        next_regression_status is not QARegressionStatus.passed
        or next_evidence_ref is None
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Verified QA issue requires a passed regression and evidence reference"
            ),
        )

    issue.status = data.status
    issue.regression_status = next_regression_status
    issue.regression_evidence_ref = next_evidence_ref
    if data.status is QAReviewStatus.verified:
        issue.verified_by = principal.user_id
        issue.verified_at = datetime.now(UTC)
    _append_event(
        db,
        issue,
        actor_id=principal.user_id,
        event_type="qa.issue_updated",
        request_id=request_id,
        summary={
            "status": issue.status.value,
            "regression_status": issue.regression_status.value,
            "regression_evidence_present": issue.regression_evidence_ref is not None,
        },
    )
    _write_audit(
        db,
        principal=principal,
        action="qa.issue_updated",
        issue=issue,
        request_id=request_id,
        before_summary=before_summary,
    )
    db.flush()
    response = _issue_detail_read(db, principal, issue)
    _store_idempotency_record(
        db,
        principal,
        operation=operation,
        key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        issue_id=issue.id,
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
        return _replay_issue_detail(db, principal, winner, request_fingerprint)
    return response
