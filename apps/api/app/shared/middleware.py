import json
import re
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from .observability import (
    current_trace_id,
    current_traceparent,
    observe_http_request,
    set_http_response_status,
)
from .redaction import redact_direct_identifiers

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RequestBodyTooLarge(Exception):
    """Raised internally while consuming an oversized streamed request body."""


class RequestIDMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id")
        request_id = _safe_request_id(incoming)
        token = request_id_var.set(request_id)

        try:
            with observe_http_request(scope, request_id=request_id) as span:

                async def send_with_header(message):
                    if message["type"] == "http.response.start":
                        set_http_response_status(span, int(message["status"]))
                        trace_headers: list[tuple[bytes, bytes]] = []
                        if trace_id := current_trace_id():
                            trace_headers.append(
                                (b"x-trace-id", trace_id.encode("ascii"))
                            )
                        if traceparent := current_traceparent():
                            trace_headers.append(
                                (b"traceparent", traceparent.encode("ascii"))
                            )
                        message["headers"] = [
                            *list(message.get("headers") or []),
                            (b"x-request-id", request_id.encode()),
                            *trace_headers,
                        ]
                    await send(message)

                await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


class RequestBodyLimitMiddleware:
    """Reject declared and streamed HTTP bodies above the configured limit."""

    def __init__(self, app, *, max_body_bytes: Callable[[], int]):
        self.app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._max_body_bytes()
        headers = dict(scope.get("headers") or [])
        content_length = _content_length(headers.get(b"content-length"))
        if content_length is not None and content_length > limit:
            await _send_request_too_large(send)
            return

        consumed = 0
        response_started = False

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body") or b"")
                if consumed > limit:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            # All current routes parse their request before beginning a
            # response.  If a future streaming route starts first, avoid a
            # malformed second response and let that route own its failure.
            if not response_started:
                await _send_request_too_large(send)


def _safe_request_id(incoming: bytes | None) -> str:
    if incoming is not None:
        try:
            candidate = incoming.decode("ascii")
        except UnicodeDecodeError:
            candidate = ""
        if (
            _REQUEST_ID_RE.fullmatch(candidate)
            and redact_direct_identifiers(candidate, max_chars=128) == candidate
        ):
            return candidate
    # Prefix locally generated IDs so structured-log redaction can distinguish
    # a server nonce from arbitrary client-provided text without losing joins.
    return f"req-{uuid.uuid4().hex}"


def _content_length(value: bytes | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


async def _send_request_too_large(send) -> None:
    request_id = request_id_var.get()
    payload: dict[str, str] = {
        "code": "REQUEST_TOO_LARGE",
        "message": "请求体超过允许大小",
    }
    if request_id != "-":
        payload["request_id"] = request_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
