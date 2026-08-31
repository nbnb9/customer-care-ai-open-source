"""Select the order-facts provider used by after-sales policy checks."""

from __future__ import annotations

from functools import lru_cache

from app.modules.customer_tools.errors import CustomerToolsConfigurationError
from app.modules.customer_tools.factory import get_customer_tools_adapter
from app.modules.customer_tools.shopify_adapter import ShopifyCustomerToolsAdapter
from app.shared.config import get_settings

from .mock_policy import get_mock_after_sales_policy_adapter
from .ports import AfterSalesOrderFactsPort


@lru_cache(maxsize=1)
def get_after_sales_order_facts_adapter() -> AfterSalesOrderFactsPort:
    """Use real Shopify reads when selected, while keeping OMS writes separate."""

    provider = get_settings().customer_tools_provider
    if provider == "mock":
        return get_mock_after_sales_policy_adapter()
    if provider == "shopify_sandbox":
        adapter = get_customer_tools_adapter()
        if not isinstance(adapter, ShopifyCustomerToolsAdapter):
            raise CustomerToolsConfigurationError(
                "Shopify customer tools adapter is unavailable"
            )
        return adapter
    raise CustomerToolsConfigurationError("Unsupported after-sales facts provider")
