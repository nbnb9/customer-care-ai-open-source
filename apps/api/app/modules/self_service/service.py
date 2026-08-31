"""Server-side R0 policy and tenant-local feature flag.

The flag is intentionally evaluated from the database for every customer
message. A supervisor can therefore stop the tenant's R0 path without waiting
for an application restart; the environment master switch remains a separate
deployment-level guard.
"""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.identity import Customer, UserRole
from app.models.self_service import R0SelfServiceFeatureFlag
from app.modules.identity.principal import CurrentPrincipal
from app.shared.config import get_settings
from app.shared.middleware import request_id_var
from app.shared.security import contains_prompt_injection

from .schemas import R0SelfServiceFeatureFlagRead, R0SelfServiceFeatureFlagUpdate


def _request_id() -> str:
    value = request_id_var.get()
    return value if value != "-" else f"request-{uuid.uuid4().hex}"


def _require_supervisor(principal: CurrentPrincipal) -> None:
    if principal.role is not UserRole.supervisor:
        raise HTTPException(status_code=403, detail="Supervisor role is required")


def _to_read(
    flag: R0SelfServiceFeatureFlag, *, master_enabled: bool
) -> R0SelfServiceFeatureFlagRead:
    has_targets = flag.allow_all_customers or bool(flag.customer_ids)
    return R0SelfServiceFeatureFlagRead(
        tenant_id=flag.tenant_id,
        enabled=flag.enabled,
        allow_all_customers=flag.allow_all_customers,
        customer_ids=list(flag.customer_ids),
        master_enabled=master_enabled,
        effective_for_tenant=master_enabled and flag.enabled and has_targets,
    )


def read_r0_feature_flag(
    db: Session, principal: CurrentPrincipal
) -> R0SelfServiceFeatureFlagRead:
    _require_supervisor(principal)
    settings = get_settings()
    flag = db.get(R0SelfServiceFeatureFlag, principal.tenant_id)
    if flag is None:
        return R0SelfServiceFeatureFlagRead(
            tenant_id=principal.tenant_id,
            enabled=False,
            allow_all_customers=False,
            customer_ids=[],
            master_enabled=settings.r0_self_service_master_enabled,
            effective_for_tenant=False,
        )
    return _to_read(flag, master_enabled=settings.r0_self_service_master_enabled)


def update_r0_feature_flag(
    db: Session,
    principal: CurrentPrincipal,
    data: R0SelfServiceFeatureFlagUpdate,
) -> R0SelfServiceFeatureFlagRead:
    _require_supervisor(principal)
    request_id = _request_id()
    requested_customer_ids = set(data.customer_ids)
    if requested_customer_ids:
        available_customer_ids = set(
            db.scalars(
                select(Customer.id).where(
                    Customer.tenant_id == principal.tenant_id,
                    Customer.id.in_(requested_customer_ids),
                )
            )
        )
        if available_customer_ids != requested_customer_ids:
            # Do not reveal whether an unknown ID belongs to a different tenant.
            raise HTTPException(
                status_code=400,
                detail=(
                    "Feature-flag customer allowlist contains an unavailable customer"
                ),
            )

    flag = db.scalar(
        select(R0SelfServiceFeatureFlag)
        .where(R0SelfServiceFeatureFlag.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    before_summary = None
    if flag is None:
        flag = R0SelfServiceFeatureFlag(tenant_id=principal.tenant_id)
        db.add(flag)
        try:
            # A missing row has no row lock. Flush first so concurrent first
            # writers resolve the primary-key race before we add the audit row
            # and apply the final last-writer-wins configuration.
            db.flush()
        except IntegrityError:
            db.rollback()
            flag = db.scalar(
                select(R0SelfServiceFeatureFlag)
                .where(R0SelfServiceFeatureFlag.tenant_id == principal.tenant_id)
                .with_for_update()
            )
            if flag is None:
                raise
            before_summary = json.dumps(
                {
                    "enabled": flag.enabled,
                    "allow_all_customers": flag.allow_all_customers,
                    "customer_ids": list(flag.customer_ids),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    else:
        before_summary = json.dumps(
            {
                "enabled": flag.enabled,
                "allow_all_customers": flag.allow_all_customers,
                "customer_ids": list(flag.customer_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    flag.enabled = data.enabled
    flag.allow_all_customers = data.allow_all_customers
    flag.customer_ids = list(data.customer_ids)
    flag.updated_by = principal.user_id
    after_summary = json.dumps(
        {
            "enabled": flag.enabled,
            "allow_all_customers": flag.allow_all_customers,
            "customer_ids": list(flag.customer_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    db.add(
        AuditEvent(
            id=f"audit-{uuid.uuid4().hex}",
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="r0_self_service.feature_flag_updated",
            resource_type="r0_self_service_feature_flag",
            resource_id=principal.tenant_id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=request_id,
        )
    )
    db.commit()
    db.refresh(flag)
    return _to_read(flag, master_enabled=get_settings().r0_self_service_master_enabled)


def is_r0_self_service_enabled(
    db: Session,
    principal: CurrentPrincipal,
    *,
    for_update: bool = False,
) -> bool:
    """Return the effective per-customer R0 entitlement, defaulting closed."""

    settings = get_settings()
    if (
        not settings.r0_self_service_master_enabled
        or principal.role is not UserRole.customer
        or principal.customer_id is None
    ):
        return False
    statement = select(R0SelfServiceFeatureFlag).where(
        R0SelfServiceFeatureFlag.tenant_id == principal.tenant_id
    )
    if for_update:
        statement = statement.with_for_update()
    flag = db.scalar(statement)
    if flag is None or not flag.enabled:
        return False
    return flag.allow_all_customers or principal.customer_id in flag.customer_ids


def r0_handoff_reason(text: str) -> str | None:
    """Deterministically keep R1-R3 and forced-human content out of R0."""

    normalized = text.lower()
    if contains_prompt_injection(text) or any(
        marker in normalized
        for marker in (
            "忽略",
            "绕过",
            "ignore previous",
            "system prompt",
            "提示词",
            "prompt",
            "安全",
            "泄漏",
            "泄露",
            "黑客",
            "越权",
            "security",
        )
    ):
        return "safety_review"
    if any(
        marker in normalized
        for marker in (
            "我要人工",
            "转人工",
            "人工客服",
            "真人客服",
            "人工服务",
            "human agent",
        )
    ):
        return "customer_requested"
    if any(
        marker in normalized
        for marker in (
            "退款",
            "退货",
            "补发",
            "补寄",
            "换货",
            "换新",
            "refund",
            "replacement",
            "replace",
            "投诉",
            "complain",
            "支付",
            "付款",
            "扣款",
            "银行卡",
            "信用卡",
            "payment",
            "charge",
            "隐私",
            "个人信息",
            "删除我的数据",
            "数据删除",
            "privacy",
            "gdpr",
        )
    ):
        return "policy_action"
    if any(
        marker in normalized
        for marker in (
            "订单",
            "物流",
            "快递",
            "发货",
            "收货",
            "配送",
            "取消订单",
            "修改地址",
            "更改地址",
            "发票",
            "order",
            "shipping",
            "track",
            "delivery",
        )
    ):
        return "order_or_account_action"
    if any(
        marker in normalized
        for marker in (
            "无法登录",
            "登录",
            "账号",
            "账户",
            "密码",
            "修改邮箱",
            "更改邮箱",
            "绑定",
            "解绑",
            "注销",
            "login",
            "account",
            "password",
        )
    ):
        return "identity_or_account"
    return None
