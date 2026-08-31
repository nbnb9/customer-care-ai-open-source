import json
import logging
import re

from .middleware import request_id_var
from .redaction import redact_direct_identifiers

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|authorization|bearer|body|content|cookie|message|password|"
    r"prompt|query|secret|token"
    r")\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_SAFE_EVENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_FORBIDDEN_LOG_KEY_PARTS = (
    "api_key",
    "authorization",
    "body",
    "content",
    "cookie",
    "message",
    "password",
    "prompt",
    "query",
    "secret",
    "token",
)
_SAFE_HEX_CORRELATION_FIELDS = {
    "trace_id": re.compile(r"(?i)[0-9a-f]{32}"),
    "span_id": re.compile(r"(?i)[0-9a-f]{16}"),
    "parent_span_id": re.compile(r"(?i)[0-9a-f]{16}"),
    "request_id": re.compile(
        r"(?i)(?:req|after-sales-worker|knowledge-worker)-[0-9a-f]{32}"
    ),
    # The persisted OTel attribute uses semantic-convention-style dotted
    # names.  Preserve only the same server-generated nonce formats as the
    # top-level request ID; arbitrary client strings still go through PII
    # redaction.
    "customer_care.request.id": re.compile(
        r"(?i)(?:req|after-sales-worker|knowledge-worker)-[0-9a-f]{32}"
    ),
}


def _safe_log_text(value: object) -> str:
    text = redact_direct_identifiers(str(value), max_chars=512)
    return _SECRET_VALUE_RE.sub(r"\1=[REDACTED]", text)


def _safe_log_value(value: object, *, field_name: str | None = None) -> object:
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if any(part in name.lower() for part in _FORBIDDEN_LOG_KEY_PARTS):
                safe[name] = "[REDACTED_FIELD]"
            else:
                safe[name] = _safe_log_value(item, field_name=name)
        return safe
    if isinstance(value, list | tuple):
        return [_safe_log_value(item, field_name=field_name) for item in value[:32]]
    if isinstance(value, str):
        # Protocol trace/span IDs are opaque fixed-length hexadecimal values.
        # Generic PII regexes can mistake numeric substrings inside them for a
        # card number, which would break log-to-trace joins.
        matcher = _SAFE_HEX_CORRELATION_FIELDS.get(field_name or "")
        if matcher is not None and matcher.fullmatch(value):
            return value
        return _safe_log_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _safe_log_text(value)


def _current_trace_id() -> str | None:
    try:
        # Keep this import lazy: middleware is imported while observability is
        # being initialized, and log formatting must never create a cycle.
        from .observability import current_trace_id

        return current_trace_id()
    except Exception:
        return None


def _safe_event(value: object) -> str:
    """Keep unstructured logger messages out of JSON telemetry by default."""

    if isinstance(value, str) and _SAFE_EVENT_RE.fullmatch(value):
        return value
    return "unstructured_log"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "request_id": _safe_log_value(
                request_id_var.get(), field_name="request_id"
            ),
            "trace_id": _safe_log_value(
                getattr(record, "trace_id", None) or _current_trace_id(),
                field_name="trace_id",
            ),
            "module": record.name,
            # A LogRecord message can contain a request body or provider error.
            # Only explicit low-cardinality event identifiers are retained.
            "event": _safe_event(getattr(record, "event", None)),
        }
        observability = getattr(record, "observability", None)
        if observability is not None:
            payload["observability"] = _safe_log_value(observability)
        if record.exc_info:
            # Stack traces can embed request/provider values. Preserve only a
            # stable type; detailed debugging belongs in an approved backend.
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    app_logger = logging.getLogger("app")
    app_logger.handlers = [handler]
    app_logger.propagate = False  # 不动 uvicorn 自己的日志
    app_logger.setLevel(logging.INFO)
