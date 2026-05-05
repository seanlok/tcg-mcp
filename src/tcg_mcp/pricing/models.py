"""Canonical, provider-agnostic pricing models.

Mirror of the grading-side `models.py`: every pricing provider normalizes
its raw response into these shapes so the MCP tool layer is provider-agnostic.

The `raw` field on each model preserves the provider-specific payload — useful
when a downstream caller needs a field we didn't normalize.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PricingProviderName = Literal["pokemontcg", "pricecharting", "snkrdunk"]


class ProductKind(str, Enum):
    """What kind of product the listing represents."""

    SINGLE = "single"          # individual card
    SEALED = "sealed"          # ETB / booster box / UPC / bundle
    UNKNOWN = "unknown"


class CardListing(BaseModel):
    """One row in a pricing-provider search result.

    Identifies a product enough to fetch its full price detail. Different
    providers use different IDs (Pokemon TCG API uses card IDs like
    'sv4-25'; PriceCharting uses numeric product IDs).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: PricingProviderName
    listing_id: str = Field(..., description="Provider-specific product ID")

    name: str | None = Field(default=None, description="Display name on the listing")
    set_name: str | None = None
    card_number: str | None = None
    year: str | None = None
    kind: ProductKind = ProductKind.UNKNOWN
    url: str | None = Field(
        default=None, description="Provider's web page for this product"
    )
    raw: dict[str, Any] = Field(default_factory=dict)


class GradedPriceLevel(BaseModel):
    """A single graded-condition price point (e.g. PSA 10 = $X)."""

    grade: str = Field(..., description="Grade label, e.g. 'PSA 10', 'BGS 9.5'")
    market: float | None = Field(default=None, description="Market price in `currency`")
    raw_field: str = Field(
        ...,
        description="Provider-native field name we sourced this from (for traceability)",
    )


class PriceQuote(BaseModel):
    """Normalized pricing for a single product at one point in time.

    Most providers split prices by *variant* (normal vs holofoil) or by
    *condition/grade* (raw vs PSA 10). We keep the most common ones at the
    top level (`market`, `low`, `high`), and a `variants` map plus a list
    of `graded_levels` for everything else.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: PricingProviderName
    listing_id: str
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    currency: Literal["USD", "EUR", "JPY"] = "USD"
    kind: ProductKind = ProductKind.UNKNOWN

    # Primary raw / ungraded prices (what most callers want)
    market: float | None = None
    low: float | None = None
    mid: float | None = None
    high: float | None = None

    # Variant prices (Pokemon TCG API: normal / holofoil / reverseHolofoil / ...)
    variants: dict[str, dict[str, float | None]] = Field(default_factory=dict)

    # Graded prices (PriceCharting: PSA 7 / 8 / 9 / 10, BGS 9.5, etc.)
    graded_levels: list[GradedPriceLevel] = Field(default_factory=list)

    # Convenience pointer back to the listing
    name: str | None = None
    url: str | None = None

    raw: dict[str, Any] = Field(default_factory=dict)


# ---- Catalog models (v0.4) -------------------------------------------------
#
# Catalog tools sit alongside pricing tools because they share the Pokemon TCG
# API as a backing store. They live here rather than in their own subpackage
# to avoid a circular dep with the PricingProvider abstraction.


class SetInfo(BaseModel):
    """Metadata for one Pokemon TCG set."""

    model_config = ConfigDict(str_strip_whitespace=True)

    set_id: str = Field(..., description="Provider-native set ID (e.g. 'sv8')")
    name: str = Field(..., description="Set name (e.g. 'Surging Sparks')")
    series: str | None = None
    printed_total: int | None = Field(
        default=None,
        description="Number of cards in the printed set (without secret rares).",
    )
    total: int | None = Field(
        default=None,
        description="Number of cards including secret rares + alt arts.",
    )
    release_date: str | None = None
    ptcgo_code: str | None = None
    images: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CatalogCard(BaseModel):
    """One card in the Pokemon TCG catalog (search/list result)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    card_id: str = Field(..., description="Provider-native card ID (e.g. 'sv8-199')")
    name: str
    set_id: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    rarity: str | None = None
    artist: str | None = None
    images: dict[str, Any] | None = None
    market_price: float | None = Field(
        default=None,
        description=(
            "Best-effort top-line market price in USD from TCGPlayer "
            "(picks the dominant variant — same logic as PriceQuote)."
        ),
    )
    raw: dict[str, Any] = Field(default_factory=dict)


def cents_to_dollars(cents: int | None) -> float | None:
    """PriceCharting encodes prices as integer pennies. Convert to float dollars.

    >>> cents_to_dollars(1732)
    17.32
    >>> cents_to_dollars(None) is None
    True
    """
    if cents is None:
        return None
    try:
        return round(int(cents) / 100, 2)
    except (TypeError, ValueError):
        return None
