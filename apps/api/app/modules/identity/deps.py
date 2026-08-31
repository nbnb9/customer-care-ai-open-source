from fastapi import Request

from app.shared.observability import bind_principal

from .principal import CurrentPrincipal
from .provider import DemoIdentityProvider, IdentityProvider

_provider: IdentityProvider = DemoIdentityProvider()


def get_current_principal(request: Request) -> CurrentPrincipal:
    principal = _provider.resolve(request)
    # Trace correlation needs tenant/role for later local-viewer ACL, but never
    # persists an end-user or verified-customer identifier in telemetry.
    bind_principal(tenant_id=principal.tenant_id, role=principal.role.value)
    return principal
