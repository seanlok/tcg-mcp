"""Tests for tcg_pricing_get_card (v0.4 smart-routed lookup)."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import PricingGetCardInput, tcg_pricing_get_card

from .test_pricing_pokemontcg import SAMPLE_CARD, SAMPLE_SEARCH_RESPONSE
from .test_pricing_pricecharting import SAMPLE_PC_RESPONSE


def _wire_pokemontcg_at_mock() -> None:
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider

    reset_pricing_registry()
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pokemontcg"] = PokemonTCGProvider(
        api_key=None, base_url="https://mock.pokemontcg.test/v2"
    )


def _wire_pricecharting_at_mock(token: str = "fake-pricecharting-token") -> None:
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pricecharting import PriceChartingProvider

    pricing_mod._registry["pricecharting"] = PriceChartingProvider(
        token=token, base_url="https://mock.pc.test/api"
    )


async def test_get_card_uses_pokemontcg_for_raw_market(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22Charizard%22&pageSize=1",
        json=SAMPLE_SEARCH_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )

    out = json.loads(
        await tcg_pricing_get_card(PricingGetCardInput(query="Charizard"))
    )
    assert "pokemontcg" in out["providers_used"]
    assert out["raw_market"]["market"] == 7800.0
    assert out["graded_levels"] == []


async def test_get_card_with_listing_id_skips_search(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    out = json.loads(
        await tcg_pricing_get_card(PricingGetCardInput(query="base1-4"))
    )
    assert out["raw_market"]["market"] == 7800.0


async def test_get_card_no_pricecharting_says_disabled_for_graded_query(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    # PriceCharting NOT registered (no token in this test).
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22Charizard%22&pageSize=1",
        json=SAMPLE_SEARCH_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )

    out = json.loads(
        await tcg_pricing_get_card(
            PricingGetCardInput(query="Charizard", grade="PSA 10")
        )
    )
    assert out["raw_market"] is not None
    assert "pricecharting_status" in out
    assert "PRICECHARTING_TOKEN" in out["pricecharting_status"]


async def test_get_card_includes_pricecharting_when_graded_and_enabled(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    _wire_pricecharting_at_mock()

    # Pokemon TCG API path
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22Charizard%22&pageSize=1",
        json=SAMPLE_SEARCH_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    # PriceCharting search + get
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&q=Charizard",
        json=SAMPLE_PC_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&id=12345",
        json=SAMPLE_PC_RESPONSE,
    )

    out = json.loads(
        await tcg_pricing_get_card(
            PricingGetCardInput(query="Charizard", grade="PSA 10")
        )
    )
    assert "pokemontcg" in out["providers_used"]
    assert "pricecharting" in out["providers_used"]
    assert out["raw_market"]["market"] == 7800.0
    # Should be filtered to PSA 10 only
    assert any(g["grade"] == "PSA 10" for g in out["graded_levels"])


async def test_get_card_prefer_provider_pricecharting_skips_pokemontcg(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    _wire_pricecharting_at_mock()

    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&q=Charizard",
        json=SAMPLE_PC_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&id=12345",
        json=SAMPLE_PC_RESPONSE,
    )

    out = json.loads(
        await tcg_pricing_get_card(
            PricingGetCardInput(
                query="Charizard",
                grade="PSA 10",
                prefer_provider="pricecharting",
            )
        )
    )
    assert "pricecharting" in out["providers_used"]
    assert "pokemontcg" not in out["providers_used"]
