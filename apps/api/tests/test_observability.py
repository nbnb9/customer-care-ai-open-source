import json
import logging
import sys

from conftest import TestingSessionLocal, auth
from sqlalchemy import select

from app.models.observability import ObservabilitySpan
from app.modules.after_sales.mock_oms import MockOmsAdapter
from app.modules.after_sales.worker import process_pending_workflows
from app.modules.knowledge.worker import process_pending_imports
from app.shared.config import get_settings
from app.shared.logging import JsonFormatter
from app.shared.middleware import _safe_request_id

CUSTOMER = "user-nw-customer-01"
AGENT = "user-nw-agent-01"
SUPERVISOR = "user-nw-supervisor-01"
CONTOSO_SUPERVISOR = "user-ct-supervisor-01"


def _traceparent(trace_id: str) -> str:
    return f"00-{trace_id}-0123456789abcdef-01"


def _headers(
    user_id: str,
    *,
    request_id: str,
    trace_id: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {
        **auth(user_id),
        "X-Request-ID": request_id,
        "traceparent": _traceparent(trace_id),
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _trace_by_id(traces: list[dict], trace_id: str) -> dict:
    return next(item for item in traces if item["trace_id"] == trace_id)


def _span_names(trace: dict) -> set[str]:
    return {span["name"] for span in trace["spans"]}


def test_request_trace_is_propagated_queryable_and_does_not_store_order_data(
    client, db
):
    request_id = "observability-order-request-001"
    trace_id = "0123456789abcdef0123456789abcdef"
    order_number = "NW-DEMO-ORDER-1001"
    response = client.post(
        "/v1/customer-tools/order-status",
        json={"order_number": order_number},
        headers=_headers(
            CUSTOMER,
            request_id=request_id,
            trace_id=trace_id,
        ),
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert response.headers["x-trace-id"] == trace_id
    assert response.headers["traceparent"].startswith(f"00-{trace_id}-")

    supervisor = client.get(
        f"/v1/admin/observability/traces/by-request/{request_id}",
        headers=auth(SUPERVISOR),
    )
    assert supervisor.status_code == 200
    trace = _trace_by_id(supervisor.json(), trace_id)
    expected_names = {"POST /v1/customer-tools", "tool.order_status", "db.query"}
    assert expected_names <= _span_names(trace), [
        (item.trace_id, item.name, item.request_id, item.tenant_id, item.attributes)
        for item in db.scalars(select(ObservabilitySpan).order_by(ObservabilitySpan.id))
    ]

    stored = list(
        db.scalars(
            select(ObservabilitySpan).where(
                ObservabilitySpan.request_id == request_id,
                ObservabilitySpan.trace_id == trace_id,
            )
        )
    )
    assert stored
    serialized = json.dumps([item.attributes for item in stored], ensure_ascii=False)
    assert order_number not in serialized
    assert "customer-nw-01" not in serialized
    assert "password" not in serialized.lower()

    assert (
        client.get(
            f"/v1/admin/observability/traces/by-request/{request_id}",
            headers=auth(CUSTOMER),
        ).status_code
        == 403
    )
    cross_tenant = client.get(
        f"/v1/admin/observability/traces/by-request/{request_id}",
        headers=auth(CONTOSO_SUPERVISOR),
    )
    assert cross_tenant.status_code == 200
    assert cross_tenant.json() == []


def test_conversation_trace_links_background_retrieval_model_and_versions(
    client, db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "r0_self_service_master_enabled", True)
    enabled = client.put(
        "/v1/admin/self-service/r0-feature-flag",
        json={
            "enabled": True,
            "allow_all_customers": False,
            "customer_ids": ["customer-nw-01"],
        },
        headers=auth(SUPERVISOR),
    )
    assert enabled.status_code == 200

    imported = client.post(
        "/v1/admin/knowledge/imports",
        json={
            "filename": "observability-public-setup.md",
            "title": "可观测性公开安装说明",
            "content": "可观测性公开安装说明：按应用内引导完成首次设置。",
            "document_key": "observability-public-setup",
            "owner": "demo-knowledge-operations",
            "audience": "public",
            "product": "demo-support",
            "region": "cn-north",
            "language": "zh-CN",
            "version": "39.0.0",
            "effective_at": "2026-01-01T00:00:00Z",
            "requested_status": "published",
            "untrusted_content": False,
        },
        headers=_headers(
            "user-nw-knowledge-operator-01",
            request_id="observability-import-request-001",
            trace_id="11111111111111111111111111111111",
        ),
    )
    assert imported.status_code == 202
    assert process_pending_imports(db, limit=10)

    created = client.post(
        "/v1/conversations",
        json={},
        headers=_headers(
            CUSTOMER,
            request_id="observability-conversation-create-001",
            trace_id="22222222222222222222222222222222",
            idempotency_key="observability-conversation-create-key-001",
        ),
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    trace_id = "33333333333333333333333333333333"
    sent = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "可观测性公开安装说明"},
        headers=_headers(
            CUSTOMER,
            request_id="observability-conversation-message-001",
            trace_id=trace_id,
            idempotency_key="observability-conversation-message-key-001",
        ),
    )
    assert sent.status_code == 202
    assert sent.headers["x-trace-id"] == trace_id

    traces_response = client.get(
        f"/v1/admin/observability/traces/conversations/{conversation_id}",
        headers=auth(SUPERVISOR),
    )
    assert traces_response.status_code == 200
    trace = _trace_by_id(traces_response.json(), trace_id)
    assert {
        "POST /v1/conversations",
        "conversation.reply_generation",
        "knowledge.retrieval",
        "gen_ai.chat",
    } <= _span_names(trace)
    model_span = next(span for span in trace["spans"] if span["name"] == "gen_ai.chat")
    assert model_span["attributes"]["customer_care.prompt.version"] == (
        "knowledge-answer-v1"
    )
    assert model_span["attributes"]["customer_care.prompt.schema_version"] == (
        "knowledge-answer-result-v1"
    )
    assert model_span["attributes"]["customer_care.model.config"] == (
        "fake-deterministic-v1;temperature=0;structured-json"
    )
    retrieval_span = next(
        span for span in trace["spans"] if span["name"] == "knowledge.retrieval"
    )
    assert retrieval_span["attributes"]["customer_care.knowledge.versions"] == "39.0.0"
    assert "可观测性公开安装说明" not in json.dumps(trace, ensure_ascii=False)

    overview = client.get("/v1/admin/observability/overview", headers=auth(SUPERVISOR))
    assert overview.status_code == 200
    assert overview.json()["ai_quality"]["model_calls"] >= 1
    assert overview.json()["cost_capacity"]["measured_cost_usd"] == 0.0
    assert overview.json()["limitations"]


def test_workflow_trace_links_draft_to_worker_and_final_state(client, db):
    trace_id = "44444444444444444444444444444444"
    created = client.post(
        "/v1/after-sales/workflows",
        json={"ticket_id": "ticket-nw-open-01", "action_type": "complaint"},
        headers=_headers(
            AGENT,
            request_id="observability-workflow-draft-001",
            trace_id=trace_id,
            idempotency_key="observability-workflow-draft-key-001",
        ),
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    confirmed = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/customer-confirmation",
        json={"decision": "confirm"},
        headers={
            **auth(CUSTOMER),
            "Idempotency-Key": "observability-workflow-confirm-key-001",
        },
    )
    assert confirmed.status_code == 200
    approved = client.post(
        f"/v1/after-sales/workflows/{workflow_id}/agent-approval",
        json={"decision": "approve"},
        headers={
            **auth(AGENT),
            "Idempotency-Key": "observability-workflow-approve-key-001",
        },
    )
    assert approved.status_code == 200
    assert process_pending_workflows(
        db,
        adapter=MockOmsAdapter(session_factory=TestingSessionLocal),
        limit=10,
    ) == [workflow_id]

    traces_response = client.get(
        f"/v1/admin/observability/traces/workflows/{workflow_id}",
        headers=auth(SUPERVISOR),
    )
    assert traces_response.status_code == 200
    trace = _trace_by_id(traces_response.json(), trace_id)
    assert {"workflow.draft_create", "workflow.execution"} <= _span_names(trace)
    worker_span = next(
        span for span in trace["spans"] if span["name"] == "workflow.execution"
    )
    assert worker_span["attributes"]["customer_care.workflow.status"] == "completed"
    assert worker_span["attributes"]["customer_care.workflow.external_status"] == (
        "completed"
    )
    assert worker_span["attributes"]["customer_care.tool.outcome"] == "completed"
    assert "NW-DEMO-ORDER-1001" not in json.dumps(trace, ensure_ascii=False)
    assert (
        client.get(
            f"/v1/admin/observability/traces/workflows/{workflow_id}",
            headers=auth(CONTOSO_SUPERVISOR),
        ).status_code
        == 404
    )


def test_json_logging_redacts_unstructured_messages_and_sensitive_fields():
    try:
        raise RuntimeError("provider says api_key=super-secret")
    except RuntimeError:
        exc_info = sys.exc_info()
        record = logging.LogRecord(
            "app.test",
            logging.ERROR,
            __file__,
            1,
            "customer message=raw-private-text password=super-secret",
            (),
            exc_info=exc_info,
        )
    record.observability = {
        "prompt": "raw prompt must not persist",
        "safe_metric": 3,
        "trace_id": "1234567890123456abcdefabcdefabcd",
        "request_id": "req-1234567890123456abcdefabcdefabcd",
        "customer_care.request.id": "req-1234567890123456abcdefabcdefabcd",
        "nested": {"authorization": "Bearer token-value"},
    }

    payload = json.loads(JsonFormatter().format(record))
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["event"] == "unstructured_log"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["observability"]["safe_metric"] == 3
    assert payload["observability"]["trace_id"] == "1234567890123456abcdefabcdefabcd"
    assert payload["observability"]["request_id"] == (
        "req-1234567890123456abcdefabcdefabcd"
    )
    assert payload["observability"]["customer_care.request.id"] == (
        "req-1234567890123456abcdefabcdefabcd"
    )
    for forbidden in (
        "raw-private-text",
        "super-secret",
        "raw prompt must not persist",
        "token-value",
    ):
        assert forbidden not in rendered
    assert _safe_request_id(b"4111111111111111").startswith("req-")
