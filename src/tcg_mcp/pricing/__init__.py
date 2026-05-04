"""Pricing provider registry — mirror of the grading-side registry."""

from __future__ import annotations

from tcg_mcp.config import Settings, get_settings
from tcg_mcp.errors import ProviderNotEnabledError
from tcg_mcp.pricing.base import PricingProvider
from tcg_mcp.pricing.models import (
    CardListing,
    GradedPriceLevel,
    PriceQuote,
    PricingProviderName,
    ProductKind,
    cents_to_dollars,
)
from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider
from tcg_mcp.pricing.pricecharting import PriceChartingProvider
from tcg_mcp.pricing.snkrdunk import SnkrdunkProvider


def _build_registry(settings: Settings) -> dict[PricingProviderName, PricingProvider]:
    registry: dict[PricingProviderName, PricingProvider] = {}

    # Pokemon TCG API works without a key (lower rate limit), so always enabled.
    registry["pokemontcg"] = PokemonTCGProvider(api_key=settings.pokemontcg_api_key)

    if settings.pricecharting_token:
        registry["pricecharting"] = PriceChartingProvider(
            token=settings.pricecharting_token,
            timeout=settings.http_timeout_seconds,
        )

    # SNKRDUNK is always registered as a stub.
    registry["snkrdunk"] = SnkrdunkProvider()

    return registry


_registry: dict[PricingProviderName, PricingProvider] | None = None


def get_pricing_registry() -> dict[PricingProviderName, PricingProvider]:
    global _registry
    if _registry is None:
        _registry = _build_registry(get_settings())
    return _registry


def reset_pricing_registry() -> None:
    """Test hook — forces the registry to rebuild on next access."""
    global _registry
    _registry = None


def get_pricing_provider(name: PricingProviderName | str) -> PricingProvider:
    """Look up a pricing provider by name.

    Raises ProviderNotEnabledError if not registered (e.g. PriceCharting
    without PRICECHARTING_TOKEN set).
    """
    registry = get_pricing_registry()
    if name not in registry:
        if name == "pricecharting":
            raise ProviderNotEnabledError(
                "PriceCharting provider is not enabled: PRICECHARTING_TOKEN is not set."
            )
        available = ", ".join(sorted(registry.keys())) or "(none)"
        raise ProviderNotEnabledError(
            f"Pricing provider '{name}' is not enabled. Available: {available}."
        )
    return registry[name]


def list_pricing_provider_names() -> list[PricingProviderName]:
    return list(get_pricing_registry().keys())


__all__ = [
    # Models
    "CardListing",
    "PriceQuote",
    "GradedPriceLevel",
    "ProductKind",
    "PricingProviderName",
    "cents_to_dollars",
    # Providers
    "PricingProvider",
    "PokemonTCGProvider",
    "PriceChartingProvider",
    "SnkrdunkProvider",
    # Registry
    "get_pricing_provider",
    "get_pricing_registry",
    "list_pricing_provider_names",
    "reset_pricing_registry",
]
