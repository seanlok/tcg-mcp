"""tcg-mcp — Pokemon TCG MCP server (local-first).

Single-server umbrella for:
  - Grading lookups (PSA today; CGC/BGS stubs)
  - Local SQLite-backed collection management
  - Pricing (Stage 2 — coming)
  - Watchlist (Stage 3 — coming)

Tool naming: tcg_<namespace>_<action>, e.g. `tcg_psa_get_cert`,
`tcg_collection_add_card`. The namespace makes capability discovery obvious
in the tool list.

Run via:
    tcg-mcp                  # console script
    python -m tcg_mcp        # module form
    uvx --from <local-path> tcg-mcp  # zero-install local
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from tcg_mcp.errors import (
    NotSupportedError,
    ProviderNotEnabledError,
    format_http_error,
)
from tcg_mcp.models import (
    CertResult,
    ImageResult,
    ResponseFormat,
)
from tcg_mcp.pricing import (
    PricingProviderName,
    ProductKind,
    get_pokemontcg_catalog_provider,
    get_pricing_provider,
    list_pricing_provider_names,
)
from tcg_mcp.providers import get_provider, list_provider_names
from tcg_mcp.storage import get_db

# stdio transport rule: never log to stdout. Use stderr.
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("tcg_mcp")

mcp = FastMCP("tcg_mcp")


# =============================================================================
# Common input fragments
# =============================================================================


class _StrictModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# =============================================================================
# PSA tools — namespaced as tcg_psa_*
# =============================================================================


class PSAGetCertInput(_StrictModel):
    cert_number: str = Field(
        ...,
        description="The PSA certificate number printed on the slab (e.g. '79721014').",
        min_length=4,
        max_length=20,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="'markdown' for human-readable output, 'json' for full data.",
    )


@mcp.tool(
    name="tcg_psa_get_cert",
    annotations={
        "title": "PSA — look up a graded card by cert number",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_psa_get_cert(params: PSAGetCertInput) -> str:
    """Look up a single graded card by its PSA cert number.

    Returns card metadata (year, brand, set, card number, subject, variety),
    grade (label + numeric), and population data (count at this grade,
    count graded higher) when PSA exposes them.
    """
    return await _run_get_cert("psa", params.cert_number, params.response_format)


class PSAGetImagesInput(_StrictModel):
    cert_number: str = Field(..., min_length=4, max_length=20)


@mcp.tool(
    name="tcg_psa_get_images",
    annotations={
        "title": "PSA — fetch front/back images for a graded card",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_psa_get_images(params: PSAGetImagesInput) -> str:
    """Fetch front/back image URLs for a PSA graded card.

    NOTE: PSA only attaches images to cards graded after October 2021 — older
    slabs return an empty list with no error.
    """
    return await _run_get_images("psa", params.cert_number)


class PSAAddToCollectionInput(_StrictModel):
    cert_number: str = Field(..., min_length=4, max_length=20)
    acquisition_price: float | None = Field(
        default=None, description="What you paid (in acquisition_currency)."
    )
    acquisition_currency: str = Field(default="USD", min_length=3, max_length=3)
    acquisition_date: str | None = Field(
        default=None,
        description="ISO 8601 date (YYYY-MM-DD). Defaults to today.",
    )
    acquisition_source: str | None = Field(
        default=None, description="Where you bought it: 'ebay', 'tcgplayer', 'lcs', etc."
    )
    notes: str | None = None
    tags: list[str] | None = None


@mcp.tool(
    name="tcg_psa_add_to_collection",
    annotations={
        "title": "PSA — look up a cert and record it as owned",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def tcg_psa_add_to_collection(params: PSAAddToCollectionInput) -> str:
    """Look up a PSA cert AND record it in your local collection in one step.

    Workflow tool — combines `tcg_psa_get_cert` with `tcg_collection_add_card`.
    Looks up the cert at PSA, normalizes the response, and writes a row into
    the local SQLite DB with your cost basis. Returns the new card_id.

    If you've already added this exact cert, returns an error rather than
    duplicating — use `tcg_collection_update` to change cost basis.
    """
    try:
        provider = get_provider("psa")
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        cert = await provider.get_cert(params.cert_number)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="psa")

    if not cert.found:
        return (
            f"Error: PSA returned no record for cert {params.cert_number}. "
            "Check the number — PSA cert numbers are 8 digits."
        )

    db = get_db()
    if db.find_by_cert("psa", params.cert_number):
        return (
            f"Error: PSA cert {params.cert_number} is already in your collection. "
            "Use tcg_collection_update to change cost basis or notes."
        )

    new_id = db.add_owned_card(
        {
            "is_graded": 1,
            "grading_provider": "psa",
            "cert_number": params.cert_number,
            "grade": cert.grade,
            "grade_numeric": cert.grade_numeric,
            "year": cert.year,
            "brand": cert.brand,
            "set_name": cert.set_name,
            "card_number": cert.card_number,
            "subject": cert.subject,
            "variety": cert.variety,
            "acquisition_date": params.acquisition_date,
            "acquisition_price": params.acquisition_price,
            "acquisition_currency": params.acquisition_currency,
            "acquisition_source": params.acquisition_source,
            "notes": params.notes,
            "tags": params.tags,
            "provider_raw": cert.raw,
            "status": "owned",
        }
    )

    return json.dumps(
        {
            "ok": True,
            "card_id": new_id,
            "summary": (
                f"Added PSA #{params.cert_number}: "
                f"{cert.year or '?'} {cert.brand or ''} {cert.subject or ''} "
                f"#{cert.card_number or ''} — {cert.grade or 'no grade'}"
            ).strip(),
        },
        indent=2,
    )


# =============================================================================
# Stub provider tools — uniform "not yet" responses
# =============================================================================


class CGCGetCertInput(_StrictModel):
    cert_number: str = Field(..., min_length=4, max_length=20)


@mcp.tool(
    name="tcg_cgc_get_cert",
    annotations={
        "title": "CGC — look up a graded card (STUB)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_cgc_get_cert(params: CGCGetCertInput) -> str:
    """CGC cert lookup — stubbed. CGC has no official public API.

    Returns a clear "not supported" error. Implementation deferred.
    """
    return await _run_get_cert("cgc", params.cert_number, ResponseFormat.MARKDOWN)


class BGSGetCertInput(_StrictModel):
    cert_number: str = Field(..., min_length=4, max_length=20)


@mcp.tool(
    name="tcg_bgs_get_cert",
    annotations={
        "title": "BGS / Beckett — look up a graded card (STUB)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_bgs_get_cert(params: BGSGetCertInput) -> str:
    """BGS cert lookup — stubbed. Beckett has no official public API.

    Returns a clear "not supported" error. Implementation deferred.
    """
    return await _run_get_cert("bgs", params.cert_number, ResponseFormat.MARKDOWN)


# =============================================================================
# Collection tools — local SQLite, no external API
# =============================================================================


class CollectionAddCardInput(_StrictModel):
    """Add a card you own — works for both raw and graded cards.

    For graded cards from a supported provider (PSA), prefer
    `tcg_psa_add_to_collection` which looks up metadata for you. Use this
    tool for raw cards or for graded cards from providers we don't support
    yet (CGC, BGS).
    """

    is_graded: bool = Field(default=False, description="True for graded slabs.")
    grading_provider: Literal["psa", "cgc", "bgs"] | None = Field(default=None)
    cert_number: str | None = Field(default=None)
    grade: str | None = None
    grade_numeric: float | None = None

    year: str | None = None
    brand: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    subject: str = Field(..., description="Player/character/Pokemon name", min_length=1)
    variety: str | None = None
    language: str = Field(default="EN", min_length=2, max_length=4)

    acquisition_date: str | None = Field(
        default=None, description="ISO 8601 date (YYYY-MM-DD)."
    )
    acquisition_price: float | None = None
    acquisition_currency: str = Field(default="USD", min_length=3, max_length=3)
    acquisition_source: str | None = None

    notes: str | None = None
    tags: list[str] | None = None


@mcp.tool(
    name="tcg_collection_add_card",
    annotations={
        "title": "Add a card you own (raw or graded)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def tcg_collection_add_card(params: CollectionAddCardInput) -> str:
    """Record a card as owned in the local SQLite DB.

    Returns the new card_id (a UUID) on success.
    """
    if params.is_graded and not (params.grading_provider and params.cert_number):
        return (
            "Error: graded cards require both grading_provider and cert_number."
        )

    db = get_db()
    fields: dict[str, Any] = {
        "is_graded": 1 if params.is_graded else 0,
        "grading_provider": params.grading_provider,
        "cert_number": params.cert_number,
        "grade": params.grade,
        "grade_numeric": params.grade_numeric,
        "year": params.year,
        "brand": params.brand,
        "set_name": params.set_name,
        "card_number": params.card_number,
        "subject": params.subject,
        "variety": params.variety,
        "language": params.language,
        "acquisition_date": params.acquisition_date,
        "acquisition_price": params.acquisition_price,
        "acquisition_currency": params.acquisition_currency,
        "acquisition_source": params.acquisition_source,
        "notes": params.notes,
        "tags": params.tags,
        "status": "owned",
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    try:
        new_id = db.add_owned_card(fields)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"

    return json.dumps({"ok": True, "card_id": new_id}, indent=2)


class CollectionListInput(_StrictModel):
    status: Literal["owned", "sold", "lost", "graded_out"] | None = "owned"
    grading_provider: Literal["psa", "cgc", "bgs"] | None = None
    subject_like: str | None = Field(
        default=None, description="Case-insensitive substring match on subject."
    )
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@mcp.tool(
    name="tcg_collection_list",
    annotations={
        "title": "List cards in your collection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_list(params: CollectionListInput) -> str:
    """List cards in the local collection, with optional filters and pagination."""
    db = get_db()
    rows = db.list_owned(
        status=params.status,
        provider=params.grading_provider,
        subject_like=params.subject_like,
        limit=params.limit,
        offset=params.offset,
    )
    return json.dumps(
        {
            "count": len(rows),
            "limit": params.limit,
            "offset": params.offset,
            "items": rows,
        },
        indent=2,
        default=str,
    )


class CollectionGetInput(_StrictModel):
    card_id: str = Field(..., min_length=4)


@mcp.tool(
    name="tcg_collection_get",
    annotations={
        "title": "Fetch a single owned card by id",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_get(params: CollectionGetInput) -> str:
    """Fetch a single owned card by its card_id."""
    db = get_db()
    row = db.get_owned_card(params.card_id)
    if row is None:
        return f"Error: no card with id {params.card_id}"
    return json.dumps(row, indent=2, default=str)


class CollectionUpdateInput(_StrictModel):
    card_id: str = Field(..., min_length=4)

    grade: str | None = None
    grade_numeric: float | None = None
    acquisition_date: str | None = None
    acquisition_price: float | None = None
    acquisition_currency: str | None = None
    acquisition_source: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    status: Literal["owned", "sold", "lost", "graded_out"] | None = None
    sold_date: str | None = None
    sold_price: float | None = None
    sold_currency: str | None = None


@mcp.tool(
    name="tcg_collection_update",
    annotations={
        "title": "Update fields on an owned card",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def tcg_collection_update(params: CollectionUpdateInput) -> str:
    """Update mutable fields on a collection card (cost basis, notes, status, ...)."""
    db = get_db()
    payload = params.model_dump(exclude_none=True)
    payload.pop("card_id", None)
    if not payload:
        return "Error: no fields provided to update."
    if not db.update_owned_card(params.card_id, payload):
        return f"Error: no card with id {params.card_id}"
    row = db.get_owned_card(params.card_id)
    return json.dumps({"ok": True, "card": row}, indent=2, default=str)


class CollectionRemoveInput(_StrictModel):
    card_id: str = Field(..., min_length=4)
    hard: bool = Field(
        default=False,
        description=(
            "Default False = soft-delete (status='sold', row preserved). "
            "True = DELETE the row (loses history)."
        ),
    )
    sold_price: float | None = None
    sold_currency: str | None = None
    sold_date: str | None = None


@mcp.tool(
    name="tcg_collection_remove",
    annotations={
        "title": "Remove (or soft-delete) a card from your collection",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_remove(params: CollectionRemoveInput) -> str:
    """Remove a card. Default is a soft-delete: status -> 'sold' and the row stays.

    Pair with sold_price + sold_date to capture the disposition.
    """
    db = get_db()
    if not db.get_owned_card(params.card_id):
        return f"Error: no card with id {params.card_id}"

    if not params.hard and any(
        x is not None for x in (params.sold_price, params.sold_currency, params.sold_date)
    ):
        db.update_owned_card(
            params.card_id,
            {
                k: v
                for k, v in {
                    "sold_price": params.sold_price,
                    "sold_currency": params.sold_currency,
                    "sold_date": params.sold_date,
                }.items()
                if v is not None
            },
        )

    db.remove_owned_card(params.card_id, hard=params.hard)
    return json.dumps({"ok": True, "hard_deleted": params.hard}, indent=2)


class CollectionValueInput(_StrictModel):
    pass  # Stage 2 will add a `provider` and `as_of` arg.


@mcp.tool(
    name="tcg_collection_value",
    annotations={
        "title": "Cost-basis summary of your collection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_value(params: CollectionValueInput) -> str:
    """Cost-basis summary across the collection.

    Returns counts (owned / graded / raw / sold) and total cost basis.
    For live market valuation that joins against pricing snapshots, see
    `tcg_collection_value_with_market`.
    """
    db = get_db()
    return json.dumps(db.collection_summary(), indent=2)


# =============================================================================
# Stage 3 — Sealed product helper (Stage 3 tool)
# =============================================================================


class CollectionAddSealedInput(_StrictModel):
    """Add a sealed product (ETB / booster box / UPC / tin / bundle) you own."""

    product_type: Literal[
        "etb",
        "booster_box",
        "upc",
        "booster_bundle",
        "tin",
        "premium_collection",
        "other_sealed",
    ] = Field(..., description="Sealed product type.")
    set_name: str = Field(..., min_length=1, description="Set name, e.g. 'Surging Sparks'")
    set_code: str | None = Field(default=None, description="Set code if known, e.g. 'sv08'")
    year: str | None = None
    language: str = Field(default="EN", min_length=2, max_length=4)
    quantity: int = Field(default=1, ge=1, le=1000)
    acquisition_date: str | None = None
    acquisition_price: float | None = Field(
        default=None, description="Per-unit price (multiply by quantity for total)."
    )
    acquisition_currency: str = Field(default="USD", min_length=3, max_length=3)
    acquisition_source: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


@mcp.tool(
    name="tcg_collection_add_sealed",
    annotations={
        "title": "Add a sealed product (ETB / booster box / UPC / tin)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def tcg_collection_add_sealed(params: CollectionAddSealedInput) -> str:
    """Record sealed product as owned.

    Behind the scenes, sealed product lives in the same `owned_cards` table
    as singles, distinguished by `product_type != 'single'`. We insert one
    row per quantity unit so cost basis and disposition track per-unit.

    Returns a list of new card_ids (one per unit).
    """
    db = get_db()
    subject = f"{params.set_name} {params.product_type.replace('_', ' ').upper()}"

    new_ids: list[str] = []
    for _ in range(params.quantity):
        fields: dict[str, Any] = {
            "is_graded": 0,
            "product_type": params.product_type,
            "subject": subject,
            "set_name": params.set_name,
            "card_number": params.set_code,
            "year": params.year,
            "language": params.language,
            "acquisition_date": params.acquisition_date,
            "acquisition_price": params.acquisition_price,
            "acquisition_currency": params.acquisition_currency,
            "acquisition_source": params.acquisition_source,
            "notes": params.notes,
            "tags": params.tags,
            "status": "owned",
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        new_ids.append(db.add_owned_card(fields))

    return json.dumps(
        {
            "ok": True,
            "card_ids": new_ids,
            "summary": (
                f"Added {params.quantity}× {subject}"
                + (f" @ ${params.acquisition_price}" if params.acquisition_price else "")
            ),
        },
        indent=2,
    )


# =============================================================================
# Stage 3 — Pricing attachment + live valuation tools
# =============================================================================


class CollectionAttachPricingInput(_StrictModel):
    card_id: str = Field(..., min_length=4)
    pricing_provider: PricingProviderName = Field(
        ..., description="Which pricing provider this listing comes from."
    )
    pricing_listing_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Listing ID on the pricing provider (e.g. Pokemon TCG API: 'sv4-25').",
    )


@mcp.tool(
    name="tcg_collection_attach_pricing",
    annotations={
        "title": "Link an owned card to a pricing-provider listing",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_attach_pricing(params: CollectionAttachPricingInput) -> str:
    """Map an owned card to a pricing listing for live valuation.

    After attaching, `tcg_pricing_snapshot` calls (using the same listing_id)
    will let `tcg_collection_value_with_market` compute live market value
    for this card.
    """
    db = get_db()
    if not db.get_owned_card(params.card_id):
        return f"Error: no card with id {params.card_id}"
    db.attach_pricing(
        params.card_id,
        pricing_provider=params.pricing_provider,
        pricing_listing_id=params.pricing_listing_id,
    )
    return json.dumps(
        {
            "ok": True,
            "card_id": params.card_id,
            "pricing_provider": params.pricing_provider,
            "pricing_listing_id": params.pricing_listing_id,
        },
        indent=2,
    )


class CollectionValueWithMarketInput(_StrictModel):
    pass  # No params — we always value the full owned set.


@mcp.tool(
    name="tcg_collection_value_with_market",
    annotations={
        "title": "Live collection valuation joining latest price snapshots",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_value_with_market(params: CollectionValueWithMarketInput) -> str:
    """Cost basis + live market value across the collection.

    For each owned card with an attached pricing listing, we look up the
    most recent pricing snapshot (matching the card's grade for graded
    cards). Cards without an attachment or without a snapshot land in
    `unpriced_count` so you know what's missing.

    Run `tcg_pricing_snapshot` periodically to keep the join fresh.
    """
    db = get_db()
    return json.dumps(db.collection_valuation(), indent=2, default=str)


# =============================================================================
# Stage 3 — Watchlist tools
# =============================================================================


class WatchlistAddInput(_StrictModel):
    card_descriptor: str = Field(
        ..., min_length=1, max_length=200,
        description="Free-form description: 'Charizard ex SIR — Surging Sparks'.",
    )
    horizon: Literal["flip", "hold", "sealed"] = Field(default="hold")
    target_price: float | None = Field(
        default=None, ge=0, description="Buy below this price."
    )
    target_currency: str = Field(default="USD", min_length=3, max_length=3)
    thesis: str | None = Field(default=None, description="Why you want this card.")


@mcp.tool(
    name="tcg_watchlist_add",
    annotations={
        "title": "Add a card to your watchlist",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def tcg_watchlist_add(params: WatchlistAddInput) -> str:
    """Add a card to the watchlist with a target buy price + thesis.

    Returns the new watchlist_id.
    """
    db = get_db()
    fields: dict[str, Any] = {
        "card_descriptor": params.card_descriptor,
        "horizon": params.horizon,
        "target_price": params.target_price,
        "target_currency": params.target_currency,
        "thesis": params.thesis,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    new_id = db.add_watchlist(fields)
    return json.dumps({"ok": True, "watchlist_id": new_id}, indent=2)


class WatchlistListInput(_StrictModel):
    horizon: Literal["flip", "hold", "sealed"] | None = None
    open_only: bool = Field(default=True, description="If True, exclude closed entries.")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@mcp.tool(
    name="tcg_watchlist_list",
    annotations={
        "title": "List watchlist entries",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_watchlist_list(params: WatchlistListInput) -> str:
    """List watchlist entries with optional horizon filter."""
    db = get_db()
    rows = db.list_watchlist(
        horizon=params.horizon,
        open_only=params.open_only,
        limit=params.limit,
        offset=params.offset,
    )
    return json.dumps(
        {"count": len(rows), "items": rows}, indent=2, default=str
    )


class WatchlistGetInput(_StrictModel):
    watchlist_id: str = Field(..., min_length=4)


@mcp.tool(
    name="tcg_watchlist_get",
    annotations={
        "title": "Fetch a watchlist entry by id",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_watchlist_get(params: WatchlistGetInput) -> str:
    """Fetch a single watchlist entry by id."""
    db = get_db()
    row = db.get_watchlist(params.watchlist_id)
    if row is None:
        return f"Error: no watchlist entry with id {params.watchlist_id}"
    return json.dumps(row, indent=2, default=str)


class WatchlistUpdateInput(_StrictModel):
    watchlist_id: str = Field(..., min_length=4)
    card_descriptor: str | None = None
    horizon: Literal["flip", "hold", "sealed"] | None = None
    target_price: float | None = Field(default=None, ge=0)
    target_currency: str | None = None
    thesis: str | None = None


@mcp.tool(
    name="tcg_watchlist_update",
    annotations={
        "title": "Edit a watchlist entry",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def tcg_watchlist_update(params: WatchlistUpdateInput) -> str:
    """Update mutable fields on a watchlist entry."""
    db = get_db()
    payload = params.model_dump(exclude_none=True)
    payload.pop("watchlist_id", None)
    if not payload:
        return "Error: no fields provided to update."
    if not db.update_watchlist(params.watchlist_id, payload):
        return f"Error: no watchlist entry with id {params.watchlist_id}"
    row = db.get_watchlist(params.watchlist_id)
    return json.dumps({"ok": True, "entry": row}, indent=2, default=str)


class WatchlistCloseInput(_StrictModel):
    watchlist_id: str = Field(..., min_length=4)
    reason: Literal["bought", "thesis_invalidated", "manual"] = Field(
        ..., description="Why this watchlist entry is being closed."
    )


@mcp.tool(
    name="tcg_watchlist_close",
    annotations={
        "title": "Close a watchlist entry",
        "readOnlyHint": False,
        # Soft-closes — reversible, but flagged destructive for safety prompts.
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_watchlist_close(params: WatchlistCloseInput) -> str:
    """Mark a watchlist entry closed (bought / thesis_invalidated / manual)."""
    db = get_db()
    if not db.get_watchlist(params.watchlist_id):
        return f"Error: no watchlist entry with id {params.watchlist_id}"
    if not db.close_watchlist(params.watchlist_id, reason=params.reason):
        return (
            f"Error: watchlist entry {params.watchlist_id} is already closed."
        )
    row = db.get_watchlist(params.watchlist_id)
    return json.dumps({"ok": True, "entry": row}, indent=2, default=str)


# =============================================================================
# Stage 3 — PSA pop-trend tools
# =============================================================================


class PSASnapshotPopInput(_StrictModel):
    cert_number: str = Field(
        ..., min_length=4, max_length=20,
        description="Any cert number for the card spec you want to track.",
    )


@mcp.tool(
    name="tcg_psa_snapshot_pop",
    annotations={
        "title": "PSA — capture a pop-report snapshot",
        "readOnlyHint": False,        # writes to local SQLite
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def tcg_psa_snapshot_pop(params: PSASnapshotPopInput) -> str:
    """Capture today's PSA pop data for the spec backing this cert.

    Looks up the cert via PSA, extracts (SpecID, grade, total_at_grade,
    population_higher), and inserts a row into pop_snapshots. Repeat over
    time and `tcg_psa_pop_trend` will surface the trajectory.
    """
    try:
        provider = get_provider("psa")
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        cert = await provider.get_cert(params.cert_number)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="psa")

    if not cert.found:
        return f"Error: PSA returned no record for cert {params.cert_number}."

    raw = cert.raw or {}
    psa_cert = raw.get("PSACert") or {}
    spec_id = str(psa_cert.get("SpecID") or "")
    if not spec_id:
        return "Error: PSA response did not include a SpecID; cannot snapshot pop."

    db = get_db()
    new_id = db.add_pop_snapshot(
        {
            "grading_provider": "psa",
            "spec_id": spec_id,
            "cert_number": params.cert_number,
            "grade": cert.grade,
            "total_at_grade": cert.total_population,
            "population_higher": cert.population_higher,
            "raw": raw,
        }
    )
    return json.dumps(
        {
            "ok": True,
            "snapshot_id": new_id,
            "spec_id": spec_id,
            "grade": cert.grade,
            "total_at_grade": cert.total_population,
            "population_higher": cert.population_higher,
        },
        indent=2,
    )


class PSAPopTrendInput(_StrictModel):
    spec_id: str | None = Field(
        default=None,
        description="PSA SpecID. If omitted, provide cert_number instead.",
    )
    cert_number: str | None = Field(
        default=None,
        description=(
            "Optional cert number. If you don't know the SpecID, we'll derive "
            "it from the most recent pop_snapshot for this cert. Will NOT make "
            "a live PSA call to look it up."
        ),
        min_length=4,
        max_length=20,
    )
    grade: str | None = Field(
        default=None,
        description="Optional grade filter, e.g. 'GEM MT 10'. None = all grades.",
    )
    days: int | None = Field(
        default=90, ge=1, le=3650,
        description="Time window in days. Default: last 90 days.",
    )


@mcp.tool(
    name="tcg_psa_pop_trend",
    annotations={
        "title": "PSA — return pop-snapshot time series for a spec",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_psa_pop_trend(params: PSAPopTrendInput) -> str:
    """Return the time series of pop snapshots we've captured for a card spec.

    Useful for answering "Has PSA 10 pop for this card grown a lot?" — but
    only works for specs you've snapshotted via `tcg_psa_snapshot_pop`.
    """
    db = get_db()

    spec_id = params.spec_id
    if not spec_id:
        if not params.cert_number:
            return "Error: provide either spec_id or cert_number."
        # Derive spec_id from a previous pop snapshot row.
        with db.connect() as conn:
            cur = conn.execute(
                "SELECT spec_id FROM pop_snapshots "
                "WHERE grading_provider = 'psa' AND cert_number = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (params.cert_number,),
            )
            row = cur.fetchone()
        if row is None:
            return (
                f"Error: no pop snapshots found for cert {params.cert_number}. "
                "Capture one with tcg_psa_snapshot_pop first."
            )
        spec_id = str(row["spec_id"])

    rows = db.list_pop_snapshots(
        grading_provider="psa",
        spec_id=spec_id,
        grade=params.grade,
        days=params.days,
    )
    return json.dumps(
        {
            "spec_id": spec_id,
            "grade": params.grade,
            "days": params.days,
            "count": len(rows),
            "snapshots": rows,
        },
        indent=2,
        default=str,
    )


# =============================================================================
# Pricing tools — namespaced as tcg_pricing_*
# =============================================================================


class PricingSearchInput(_StrictModel):
    query: str = Field(
        ...,
        description=(
            "Free-text search. Pokemon TCG API supports Lucene syntax "
            '(e.g. \'name:charizard set.id:base1\'); a simple name like '
            "\"Charizard\" works too."
        ),
        min_length=1,
        max_length=200,
    )
    provider: PricingProviderName = Field(
        default="pokemontcg",
        description="Which pricing provider to query.",
    )
    kind: Literal["single", "sealed", "unknown"] = Field(
        default="unknown",
        description="Product kind hint (some providers ignore this).",
    )
    limit: int = Field(default=20, ge=1, le=100)


@mcp.tool(
    name="tcg_pricing_search",
    annotations={
        "title": "Pricing — search a provider for matching products",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_pricing_search(params: PricingSearchInput) -> str:
    """Search a pricing provider for matching products.

    Returns a list of CardListing rows. Use `tcg_pricing_get` afterward with
    one of the `listing_id`s to fetch full price data.
    """
    try:
        provider = get_pricing_provider(params.provider)
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        listings = await provider.search(
            params.query, kind=ProductKind(params.kind), limit=params.limit
        )
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider=params.provider)

    return json.dumps(
        {
            "provider": params.provider,
            "query": params.query,
            "count": len(listings),
            "items": [item.model_dump() for item in listings],
        },
        indent=2,
        default=str,
    )


class PricingGetInput(_StrictModel):
    listing_id: str = Field(
        ...,
        description=(
            "Provider-specific product ID (Pokemon TCG API: 'sv4-25'; "
            "PriceCharting: a numeric ID)."
        ),
        min_length=1,
        max_length=64,
    )
    provider: PricingProviderName = Field(default="pokemontcg")


@mcp.tool(
    name="tcg_pricing_get",
    annotations={
        "title": "Pricing — fetch full price quote for one listing",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_pricing_get(params: PricingGetInput) -> str:
    """Fetch the full PriceQuote for one listing on the chosen provider.

    Returns top-level market/low/high (USD or EUR depending on provider),
    a `variants` map (Pokemon TCG API), and a `graded_levels` list
    (PriceCharting). The full provider payload lives in `raw`.
    """
    try:
        provider = get_pricing_provider(params.provider)
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        quote = await provider.get_price(params.listing_id)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider=params.provider)

    return quote.model_dump_json(indent=2)


class PricingSnapshotInput(_StrictModel):
    listing_id: str = Field(..., min_length=1, max_length=64)
    provider: PricingProviderName = Field(default="pokemontcg")


@mcp.tool(
    name="tcg_pricing_snapshot",
    annotations={
        "title": "Pricing — fetch and persist a price snapshot to local DB",
        "readOnlyHint": False,        # writes to local SQLite
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def tcg_pricing_snapshot(params: PricingSnapshotInput) -> str:
    """Fetch a price quote and write it as a row in the pricing_snapshots table.

    For graded providers (PriceCharting), every graded_level becomes its own
    snapshot row (so you can later track PSA 10 vs PSA 9 separately). The
    raw provider payload is stored on the FIRST row only to avoid bloat.
    """
    try:
        provider = get_pricing_provider(params.provider)
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        quote = await provider.get_price(params.listing_id)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider=params.provider)

    db = get_db()
    snapshot_ids: list[str] = []

    # Always save the raw / ungraded primary row.
    snapshot_ids.append(
        db.add_pricing_snapshot(
            {
                "provider": params.provider,
                "listing_id": params.listing_id,
                "grade": None,
                "currency": quote.currency,
                "market": quote.market,
                "low": quote.low,
                "high": quote.high,
                "raw": quote.raw,  # raw payload preserved on the ungraded row
            }
        )
    )

    # Each graded level gets its own snapshot row, no raw payload duplicated.
    for level in quote.graded_levels:
        snapshot_ids.append(
            db.add_pricing_snapshot(
                {
                    "provider": params.provider,
                    "listing_id": params.listing_id,
                    "grade": level.grade,
                    "currency": quote.currency,
                    "market": level.market,
                    "low": level.market,
                    "high": level.market,
                }
            )
        )

    return json.dumps(
        {
            "ok": True,
            "snapshot_ids": snapshot_ids,
            "summary": {
                "provider": params.provider,
                "listing_id": params.listing_id,
                "primary_market": quote.market,
                "graded_levels_saved": len(quote.graded_levels),
                "currency": quote.currency,
            },
        },
        indent=2,
    )


# =============================================================================
# Stage v0.3 — bulk snapshot + history
# =============================================================================


class PricingSnapshotCollectionInput(_StrictModel):
    """Snapshot every owned card that has a pricing listing attached."""

    provider: PricingProviderName | None = Field(
        default=None,
        description=(
            "Optional filter — only snapshot cards on this pricing provider. "
            "None = snapshot all attached cards across providers."
        ),
    )
    max_age_hours: float = Field(
        default=24.0,
        ge=0.0,
        description=(
            "Skip cards whose latest snapshot is fresher than this. "
            "Default 24h. Set to 0 to force-refresh all attached cards."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "If True, report what would be snapshotted without making any "
            "API calls or writing rows."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="Optional cap on cards processed (useful for testing).",
    )


@mcp.tool(
    name="tcg_pricing_snapshot_collection",
    annotations={
        "title": "Pricing — bulk snapshot every attached card in the collection",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def tcg_pricing_snapshot_collection(
    params: PricingSnapshotCollectionInput,
) -> str:
    """Walk the collection and snapshot every owned card with an attached
    pricing listing, respecting per-provider rate limits.

    Skips cards whose most recent snapshot is younger than `max_age_hours`
    so repeated calls don't burn API quota redundantly. One card failing
    does not abort the rest — failures are reported per-card.

    Returns a summary with counts (snapshotted / skipped_recent / failed)
    and a per-provider breakdown.
    """
    db = get_db()
    rows = db.list_owned_with_pricing(provider=params.provider, limit=params.limit)

    summary = {
        "owned_with_pricing": len(rows),
        "snapshotted": 0,
        "skipped_recent": 0,
        "failed": 0,
    }
    by_provider: dict[str, dict[str, int]] = {}
    snapshots_made: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    fresh_cutoff_seconds = params.max_age_hours * 3600.0

    for row in rows:
        provider_name = row["pricing_provider"]
        listing_id = row["pricing_listing_id"]
        card_id = row["id"]
        grade = row.get("grade") if row.get("is_graded") else None
        bp = by_provider.setdefault(
            provider_name,
            {"snapshotted": 0, "skipped_recent": 0, "failed": 0},
        )

        # Freshness check — skip if a snapshot exists newer than max_age_hours.
        if fresh_cutoff_seconds > 0:
            latest = db.latest_snapshot(provider_name, listing_id, grade=grade)
            if latest is not None and latest.get("captured_at"):
                age_seconds = _captured_age_seconds(latest["captured_at"])
                if age_seconds is not None and age_seconds < fresh_cutoff_seconds:
                    summary["skipped_recent"] += 1
                    bp["skipped_recent"] += 1
                    continue

        if params.dry_run:
            snapshots_made.append(
                {
                    "card_id": card_id,
                    "provider": provider_name,
                    "listing_id": listing_id,
                    "grade": grade,
                    "snapshot_ids": [],
                    "dry_run": True,
                }
            )
            summary["snapshotted"] += 1
            bp["snapshotted"] += 1
            continue

        # Fetch + persist (uses the pricing provider's own TokenBucket
        # so PriceCharting's 1-req/sec limit is respected naturally).
        try:
            provider_impl = get_pricing_provider(provider_name)
        except ProviderNotEnabledError as e:
            failures.append(
                {"card_id": card_id, "provider": provider_name, "error": str(e)}
            )
            summary["failed"] += 1
            bp["failed"] += 1
            continue

        try:
            quote = await provider_impl.get_price(listing_id)
        except NotSupportedError as e:
            failures.append(
                {"card_id": card_id, "provider": provider_name, "error": str(e)}
            )
            summary["failed"] += 1
            bp["failed"] += 1
            continue
        except Exception as e:  # noqa: BLE001
            failures.append(
                {
                    "card_id": card_id,
                    "provider": provider_name,
                    "error": format_http_error(e, provider=provider_name),
                }
            )
            summary["failed"] += 1
            bp["failed"] += 1
            continue

        ids: list[str] = []
        # Ungraded / primary row.
        ids.append(
            db.add_pricing_snapshot(
                {
                    "provider": provider_name,
                    "listing_id": listing_id,
                    "grade": None,
                    "currency": quote.currency,
                    "market": quote.market,
                    "low": quote.low,
                    "high": quote.high,
                    "raw": quote.raw,
                }
            )
        )
        # Per-grade rows for providers that return graded levels (PriceCharting).
        for level in quote.graded_levels:
            ids.append(
                db.add_pricing_snapshot(
                    {
                        "provider": provider_name,
                        "listing_id": listing_id,
                        "grade": level.grade,
                        "currency": quote.currency,
                        "market": level.market,
                        "low": level.market,
                        "high": level.market,
                    }
                )
            )

        snapshots_made.append(
            {
                "card_id": card_id,
                "provider": provider_name,
                "listing_id": listing_id,
                "grade": grade,
                "snapshot_ids": ids,
            }
        )
        summary["snapshotted"] += 1
        bp["snapshotted"] += 1

    return json.dumps(
        {
            "ok": True,
            "dry_run": params.dry_run,
            "summary": summary,
            "by_provider": by_provider,
            "snapshots": snapshots_made,
            "failures": failures,
        },
        indent=2,
        default=str,
    )


class PricingHistoryInput(_StrictModel):
    """Time-series query against the local pricing_snapshots table."""

    provider: PricingProviderName = Field(...)
    listing_id: str = Field(..., min_length=1, max_length=64)
    grade: str | None = Field(
        default=None,
        description=(
            "Grade filter — None = ungraded series, "
            "or pass an explicit string like 'PSA 10' for graded series."
        ),
    )
    days: int = Field(
        default=90, ge=1, le=3650,
        description="Window in days. Default 90.",
    )
    limit: int = Field(default=200, ge=1, le=1000)


@mcp.tool(
    name="tcg_pricing_get_history",
    annotations={
        "title": "Pricing — return a time series of saved snapshots",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_pricing_get_history(params: PricingHistoryInput) -> str:
    """Return the time series of pricing snapshots saved for one listing.

    Useful for answering "is PSA 10 trending up?" or "what was Charizard ex
    SIR worth a month ago?" — but only works for listings you've snapshotted
    via `tcg_pricing_snapshot` or `tcg_pricing_snapshot_collection`.

    Returned snapshots are oldest-first.
    """
    db = get_db()
    rows = db.list_pricing_snapshots(
        provider=params.provider,
        listing_id=params.listing_id,
        grade=params.grade,
        days=params.days,
        limit=params.limit,
    )
    return json.dumps(
        {
            "provider": params.provider,
            "listing_id": params.listing_id,
            "grade": params.grade,
            "days": params.days,
            "count": len(rows),
            "snapshots": rows,
        },
        indent=2,
        default=str,
    )


def _captured_age_seconds(captured_at: str) -> float | None:
    """Compute seconds since `captured_at` (a SQLite-style timestamp string).

    SQLite's `datetime('now')` returns naive UTC like '2026-05-04 12:34:56'.
    """
    from datetime import datetime, timezone

    if not captured_at:
        return None
    try:
        dt = datetime.fromisoformat(captured_at.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds()


# =============================================================================
# v0.4 — Catalog tools (Pokemon TCG API)
# =============================================================================


class CatalogGetSetInput(_StrictModel):
    set_id: str = Field(..., min_length=1, max_length=32, description="e.g. 'sv8'")


@mcp.tool(
    name="tcg_catalog_get_set",
    annotations={
        "title": "Catalog — fetch one Pokemon TCG set's metadata",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_catalog_get_set(params: CatalogGetSetInput) -> str:
    """Fetch a Pokemon TCG set's metadata (name, total cards, release date, …).

    Free — no API key required (Pokemon TCG API).
    """
    catalog = get_pokemontcg_catalog_provider()
    if catalog is None:
        return "Error: Pokemon TCG catalog provider is not enabled."
    try:
        data = await catalog.get_set(params.set_id)
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="pokemontcg")
    if data is None:
        return f"Error: set '{params.set_id}' not found in Pokemon TCG API."
    return json.dumps(_normalize_set(data), indent=2, default=str)


class CatalogSearchSetInput(_StrictModel):
    query: str = Field(
        ..., min_length=1, max_length=200,
        description=(
            "Free-text or Lucene query. Plain text becomes a name prefix search "
            "(e.g. 'Surging' → matches 'Surging Sparks'). Pass full Lucene like "
            "'series:\"Scarlet & Violet\"' for advanced filtering."
        ),
    )
    limit: int = Field(default=20, ge=1, le=100)


@mcp.tool(
    name="tcg_catalog_search_set",
    annotations={
        "title": "Catalog — search Pokemon TCG sets by name",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_catalog_search_set(params: CatalogSearchSetInput) -> str:
    """Search Pokemon TCG sets. Returns set IDs, names, release dates, totals."""
    catalog = get_pokemontcg_catalog_provider()
    if catalog is None:
        return "Error: Pokemon TCG catalog provider is not enabled."
    try:
        rows = await catalog.search_sets(params.query, limit=params.limit)
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="pokemontcg")
    return json.dumps(
        {
            "query": params.query,
            "count": len(rows),
            "items": [_normalize_set(r) for r in rows],
        },
        indent=2,
        default=str,
    )


class CatalogListCardsInSetInput(_StrictModel):
    set_id: str = Field(..., min_length=1, max_length=32)
    rarity: str | None = Field(
        default=None,
        description=(
            "Optional rarity filter, e.g. 'Special Illustration Rare', "
            "'Hyper Rare', 'Double Rare'."
        ),
    )
    limit: int = Field(default=250, ge=1, le=250)
    offset: int = Field(default=0, ge=0)


@mcp.tool(
    name="tcg_catalog_list_cards_in_set",
    annotations={
        "title": "Catalog — list every card in a Pokemon TCG set",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_catalog_list_cards_in_set(params: CatalogListCardsInSetInput) -> str:
    """List every card in a set (optionally filtered by rarity), with TCGPlayer
    market price where available. Useful for set-completion analysis and
    rarity-tier breakdowns.
    """
    catalog = get_pokemontcg_catalog_provider()
    if catalog is None:
        return "Error: Pokemon TCG catalog provider is not enabled."
    try:
        rows = await catalog.list_cards_in_set(
            params.set_id,
            rarity=params.rarity,
            limit=params.limit,
            offset=params.offset,
        )
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="pokemontcg")
    return json.dumps(
        {
            "set_id": params.set_id,
            "rarity_filter": params.rarity,
            "count": len(rows),
            "items": [_normalize_catalog_card(r) for r in rows],
        },
        indent=2,
        default=str,
    )


# =============================================================================
# v0.4 — Collection set-completion + richer search
# =============================================================================


class CollectionSetCompletionInput(_StrictModel):
    set_id: str = Field(..., min_length=1, max_length=32)
    rarity: str | None = Field(
        default=None,
        description=(
            "Optional rarity filter — e.g. 'Special Illustration Rare' to ask "
            "'how many SIRs do I have from Surging Sparks?'"
        ),
    )


@mcp.tool(
    name="tcg_collection_set_completion",
    annotations={
        "title": "Collection — set-completion progress with gap pricing",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_collection_set_completion(
    params: CollectionSetCompletionInput,
) -> str:
    """Compute set-completion progress for a Pokemon TCG set.

    For each card in the set (optionally filtered by rarity):
      * Owned? Match owned_cards either by attached `pricing_listing_id`
        equal to the catalog card's `id` (precise), or as a fallback by
        case-insensitive subject match + card_number exact match.
      * Watchlist hit? Cross-reference `watchlist.card_descriptor` for
        substring matches against the card name + set name.
      * Missing? Sum current Pokemon TCG API market price for the gap.

    Returns owned/missing counts, completion percentage, gap cost in USD,
    and per-card breakdown.
    """
    catalog = get_pokemontcg_catalog_provider()
    if catalog is None:
        return "Error: Pokemon TCG catalog provider is not enabled."
    db = get_db()

    try:
        set_data = await catalog.get_set(params.set_id)
        if set_data is None:
            return f"Error: set '{params.set_id}' not found in Pokemon TCG API."
        cards_raw = await catalog.list_cards_in_set(
            params.set_id, rarity=params.rarity, limit=250
        )
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider="pokemontcg")

    # Build lookup tables for matching.
    owned_rows = db.list_owned(status="owned", limit=500)
    owned_by_listing: dict[str, dict[str, Any]] = {}
    owned_by_subject_number: dict[tuple[str, str], dict[str, Any]] = {}
    for row in owned_rows:
        listing = row.get("pricing_listing_id")
        if listing:
            owned_by_listing[listing] = row
        subj = (row.get("subject") or "").strip().lower()
        cnum = (row.get("card_number") or "").strip()
        if subj and cnum:
            owned_by_subject_number[(subj, cnum)] = row

    watchlist_rows = db.list_watchlist(open_only=True, limit=500)
    set_name_lower = (set_data.get("name") or "").lower()

    items: list[dict[str, Any]] = []
    owned_count = 0
    missing_market_total = 0.0
    rarity_breakdown: dict[str, dict[str, int]] = {}

    for card in cards_raw:
        card_id = card.get("id") or ""
        card_name = (card.get("name") or "").strip()
        card_number = (card.get("number") or "").strip()
        card_rarity = (card.get("rarity") or "Unknown").strip()

        # Match against owned.
        owned = owned_by_listing.get(card_id)
        match_method: str | None = None
        if owned is not None:
            match_method = "pricing_listing_id"
        else:
            owned = owned_by_subject_number.get(
                (card_name.lower(), card_number)
            )
            if owned is not None:
                match_method = "subject+card_number"

        # Watchlist intersection (only meaningful if not owned).
        on_watchlist = False
        watchlist_descriptor: str | None = None
        if owned is None:
            for w in watchlist_rows:
                desc = (w.get("card_descriptor") or "").lower()
                if (
                    card_name.lower() in desc
                    or (set_name_lower and set_name_lower in desc)
                ):
                    on_watchlist = True
                    watchlist_descriptor = w.get("card_descriptor")
                    break

        # Market price (top-level USD from TCGPlayer).
        market_price = _extract_card_market_price(card)

        # Aggregate.
        rb = rarity_breakdown.setdefault(
            card_rarity, {"owned": 0, "missing": 0, "missing_value_usd": 0}
        )
        if owned is not None:
            owned_count += 1
            rb["owned"] += 1
        else:
            rb["missing"] += 1
            if market_price is not None:
                missing_market_total += market_price
                # We sum into a separate float; ints in dict are display-only.
                rb["missing_value_usd"] = round(
                    rb["missing_value_usd"] + market_price, 2
                )

        items.append(
            {
                "card_id": card_id,
                "name": card_name,
                "card_number": card_number,
                "rarity": card_rarity,
                "market_price": market_price,
                "owned": owned is not None,
                "owned_card_id": owned["id"] if owned else None,
                "match_method": match_method,
                "on_watchlist": on_watchlist,
                "watchlist_descriptor": watchlist_descriptor,
            }
        )

    total = len(items)
    missing = total - owned_count
    pct = round((owned_count / total) * 100, 1) if total else 0.0

    return json.dumps(
        {
            "set_id": params.set_id,
            "set_name": set_data.get("name"),
            "rarity_filter": params.rarity,
            "summary": {
                "total_cards": total,
                "owned": owned_count,
                "missing": missing,
                "completion_pct": pct,
                "missing_value_usd": round(missing_market_total, 2),
            },
            "by_rarity": rarity_breakdown,
            "items": items,
        },
        indent=2,
        default=str,
    )


class CollectionSearchInput(_StrictModel):
    query: str = Field(
        ..., min_length=1, max_length=200,
        description=(
            "Free-text search across subject, set_name, brand, variety, "
            "notes, and tags. Case-insensitive substring."
        ),
    )
    status: Literal["owned", "sold", "lost", "graded_out"] | None = "owned"
    is_graded: bool | None = None
    language: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@mcp.tool(
    name="tcg_collection_search",
    annotations={
        "title": "Collection — full-text search across owned cards",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_collection_search(params: CollectionSearchInput) -> str:
    """Search owned cards across subject, set_name, brand, variety, notes,
    and tags — richer than `tcg_collection_list`'s subject-only filter.
    """
    db = get_db()
    rows = db.search_owned(
        query=params.query,
        status=params.status,
        is_graded=params.is_graded,
        language=params.language,
        limit=params.limit,
        offset=params.offset,
    )
    return json.dumps(
        {
            "query": params.query,
            "count": len(rows),
            "items": rows,
        },
        indent=2,
        default=str,
    )


# =============================================================================
# v0.4 — Smart-routing pricing tool
# =============================================================================


class PricingGetCardInput(_StrictModel):
    query: str = Field(
        ..., min_length=1, max_length=200,
        description=(
            "Free-text card name, or a Pokemon TCG API listing ID like "
            "'sv8-199' (auto-detected and used directly)."
        ),
    )
    grade: str | None = Field(
        default=None,
        description=(
            "If provided AND PriceCharting is enabled, includes graded "
            "prices for this grade (e.g. 'PSA 10'). Always-free path "
            "(Pokemon TCG API raw market) returns regardless."
        ),
    )
    prefer_provider: PricingProviderName | None = Field(
        default=None,
        description="Force a specific provider. None = smart routing.",
    )


@mcp.tool(
    name="tcg_pricing_get_card",
    annotations={
        "title": "Pricing — smart-routed lookup with optional graded data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def tcg_pricing_get_card(params: PricingGetCardInput) -> str:
    """Smart-routed price lookup. One call, the right provider(s) under the hood.

    Strategy:
      * Always queries Pokemon TCG API (free) — gives raw/ungraded market.
      * If `grade` is provided AND PriceCharting is enabled (token set),
        ALSO queries PriceCharting for the graded price level.
      * `prefer_provider` overrides routing entirely.

    Designed so an agent can ask "what's Charizard ex Surging Sparks worth"
    without knowing which provider to call first.
    """
    out: dict[str, Any] = {
        "query": params.query,
        "grade": params.grade,
        "providers_used": [],
        "raw_market": None,
        "graded_levels": [],
        "best_match": None,
    }

    # Did the user pass a provider-specific listing ID directly?
    looks_like_pokemontcg_id = "-" in params.query and " " not in params.query
    pokemontcg = get_pricing_provider("pokemontcg")

    # --- Primary: Pokemon TCG API ---
    if params.prefer_provider in (None, "pokemontcg"):
        try:
            if looks_like_pokemontcg_id:
                quote = await pokemontcg.get_price(params.query)
                listings = []
            else:
                listings = await pokemontcg.search(params.query, limit=1)
                quote = None
                if listings:
                    quote = await pokemontcg.get_price(listings[0].listing_id)
            if quote is not None and quote.market is not None:
                out["providers_used"].append("pokemontcg")
                out["raw_market"] = {
                    "currency": quote.currency,
                    "market": quote.market,
                    "low": quote.low,
                    "mid": quote.mid,
                    "high": quote.high,
                    "url": quote.url,
                }
                out["best_match"] = {
                    "provider": "pokemontcg",
                    "listing_id": quote.listing_id,
                    "name": quote.name,
                }
        except Exception as e:  # noqa: BLE001
            out["pokemontcg_error"] = format_http_error(e, provider="pokemontcg")

    # --- Optional: PriceCharting graded ---
    want_graded = params.grade is not None and params.prefer_provider in (
        None, "pricecharting"
    )
    if want_graded:
        try:
            pc = get_pricing_provider("pricecharting")
        except ProviderNotEnabledError:
            out["pricecharting_status"] = (
                "disabled — set PRICECHARTING_TOKEN to enable graded prices"
            )
        else:
            try:
                listings = await pc.search(params.query, limit=1)
                if listings:
                    quote = await pc.get_price(listings[0].listing_id)
                    out["providers_used"].append("pricecharting")
                    matching = [
                        {"grade": lvl.grade, "market": lvl.market}
                        for lvl in quote.graded_levels
                        if lvl.grade.lower() == params.grade.lower()  # type: ignore[union-attr]
                    ]
                    out["graded_levels"] = matching or [
                        {"grade": lvl.grade, "market": lvl.market}
                        for lvl in quote.graded_levels
                    ]
            except Exception as e:  # noqa: BLE001
                out["pricecharting_error"] = format_http_error(
                    e, provider="pricecharting"
                )

    return json.dumps(out, indent=2, default=str)


# =============================================================================
# Catalog normalizers
# =============================================================================


def _normalize_set(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "set_id": data.get("id"),
        "name": data.get("name"),
        "series": data.get("series"),
        "printed_total": data.get("printedTotal"),
        "total": data.get("total"),
        "release_date": data.get("releaseDate"),
        "ptcgo_code": data.get("ptcgoCode"),
        "images": data.get("images"),
    }


def _normalize_catalog_card(card: dict[str, Any]) -> dict[str, Any]:
    """Strip the catalog card down to what tools / agents need."""
    market = _extract_card_market_price(card)
    set_block = card.get("set") or {}
    return {
        "card_id": card.get("id"),
        "name": card.get("name"),
        "set_id": set_block.get("id"),
        "set_name": set_block.get("name"),
        "card_number": card.get("number"),
        "rarity": card.get("rarity"),
        "artist": card.get("artist"),
        "market_price_usd": market,
        "tcgplayer_url": (card.get("tcgplayer") or {}).get("url"),
        "images": card.get("images"),
    }


def _extract_card_market_price(card: dict[str, Any]) -> float | None:
    """Pull a single representative USD market price from a catalog card.

    Mirrors `_pick_primary_variant` in pricing/pokemontcg.py — prefer
    1stEditionHolofoil > holofoil > normal > anything else.
    """
    prices = (card.get("tcgplayer") or {}).get("prices") or {}
    if not isinstance(prices, dict) or not prices:
        return None
    for variant in (
        "1stEditionHolofoil", "holofoil", "1stEditionNormal",
        "reverseHolofoil", "normal", "unlimitedHolofoil", "unlimited",
    ):
        v = prices.get(variant)
        if isinstance(v, dict) and v.get("market") is not None:
            try:
                return float(v["market"])
            except (TypeError, ValueError):
                pass
    # Fallback: first variant with any market
    for v in prices.values():
        if isinstance(v, dict) and v.get("market") is not None:
            try:
                return float(v["market"])
            except (TypeError, ValueError):
                pass
    return None


# =============================================================================
# Meta tool
# =============================================================================


class ListProvidersInput(_StrictModel):
    pass


@mcp.tool(
    name="tcg_list_providers",
    annotations={
        "title": "List enabled grading + pricing providers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def tcg_list_providers(params: ListProvidersInput) -> str:
    """Show which grading / pricing providers are enabled in this process.

    Useful as a first call when introspecting the server's capabilities.
    """
    info: dict[str, Any] = {"grading": {}, "pricing": {}}

    # Grading
    for n in list_provider_names():
        if n == "psa":
            info["grading"][n] = {
                "status": "enabled",
                "env_var": "PSA_API_TOKEN",
                "docs": "https://www.psacard.com/publicapi",
                "tools": [
                    "tcg_psa_get_cert",
                    "tcg_psa_get_images",
                    "tcg_psa_add_to_collection",
                ],
            }
        elif n == "cgc":
            info["grading"][n] = {
                "status": "stub",
                "note": "No official CGC API yet. Planned via scraping or GemRate.",
                "tools": ["tcg_cgc_get_cert"],
            }
        elif n == "bgs":
            info["grading"][n] = {
                "status": "stub",
                "note": "No official Beckett API yet. Planned via scraping or GemRate.",
                "tools": ["tcg_bgs_get_cert"],
            }

    # Pricing
    pricing_names = list_pricing_provider_names()
    if "pokemontcg" in pricing_names:
        info["pricing"]["pokemontcg"] = {
            "status": "enabled",
            "env_var": "POKEMONTCG_API_KEY (optional)",
            "currency": "USD (TCGPlayer) — Cardmarket EUR available in raw",
            "docs": "https://docs.pokemontcg.io/",
            "rate_limit": "1000/day unkeyed; 20000/day with key",
        }
    if "pricecharting" in pricing_names:
        info["pricing"]["pricecharting"] = {
            "status": "enabled",
            "env_var": "PRICECHARTING_TOKEN",
            "currency": "USD",
            "docs": "https://www.pricecharting.com/api-documentation",
            "rate_limit": "1 req/sec",
        }
    else:
        info["pricing"]["pricecharting"] = {
            "status": "disabled",
            "env_var": "PRICECHARTING_TOKEN",
            "note": "Set PRICECHARTING_TOKEN to enable.",
        }
    if "snkrdunk" in pricing_names:
        info["pricing"]["snkrdunk"] = {
            "status": "stub",
            "currency": "JPY",
            "note": "No public SNKRDUNK API as of 2026-05.",
        }

    info["_tools"] = {
        "pricing": [
            "tcg_pricing_search",
            "tcg_pricing_get",
            "tcg_pricing_get_card",
            "tcg_pricing_snapshot",
            "tcg_pricing_snapshot_collection",
            "tcg_pricing_get_history",
        ],
        "catalog": [
            "tcg_catalog_get_set",
            "tcg_catalog_search_set",
            "tcg_catalog_list_cards_in_set",
        ],
        "collection": [
            "tcg_collection_search",
            "tcg_collection_set_completion",
        ],
    }
    return json.dumps(info, indent=2)


# =============================================================================
# Shared helpers
# =============================================================================


async def _run_get_cert(provider_name: str, cert_number: str, fmt: ResponseFormat) -> str:
    try:
        provider = get_provider(provider_name)
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        result = await provider.get_cert(cert_number)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider=provider_name)

    if not result.found:
        return (
            f"No record found for {provider_name.upper()} cert {cert_number}. "
            f"Double-check the cert number."
        )

    if fmt == ResponseFormat.JSON:
        return result.model_dump_json(indent=2)
    return _format_cert_markdown(result)


async def _run_get_images(provider_name: str, cert_number: str) -> str:
    try:
        provider = get_provider(provider_name)
    except ProviderNotEnabledError as e:
        return f"Error: {e}"

    try:
        images: list[ImageResult] = await provider.get_images(cert_number)
    except NotSupportedError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return format_http_error(e, provider=provider_name)

    return json.dumps(
        {
            "provider": provider_name,
            "cert_number": cert_number,
            "count": len(images),
            "images": [img.model_dump() for img in images],
        },
        indent=2,
    )


def _format_cert_markdown(c: CertResult) -> str:
    lines = [f"# {c.provider.upper()} Cert #{c.cert_number}"]
    if c.subject:
        bits = [c.year, c.brand, c.subject, c.variety, c.card_number]
        lines.append(" ".join(b for b in bits if b))
    if c.grade:
        grade_line = f"**Grade:** {c.grade}"
        if c.qualifier:
            grade_line += f" *(qualifier: {c.qualifier})*"
        lines.append(grade_line)
    if c.is_dual_cert:
        lines.append(
            f"**Dual cert (auto):** {c.autograph_grade or '?'} — signed by "
            f"{c.primary_signers or '?'}"
        )
    pop_bits = []
    if c.total_population is not None:
        pop_bits.append(f"total at this grade: **{c.total_population}**")
    if c.population_higher is not None:
        pop_bits.append(f"graded higher: **{c.population_higher}**")
    if pop_bits:
        lines.append("**Population:** " + ", ".join(pop_bits))
    lines.append("")
    lines.append("_Use response_format='json' to see the full normalized + raw payload._")
    return "\n".join(lines)


# =============================================================================
# Entrypoint
# =============================================================================


def main() -> None:
    """Console-script entrypoint."""
    # Apply DB migrations once at startup. Cheap (idempotent CREATE IF NOT EXISTS).
    get_db()
    mcp.run()  # stdio transport by default
