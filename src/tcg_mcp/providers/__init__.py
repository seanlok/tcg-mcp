"""Provider registry.

Providers self-register at import time iff their required credentials are
present in the environment. Tools call `get_provider("psa")` and either get
a working instance or a clear `ProviderNotEnabledError`.
"""

from __future__ import annotations

from tcg_mcp.config import Settings, get_settings
from tcg_mcp.errors import ProviderNotEnabledError
from tcg_mcp.models import ProviderName
from tcg_mcp.providers.base import GradingProvider
from tcg_mcp.providers.bgs import BGSProvider
from tcg_mcp.providers.cgc import CGCProvider
from tcg_mcp.providers.psa import PSAProvider


def _build_registry(settings: Settings) -> dict[ProviderName, GradingProvider]:
    """Construct the provider registry from current settings.

    Providers without credentials are simply omitted; attempting to use them
    later raises ProviderNotEnabledError with an actionable message.
    """
    registry: dict[ProviderName, GradingProvider] = {}

    if settings.psa_api_token:
        registry["psa"] = PSAProvider(
            token=settings.psa_api_token,
            base_url=settings.psa_api_base_url,
            timeout=settings.http_timeout_seconds,
        )

    # CGC and BGS are stubs in v0.1. We always register them so their NAME is
    # routable (returns NotSupportedError) — but only if a future credential
    # were provided. For now we register them unconditionally so users can see
    # them in `grading_list_providers` as "stubbed".
    registry["cgc"] = CGCProvider()
    registry["bgs"] = BGSProvider()

    return registry


# Module-level cache. Reset by tests via reset_registry().
_registry: dict[ProviderName, GradingProvider] | None = None


def get_registry() -> dict[ProviderName, GradingProvider]:
    global _registry
    if _registry is None:
        _registry = _build_registry(get_settings())
    return _registry


def reset_registry() -> None:
    """Test hook — forces the registry to be rebuilt on next access."""
    global _registry
    _registry = None


def get_provider(name: ProviderName | str) -> GradingProvider:
    """Look up a provider by name.

    Raises:
        ProviderNotEnabledError: if the requested provider has no credentials
            configured (or is otherwise not in the registry).
    """
    registry = get_registry()
    if name not in registry:
        available = ", ".join(sorted(registry.keys())) or "(none)"
        if name == "psa":
            raise ProviderNotEnabledError(
                "PSA provider is not enabled: PSA_API_TOKEN is not set. "
                "Get a token at https://www.psacard.com/publicapi and export it as PSA_API_TOKEN."
            )
        raise ProviderNotEnabledError(
            f"Provider '{name}' is not enabled. Available providers: {available}."
        )
    return registry[name]


def list_provider_names() -> list[ProviderName]:
    """Return the names of all registered providers (enabled or stubbed)."""
    return list(get_registry().keys())


__all__ = [
    "GradingProvider",
    "PSAProvider",
    "CGCProvider",
    "BGSProvider",
    "get_provider",
    "get_registry",
    "list_provider_names",
    "reset_registry",
]
