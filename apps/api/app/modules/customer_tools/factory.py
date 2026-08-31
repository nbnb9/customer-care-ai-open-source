from functools import lru_cache
from pathlib import Path

from app.shared.config import get_settings

from .errors import CustomerToolsConfigurationError
from .mock_adapter import get_mock_customer_tools_adapter
from .ports import CustomerToolsPort
from .shopify_adapter import ShopifyCustomerToolsAdapter


@lru_cache(maxsize=1)
def get_customer_tools_adapter() -> CustomerToolsPort:
    settings = get_settings()
    if settings.customer_tools_provider == "mock":
        return get_mock_customer_tools_adapter()
    if settings.customer_tools_provider == "shopify_sandbox":
        return ShopifyCustomerToolsAdapter.from_files(
            bindings_file=Path(settings.shopify_sandbox_bindings_file),
            client_id_file=Path(settings.shopify_client_id_file),
            client_secret_file=Path(settings.shopify_client_secret_file),
            api_version=settings.shopify_api_version,
            timeout_seconds=settings.shopify_timeout_seconds,
        )
    raise CustomerToolsConfigurationError("Unsupported customer tools provider")
