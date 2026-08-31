from typing import Protocol

from fastapi import HTTPException, Request

from app.models.identity import UserRole
from app.shared.config import get_settings

from .principal import CurrentPrincipal


class IdentityProvider(Protocol):
    """为真实 OIDC 预留的接口；目前只有 Demo 实现。"""

    def resolve(self, request: Request) -> CurrentPrincipal: ...


# 固定合成身份，与 data/fixtures/world.json 对齐；仅开发/测试环境使用
_DEMO_PRINCIPALS: dict[str, CurrentPrincipal] = {
    "user-nw-customer-01": CurrentPrincipal(
        user_id="user-nw-customer-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-nw-01",
    ),
    "user-nw-customer-02": CurrentPrincipal(
        user_id="user-nw-customer-02",
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-nw-02",
    ),
    "user-nw-agent-01": CurrentPrincipal(
        user_id="user-nw-agent-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.agent,
        verification_level="demo_verified",
    ),
    "user-nw-agent-02": CurrentPrincipal(
        user_id="user-nw-agent-02",
        tenant_id="tenant-northwind-demo",
        role=UserRole.agent,
        verification_level="demo_verified",
    ),
    "user-nw-supervisor-01": CurrentPrincipal(
        user_id="user-nw-supervisor-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.supervisor,
        verification_level="demo_verified",
    ),
    "user-nw-knowledge-operator-01": CurrentPrincipal(
        user_id="user-nw-knowledge-operator-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.knowledge_operator,
        verification_level="demo_verified",
    ),
    "user-ct-customer-01": CurrentPrincipal(
        user_id="user-ct-customer-01",
        tenant_id="tenant-contoso-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-ct-01",
    ),
    "user-ct-customer-02": CurrentPrincipal(
        user_id="user-ct-customer-02",
        tenant_id="tenant-contoso-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-ct-02",
    ),
    "user-ct-agent-01": CurrentPrincipal(
        user_id="user-ct-agent-01",
        tenant_id="tenant-contoso-demo",
        role=UserRole.agent,
        verification_level="demo_verified",
    ),
    "user-ct-agent-02": CurrentPrincipal(
        user_id="user-ct-agent-02",
        tenant_id="tenant-contoso-demo",
        role=UserRole.agent,
        verification_level="demo_verified",
    ),
    "user-ct-supervisor-01": CurrentPrincipal(
        user_id="user-ct-supervisor-01",
        tenant_id="tenant-contoso-demo",
        role=UserRole.supervisor,
        verification_level="demo_verified",
    ),
    "user-ct-knowledge-operator-01": CurrentPrincipal(
        user_id="user-ct-knowledge-operator-01",
        tenant_id="tenant-contoso-demo",
        role=UserRole.knowledge_operator,
        verification_level="demo_verified",
    ),
}


class DemoIdentityProvider:
    def resolve(self, request: Request) -> CurrentPrincipal:
        settings = get_settings()
        if (
            settings.app_env not in {"development", "test"}
            or not settings.allow_demo_identity
        ):
            raise HTTPException(status_code=503, detail="Demo identity is disabled")

        user_id = request.headers.get("X-Demo-User-ID")
        if user_id not in _DEMO_PRINCIPALS:
            raise HTTPException(status_code=401, detail="缺少或未知的 X-Demo-User-ID")
        return _DEMO_PRINCIPALS[user_id]
