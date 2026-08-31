"""Server-controlled R1 read-tool orchestration and privacy-safe auditing."""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.modules.identity.policies import has_verified_customer_context
from app.modules.identity.principal import CurrentPrincipal
from app.shared.middleware import request_id_var
from app.shared.observability import observe_span, set_span_attributes

from .errors import CustomerToolsProviderError
from .factory import get_customer_tools_adapter
from .ports import OrderQueryPort, WarrantyEligibilityPort
from .schemas import (
    OrderLookupRequest,
    OrderStatusRead,
    WarrantyEligibilityRead,
)


def _request_id() -> str:
    value = request_id_var.get()
    return value if value != "-" else f"request-{uuid.uuid4().hex}"


def _json_summary(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_verified_customer(principal: CurrentPrincipal) -> str:
    if not has_verified_customer_context(principal):
        raise HTTPException(
            status_code=403,
            detail="Only verified customers can use customer read tools",
        )
    # The policy above is the single authorization point. This assert teaches
    # type checkers and future maintainers that request data never fills it.
    assert principal.customer_id is not None
    return principal.customer_id


def _record_read(
    db: Session,
    *,
    principal: CurrentPrincipal,
    tool_name: str,
    result_status: str,
) -> None:
    """Persist an audit event without candidate order numbers or PII fields."""

    db.add(
        AuditEvent(
            id=f"audit-{uuid.uuid4().hex}",
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action=f"customer_read_tool.{tool_name}.read",
            resource_type="customer_read_tool",
            # Do not place an order number in resource_id: it is externally
            # supplied and would otherwise be copied into every trace.
            resource_id=tool_name,
            before_summary=_json_summary(
                {
                    "parameter_summary": {
                        "candidate_order_number": "provided",
                        "verified_customer_id": "server_derived",
                    },
                    "tool_name": tool_name,
                }
            ),
            after_summary=_json_summary({"result_status": result_status}),
            request_id=_request_id(),
        )
    )
    db.commit()


def query_order_status(
    db: Session,
    principal: CurrentPrincipal,
    data: OrderLookupRequest,
    *,
    port: OrderQueryPort | None = None,
) -> OrderStatusRead:
    verified_customer_id = _require_verified_customer(principal)
    adapter = port if port is not None else get_customer_tools_adapter()
    with observe_span(
        "tool.order_status",
        kind=SpanKind.CLIENT,
        attributes={"customer_care.tool.name": "order_status"},
    ):
        try:
            result = adapter.query_order_status(
                tenant_id=principal.tenant_id,
                verified_customer_id=verified_customer_id,
                candidate_order_number=data.order_number,
            )
        except CustomerToolsProviderError as exc:
            set_span_attributes({"customer_care.tool.outcome": "provider_unavailable"})
            _record_read(
                db,
                principal=principal,
                tool_name="order_status",
                result_status="provider_unavailable",
            )
            raise HTTPException(
                status_code=503,
                detail="Order provider is temporarily unavailable",
            ) from exc
        if result is None:
            set_span_attributes(
                {"customer_care.tool.outcome": "not_found_or_not_owned"}
            )
            _record_read(
                db,
                principal=principal,
                tool_name="order_status",
                result_status="not_found_or_not_owned",
            )
            # Use the same response for a foreign and a nonexistent order to avoid
            # turning this read endpoint into an ownership oracle.
            raise HTTPException(status_code=404, detail="Order not found")

        set_span_attributes({"customer_care.tool.outcome": "found"})
        _record_read(
            db,
            principal=principal,
            tool_name="order_status",
            result_status="found",
        )
    return OrderStatusRead(
        order_number=result.order_number,
        status=result.status,
        ordered_at=result.ordered_at,
    )


def query_warranty_eligibility(
    db: Session,
    principal: CurrentPrincipal,
    data: OrderLookupRequest,
    *,
    port: WarrantyEligibilityPort | None = None,
) -> WarrantyEligibilityRead:
    verified_customer_id = _require_verified_customer(principal)
    adapter = port if port is not None else get_customer_tools_adapter()
    with observe_span(
        "tool.warranty_eligibility",
        kind=SpanKind.CLIENT,
        attributes={"customer_care.tool.name": "warranty_eligibility"},
    ):
        try:
            result = adapter.query_warranty_eligibility(
                tenant_id=principal.tenant_id,
                verified_customer_id=verified_customer_id,
                candidate_order_number=data.order_number,
            )
        except CustomerToolsProviderError as exc:
            set_span_attributes({"customer_care.tool.outcome": "provider_unavailable"})
            _record_read(
                db,
                principal=principal,
                tool_name="warranty_eligibility",
                result_status="provider_unavailable",
            )
            raise HTTPException(
                status_code=503,
                detail="Order provider is temporarily unavailable",
            ) from exc
        if result is None:
            set_span_attributes(
                {"customer_care.tool.outcome": "not_found_or_not_owned"}
            )
            _record_read(
                db,
                principal=principal,
                tool_name="warranty_eligibility",
                result_status="not_found_or_not_owned",
            )
            raise HTTPException(status_code=404, detail="Order not found")

        set_span_attributes({"customer_care.tool.outcome": "found"})
        _record_read(
            db,
            principal=principal,
            tool_name="warranty_eligibility",
            result_status="found",
        )
    return WarrantyEligibilityRead(
        order_number=result.order_number,
        eligible=result.eligible,
        coverage_ends_at=result.coverage_ends_at,
    )
