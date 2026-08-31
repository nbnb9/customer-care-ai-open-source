from app.models.identity import UserRole
from app.shared.config import get_settings

from .principal import CurrentPrincipal

# R1 read tools may only use an identity context that has already been verified
# by the identity provider.  `demo_verified` is deliberately accepted only to
# support the synthetic development/test provider; a production provider must
# map its verified assurance state to `verified` before it constructs the
# CurrentPrincipal.
VERIFIED_CUSTOMER_LEVELS = frozenset({"demo_verified", "verified"})


def has_verified_customer_context(principal: CurrentPrincipal) -> bool:
    if (
        principal.role is not UserRole.customer
        or principal.customer_id is None
        or principal.verification_level not in VERIFIED_CUSTOMER_LEVELS
    ):
        return False
    if principal.verification_level == "demo_verified":
        return get_settings().app_env in {"development", "test"}
    return True


def can_view_ticket(
    principal: CurrentPrincipal, *, ticket_tenant_id: str, ticket_customer_id: str
) -> bool:
    if principal.role is UserRole.customer:
        # 客户只能看自己的工单（同租户 + 工单属于自己）
        return (
            principal.tenant_id == ticket_tenant_id
            and principal.customer_id == ticket_customer_id
        )
    # 只有坐席/主管拥有服务台队列权限；知识运营角色不自动获得工单权限。
    return principal.role in (UserRole.agent, UserRole.supervisor) and (
        principal.tenant_id == ticket_tenant_id
    )


def can_update_ticket(principal: CurrentPrincipal, *, ticket_tenant_id: str) -> bool:
    if principal.role is UserRole.customer:
        return False  # 客户不能改工单，只能评论（评论在步骤 18 单独处理）
    return principal.role in (UserRole.agent, UserRole.supervisor) and (
        principal.tenant_id == ticket_tenant_id
    )


def can_view_queue(principal: CurrentPrincipal) -> bool:
    # 队列是坐席/主管视角，客户没有队列
    return principal.role in (UserRole.agent, UserRole.supervisor)
