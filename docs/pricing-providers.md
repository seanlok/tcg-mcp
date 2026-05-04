# Pricing Providers — Stage 2 Plan

Research notes and design plan for the Stage 2 pricing provider abstraction.
Stage 1 builds the package rename + collection. Stage 2 adds pricing.

---

## Provider survey

### Pokemon TCG API (`pokemontcg.io`)

- **Status:** Best free starting point. Now operated under [Scrydex](https://scrydex.com/).
- **Auth:** API key (free, register at https://dev.pokemontcg.io/). Header: `X-Api-Key: <key>`.
- **Rate limits:** Without key — 1000/day, 30/min. With key — 20,000/day default; higher tiers via Discord/email.
- **What it returns:** Card metadata + sets + types/subtypes. Card object includes:
  - `tcgplayer.prices.{normal,holofoil,reverseHolofoil,1stEditionHolofoil,...}.{low,mid,high,market,directLow}` in USD
  - `cardmarket.prices.{averageSellPrice,lowPrice,trendPrice,germanProLow,suggestedPrice,reverseHoloSell,reverseHoloLow,reverseHoloTrend,lowPriceExPlus,avg1,avg7,avg30,reverseHoloAvg1,reverseHoloAvg7,reverseHoloAvg30}` in EUR
- **Coverage:** English + Japanese expansions. Sealed product not directly priced (you'd derive from singles or a different source).
- **MCP shape:**
  - `tcg_pricing_pokemontcg_get_card(card_id)` → market prices for a known card ID
  - `tcg_pricing_pokemontcg_search_card(name, set?, number?)` → resolve a card identity first

### PriceCharting

- **Status:** Paid subscription. The strongest single source for **graded card** pricing — has slab-grade-level price points.
- **Auth:** API token passed as `t=<token>` query param. Subscription required.
- **Rate limits:** **1 call per second**, CSV calls limited to **1 per 10 minutes**. Exceeding triggers blocking and (per their docs) account suspension.
- **What it returns:** `loose-price`, `cib-price`, `new-price` for sealed; for trading cards: ungraded + graded (PSA 7/8/9/10, BGS, etc.) market prices.
- **Endpoints:**
  - `GET https://www.pricecharting.com/api/product?t=TOKEN&q=<query>` — search/lookup
  - `GET https://www.pricecharting.com/api/product?t=TOKEN&id=<id>` — by product ID
  - CSV export endpoint for bulk
- **MCP shape:**
  - `tcg_pricing_pricecharting_search(query)`
  - `tcg_pricing_pricecharting_get(product_id)` → returns raw + graded prices

### SNKRDUNK

- **Status:** **No public API found** as of May 2026. SNKRDUNK is a Japanese marketplace popular for JP Pokemon singles and sealed; the listing site at https://snkrdunk.com/en/brands/pokemon/trading-cards is browsable but has no documented developer API.
- **Likely paths to support it:**
  1. Polite scraping of search/listing pages (ToS-sensitive; needs review).
  2. Reverse-engineer the mobile app's backend API (also ToS-sensitive).
  3. Watch for an official API announcement.
- **Recommendation:** Defer until either an API is published or we explicitly approve a scraping policy. Keep `snkrdunk.py` as a stub provider that returns NotSupportedError, similar to how CGC/BGS are handled in `grading_mcp`.

### Other candidates (not in scope yet, but listed for completeness)

| Provider | Notes |
|---|---|
| **TCGPlayer Partner API** | Powerful but requires partner application; not always granted to individuals. Already covered indirectly via Pokemon TCG API. |
| **Cardmarket** | EU pricing. Already covered indirectly via Pokemon TCG API. Direct API exists with OAuth but requires a Cardmarket account. |
| **eBay Marketplace Insights** | Sold-listing data. Paid; requires application. Best path for "what did this actually sell for" comps. |
| **JustTCG** | Aggregator, paid API. Could be a backstop. |
| **Scrydex** | Now hosts pokemontcg.io; future-proof option. |
| **PokemonPriceTracker** | Has a free tier with TCGPlayer + eBay data. Worth evaluating. |

---

## Provider abstraction (Stage 2 design)

Mirror the grading-provider pattern. Define a `PricingProvider` Protocol in
`src/tcg_mcp/pricing/base.py`:

```python
@runtime_checkable
class PricingProvider(Protocol):
    name: str  # "pokemontcg" | "pricecharting" | "snkrdunk" | ...
    capabilities: set[Capability]  # {"singles_raw", "singles_graded", "sealed", "japanese"}

    async def search(self, query: str, limit: int = 20) -> list[CardListing]: ...
    async def get_price(self, listing_id: str) -> PriceQuote: ...
    async def get_graded_price(self, listing_id: str, grade: GradeSpec) -> PriceQuote: ...
```

Canonical models:

```python
class PriceQuote(BaseModel):
    provider: str
    listing_id: str
    currency: str  # "USD" | "EUR" | "JPY"
    market: float | None
    low: float | None
    high: float | None
    last_updated: datetime
    grade: str | None  # None = raw
    raw: dict
```

---

## Rate limiting & retry

Every provider must declare its rate limit. A shared decorator/helper in
`src/tcg_mcp/pricing/throttle.py` will:

1. Apply a **per-provider token bucket** (e.g. 1 req/sec for PriceCharting,
   30 req/min for unkeyed Pokemon TCG API).
2. Retry on **429** and **5xx** with exponential backoff and jitter.
3. Surface a clean MCP error after N retries: `"Error: <provider> rate limit
   persisted; try again in N seconds"`.

Implementation sketch — use `asyncio.Semaphore` + a sliding window OR adopt
the `aiolimiter` package (small, focused). Decision deferred until Stage 2
implementation begins.

---

## Tool surface (Stage 2)

| Tool | Purpose |
|---|---|
| `tcg_pricing_get_card` | Generic — pick best provider based on query (Pokemon TCG API by default) |
| `tcg_pricing_get_graded` | Graded price (uses PriceCharting if available) |
| `tcg_pricing_get_sealed` | ETB/booster box pricing (PriceCharting + Pokemon TCG API) |
| `tcg_pricing_pokemontcg_search` | Provider-specific escape hatch |
| `tcg_pricing_pricecharting_search` | Provider-specific escape hatch |
| `tcg_pricing_snapshot` | Save current prices for one or more cards into the local SQLite DB for trend analysis |

The "generic" tools (`get_card`, `get_graded`, `get_sealed`) pick a provider
based on capability + availability. The provider-specific tools are escape
hatches when the user wants exact behavior.

---

## Required env vars (Stage 2)

| Env var | Required for | Notes |
|---|---|---|
| `POKEMONTCG_API_KEY` | Pokemon TCG API enablement | Optional but strongly recommended (1000/day → 20000/day) |
| `PRICECHARTING_TOKEN` | PriceCharting | Required to enable that provider |
| `SNKRDUNK_*` | (not yet) | Not applicable until API support exists |

Missing env var = provider silently disabled, surfaced via
`tcg_list_providers` so the user sees what's enabled.

---

## Sources

- [PriceCharting API Documentation](https://www.pricecharting.com/api-documentation)
- [Pokemon TCG API — Rate Limits](https://docs.pokemontcg.io/getting-started/rate-limits/)
- [Pokemon TCG API — Card object](https://docs.pokemontcg.io/api-reference/cards/card-object/)
- [Scrydex (now hosts pokemontcg.io)](https://scrydex.com/)
- [SNKRDUNK Pokemon TCG section](https://snkrdunk.com/en/brands/pokemon/trading-cards)
- [aiolimiter (Python rate-limit lib)](https://github.com/mjpieters/aiolimiter)
