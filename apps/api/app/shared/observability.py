"""Privacy-minimized OpenTelemetry plumbing for the local Step 39 baseline.

The application deliberately creates spans manually instead of enabling broad
auto-instrumentation.  That keeps request bodies, SQL text/parameters, model
prompts, responses, credentials and raw provider errors out of telemetry by
construction.  A tiny database-backed exporter provides a local trace viewer;
it is not a production collector, metrics backend, retention system or SIEM.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import delete, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

logger = logging.getLogger("app.observability")

_PROPAGATOR = TraceContextTextMapPropagator()
_SETUP_LOCK = threading.Lock()
_PROVIDER: TracerProvider | None = None
_SESSION_FACTORY: Callable[[], Session] | None = None
_REGISTERED_ENGINES: set[int] = set()
_EXPORTING_VAR: ContextVar[bool] = ContextVar("observability_exporting", default=False)

_TENANT_ID_VAR: ContextVar[str | None] = ContextVar(
    "observability_tenant_id", default=None
)
_ROLE_VAR: ContextVar[str | None] = ContextVar("observability_role", default=None)
_CONVERSATION_ID_VAR: ContextVar[str | None] = ContextVar(
    "observability_conversation_id", default=None
)
_TICKET_ID_VAR: ContextVar[str | None] = ContextVar(
    "observability_ticket_id", default=None
)
_WORKFLOW_ID_VAR: ContextVar[str | None] = ContextVar(
    "observability_workflow_id", default=None
)
_KNOWLEDGE_VERSIONS_VAR: ContextVar[str | None] = ContextVar(
    "observability_knowledge_versions", default=None
)

_ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.version",
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "db.system.name",
        "db.operation.name",
        "db.response.rows",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "error.type",
        "customer_care.request.id",
        "customer_care.tenant.id",
        "customer_care.principal.role",
        "customer_care.conversation.id",
        "customer_care.ticket.id",
        "customer_care.workflow.id",
        "customer_care.worker.name",
        "customer_care.prompt.version",
        "customer_care.prompt.schema_version",
        "customer_care.model.config",
        "customer_care.knowledge.versions",
        "customer_care.knowledge.version_count",
        "customer_care.knowledge.import_status",
        "customer_care.retrieval.candidate_count",
        "customer_care.retrieval.result_count",
        "customer_care.tool.name",
        "customer_care.tool.outcome",
        "customer_care.workflow.action_type",
        "customer_care.workflow.status",
        "customer_care.workflow.external_status",
        "customer_care.ai.outcome",
        "customer_care.ai.citation_count",
        "customer_care.safety.outcome",
        "customer_care.cost.status",
        "customer_care.cost.currency",
        "customer_care.cost.amount",
        "customer_care.cost.reason",
    }
)
_MAX_ATTRIBUTE_TEXT = 256
_DB_SPAN_STACK_KEY = "customer_care_observability_db_spans"
_INHERITED_CONTEXT_KEYS = frozenset(
    {
        "customer_care.request.id",
        "customer_care.tenant.id",
        "customer_care.principal.role",
        "customer_care.conversation.id",
        "customer_care.ticket.id",
        "customer_care.workflow.id",
        "customer_care.knowledge.versions",
        "customer_care.knowledge.version_count",
    }
)


def setup_observability() -> None:
    """Initialize the local SDK/exporter once per process."""

    global _PROVIDER
    if _PROVIDER is not None:
        return
    with _SETUP_LOCK:
        if _PROVIDER is not None:
            return
        from app.db import SessionLocal, engine
        from app.shared.config import get_settings

        settings = get_settings()
        _PROVIDER = TracerProvider(
            resource=Resource.create(
                {
                    SERVICE_NAME: "customer-care-api",
                    SERVICE_VERSION: settings.deployment_version,
                    "deployment.version": settings.deployment_version,
                }
            )
        )
        _PROVIDER.add_span_processor(SimpleSpanProcessor(_SafeDatabaseSpanExporter()))
        # Test fixtures configure an isolated session factory before the first
        # request. Do not overwrite it here, otherwise completed spans leak
        # into a developer database and the local viewer sees nothing.
        if _SESSION_FACTORY is None:
            configure_observability_storage(session_factory=SessionLocal, engine=engine)


def configure_observability_storage(
    *,
    session_factory: Callable[[], Session],
    engine: Engine | None = None,
) -> None:
    """Override storage/DB timing hooks for isolated test databases.

    Application deployments use ``SessionLocal``. Tests deliberately use a
    separate session factory so telemetry cannot leak into a developer DB.
    """

    global _SESSION_FACTORY
    _SESSION_FACTORY = session_factory
    if engine is not None:
        _configure_db_engine(engine)


def _tracer():
    setup_observability()
    assert _PROVIDER is not None
    return _PROVIDER.get_tracer("customer-care.observability")


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def bind_principal(*, tenant_id: str, role: str) -> None:
    """Attach only tenant and role, never user/customer identifiers."""

    _TENANT_ID_VAR.set(tenant_id)
    _ROLE_VAR.set(role)
    set_span_attributes(
        {
            "customer_care.tenant.id": tenant_id,
            "customer_care.principal.role": role,
        }
    )


def bind_resources(
    *,
    conversation_id: str | None = None,
    ticket_id: str | None = None,
    workflow_id: str | None = None,
) -> None:
    """Bind opaque business correlation IDs to the current trace context."""

    attributes: dict[str, str] = {}
    if conversation_id is not None:
        _CONVERSATION_ID_VAR.set(conversation_id)
        attributes["customer_care.conversation.id"] = conversation_id
    if ticket_id is not None:
        _TICKET_ID_VAR.set(ticket_id)
        attributes["customer_care.ticket.id"] = ticket_id
    if workflow_id is not None:
        _WORKFLOW_ID_VAR.set(workflow_id)
        attributes["customer_care.workflow.id"] = workflow_id
    set_span_attributes(attributes)


def bind_knowledge_versions(versions: Sequence[str]) -> None:
    """Associate bounded, metadata-only knowledge versions with descendants."""

    normalized = sorted({_safe_version(value) for value in versions if value})[:16]
    value = ",".join(normalized) if normalized else None
    _KNOWLEDGE_VERSIONS_VAR.set(value)
    set_span_attributes(
        {
            "customer_care.knowledge.version_count": len(normalized),
            **({"customer_care.knowledge.versions": value} if value else {}),
        }
    )


@contextmanager
def observe_span(
    name: str,
    *,
    attributes: Mapping[str, object] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    parent_context: Context | None = None,
    inherited_attributes: Mapping[str, object] | None = None,
) -> Iterator[Any]:
    """Create a span with allow-listed attributes and error-type-only capture."""

    tracer = _tracer()
    inherited = (
        _bound_context_attributes()
        if inherited_attributes is None
        else inherited_attributes
    )
    with tracer.start_as_current_span(name, context=parent_context, kind=kind) as span:
        _apply_context_attributes(span, inherited_attributes=inherited)
        if attributes:
            _set_span_attributes(span, attributes)
        try:
            yield span
        except Exception as exc:
            _record_exception_type(span, exc)
            raise


@contextmanager
def observe_http_request(
    scope: Mapping[str, object], *, request_id: str
) -> Iterator[Any]:
    """Create a server span from W3C ``traceparent`` without logging headers."""

    tokens = _clear_bound_context()
    parent_context = _extract_parent_context(scope)
    method = str(scope.get("method") or "HTTP").upper()
    route = _safe_route_group(str(scope.get("path") or "/"))
    try:
        with observe_span(
            f"{method} {route}",
            kind=SpanKind.SERVER,
            parent_context=parent_context,
            inherited_attributes={},
            attributes={
                "http.request.method": method,
                "http.route": route,
                "customer_care.request.id": request_id,
            },
        ) as span:
            yield span
    finally:
        _reset_bound_context(tokens)


@contextmanager
def observe_background_task(
    name: str,
    *,
    traceparent: str | None,
    request_id: str,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[Any]:
    """Continue a captured W3C parent context in a BackgroundTask/worker."""

    from .middleware import request_id_var

    request_token = request_id_var.set(request_id)
    tokens = _clear_bound_context()
    try:
        with observe_span(
            name,
            kind=SpanKind.CONSUMER,
            parent_context=_extract_traceparent(traceparent),
            inherited_attributes={},
            attributes=attributes,
        ) as span:
            yield span
    finally:
        _reset_bound_context(tokens)
        request_id_var.reset(request_token)


def set_span_attributes(attributes: Mapping[str, object]) -> None:
    _set_span_attributes(trace.get_current_span(), attributes)


def set_span_outcome(outcome: str) -> None:
    set_span_attributes({"customer_care.ai.outcome": outcome})


def mark_span_error(span: Any, exc: Exception) -> None:
    _record_exception_type(span, exc)


def set_http_response_status(span: Any, status_code: int) -> None:
    _set_span_attributes(span, {"http.response.status_code": status_code})
    if status_code >= 500:
        span.set_status(Status(StatusCode.ERROR))


def set_model_usage(
    span: Any,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    attributes: dict[str, object] = {}
    if input_tokens is not None and input_tokens >= 0:
        attributes["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None and output_tokens >= 0:
        attributes["gen_ai.usage.output_tokens"] = output_tokens
    _set_span_attributes(span, attributes)


def _safe_version(value: str) -> str:
    compact = value.strip()
    if (
        compact
        and len(compact) <= 64
        and all(character.isalnum() or character in ".-_" for character in compact)
    ):
        return compact
    return f"version-sha256-{uuid.uuid5(uuid.NAMESPACE_OID, value).hex[:16]}"


def _request_id_from_context() -> str | None:
    try:
        from .middleware import request_id_var

        value = request_id_var.get()
    except Exception:
        return None
    return None if value == "-" else value


def _bound_context_attributes() -> dict[str, bool | float | int | str]:
    """Read correlation metadata from ContextVars or the active parent span.

    Sync FastAPI dependencies can execute in a copied worker-thread context.
    They correctly enrich the active HTTP span but their ContextVar mutation is
    not guaranteed to flow back to later sync call sites. Reading the parent
    span makes the tenant/role binding survive that framework boundary without
    ever carrying a user or customer identifier.
    """

    current = trace.get_current_span()
    raw_attributes = getattr(current, "attributes", {}) or {}
    inherited = {
        key: value
        for key, value in raw_attributes.items()
        if key in _INHERITED_CONTEXT_KEYS
    }
    return _safe_attributes(inherited)


def _apply_context_attributes(
    span: Any,
    *,
    inherited_attributes: Mapping[str, object] | None = None,
) -> None:
    attributes = dict(
        _bound_context_attributes()
        if inherited_attributes is None
        else _safe_attributes(inherited_attributes)
    )
    request_id = _request_id_from_context()
    if request_id is not None:
        attributes["customer_care.request.id"] = request_id
    if tenant_id := _TENANT_ID_VAR.get():
        attributes["customer_care.tenant.id"] = tenant_id
    if role := _ROLE_VAR.get():
        attributes["customer_care.principal.role"] = role
    if conversation_id := _CONVERSATION_ID_VAR.get():
        attributes["customer_care.conversation.id"] = conversation_id
    if ticket_id := _TICKET_ID_VAR.get():
        attributes["customer_care.ticket.id"] = ticket_id
    if workflow_id := _WORKFLOW_ID_VAR.get():
        attributes["customer_care.workflow.id"] = workflow_id
    if knowledge_versions := _KNOWLEDGE_VERSIONS_VAR.get():
        attributes["customer_care.knowledge.versions"] = knowledge_versions
        attributes["customer_care.knowledge.version_count"] = len(
            knowledge_versions.split(",")
        )
    _set_span_attributes(span, attributes)


def _set_span_attributes(span: Any, attributes: Mapping[str, object]) -> None:
    for key, value in _safe_attributes(attributes).items():
        span.set_attribute(key, value)


def _safe_attributes(
    attributes: Mapping[str, object],
) -> dict[str, bool | float | int | str]:
    sanitized: dict[str, bool | float | int | str] = {}
    for key, value in attributes.items():
        if key not in _ALLOWED_ATTRIBUTE_KEYS or value is None:
            continue
        if isinstance(value, bool | int | float):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:_MAX_ATTRIBUTE_TEXT]
    return sanitized


def _record_exception_type(span: Any, exc: Exception) -> None:
    # ``record_exception`` would include the raw exception message. Keep only
    # a stable type so malformed inputs/provider secrets cannot enter traces.
    _set_span_attributes(span, {"error.type": type(exc).__name__})
    span.set_status(Status(StatusCode.ERROR))


def _clear_bound_context() -> tuple[object, ...]:
    return (
        _TENANT_ID_VAR.set(None),
        _ROLE_VAR.set(None),
        _CONVERSATION_ID_VAR.set(None),
        _TICKET_ID_VAR.set(None),
        _WORKFLOW_ID_VAR.set(None),
        _KNOWLEDGE_VERSIONS_VAR.set(None),
    )


def _reset_bound_context(tokens: tuple[object, ...]) -> None:
    _TENANT_ID_VAR.reset(tokens[0])  # type: ignore[arg-type]
    _ROLE_VAR.reset(tokens[1])  # type: ignore[arg-type]
    _CONVERSATION_ID_VAR.reset(tokens[2])  # type: ignore[arg-type]
    _TICKET_ID_VAR.reset(tokens[3])  # type: ignore[arg-type]
    _WORKFLOW_ID_VAR.reset(tokens[4])  # type: ignore[arg-type]
    _KNOWLEDGE_VERSIONS_VAR.reset(tokens[5])  # type: ignore[arg-type]


def _extract_parent_context(scope: Mapping[str, object]) -> Context | None:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list | tuple):
        return None
    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        key, value = item
        if key == b"traceparent" and isinstance(value, bytes):
            try:
                return _extract_traceparent(value.decode("ascii"))
            except UnicodeDecodeError:
                return None
    return None


def _extract_traceparent(traceparent: str | None) -> Context | None:
    if traceparent is None or len(traceparent) > 128 or not traceparent.isascii():
        return None
    return _PROPAGATOR.extract({"traceparent": traceparent})


def _safe_route_group(path: str) -> str:
    """Keep HTTP telemetry low-cardinality and free of resource IDs/query text."""

    pieces = [piece for piece in path.split("/") if piece]
    if not pieces:
        return "/"
    if pieces[0] != "v1":
        return f"/{pieces[0][:40]}"
    if len(pieces) == 1:
        return "/v1"
    if pieces[1] == "admin" and len(pieces) >= 3:
        return f"/v1/admin/{pieces[2][:40]}"
    return f"/v1/{pieces[1][:40]}"


def _configure_db_engine(engine: Engine) -> None:
    identity = id(engine)
    if identity in _REGISTERED_ENGINES:
        return
    _REGISTERED_ENGINES.add(identity)

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del cursor, parameters, context, executemany
        if _EXPORTING_VAR.get() or current_trace_id() is None:
            return
        operation = (
            statement.lstrip().split(maxsplit=1)[0].upper() if statement else "SQL"
        )
        span = _tracer().start_span("db.query", kind=SpanKind.CLIENT)
        _apply_context_attributes(span)
        _set_span_attributes(
            span,
            {
                "db.system.name": "postgresql",
                "db.operation.name": operation,
            },
        )
        connection.info.setdefault(_DB_SPAN_STACK_KEY, []).append(span)

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del statement, parameters, context, executemany
        span = _pop_db_span(connection)
        if span is None:
            return
        row_count = getattr(cursor, "rowcount", -1)
        if isinstance(row_count, int) and row_count >= 0:
            _set_span_attributes(span, {"db.response.rows": row_count})
        span.end()

    @event.listens_for(engine, "handle_error")
    def _handle_error(exception_context: Any) -> None:
        connection = getattr(exception_context, "connection", None)
        if not isinstance(connection, Connection):
            return
        span = _pop_db_span(connection)
        if span is None:
            return
        original = getattr(exception_context, "original_exception", None)
        if isinstance(original, Exception):
            _record_exception_type(span, original)
        else:
            span.set_status(Status(StatusCode.ERROR))
        span.end()


def _pop_db_span(connection: Connection) -> Any | None:
    stack = connection.info.get(_DB_SPAN_STACK_KEY)
    if not isinstance(stack, list) or not stack:
        return None
    return stack.pop()


class _SafeDatabaseSpanExporter(SpanExporter):
    """Persist only a strict allow-list of completed span metadata."""

    def export(self, spans: Sequence[Any]) -> SpanExportResult:
        records: list[dict[str, object]] = []
        for span in spans:
            record = _span_record(span)
            if record is not None:
                records.append(record)
        if not records:
            return SpanExportResult.SUCCESS

        for record in records:
            if record["name"] == "db.query":
                # DB spans remain in the local viewer, but logging every SQL
                # duration would make telemetry volume/cost drown out customer,
                # AI and workflow signals.
                continue
            logger.info(
                "observability span completed",
                extra={
                    "event": "observability.span_completed",
                    "trace_id": record["trace_id"],
                    "observability": record,
                },
            )

        factory = _SESSION_FACTORY
        if factory is None:
            logger.warning(
                "observability storage is unavailable",
                extra={
                    "event": "observability.storage_unavailable",
                    "observability": {"span_count": len(records)},
                },
            )
            return SpanExportResult.FAILURE

        token = _EXPORTING_VAR.set(True)
        try:
            from app.models.observability import ObservabilitySpan
            from app.shared.config import get_settings

            cutoff = datetime.now(UTC) - timedelta(
                hours=get_settings().observability_trace_retention_hours
            )
            with factory() as db:
                db.execute(
                    delete(ObservabilitySpan).where(ObservabilitySpan.ended_at < cutoff)
                )
                db.add_all(
                    [
                        ObservabilitySpan(
                            id=f"observability-span-{uuid.uuid4().hex}",
                            trace_id=record["trace_id"],
                            span_id=record["span_id"],
                            parent_span_id=record["parent_span_id"],
                            request_id=record["request_id"],
                            tenant_id=record["tenant_id"],
                            conversation_id=record["conversation_id"],
                            ticket_id=record["ticket_id"],
                            workflow_id=record["workflow_id"],
                            name=record["name"],
                            kind=record["kind"],
                            status=record["status"],
                            started_at=record["started_at"],
                            ended_at=record["ended_at"],
                            duration_ms=record["duration_ms"],
                            attributes=record["attributes"],
                        )
                        for record in records
                    ]
                )
                db.commit()
        except Exception as exc:
            logger.warning(
                "observability span persistence failed",
                extra={
                    "event": "observability.storage_failed",
                    "observability": {
                        "error_type": type(exc).__name__,
                        "span_count": len(records),
                    },
                },
            )
            return SpanExportResult.FAILURE
        finally:
            _EXPORTING_VAR.reset(token)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _span_record(span: Any) -> dict[str, object] | None:
    context = span.get_span_context()
    if not context.is_valid:
        return None
    attributes = _safe_attributes(dict(span.attributes or {}))
    resource_attributes = _safe_attributes(dict(span.resource.attributes or {}))
    attributes = {**resource_attributes, **attributes}
    start_ns = int(span.start_time)
    end_ns = int(span.end_time or start_ns)
    started_at = datetime.fromtimestamp(start_ns / 1_000_000_000, UTC)
    ended_at = datetime.fromtimestamp(end_ns / 1_000_000_000, UTC)
    parent = getattr(span, "parent", None)
    parent_span_id = (
        f"{parent.span_id:016x}"
        if parent is not None and getattr(parent, "is_valid", False)
        else None
    )
    status = getattr(getattr(span, "status", None), "status_code", StatusCode.UNSET)
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
        "parent_span_id": parent_span_id,
        "request_id": attributes.get("customer_care.request.id"),
        "tenant_id": attributes.get("customer_care.tenant.id"),
        "conversation_id": attributes.get("customer_care.conversation.id"),
        "ticket_id": attributes.get("customer_care.ticket.id"),
        "workflow_id": attributes.get("customer_care.workflow.id"),
        "name": str(span.name)[:160],
        "kind": str(getattr(span, "kind", SpanKind.INTERNAL).name),
        "status": str(getattr(status, "name", "UNSET")),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": max(0, round((end_ns - start_ns) / 1_000_000)),
        "attributes": attributes,
    }
