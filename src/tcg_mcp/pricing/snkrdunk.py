"""SNKRDUNK pricing provider — STUB.

SNKRDUNK is a Japanese marketplace popular for JP Pokemon TCG singles + sealed.
As of 2026-05, no public developer API has been documented. The marketplace
listings live at https://snkrdunk.com/en/brands/pokemon/trading-cards.

Future paths to enable:
1. Watch for an officially released API (preferred).
2. Polite scraping of search/listing pages — ToS review required first.
3. Reverse-engineer the mobile app's backend API — also ToS-sensitive.

Until one of those lands, this provider is registered (so its name is routable
and discoverable via tcg_list_providers) but raises NotSupportedError on use.
"""

from __future__ import annotations

from tcg_mcp.errors import NotSupportedError
from tcg_mcp.pricing.base import BasePricingProvider
from tcg_mcp.pricing.models import CardListing, PriceQuote, ProductKind


class SnkrdunkProvider(BasePricingProvider):
    name = "snkrdunk"
    currency = "JPY"

    async def search(
        self,
        query: str,
        *,
        kind: ProductKind = ProductKind.UNKNOWN,
        limit: int = 20,
    ) -> list[CardListing]:
        raise NotSupportedError(
            "SNKRDUNK provider is a stub — no public API available as of 2026-05. "
            "Search support is deferred."
        )

    async def get_price(self, listing_id: str) -> PriceQuote:
        raise NotSupportedError(
            "SNKRDUNK provider is a stub — no public API available as of 2026-05. "
            "Price lookup is deferred."
        )
