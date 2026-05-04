"""PriceCharting pricing provider.

Endpoints:
    GET /api/product?t=TOKEN&q=<query>   — search/lookup by name
    GET /api/product?t=TOKEN&id=<id>     — fetch by product ID

Auth: token in `t` query param. Paid subscription required.

Important encoding quirk: PriceCharting returns prices as integer pennies.
$17.32 comes back as `1732`. We convert via `cents_to_dollars`.

Rate limit (per docs): 1 call per second. CSV calls 1 per 10 minutes.
We enforce 1 req/sec at the bucket level; CSV is not used here.

Trading-card response fields we look for (best-effort — PriceCharting docs
list these for cards but the exact key set varies):
    loose-price          — ungraded card market price
    cib-price            — sealed-equivalent / mint
    new-price            — N/A for raw cards; sometimes used
    graded-price         — generic "PSA 9" floor (legacy)
    manual-only-price    — usually N/A for cards
    box-only-price       — usually N/A for cards
    bgs-10-price         — BGS 10
    condition-17-price   — PSA 10 (PriceCharting condition code)
    condition-18-price   — PSA 9
    condition-19-price   — PSA 8
    ...

We surface the primary three at the top level (`market` ← loose-price,
`high` ← cib-price, `low` left null) and stuff every `*-price` field that
looks graded into `graded_levels`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from tcg_mcp.pricing.base import BasePricingProvider
from tcg_mcp.pricing.models import (
    CardListing,
    GradedPriceLevel,
    PriceQuote,
    ProductKind,
    cents_to_dollars,
)
from tcg_mcp.pricing.throttle import TokenBucket, with_retries

log = logging.getLogger(__name__)


# Map of PriceCharting "condition-N-price" codes for trading cards.
# Sourced from PriceCharting community docs — verify with real responses.
_CONDITION_CODE_TO_GRADE = {
    "17": "PSA 10",
    "18": "PSA 9",
    "19": "PSA 8",
    "20": "PSA 7",
    "21": "PSA 6",
    "22": "PSA 5",
    "23": "PSA 4",
    "24": "PSA 3",
    "25": "PSA 2",
    "26": "PSA 1",
}

# Recognized graded-price field names whose semantic is clear from the name.
_NAMED_GRADED_FIELDS: dict[str, str] = {
    "graded-price": "PSA 9",          # PriceCharting historical convention
    "bgs-10-price": "BGS 10",
    "bgs-95-price": "BGS 9.5",
    "bgs-9-price": "BGS 9",
    "cgc-10-price": "CGC 10",
    "cgc-95-price": "CGC 9.5",
    "cgc-9-price": "CGC 9",
}

_CONDITION_FIELD_RE = re.compile(r"^condition-(\d+)-price$")


class PriceChartingProvider(BasePricingProvider):
    name = "pricecharting"
    currency = "USD"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://www.pricecharting.com/api",
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ValueError("PriceChartingProvider requires a non-empty token")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Their docs are firm: 1 req/sec. We give ourselves 1 token, 1/sec refill.
        self._bucket = TokenBucket(capacity=1, refill_rate=1.0)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )

    async def search(
        self,
        query: str,
        *,
        kind: ProductKind = ProductKind.UNKNOWN,
        limit: int = 20,
    ) -> list[CardListing]:
        """Search by query string.

        Note: the public PriceCharting `/api/product` endpoint returns ONE
        product per call (the best match). We surface it as a single-item
        list to keep the interface uniform with multi-result providers.
        For deeper search use cases, callers can fall back to the website
        URL and parse listings client-side.
        """
        params = {"t": self._token, "q": query}

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get("/product", params=params)
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pricecharting")
        if payload.get("status") != "success" or not payload.get("id"):
            return []
        return [_listing_from_payload(payload, query=query)][:limit]

    async def get_price(self, listing_id: str) -> PriceQuote:
        params = {"t": self._token, "id": listing_id}

        async def do() -> dict[str, Any]:
            await self._bucket.acquire()
            async with self._client() as client:
                r = await client.get("/product", params=params)
                r.raise_for_status()
                return r.json()

        payload = await with_retries(do, provider="pricecharting")
        if payload.get("status") != "success":
            return PriceQuote(
                provider="pricecharting",
                listing_id=listing_id,
                currency="USD",
                kind=ProductKind.SINGLE,
                raw=payload,
            )

        loose = cents_to_dollars(payload.get("loose-price"))
        cib = cents_to_dollars(payload.get("cib-price"))
        new = cents_to_dollars(payload.get("new-price"))

        graded: list[GradedPriceLevel] = []

        # Named graded fields (graded-price, bgs-10-price, ...)
        for field, grade_label in _NAMED_GRADED_FIELDS.items():
            v = cents_to_dollars(payload.get(field))
            if v is not None:
                graded.append(
                    GradedPriceLevel(grade=grade_label, market=v, raw_field=field)
                )

        # condition-NN-price fields (PSA grades by code)
        for k, v in payload.items():
            if not isinstance(k, str):
                continue
            m = _CONDITION_FIELD_RE.match(k)
            if not m:
                continue
            code = m.group(1)
            label = _CONDITION_CODE_TO_GRADE.get(code, f"PriceCharting condition {code}")
            usd = cents_to_dollars(v)
            if usd is not None:
                graded.append(
                    GradedPriceLevel(grade=label, market=usd, raw_field=k)
                )

        return PriceQuote(
            provider="pricecharting",
            listing_id=listing_id,
            currency="USD",
            kind=ProductKind.SINGLE,
            market=loose,
            low=loose,                      # No separate "low" — use loose
            high=cib if cib is not None else new,
            variants={},                    # PriceCharting doesn't split variants
            graded_levels=graded,
            name=_str_or_none(payload.get("product-name")),
            url=_product_url(payload.get("id")),
            raw=payload,
        )


# ---- helpers ----------------------------------------------------------------


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _product_url(product_id: Any) -> str | None:
    if not product_id:
        return None
    return f"https://www.pricecharting.com/game/{product_id}"


def _listing_from_payload(payload: dict[str, Any], *, query: str) -> CardListing:
    pid = str(payload.get("id") or "")
    return CardListing(
        provider="pricecharting",
        listing_id=pid,
        name=_str_or_none(payload.get("product-name")),
        set_name=_str_or_none(payload.get("console-name")),  # PC's set field
        year=_str_or_none(payload.get("release-date")),
        kind=ProductKind.SINGLE,
        url=_product_url(pid),
        raw=payload,
    )
