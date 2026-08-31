class CustomerToolsProviderError(RuntimeError):
    """Provider failure that never contains credentials or customer data."""


class CustomerToolsConfigurationError(CustomerToolsProviderError):
    """The selected provider is not safely usable with the current configuration."""


class CustomerToolsUnavailableError(CustomerToolsProviderError):
    """The provider could not produce a trustworthy result."""
