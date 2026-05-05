"""Pokemon TCG API (pokemontcg.io v2) pricing provider.

Endpoints used:
    GET /v2/cards          — search cards (Lucene-style q= query)
    GET /v2/cards/{id}     — fetch a single card with prices

Auth (optional but recommended): X-Api-Key header.

Card object pricing fields (USD via TCGPlayer, EUR via Cardmarket):
    tcgplayer.prices.{normal|holofoil|reverseHolofoil|...}
        .{low, mid, high, market, directLow}
    cardmarket.prices
        .{averageSellPrice, lowPrice, trendPrice, ...}

We surface TCGPlayer prices as primary (USD); Cardmarket lives in `raw`.

Rate limits (from docs.pokemontcg.io):
    Without API key: 1000 requests/day, 30 requests/minute
    With API key (default): 20,000/day; higher tiers via Discord/email
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tcg_mcp.pricing.base import BasePricingProvider
from tcg_mcp.pricing.models import (
    CardListing,
    PriceQuote,
    ProductKind,
)
from tcg_mcp.pricing.throttle import TokenBucket, with_retries

log = logging.getLogger(__name__)


class PokemonTCGProvider(BasePricingProvider):
    name = "pokemontcg"
    currency = "USD"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.pokemontcg.io/v2",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # 30/min unkeyed; with a key, the docs say 20k/day default which is
        # ~14/min. We pick a conservative ~0.5/sec either way.
        self._bucket = TokenBucket(capacity=2, refill_rate=0.5)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._api_key:
            h["X-Api-Key"] = self._api_key
        return h

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
        )

    async def search(
        self,
        query: str,
        *,
        kind: ProductKind = ProductKind.UNKNOWN,
        limit: int = 20,
    ) -> list[CardListing]:
        # Pokemon TCG API uses Lucene-style queries. Treat the user query as
        # a name search by default; advanced callers can pass a full query
        # like 'name:charizard set.id:base1' and we pass it through.
        params = {
            "q": query if ":" in query else f'name:"{query}"',
            "pageSize": min(limit, 250),
        }

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get("/cards", params=params)
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pokemontcg")
        data = payload.get("data", []) or []

        out: list[CardListing] = []
        for card in data[:limit]:
            out.append(
                CardListing(
                    provider="pokemontcg",
                    listing_id=str(card.get("id") or ""),
                    name=_str_or_none(card.get("name")),
                    set_name=_get_nested(card, "set", "name"),
                    card_number=_str_or_none(card.get("number")),
                    year=_str_or_none(_get_nested(card, "set", "releaseDate")),
                    kind=ProductKind.SINGLE,
                    url=_get_nested(card, "tcgplayer", "url"),
                    raw=card,
                )
            )
        return out

    async def get_price(self, listing_id: str) -> PriceQuote:
        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get(f"/cards/{listing_id}")
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pokemontcg")
        card = (payload or {}).get("data") or {}
        if not card:
            return PriceQuote(
                provider="pokemontcg",
                listing_id=listing_id,
                currency="USD",
                kind=ProductKind.SINGLE,
                raw=payload or {},
            )

        tcg_prices = _get_nested(card, "tcgplayer", "prices") or {}
        # Pick the most likely "primary" variant for top-level fields.
        primary_variant = _pick_primary_variant(tcg_prices)
        primary = tcg_prices.get(primary_variant, {}) if primary_variant else {}

        # Build variants map — every variant gets included for power users.
        variants: dict[str, dict[str, float | None]] = {}
        for vname, vprices in tcg_prices.items():
            if not isinstance(vprices, dict):
                continue
            variants[vname] = {
                k: _float_or_none(vprices.get(k))
                for k in ("low", "mid", "high", "market", "directLow")
            }

        return PriceQuote(
            provider="pokemontcg",
            listing_id=listing_id,
            currency="USD",
            kind=ProductKind.SINGLE,
            market=_float_or_none(primary.get("market")),
            low=_float_or_none(primary.get("low")),
            mid=_float_or_none(primary.get("mid")),
            high=_float_or_none(primary.get("high")),
            variants=variants,
            graded_levels=[],  # Pokemon TCG API doesn't ship graded prices
            name=_str_or_none(card.get("name")),
            url=_get_nested(card, "tcgplayer", "url"),
            raw=card,
        )

    # ---- Catalog endpoints (v0.4) ------------------------------------------
    # These don't fit the PricingProvider Protocol but live on the same class
    # to share the httpx client, throttle, and API key.

    async def get_set(self, set_id: str) -> dict[str, Any] | None:
        """Fetch one set's metadata. Returns the raw `data` dict or None."""

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get(f"/sets/{set_id}")
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pokemontcg")
        return (payload or {}).get("data") or None

    async def search_sets(
        self, query: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search sets by Lucene query (e.g. 'name:Surging*'). Returns raw dicts."""
        if ":" not in query:
            q = f'name:"{query}*"'
        else:
            q = query
        params = {"q": q, "pageSize": min(limit, 250)}

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get("/sets", params=params)
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pokemontcg")
        return list((payload or {}).get("data") or [])[:limit]

    async def list_cards_in_set(
        self,
        set_id: str,
        *,
        rarity: str | None = None,
        limit: int = 250,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List every card in a set, optionally filtered by rarity."""
        q = f'set.id:{set_id}'
        if rarity:
            # Lucene uses + for AND; quote the rarity since it has spaces.
            q = f'{q} rarity:"{rarity}"'
        # Pokemon TCG API uses page-based pagination, not offset.
        page_size = min(limit, 250)
        page = (offset // page_size) + 1 if page_size else 1
        params = {"q": q, "pageSize": page_size, "page": page}

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get("/cards", params=params)
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pokemontcg")
        return list((payload or {}).get("data") or [])[:limit]


# ---- helpers ----------------------------------------------------------------


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_nested(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# Pokemon TCG API variant keys, in rough preference order.
# We pick the first one present in the response as the "primary".
_VARIANT_PRIORITY = (
    "1stEditionHolofoil",
    "holofoil",
    "1stEditionNormal",
    "reverseHolofoil",
    "normal",
    "unlimitedHolofoil",
    "unlimited",
)


def _pick_primary_variant(tcg_prices: dict[str, Any]) -> str | None:
    if not isinstance(tcg_prices, dict):
        return None
    for v in _VARIANT_PRIORITY:
        if v in tcg_prices and isinstance(tcg_prices[v], dict):
            return v
    # Fallback: first variant with a market price
    for k, vprices in tcg_prices.items():
        if isinstance(vprices, dict) and vprices.get("market") is not None:
            return k
    # Fallback: first variant at all
    for k, vprices in tcg_prices.items():
        if isinstance(vprices, dict):
            return k
    return None
