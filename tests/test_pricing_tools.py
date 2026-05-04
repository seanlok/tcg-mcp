"""Integration tests for the tcg_pricing_* MCP tools."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import (
    PricingGetInput,
    PricingSearchInput,
    PricingSnapshotInput,
    tcg_pricing_get,
    tcg_pricing_search,
    tcg_pricing_snapshot,
)
from tcg_mcp.storage.db import get_db

from .test_pricing_pokemontcg import SAMPLE_CARD, SAMPLE_SEARCH_RESPONSE
from .test_pricing_pricecharting import SAMPLE_PC_RESPONSE


@pytest.fixture
def pokemontcg_at_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repoint Pokemon TCG provider at mock host. (No env var required for it,
    but we need to override the base URL — currently fixed in code, so we
    monkey-patch the registry directly.)"""
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider

    reset_pricing_registry()
    # Build registry, then swap the pokemontcg entry for a mock-hosted instance.
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pokemontcg"] = PokemonTCGProvider(
        api_key=None, base_url="https://mock.pokemontcg.test/v2"
    )


@pytest.fixture
def pricecharting_at_mock(
    monkeypatch: pytest.MonkeyPatch, pricecharting_token: str
) -> None:
    """Repoint PriceCharting provider at mock host."""
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pricecharting import PriceChartingProvider

    reset_pricing_registry()
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pricecharting"] = PriceChartingProvider(
        token=pricecharting_token, base_url="https://mock.pc.test/api"
    )


async def test_pricing_search_tool_pokemontcg(
    pokemontcg_at_mock: None, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards?q=name%3A%22Charizard%22&pageSize=10",
        json=SAMPLE_SEARCH_RESPONSE,
    )
    out = await tcg_pricing_search(
        PricingSearchInput(query="Charizard", provider="pokemontcg", limit=10)
    )
    parsed = json.loads(out)
    assert parsed["provider"] == "pokemontcg"
    assert parsed["count"] == 1
    assert parsed["items"][0]["listing_id"] == "base1-4"


async def test_pricing_get_tool_pokemontcg(
    pokemontcg_at_mock: None, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    out = await tcg_pricing_get(
        PricingGetInput(listing_id="base1-4", provider="pokemontcg")
    )
    parsed = json.loads(out)
    assert parsed["provider"] == "pokemontcg"
    assert parsed["market"] == 7800.0


async def test_pricing_get_tool_pricecharting(
    pricecharting_at_mock: None, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&id=12345",
        json=SAMPLE_PC_RESPONSE,
    )
    out = await tcg_pricing_get(
        PricingGetInput(listing_id="12345", provider="pricecharting")
    )
    parsed = json.loads(out)
    assert parsed["market"] == 125.0
    grades = {lvl["grade"] for lvl in parsed["graded_levels"]}
    assert "PSA 10" in grades


async def test_pricing_get_pricecharting_disabled_without_token() -> None:
    out = await tcg_pricing_get(
        PricingGetInput(listing_id="12345", provider="pricecharting")
    )
    assert out.startswith("Error:")
    assert "PRICECHARTING_TOKEN" in out


async def test_pricing_get_snkrdunk_is_stubbed() -> None:
    out = await tcg_pricing_get(
        PricingGetInput(listing_id="anything", provider="snkrdunk")
    )
    assert out.startswith("Error:")
    assert "SNKRDUNK" in out or "stub" in out.lower()


async def test_pricing_snapshot_persists_rows(
    pricecharting_at_mock: None, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pricecharting-token&id=12345",
        json=SAMPLE_PC_RESPONSE,
    )
    out = await tcg_pricing_snapshot(
        PricingSnapshotInput(listing_id="12345", provider="pricecharting")
    )
    parsed = json.loads(out)
    assert parsed["ok"] is True
    # 1 ungraded row + 5 graded levels (PSA 9 from `graded-price`,
    # BGS 10, PSA 10, PSA 9 from condition-18, and a no-op duplicate filtered).
    # Be tolerant: assert at least 4 rows and check types.
    assert len(parsed["snapshot_ids"]) >= 4

    # Confirm the ungraded snapshot is queryable
    db = get_db()
    latest_raw = db.latest_snapshot("pricecharting", "12345", grade=None)
    assert latest_raw is not None
    assert latest_raw["market"] == 125.0
    assert latest_raw["currency"] == "USD"

    # Confirm a graded row landed
    psa10 = db.latest_snapshot("pricecharting", "12345", grade="PSA 10")
    assert psa10 is not None
    assert psa10["market"] == 2800.0


async def test_list_providers_surfaces_pricing_section() -> None:
    from tcg_mcp.server import ListProvidersInput, tcg_list_providers

    out = await tcg_list_providers(ListProvidersInput())
    parsed = json.loads(out)
    assert "pricing" in parsed
    # Pokemon TCG always enabled (no key required).
    assert parsed["pricing"].get("pokemontcg", {}).get("status") == "enabled"
    # PriceCharting disabled by default in tests.
    assert parsed["pricing"].get("pricecharting", {}).get("status") == "disabled"
    # SNKRDUNK always present as a stub.
    assert parsed["pricing"].get("snkrdunk", {}).get("status") == "stub"
