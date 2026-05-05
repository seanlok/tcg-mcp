"""Tests for tcg_collection_set_completion (v0.4)."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import (
    CollectionAddCardInput,
    CollectionAttachPricingInput,
    CollectionSetCompletionInput,
    WatchlistAddInput,
    tcg_collection_add_card,
    tcg_collection_attach_pricing,
    tcg_collection_set_completion,
    tcg_watchlist_add,
)

from .test_catalog import SAMPLE_SET

# Two cards in the set: one with the SIR rarity, one Hyper Rare.
CARD_SIR = {
    "id": "sv8-199",
    "name": "Charizard ex",
    "number": "199",
    "rarity": "Special Illustration Rare",
    "set": {"id": "sv8", "name": "Surging Sparks"},
    "tcgplayer": {
        "url": "https://prices.pokemontcg.io/tcgplayer/sv8-199",
        "prices": {"holofoil": {"market": 110.0}},
    },
}

CARD_HR = {
    "id": "sv8-220",
    "name": "Pikachu ex",
    "number": "220",
    "rarity": "Hyper Rare",
    "set": {"id": "sv8", "name": "Surging Sparks"},
    "tcgplayer": {
        "url": "https://prices.pokemontcg.io/tcgplayer/sv8-220",
        "prices": {"holofoil": {"market": 45.0}},
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


def _stub_set_endpoints(httpx_mock: HTTPXMock, cards: list[dict]) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/sets/sv8",
        json={"data": SAMPLE_SET},
        is_reusable=True,
    )
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=set.id%3Asv8&pageSize=250&page=1",
        json={"data": cards},
        is_reusable=True,
    )


async def test_completion_empty_collection(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    _stub_set_endpoints(httpx_mock, [CARD_SIR, CARD_HR])

    out = json.loads(
        await tcg_collection_set_completion(
            CollectionSetCompletionInput(set_id="sv8")
        )
    )
    s = out["summary"]
    assert s["total_cards"] == 2
    assert s["owned"] == 0
    assert s["missing"] == 2
    assert s["completion_pct"] == 0.0
    assert s["missing_value_usd"] == 155.0   # 110 + 45


async def test_completion_with_attached_pricing_match(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    _stub_set_endpoints(httpx_mock, [CARD_SIR, CARD_HR])

    add = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(subject="Charizard ex", acquisition_price=80.0)
        )
    )
    await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id=add["card_id"],
            pricing_provider="pokemontcg",
            pricing_listing_id="sv8-199",
        )
    )

    out = json.loads(
        await tcg_collection_set_completion(
            CollectionSetCompletionInput(set_id="sv8")
        )
    )
    s = out["summary"]
    assert s["owned"] == 1
    assert s["missing"] == 1
    assert s["missing_value_usd"] == 45.0   # only HR remaining

    sir_item = next(i for i in out["items"] if i["card_id"] == "sv8-199")
    assert sir_item["owned"] is True
    assert sir_item["match_method"] == "pricing_listing_id"


async def test_completion_subject_card_number_fallback_match(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    _stub_set_endpoints(httpx_mock, [CARD_SIR, CARD_HR])

    # Add the card with no pricing attachment, just subject + card_number.
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Pikachu ex", card_number="220")
    )
    out = json.loads(
        await tcg_collection_set_completion(
            CollectionSetCompletionInput(set_id="sv8")
        )
    )
    pika = next(i for i in out["items"] if i["card_id"] == "sv8-220")
    assert pika["owned"] is True
    assert pika["match_method"] == "subject+card_number"


async def test_completion_rarity_filter(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/sets/sv8",
        json={"data": SAMPLE_SET},
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=(
            "https://mock.pokemontcg.test/v2/cards"
            "?q=set.id%3Asv8+rarity%3A%22Special+Illustration+Rare%22"
            "&pageSize=250&page=1"
        ),
        json={"data": [CARD_SIR]},
        is_reusable=True,
    )

    out = json.loads(
        await tcg_collection_set_completion(
            CollectionSetCompletionInput(
                set_id="sv8", rarity="Special Illustration Rare"
            )
        )
    )
    assert out["summary"]["total_cards"] == 1
    assert out["rarity_filter"] == "Special Illustration Rare"


async def test_completion_watchlist_intersection(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    _stub_set_endpoints(httpx_mock, [CARD_SIR, CARD_HR])

    # Add a watchlist entry whose descriptor mentions the SIR card name.
    await tcg_watchlist_add(
        WatchlistAddInput(
            card_descriptor="Charizard ex SIR — Surging Sparks",
            target_price=180.0,
        )
    )

    out = json.loads(
        await tcg_collection_set_completion(
            CollectionSetCompletionInput(set_id="sv8")
        )
    )
    sir = next(i for i in out["items"] if i["card_id"] == "sv8-199")
    assert sir["on_watchlist"] is True
    assert sir["watchlist_descriptor"] is not None
