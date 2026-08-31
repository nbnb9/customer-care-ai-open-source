"""R2 deterministic eligibility rules over server-owned order facts."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.after_sales import AfterSalesActionType

from .ports import AfterSalesOrderFactsPort, OwnedOrderFacts


@dataclass(frozen=True)
class PolicyDecision:
    facts: OwnedOrderFacts
    action_type: AfterSalesActionType
    eligible: bool
    decision_code: str

    @property
    def policy_snapshot(self) -> dict[str, object]:
        return {
            "policy_version": self.facts.policy_version,
            "eligible": self.eligible,
            "decision_code": self.decision_code,
            "action_type": self.action_type.value,
            "order_number": self.facts.order_number,
            "order_status": self.facts.status,
            "warranty_active": self.facts.warranty_active,
        }

    @property
    def customer_summary(self) -> dict[str, object]:
        action_label = {
            AfterSalesActionType.return_request: "退货申请",
            AfterSalesActionType.replacement: "补发申请",
            AfterSalesActionType.complaint: "投诉案例",
        }[self.action_type]
        impact = {
            AfterSalesActionType.return_request: (
                "经人工批准后创建受控退货申请；不退款、不取消订单。"
            ),
            AfterSalesActionType.replacement: (
                "经人工批准后创建受控补发申请；不退款、不取消订单。"
            ),
            AfterSalesActionType.complaint: (
                "经人工批准后创建受控投诉案例；不向外部渠道发送消息。"
            ),
        }[self.action_type]
        return {
            "action_label": action_label,
            "impact_summary": impact,
            # Fixture intentionally contains no price/benefit facts.  A null
            # value is safer and more truthful than fabricating a refund amount.
            "amount_minor": None,
            "currency": None,
            "manual_approval_required": True,
            "risk_notices": [
                "确认不代表退款、取消订单或向外部发送消息。",
                "提交后仍需坐席审批，且仅由受控 Worker 执行允许的动作。",
            ],
        }


def evaluate_policy(
    *,
    adapter: AfterSalesOrderFactsPort,
    tenant_id: str,
    customer_id: str,
    order_id: str,
    action_type: AfterSalesActionType,
) -> PolicyDecision | None:
    """Return only a deterministic decision on a server-scoped owned order."""

    facts = adapter.lookup_owned_order_facts(
        tenant_id=tenant_id,
        customer_id=customer_id,
        order_id=order_id,
    )
    if facts is None:
        return None

    if action_type is AfterSalesActionType.return_request:
        eligible = facts.status == "delivered"
        code = "RETURN_ELIGIBLE" if eligible else "RETURN_REQUIRES_DELIVERED"
    elif action_type is AfterSalesActionType.replacement:
        eligible = facts.status == "delivered" and facts.warranty_active
        code = (
            "REPLACEMENT_ELIGIBLE"
            if eligible
            else "REPLACEMENT_REQUIRES_DELIVERED_ACTIVE_WARRANTY"
        )
    else:
        # The mock can record a complaint for an owned order, but cannot send a
        # public message.  Human approval remains mandatory for every action.
        eligible = True
        code = "COMPLAINT_ELIGIBLE"
    return PolicyDecision(
        facts=facts,
        action_type=action_type,
        eligible=eligible,
        decision_code=code,
    )
