"""Tenant-scoped projections over privacy-minimized OpenTelemetry spans."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.after_sales import AfterSalesWorkflow
from app.models.conversations import Conversation
from app.models.identity import UserRole
from app.models.observability import ObservabilitySpan
from app.modules.identity.principal import CurrentPrincipal

from .schemas import (
    ObservabilityOverviewRead,
    ObservabilitySpanRead,
    ObservabilityTraceRead,
)

_OVERVIEW_SPAN_LIMIT = 500


def _require_supervisor(principal: CurrentPrincipal) -> None:
    if principal.role is not UserRole.supervisor:
        raise HTTPException(status_code=403, detail="Supervisor role is required")


def _trace_read(rows: list[ObservabilitySpan]) -> ObservabilityTraceRead:
    if not rows:
        raise ValueError("a trace must have at least one span")
    return ObservabilityTraceRead(
        trace_id=rows[0].trace_id,
        request_ids=sorted({item.request_id for item in rows if item.request_id}),
        conversation_ids=sorted(
            {item.conversation_id for item in rows if item.conversation_id}
        ),
        ticket_ids=sorted({item.ticket_id for item in rows if item.ticket_id}),
        workflow_ids=sorted({item.workflow_id for item in rows if item.workflow_id}),
        spans=[ObservabilitySpanRead.model_validate(item) for item in rows],
    )


def _group_traces(rows: list[ObservabilitySpan]) -> list[ObservabilityTraceRead]:
    grouped: dict[str, list[ObservabilitySpan]] = defaultdict(list)
    for row in rows:
        grouped[row.trace_id].append(row)
    return [
        _trace_read(grouped[trace_id])
        for trace_id in sorted(
            grouped,
            key=lambda value: grouped[value][0].started_at,
            reverse=True,
        )
    ]


def traces_for_request(
    db: Session, principal: CurrentPrincipal, request_id: str
) -> list[ObservabilityTraceRead]:
    _require_supervisor(principal)
    rows = list(
        db.scalars(
            select(ObservabilitySpan)
            .where(
                ObservabilitySpan.tenant_id == principal.tenant_id,
                ObservabilitySpan.request_id == request_id,
            )
            .order_by(ObservabilitySpan.started_at, ObservabilitySpan.id)
        )
    )
    return _group_traces(rows)


def traces_for_conversation(
    db: Session, principal: CurrentPrincipal, conversation_id: str
) -> list[ObservabilityTraceRead]:
    _require_supervisor(principal)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = list(
        db.scalars(
            select(ObservabilitySpan)
            .where(
                ObservabilitySpan.tenant_id == principal.tenant_id,
                ObservabilitySpan.conversation_id == conversation.id,
            )
            .order_by(ObservabilitySpan.started_at, ObservabilitySpan.id)
        )
    )
    return _group_traces(rows)


def traces_for_workflow(
    db: Session, principal: CurrentPrincipal, workflow_id: str
) -> list[ObservabilityTraceRead]:
    """Read a tenant-owned R2 workflow trace without exposing workflow data."""

    _require_supervisor(principal)
    workflow = db.scalar(
        select(AfterSalesWorkflow).where(
            AfterSalesWorkflow.id == workflow_id,
            AfterSalesWorkflow.tenant_id == principal.tenant_id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="After-sales workflow not found")
    rows = list(
        db.scalars(
            select(ObservabilitySpan)
            .where(
                ObservabilitySpan.tenant_id == principal.tenant_id,
                ObservabilitySpan.workflow_id == workflow.id,
            )
            .order_by(ObservabilitySpan.started_at, ObservabilitySpan.id)
        )
    )
    return _group_traces(rows)


def get_overview(db: Session, principal: CurrentPrincipal) -> ObservabilityOverviewRead:
    _require_supervisor(principal)
    retained_span_count = db.scalar(
        select(func.count())
        .select_from(ObservabilitySpan)
        .where(ObservabilitySpan.tenant_id == principal.tenant_id)
    )
    rows = list(
        db.scalars(
            select(ObservabilitySpan)
            .where(ObservabilitySpan.tenant_id == principal.tenant_id)
            .order_by(ObservabilitySpan.ended_at.desc(), ObservabilitySpan.id.desc())
            .limit(_OVERVIEW_SPAN_LIMIT)
        )
    )
    http_spans = [span for span in rows if span.kind == "SERVER"]
    model_spans = [span for span in rows if span.name == "gen_ai.chat"]
    safety_spans = [
        span
        for span in rows
        if span.attributes.get("customer_care.safety.outcome") is not None
    ]
    workflow_spans = [
        span
        for span in rows
        if span.name.startswith("workflow.") or span.name.startswith("tool.")
    ]

    model_outcomes = Counter(
        _attribute_text(span, "customer_care.ai.outcome", "unknown")
        for span in model_spans
    )
    safety_outcomes = Counter(
        _attribute_text(span, "customer_care.safety.outcome", "unknown")
        for span in safety_spans
    )
    workflow_statuses = Counter(
        _attribute_text(span, "customer_care.workflow.status", "unknown")
        for span in workflow_spans
    )
    tool_outcomes = Counter(
        _attribute_text(span, "customer_care.tool.outcome", "unknown")
        for span in workflow_spans
        if span.attributes.get("customer_care.tool.name") is not None
    )

    input_tokens = _sum_int(model_spans, "gen_ai.usage.input_tokens")
    output_tokens = _sum_int(model_spans, "gen_ai.usage.output_tokens")
    measured_cost = sum(
        _attribute_number(span, "customer_care.cost.amount")
        for span in model_spans
        if span.attributes.get("customer_care.cost.status") == "measured"
        and span.attributes.get("customer_care.cost.currency") == "USD"
    )
    unpriced_calls = sum(
        1
        for span in model_spans
        if span.attributes.get("customer_care.cost.status") != "measured"
    )

    return ObservabilityOverviewRead(
        generated_at=datetime.now(UTC),
        retained_span_count=int(retained_span_count or 0),
        analysis_window_span_count=len(rows),
        customer_experience={
            "http_requests": len(http_spans),
            "http_5xx": sum(
                1
                for span in http_spans
                if _attribute_number(span, "http.response.status_code") >= 500
            ),
            "p95_latency_ms": _p95([span.duration_ms for span in http_spans]),
        },
        ai_quality={
            "model_calls": len(model_spans),
            "model_error_spans": sum(
                1 for span in model_spans if span.status == "ERROR"
            ),
            "outcomes": dict(sorted(model_outcomes.items())),
        },
        safety={"outcomes": dict(sorted(safety_outcomes.items()))},
        workflow={
            "workflow_or_tool_spans": len(workflow_spans),
            "workflow_statuses": dict(sorted(workflow_statuses.items())),
            "tool_outcomes": dict(sorted(tool_outcomes.items())),
        },
        cost_capacity={
            "input_tokens_reported": input_tokens,
            "output_tokens_reported": output_tokens,
            "measured_cost_usd": round(measured_cost, 6),
            "unpriced_model_calls": unpriced_calls,
            "model_p95_latency_ms": _p95([span.duration_ms for span in model_spans]),
        },
        limitations=[
            "仅为本地、租户隔离的 Trace Viewer；不是 Prometheus/Grafana "
            "或生产 Collector。",
            "真实 Provider 的价格未配置时成本明确标为 unavailable，绝不估算。",
            "没有采集客户正文、提示词、模型输出、SQL 参数、API Key 或原始异常信息。",
        ],
    )


def _attribute_text(span: ObservabilitySpan, key: str, default: str) -> str:
    value = span.attributes.get(key)
    return value if isinstance(value, str) else default


def _attribute_number(span: ObservabilitySpan, key: str) -> float:
    value = span.attributes.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def _sum_int(spans: list[ObservabilitySpan], key: str) -> int:
    return sum(int(_attribute_number(span, key)) for span in spans)


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]
