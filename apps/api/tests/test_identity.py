from conftest import auth

from app.models.identity import UserRole
from app.modules.identity.policies import (
    can_update_ticket,
    can_view_queue,
    can_view_ticket,
)
from app.modules.identity.principal import CurrentPrincipal
from app.shared.config import get_settings


def _p(user_id, tenant_id, role, customer_id=None):
    return CurrentPrincipal(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        verification_level="demo_verified",
        customer_id=customer_id,
    )


def test_customer_can_view_own_ticket():
    p = _p(
        "user-nw-customer-01",
        "tenant-northwind-demo",
        UserRole.customer,
        "customer-nw-01",
    )
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-northwind-demo",
            ticket_customer_id="customer-nw-01",
        )
        is True
    )


def test_customer_cannot_view_other_customer_ticket():
    p = _p(
        "user-nw-customer-01",
        "tenant-northwind-demo",
        UserRole.customer,
        "customer-nw-01",
    )
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-northwind-demo",
            ticket_customer_id="customer-nw-02",
        )
        is False
    )


def test_customer_cannot_view_cross_tenant_ticket():
    p = _p(
        "user-nw-customer-01",
        "tenant-northwind-demo",
        UserRole.customer,
        "customer-nw-01",
    )
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-contoso-demo",
            ticket_customer_id="customer-ct-01",
        )
        is False
    )


def test_agent_can_view_own_tenant_ticket():
    p = _p("user-nw-agent-01", "tenant-northwind-demo", UserRole.agent)
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-northwind-demo",
            ticket_customer_id="customer-nw-01",
        )
        is True
    )


def test_agent_cannot_view_cross_tenant_ticket():
    p = _p("user-nw-agent-01", "tenant-northwind-demo", UserRole.agent)
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-contoso-demo",
            ticket_customer_id="customer-ct-01",
        )
        is False
    )


def test_supervisor_can_view_team_scope():
    p = _p("user-nw-supervisor-01", "tenant-northwind-demo", UserRole.supervisor)
    assert (
        can_view_ticket(
            p,
            ticket_tenant_id="tenant-northwind-demo",
            ticket_customer_id="customer-nw-02",
        )
        is True
    )


def test_customer_cannot_update_ticket():
    p = _p(
        "user-nw-customer-01",
        "tenant-northwind-demo",
        UserRole.customer,
        "customer-nw-01",
    )
    assert can_update_ticket(p, ticket_tenant_id="tenant-northwind-demo") is False


def test_agent_can_update_own_tenant_ticket():
    p = _p("user-nw-agent-01", "tenant-northwind-demo", UserRole.agent)
    assert can_update_ticket(p, ticket_tenant_id="tenant-northwind-demo") is True


def test_customer_cannot_view_queue():
    p = _p(
        "user-nw-customer-01",
        "tenant-northwind-demo",
        UserRole.customer,
        "customer-nw-01",
    )
    assert can_view_queue(p) is False


def test_agent_can_view_queue():
    p = _p("user-nw-agent-01", "tenant-northwind-demo", UserRole.agent)
    assert can_view_queue(p) is True


def test_demo_identity_requires_explicit_gate(client):
    settings = get_settings()
    settings.allow_demo_identity = False
    try:
        res = client.get("/whoami", headers=auth("user-nw-agent-01"))
        assert res.status_code == 503
    finally:
        settings.allow_demo_identity = True


def test_demo_identity_allows_explicit_test_environment(client):
    settings = get_settings()
    original_env = settings.app_env
    settings.app_env = "test"
    try:
        res = client.get("/whoami", headers=auth("user-nw-agent-01"))
        assert res.status_code == 200
    finally:
        settings.app_env = original_env


def test_demo_identity_remains_disabled_in_production(client):
    settings = get_settings()
    original_env = settings.app_env
    settings.app_env = "production"
    try:
        res = client.get("/whoami", headers=auth("user-nw-agent-01"))
        assert res.status_code == 503
    finally:
        settings.app_env = original_env
