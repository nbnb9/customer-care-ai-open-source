import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.modules.customer_tools.errors import (
    CustomerToolsConfigurationError,
    CustomerToolsUnavailableError,
)
from app.modules.customer_tools.shopify_adapter import (
    ShopifyCustomerToolsAdapter,
    ShopifySandboxBindings,
)
from app.shared.config import Settings

pytestmark = pytest.mark.offline

SHOP = "careops-sandbox.myshopify.com"
CUSTOMER_GID = "gid://shopify/Customer/1001"


def binding_payload(
    *, shop_domain: str = SHOP, customer_gid: str = CUSTOMER_GID
) -> dict[str, object]:
    return {
        "version": 1,
        "tenants": {
            "tenant-northwind-demo": {
                "shop_domain": shop_domain,
                "customers": {"customer-nw-01": customer_gid},
                "orders": {"order-nw-1001": "gid://shopify/Order/2001"},
                "warranty_policy": {
                    "version": "northwind-cn-v1",
                    "coverage_days": 365,
                },
            }
        },
    }


def order_response(*, customer_gid: str = CUSTOMER_GID) -> dict[str, object]:
    return {
        "data": {
            "orders": {
                "nodes": [
                    {
                        "id": "gid://shopify/Order/2001",
                        "name": "#1001",
                        "createdAt": "2026-01-10T08:00:00Z",
                        "cancelledAt": None,
                        "displayFulfillmentStatus": "FULFILLED",
                        "customer": {"id": customer_gid},
                        "returns": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/Return/3001",
                                    "status": "OPEN",
                                }
                            ]
                        },
                    }
                ]
            }
        }
    }


def adapter_with_handler(
    handler,
    *,
    now: datetime = datetime(2026, 8, 30, tzinfo=UTC),
) -> ShopifyCustomerToolsAdapter:
    return ShopifyCustomerToolsAdapter(
        bindings=ShopifySandboxBindings.from_payload(binding_payload()),
        client_id="client-id-not-a-real-secret",
        client_secret="client-secret-not-a-real-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now,
    )


def test_shopify_adapter_uses_exact_hosts_minimal_query_and_cached_token():
    calls = {"token": 0, "graphql": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            calls["token"] += 1
            assert request.url.host == "careops-sandbox.myshopify.com"
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["client_credentials"],
                "client_id": ["client-id-not-a-real-secret"],
                "client_secret": ["client-secret-not-a-real-secret"],
            }
            return httpx.Response(
                200,
                json={
                    "access_token": "short-lived-test-token",
                    "scope": "read_orders,read_returns",
                    "expires_in": 86399,
                },
            )

        calls["graphql"] += 1
        assert request.url == (
            "https://careops-sandbox.myshopify.com/admin/api/2026-07/graphql.json"
        )
        assert request.headers["x-shopify-access-token"] == ("short-lived-test-token")
        body = json.loads(request.content)
        assert body["variables"] == {"query": "name:1001"}
        query = body["query"]
        assert "customer { id }" in query
        assert "returns(first: 5)" in query
        for forbidden in ("email", "phone", "address", "shippingAddress"):
            assert forbidden not in query
        return httpx.Response(200, json=order_response())

    adapter = adapter_with_handler(handler)
    result = adapter.query_order_status(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="1001",
    )
    warranty = adapter.query_warranty_eligibility(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="1001",
    )

    assert result is not None
    assert result.order_number == "#1001"
    assert result.status == "fulfilled"
    assert result.ordered_at == datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
    assert warranty is not None
    assert warranty.eligible is True
    assert warranty.coverage_ends_at == datetime(2027, 1, 10, 8, 0, tzinfo=UTC)
    assert calls == {"token": 1, "graphql": 2}
    assert adapter.diagnostic_summary() == {
        "adapter": "shopify-sandbox-read-v1",
        "api_version": "2026-07",
        "tenant_bindings": 1,
        "required_scopes": ["read_orders", "read_returns"],
        "external_writes": False,
    }


def test_shopify_adapter_rejects_foreign_customer_after_provider_read():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(
                200,
                json={
                    "access_token": "test-token",
                    "scope": "read_orders read_returns",
                    "expires_in": 86399,
                },
            )
        return httpx.Response(
            200,
            json=order_response(customer_gid="gid://shopify/Customer/9999"),
        )

    result = adapter_with_handler(handler).query_order_status(
        tenant_id="tenant-northwind-demo",
        verified_customer_id="customer-nw-01",
        candidate_order_number="1001",
    )

    assert result is None


def test_shopify_after_sales_facts_use_bound_order_and_fail_closed_on_delivery():
    seen_graphql_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(
                200,
                json={
                    "access_token": "test-token",
                    "scope": "read_orders read_returns",
                    "expires_in": 86399,
                },
            )
        seen_graphql_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"data": {"order": order_response()["data"]["orders"]["nodes"][0]}},
        )

    facts = adapter_with_handler(handler).lookup_owned_order_facts(
        tenant_id="tenant-northwind-demo",
        customer_id="customer-nw-01",
        order_id="order-nw-1001",
    )

    assert facts is not None
    assert facts.order_id == "order-nw-1001"
    assert facts.order_number == "#1001"
    assert facts.status == "fulfilled"
    assert facts.warranty_active is True
    assert facts.policy_version == "r2-shopify-read-v1:northwind-cn-v1"
    assert facts.source_system == "shopify_sandbox"
    assert seen_graphql_body["variables"] == {"orderId": "gid://shopify/Order/2001"}
    query = str(seen_graphql_body["query"])
    assert "order(id: $orderId)" in query
    for forbidden in ("email", "phone", "address", "shippingAddress"):
        assert forbidden not in query


def test_shopify_after_sales_facts_reject_foreign_customer_and_unbound_order():
    calls = {"graphql": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(
                200,
                json={
                    "access_token": "test-token",
                    "scope": "read_orders read_returns",
                    "expires_in": 86399,
                },
            )
        calls["graphql"] += 1
        foreign = order_response(customer_gid="gid://shopify/Customer/9999")
        return httpx.Response(
            200,
            json={"data": {"order": foreign["data"]["orders"]["nodes"][0]}},
        )

    adapter = adapter_with_handler(handler)
    assert (
        adapter.lookup_owned_order_facts(
            tenant_id="tenant-northwind-demo",
            customer_id="customer-nw-01",
            order_id="order-nw-1001",
        )
        is None
    )
    assert (
        adapter.lookup_owned_order_facts(
            tenant_id="tenant-northwind-demo",
            customer_id="customer-nw-01",
            order_id="order-nw-unknown",
        )
        is None
    )
    assert calls == {"graphql": 1}


def test_shopify_adapter_does_not_call_provider_for_unbound_scope():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected external request: {request.url}")

    adapter = adapter_with_handler(handler)

    assert (
        adapter.query_order_status(
            tenant_id="tenant-contoso-demo",
            verified_customer_id="customer-ct-01",
            candidate_order_number="1001",
        )
        is None
    )


def test_shopify_adapter_fails_closed_when_scope_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "test-token",
                "scope": "read_orders",
                "expires_in": 86399,
            },
        )

    with pytest.raises(
        CustomerToolsConfigurationError, match="missing required read scopes"
    ):
        adapter_with_handler(handler).query_order_status(
            tenant_id="tenant-northwind-demo",
            verified_customer_id="customer-nw-01",
            candidate_order_number="1001",
        )


def test_shopify_adapter_maps_timeout_without_secret_or_order_leakage():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream included private material", request=request)

    with pytest.raises(CustomerToolsUnavailableError) as exc_info:
        adapter_with_handler(handler).query_order_status(
            tenant_id="tenant-northwind-demo",
            verified_customer_id="customer-nw-01",
            candidate_order_number="customer-visible-order-1001",
        )

    error_text = str(exc_info.value)
    assert "client-secret-not-a-real-secret" not in error_text
    assert "customer-visible-order-1001" not in error_text
    assert error_text == "Shopify token exchange is unavailable"


@pytest.mark.parametrize(
    "shop_domain",
    [
        "https://careops-sandbox.myshopify.com",
        "careops-sandbox.myshopify.com.evil.test",
        "CAREOPS-SANDBOX.myshopify.com",
        "careops_sandbox.myshopify.com",
    ],
)
def test_shopify_bindings_reject_ssrf_shaped_shop_domains(shop_domain: str):
    with pytest.raises(
        CustomerToolsConfigurationError, match=r"exact myshopify\.com host"
    ):
        ShopifySandboxBindings.from_payload(binding_payload(shop_domain=shop_domain))


def test_shopify_provider_settings_are_fail_closed():
    base = {
        "_env_file": None,
        "app_env": "development",
        "database_url": "postgresql+psycopg://app:password@localhost/example",
    }
    with pytest.raises(ValueError, match="Missing Shopify sandbox file settings"):
        Settings(**base, customer_tools_provider="shopify_sandbox")
    with pytest.raises(ValueError, match="not supported by the read-only adapter"):
        Settings(**base, shopify_external_writes_enabled=True)
    with pytest.raises(ValueError, match="pinned to 2026-07"):
        Settings(**base, shopify_api_version="unstable")
