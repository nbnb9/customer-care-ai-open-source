"""Read-only Shopify Dev Store adapter for the Step 41.5 sandbox gate."""

from __future__ import annotations

import json
import re
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.modules.after_sales.ports import OwnedOrderFacts

from .errors import (
    CustomerToolsConfigurationError,
    CustomerToolsUnavailableError,
)
from .ports import OrderStatusResult, WarrantyEligibilityResult

_SHOP_DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")
_CUSTOMER_GID = re.compile(r"gid://shopify/Customer/[1-9][0-9]*")
_ORDER_GID = re.compile(r"gid://shopify/Order/[1-9][0-9]*")
_SAFE_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REQUIRED_SCOPES = frozenset({"read_orders", "read_returns"})

_ORDER_QUERY = """
query OrderForVerifiedCustomer($query: String!) {
  orders(first: 2, query: $query) {
    nodes {
      id
      name
      createdAt
      cancelledAt
      displayFulfillmentStatus
      customer { id }
      returns(first: 5) { nodes { id status } }
    }
  }
}
""".strip()

_ORDER_FACTS_QUERY = """
query OrderFactsForVerifiedCustomer($orderId: ID!) {
  order(id: $orderId) {
    id
    name
    createdAt
    cancelledAt
    displayFulfillmentStatus
    customer { id }
    returns(first: 5) { nodes { id status } }
  }
}
""".strip()


@dataclass(frozen=True)
class ShopifyTenantBinding:
    shop_domain: str
    customers: Mapping[str, str]
    orders: Mapping[str, str]
    warranty_policy_version: str
    warranty_coverage_days: int


@dataclass(frozen=True)
class ShopifySandboxBindings:
    tenants: Mapping[str, ShopifyTenantBinding]

    @classmethod
    def from_payload(cls, payload: object) -> ShopifySandboxBindings:
        root = _require_mapping(payload, "bindings")
        _require_exact_keys(root, {"version", "tenants"}, "bindings")
        if root.get("version") != 1:
            raise CustomerToolsConfigurationError("Shopify bindings version must be 1")
        tenant_payload = _require_mapping(root.get("tenants"), "tenants")
        if not tenant_payload:
            raise CustomerToolsConfigurationError(
                "Shopify bindings must contain at least one tenant"
            )

        tenants: dict[str, ShopifyTenantBinding] = {}
        for tenant_id, raw_binding in tenant_payload.items():
            if not isinstance(tenant_id, str) or not _SAFE_TENANT.fullmatch(tenant_id):
                raise CustomerToolsConfigurationError(
                    "Shopify bindings contain an invalid tenant identifier"
                )
            binding = _require_mapping(raw_binding, "tenant binding")
            _require_exact_keys(
                binding,
                {"shop_domain", "customers", "orders", "warranty_policy"},
                "tenant binding",
            )
            shop_domain = binding.get("shop_domain")
            if not isinstance(shop_domain, str) or not _SHOP_DOMAIN.fullmatch(
                shop_domain
            ):
                raise CustomerToolsConfigurationError(
                    "Shopify shop_domain must be an exact myshopify.com host"
                )

            customers = _require_mapping(binding.get("customers"), "customers")
            if not customers:
                raise CustomerToolsConfigurationError(
                    "Each Shopify tenant binding needs a customer mapping"
                )
            customer_map: dict[str, str] = {}
            for customer_id, customer_gid in customers.items():
                if (
                    not isinstance(customer_id, str)
                    or not _SAFE_TENANT.fullmatch(customer_id)
                    or not isinstance(customer_gid, str)
                    or not _CUSTOMER_GID.fullmatch(customer_gid)
                ):
                    raise CustomerToolsConfigurationError(
                        "Shopify bindings contain an invalid customer mapping"
                    )
                customer_map[customer_id] = customer_gid

            orders = _require_mapping(binding.get("orders"), "orders")
            if not orders:
                raise CustomerToolsConfigurationError(
                    "Each Shopify tenant binding needs an order mapping"
                )
            order_map: dict[str, str] = {}
            for order_id, order_gid in orders.items():
                if (
                    not isinstance(order_id, str)
                    or not _SAFE_TENANT.fullmatch(order_id)
                    or not isinstance(order_gid, str)
                    or not _ORDER_GID.fullmatch(order_gid)
                ):
                    raise CustomerToolsConfigurationError(
                        "Shopify bindings contain an invalid order mapping"
                    )
                order_map[order_id] = order_gid

            policy = _require_mapping(binding.get("warranty_policy"), "warranty_policy")
            _require_exact_keys(policy, {"version", "coverage_days"}, "warranty_policy")
            policy_version = policy.get("version")
            coverage_days = policy.get("coverage_days")
            if (
                not isinstance(policy_version, str)
                or not _SAFE_TENANT.fullmatch(policy_version)
                or not isinstance(coverage_days, int)
                or isinstance(coverage_days, bool)
                or not 1 <= coverage_days <= 3650
            ):
                raise CustomerToolsConfigurationError(
                    "Shopify warranty policy is invalid"
                )
            tenants[tenant_id] = ShopifyTenantBinding(
                shop_domain=shop_domain,
                customers=customer_map,
                orders=order_map,
                warranty_policy_version=policy_version,
                warranty_coverage_days=coverage_days,
            )
        return cls(tenants=tenants)

    @classmethod
    def from_file(cls, path: Path) -> ShopifySandboxBindings:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CustomerToolsConfigurationError(
                "Shopify bindings file is unavailable or invalid"
            ) from exc
        return cls.from_payload(payload)


@dataclass(frozen=True)
class _AccessToken:
    value: str
    expires_at: float


@dataclass(frozen=True)
class _OrderSnapshot:
    order_number: str
    status: str
    ordered_at: datetime
    return_statuses: tuple[str, ...]


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CustomerToolsConfigurationError(f"Shopify {label} must be an object")
    return value


def _require_provider_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CustomerToolsUnavailableError(f"Shopify returned invalid {label} data")
    return value


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise CustomerToolsConfigurationError(
            f"Shopify {label} contains missing or unsupported fields"
        )


def _read_secret(path: Path, label: str) -> str:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise CustomerToolsConfigurationError(
                f"Shopify {label} file must be a private regular file"
            )
        value = path.read_text(encoding="utf-8").strip()
    except CustomerToolsConfigurationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise CustomerToolsConfigurationError(
            f"Shopify {label} file is unavailable"
        ) from exc
    if not value or len(value) > 4096 or any(char.isspace() for char in value):
        raise CustomerToolsConfigurationError(f"Shopify {label} is invalid")
    return value


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise CustomerToolsUnavailableError("Shopify returned an invalid order")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CustomerToolsUnavailableError(
            "Shopify returned an invalid order"
        ) from exc
    if parsed.tzinfo is None:
        raise CustomerToolsUnavailableError("Shopify returned an invalid order")
    return parsed.astimezone(UTC)


def _normalize_order_number(value: str) -> str:
    return value.removeprefix("#").casefold()


def _status(node: Mapping[str, object]) -> str:
    if node.get("cancelledAt") is not None:
        return "cancelled"
    value = node.get("displayFulfillmentStatus")
    if not isinstance(value, str) or not value:
        raise CustomerToolsUnavailableError("Shopify returned an invalid order")
    return value.casefold()


class ShopifyCustomerToolsAdapter:
    """Minimal Shopify projection with server-controlled ownership checking."""

    def __init__(
        self,
        *,
        bindings: ShopifySandboxBindings,
        client_id: str,
        client_secret: str,
        api_version: str = "2026-07",
        timeout_seconds: float = 8.0,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._bindings = bindings
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_version = api_version
        self._monotonic = monotonic
        self._now = now
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._token_lock = threading.Lock()
        self._tokens: dict[str, _AccessToken] = {}

    @classmethod
    def from_files(
        cls,
        *,
        bindings_file: Path,
        client_id_file: Path,
        client_secret_file: Path,
        api_version: str,
        timeout_seconds: float,
    ) -> ShopifyCustomerToolsAdapter:
        return cls(
            bindings=ShopifySandboxBindings.from_file(bindings_file),
            client_id=_read_secret(client_id_file, "client ID"),
            client_secret=_read_secret(client_secret_file, "client secret"),
            api_version=api_version,
            timeout_seconds=timeout_seconds,
        )

    def query_order_status(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> OrderStatusResult | None:
        snapshot = self._owned_order(
            tenant_id=tenant_id,
            verified_customer_id=verified_customer_id,
            candidate_order_number=candidate_order_number,
        )
        if snapshot is None:
            return None
        return OrderStatusResult(
            order_number=snapshot.order_number,
            status=snapshot.status,
            ordered_at=snapshot.ordered_at,
        )

    def query_warranty_eligibility(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> WarrantyEligibilityResult | None:
        binding = self._bindings.tenants.get(tenant_id)
        if binding is None:
            return None
        snapshot = self._owned_order(
            tenant_id=tenant_id,
            verified_customer_id=verified_customer_id,
            candidate_order_number=candidate_order_number,
        )
        if snapshot is None:
            return None
        coverage_ends_at = snapshot.ordered_at + timedelta(
            days=binding.warranty_coverage_days
        )
        return WarrantyEligibilityResult(
            order_number=snapshot.order_number,
            eligible=coverage_ends_at >= self._now(),
            coverage_ends_at=coverage_ends_at,
        )

    def lookup_owned_order_facts(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> OwnedOrderFacts | None:
        """Resolve an internal order binding and re-check Shopify ownership."""

        binding = self._bindings.tenants.get(tenant_id)
        if binding is None:
            return None
        customer_gid = binding.customers.get(customer_id)
        order_gid = binding.orders.get(order_id)
        if customer_gid is None or order_gid is None:
            return None

        token = self._access_token(binding.shop_domain)
        payload = self._post_json(
            f"https://{binding.shop_domain}/admin/api/{self._api_version}/graphql.json",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            json_payload={
                "query": _ORDER_FACTS_QUERY,
                "variables": {"orderId": order_gid},
            },
            operation="order facts query",
        )
        if payload.get("errors"):
            raise CustomerToolsUnavailableError("Shopify order facts query failed")
        data = _require_provider_mapping(payload.get("data"), "GraphQL")
        raw_order = data.get("order")
        if raw_order is None:
            return None
        node = _require_provider_mapping(raw_order, "order")
        if node.get("id") != order_gid:
            raise CustomerToolsUnavailableError("Shopify returned an invalid order")
        snapshot = self._owned_snapshot(node=node, customer_gid=customer_gid)
        if snapshot is None:
            return None
        coverage_ends_at = snapshot.ordered_at + timedelta(
            days=binding.warranty_coverage_days
        )
        return OwnedOrderFacts(
            order_id=order_id,
            order_number=snapshot.order_number,
            # FULFILLED does not prove carrier delivery. Keeping this distinct
            # makes return/replacement policy fail closed until delivery facts
            # are supplied by an appropriate fulfillment/carrier contract.
            status=snapshot.status,
            warranty_active=coverage_ends_at >= self._now(),
            policy_version=(f"r2-shopify-read-v1:{binding.warranty_policy_version}"),
            source_system="shopify_sandbox",
        )

    def diagnostic_summary(self) -> dict[str, object]:
        """Return non-secret configuration facts for the sandbox smoke command."""

        return {
            "adapter": "shopify-sandbox-read-v1",
            "api_version": self._api_version,
            "tenant_bindings": len(self._bindings.tenants),
            "required_scopes": sorted(_REQUIRED_SCOPES),
            "external_writes": False,
        }

    def _owned_order(
        self,
        *,
        tenant_id: str,
        verified_customer_id: str,
        candidate_order_number: str,
    ) -> _OrderSnapshot | None:
        binding = self._bindings.tenants.get(tenant_id)
        if binding is None:
            return None
        customer_gid = binding.customers.get(verified_customer_id)
        if customer_gid is None:
            return None

        token = self._access_token(binding.shop_domain)
        payload = self._post_json(
            f"https://{binding.shop_domain}/admin/api/{self._api_version}/graphql.json",
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            json_payload={
                "query": _ORDER_QUERY,
                "variables": {"query": f"name:{candidate_order_number}"},
            },
            operation="order query",
        )
        if payload.get("errors"):
            raise CustomerToolsUnavailableError("Shopify order query failed")
        data = _require_provider_mapping(payload.get("data"), "GraphQL")
        orders = _require_provider_mapping(data.get("orders"), "orders")
        nodes = orders.get("nodes")
        if not isinstance(nodes, list):
            raise CustomerToolsUnavailableError("Shopify returned an invalid order")

        exact: list[Mapping[str, object]] = []
        candidate = _normalize_order_number(candidate_order_number)
        for raw_node in nodes:
            node = _require_provider_mapping(raw_node, "order")
            name = node.get("name")
            if isinstance(name, str) and _normalize_order_number(name) == candidate:
                exact.append(node)
        if not exact:
            return None
        if len(exact) != 1:
            raise CustomerToolsUnavailableError("Shopify order match is ambiguous")

        return self._owned_snapshot(node=exact[0], customer_gid=customer_gid)

    def _owned_snapshot(
        self, *, node: Mapping[str, object], customer_gid: str
    ) -> _OrderSnapshot | None:
        node_gid = node.get("id")
        if not isinstance(node_gid, str) or not _ORDER_GID.fullmatch(node_gid):
            raise CustomerToolsUnavailableError("Shopify returned an invalid order")
        customer = node.get("customer")
        if not isinstance(customer, dict) or customer.get("id") != customer_gid:
            return None
        name = node.get("name")
        if not isinstance(name, str) or not name:
            raise CustomerToolsUnavailableError("Shopify returned an invalid order")

        returns = _require_provider_mapping(node.get("returns"), "returns")
        return_nodes = returns.get("nodes")
        if not isinstance(return_nodes, list):
            raise CustomerToolsUnavailableError("Shopify returned invalid returns")
        return_statuses: list[str] = []
        for raw_return in return_nodes:
            return_node = _require_provider_mapping(raw_return, "return")
            status = return_node.get("status")
            if not isinstance(status, str) or not status:
                raise CustomerToolsUnavailableError("Shopify returned invalid returns")
            return_statuses.append(status.casefold())

        return _OrderSnapshot(
            order_number=name,
            status=_status(node),
            ordered_at=_parse_datetime(node.get("createdAt")),
            return_statuses=tuple(return_statuses),
        )

    def _access_token(self, shop_domain: str) -> str:
        with self._token_lock:
            cached = self._tokens.get(shop_domain)
            if cached is not None and cached.expires_at - self._monotonic() > 60:
                return cached.value

            payload = self._post_json(
                f"https://{shop_domain}/admin/oauth/access_token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                form={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                operation="token exchange",
            )
            token = payload.get("access_token")
            scope = payload.get("scope")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(scope, str)
                or not isinstance(expires_in, int)
                or isinstance(expires_in, bool)
                or expires_in <= 60
            ):
                raise CustomerToolsUnavailableError(
                    "Shopify token exchange returned an invalid response"
                )
            granted = frozenset(part for part in re.split(r"[,\s]+", scope) if part)
            if not _REQUIRED_SCOPES.issubset(granted):
                raise CustomerToolsConfigurationError(
                    "Shopify token is missing required read scopes"
                )
            self._tokens[shop_domain] = _AccessToken(
                value=token,
                expires_at=self._monotonic() + expires_in,
            )
            return token

    def _post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        operation: str,
        form: Mapping[str, str] | None = None,
        json_payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = self._client.post(
                url,
                headers=dict(headers),
                data=dict(form) if form is not None else None,
                json=dict(json_payload) if json_payload is not None else None,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise CustomerToolsUnavailableError(
                f"Shopify {operation} is unavailable"
            ) from exc
        if response.status_code == 429:
            raise CustomerToolsUnavailableError("Shopify is rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise CustomerToolsUnavailableError(f"Shopify {operation} failed")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise CustomerToolsUnavailableError(
                f"Shopify {operation} returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise CustomerToolsUnavailableError(
                f"Shopify {operation} returned an invalid response"
            )
        return payload
