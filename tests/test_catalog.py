"""Tests for the Pokemon TCG catalog tools (v0.4)."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import (
    CatalogGetSetInput,
    CatalogListCardsInSetInput,
    CatalogSearchSetInput,
    tcg_catalog_get_set,
    tcg_catalog_list_cards_in_set,
    tcg_catalog_search_set,
)

SAMPLE_SET = {
    "id": "sv8",
    "name": "Surging Sparks",
    "series": "Scarlet & Violet",
    "printedTotal": 191,
    "total": 252,
    "releaseDate": "2024/11/08",
    "ptcgoCode": "SSP",
    "images": {"symbol": "x", "logo": "y"},
}

SAMPLE_CATALOG_CARD = {
    "id": "sv8-199",
    "name": "Charizard ex",
    "number": "199",
    "rarity": "Special Illustration Rare",
    "artist": "AKIRA EGAWA",
    "set": {"id": "sv8", "name": "Surging Sparks"},
    "tcgplayer": {
        "url": "https://prices.pokemontcg.io/tcgplayer/sv8-199",
        "prices": {
            "holofoil": {"low": 80, "mid": 120, "high": 250, "market": 105.34}
        },
    },
}


def _wire_pokemontcg_at_mock() -> None:
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider

    reset_pricing_registry()
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pokemontcg"] = PokemonTCGProvider(
        api_key=None, base_url="https://mock.pokemontcg.test/v2"
    )


async def test_get_set_returns_normalized_metadata(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/sets/sv8",
        json={"data": SAMPLE_SET},
    )
    out = json.loads(
        await tcg_catalog_get_set(CatalogGetSetInput(set_id="sv8"))
    )
    assert out["set_id"] == "sv8"
    assert out["name"] == "Surging Sparks"
    assert out["printed_total"] == 191
    assert out["total"] == 252


async def test_get_set_handles_missing(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/sets/nope",
        json={"data": None},
    )
    out = await tcg_catalog_get_set(CatalogGetSetInput(set_id="nope"))
    assert out.startswith("Error:") or "not found" in out.lower()


async def test_search_set_translates_simple_query(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url=(
            "https://mock.pokemontcg.test/v2/sets"
            "?q=name%3A%22Surging%2A%22&pageSize=20"
        ),
        json={"data": [SAMPLE_SET]},
    )
    out = json.loads(
        await tcg_catalog_search_set(
            CatalogSearchSetInput(query="Surging", limit=20)
        )
    )
    assert out["count"] == 1
    assert out["items"][0]["set_id"] == "sv8"


async def test_search_set_passes_through_lucene(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url=(
            "https://mock.pokemontcg.test/v2/sets"
            "?q=series%3A%22Scarlet+%26+Violet%22&pageSize=20"
        ),
        json={"data": [SAMPLE_SET]},
    )
    out = json.loads(
        await tcg_catalog_search_set(
            CatalogSearchSetInput(query='series:"Scarlet & Violet"')
        )
    )
    assert out["count"] == 1


async def test_list_cards_in_set_with_rarity_filter(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url=(
            "https://mock.pokemontcg.test/v2/cards"
            "?q=set.id%3Asv8+rarity%3A%22Special+Illustration+Rare%22"
            "&pageSize=250&page=1"
        ),
        json={"data": [SAMPLE_CATALOG_CARD]},
    )
    out = json.loads(
        await tcg_catalog_list_cards_in_set(
            CatalogListCardsInSetInput(
                set_id="sv8", rarity="Special Illustration Rare"
            )
        )
    )
    assert out["count"] == 1
    item = out["items"][0]
    assert item["card_id"] == "sv8-199"
    assert item["rarity"] == "Special Illustration Rare"
    assert item["market_price_usd"] == 105.34
    assert item["set_name"] == "Surging Sparks"


async def test_list_cards_handles_no_rarity(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=set.id%3Asv8&pageSize=250&page=1",
        json={"data": [SAMPLE_CATALOG_CARD]},
    )
    out = json.loads(
        await tcg_catalog_list_cards_in_set(
            CatalogListCardsInSetInput(set_id="sv8")
        )
    )
    assert out["count"] == 1
    assert out["rarity_filter"] is None
