"""Pricing provider abstraction.

Every pricing service (Pokemon TCG API, PriceCharting, SNKRDUNK, ...)
implements this Protocol. The MCP tool layer dispatches on the `provider`
argument, fetches the matching `PricingProvider` from the registry, and
calls into it.

Mirror of `tcg_mcp.providers.base.GradingProvider` — same shape, different
domain.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tcg_mcp.errors import NotSupportedError
from tcg_mcp.pricing.models import (
    CardListing,
    PriceQuote,
    PricingProviderName,
    ProductKind,
)


@runtime_checkable
class PricingProvider(Protocol):
    """Uniform interface for a card-pricing data source."""

    name: PricingProviderName
    """Short identifier — must match a value in PricingProviderName."""

    currency: str
    """Default currency this provider returns. e.g. 'USD', 'EUR', 'JPY'."""

    async def search(
        self,
        query: str,
        *,
        kind: ProductKind = ProductKind.UNKNOWN,
        limit: int = 20,
    ) -> list[CardListing]:
        """Search for products by free-text query."""
        ...

    async def get_price(self, listing_id: str) -> PriceQuote:
        """Fetch the full price quote for a single listing."""
        ...


class BasePricingProvider:
    """Convenience base; concrete providers can inherit and override."""

    name: PricingProviderName
    currency: str = "USD"

    async def search(
        self,
        query: str,
        *,
        kind: ProductKind = ProductKind.UNKNOWN,
        limit: int = 20,
    ) -> list[CardListing]:
        raise NotSupportedError(
            f"Pricing provider '{self.name}' does not support search."
        )

    async def get_price(self, listing_id: str) -> PriceQuote:
        raise NotSupportedError(
            f"Pricing provider '{self.name}' does not support price lookup."
        )
