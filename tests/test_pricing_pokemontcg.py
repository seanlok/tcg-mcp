"""Pokemon TCG API provider tests with mocked httpx."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tcg_mcp.pricing.models import ProductKind
from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider, _pick_primary_variant

SAMPLE_CARD = {
    "id": "base1-4",
    "name": "Charizard",
    "number": "4",
    "set": {"id": "base1", "name": "Base", "releaseDate": "1999/01/09"},
    "tcgplayer": {
        "url": "https://tcgplayer.com/card/123",
        "prices": {
            "1stEditionHolofoil": {
                "low": 5000.0,
                "mid": 8000.0,
                "high": 15000.0,
                "market": 7800.0,
                "directLow": 7500.0,
            },
            "holofoil": {
                "low": 200.0,
                "mid": 400.0,
                "high": 1500.0,
                "market": 380.0,
                "directLow": 350.0,
            },
        },
    },
    "cardmarket": {
        "prices": {
            "averageSellPrice": 6000.0,
            "trendPrice": 6500.0,
        }
    },
}

SAMPLE_SEARCH_RESPONSE = {
    "data": [SAMPLE_CARD],
    "page": 1,
    "pageSize": 20,
    "count": 1,
    "totalCount": 1,
}


@pytest.fixture
def provider() -> PokemonTCGProvider:
    # Throttle is internal — fine to use the default; we keep the mock fast.
    return PokemonTCGProvider(
        api_key="fake-key",
        base_url="https://mock.pokemontcg.test/v2",
    )


def test_pick_primary_variant_prefers_first_edition() -> None:
    prices = {
        "normal": {"market": 1.0},
        "holofoil": {"market": 2.0},
        "1stEditionHolofoil": {"market": 3.0},
    }
    assert _pick_primary_variant(prices) == "1stEditionHolofoil"


def test_pick_primary_variant_falls_back_to_first_with_market() -> None:
    prices = {"weirdVariant": {"market": 5.0}}
    assert _pick_primary_variant(prices) == "weirdVariant"


def test_pick_primary_variant_handles_empty() -> None:
    assert _pick_primary_variant({}) is None


async def test_search_translates_simple_query_to_name_filter(
    provider: PokemonTCGProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22Charizard%22&pageSize=20",
        json=SAMPLE_SEARCH_RESPONSE,
    )

    listings = await provider.search("Charizard", limit=20)
    assert len(listings) == 1
    item = listings[0]
    assert item.provider == "pokemontcg"
    assert item.listing_id == "base1-4"
    assert item.name == "Charizard"
    assert item.set_name == "Base"
    assert item.kind == ProductKind.SINGLE


async def test_search_passes_through_lucene_query(
    provider: PokemonTCGProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=(
            "https://mock.pokemontcg.test/v2/cards"
            "?q=name%3Acharizard+set.id%3Abase1&pageSize=20"
        ),
        json=SAMPLE_SEARCH_RESPONSE,
    )
    out = await provider.search("name:charizard set.id:base1", limit=20)
    assert len(out) == 1


async def test_search_sends_api_key_header(
    provider: PokemonTCGProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22X%22&pageSize=5",
        json={"data": []},
    )
    await provider.search("X", limit=5)
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "fake-key"


async def test_get_price_uses_first_edition_variant_as_primary(
    provider: PokemonTCGProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    quote = await provider.get_price("base1-4")
    assert quote.provider == "pokemontcg"
    assert quote.currency == "USD"
    assert quote.market == 7800.0
    assert quote.low == 5000.0
    assert quote.high == 15000.0
    assert "1stEditionHolofoil" in quote.variants
    assert "holofoil" in quote.variants
    # PriceCharting-only graded prices stay empty here.
    assert quote.graded_levels == []


async def test_get_price_handles_empty_response(
    provider: PokemonTCGProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/missing",
        json={"data": None},
    )
    quote = await provider.get_price("missing")
    assert quote.market is None
    assert quote.variants == {}
