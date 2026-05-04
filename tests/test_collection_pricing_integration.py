"""Integration tests for tcg_collection_attach_pricing + tcg_collection_value_with_market."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import (
    CollectionAddCardInput,
    CollectionAttachPricingInput,
    CollectionValueWithMarketInput,
    PricingSnapshotInput,
    tcg_collection_add_card,
    tcg_collection_attach_pricing,
    tcg_collection_value_with_market,
    tcg_pricing_snapshot,
)

from .test_pricing_pokemontcg import SAMPLE_CARD


def _wire_pokemontcg_at_mock() -> None:
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider

    reset_pricing_registry()
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pokemontcg"] = PokemonTCGProvider(
        api_key=None, base_url="https://mock.pokemontcg.test/v2"
    )


async def test_attach_pricing_links_card_to_listing(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()

    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(
                subject="Charizard",
                year="1999",
                acquisition_price=120.0,
            )
        )
    )
    card_id = add_out["card_id"]

    out = json.loads(
        await tcg_collection_attach_pricing(
            CollectionAttachPricingInput(
                card_id=card_id,
                pricing_provider="pokemontcg",
                pricing_listing_id="base1-4",
            )
        )
    )
    assert out["ok"] is True
    assert out["pricing_listing_id"] == "base1-4"


async def test_attach_pricing_unknown_card_returns_error() -> None:
    out = await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id="bogus-id",
            pricing_provider="pokemontcg",
            pricing_listing_id="base1-4",
        )
    )
    assert out.startswith("Error:")


async def test_value_with_market_joins_latest_snapshot(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()

    # 1) Add a raw card.
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(
                subject="Charizard",
                year="1999",
                acquisition_price=120.0,
            )
        )
    )
    card_id = add_out["card_id"]

    # 2) Attach pricing.
    await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id=card_id,
            pricing_provider="pokemontcg",
            pricing_listing_id="base1-4",
        )
    )

    # 3) Snapshot pricing for that listing.
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    snap_out = json.loads(
        await tcg_pricing_snapshot(
            PricingSnapshotInput(listing_id="base1-4", provider="pokemontcg")
        )
    )
    assert snap_out["ok"] is True

    # 4) Live valuation should now find the snapshot and compute unrealized.
    val = json.loads(
        await tcg_collection_value_with_market(CollectionValueWithMarketInput())
    )
    assert val["owned_count"] == 1
    assert val["priced_count"] == 1
    assert val["unpriced_count"] == 0
    assert val["total_cost_basis"] == 120.0
    # Sample card's primary market is 7800.0 (1stEditionHolofoil)
    assert val["total_market"] == 7800.0
    assert val["unrealized_total"] == round(7800.0 - 120.0, 2)

    item = val["items"][0]
    assert item["card_id"] == card_id
    assert item["snapshot_provider"] == "pokemontcg"
    assert item["snapshot_listing_id"] == "base1-4"


async def test_value_with_market_counts_unpriced() -> None:
    # Card with no pricing attached → falls into unpriced_count.
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(subject="Mew", acquisition_price=10.0)
        )
    )
    assert add_out["ok"] is True

    val = json.loads(
        await tcg_collection_value_with_market(CollectionValueWithMarketInput())
    )
    assert val["owned_count"] == 1
    assert val["unpriced_count"] == 1
    assert val["priced_count"] == 0
    assert val["total_cost_basis"] == 10.0
    assert val["total_market"] == 0.0


async def test_graded_card_uses_grade_specific_snapshot(httpx_mock: HTTPXMock) -> None:
    """A graded card should match a snapshot at its specific grade, not the
    raw/ungraded one — falling back to ungraded only if no graded snapshot exists.
    """
    from tcg_mcp.storage.db import get_db

    # Add a graded card directly (PriceCharting-style listing + PSA 10 grade).
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(
                is_graded=True,
                grading_provider="psa",
                cert_number="79721014",
                subject="Charizard",
                grade="PSA 10",
                grade_numeric=10.0,
                acquisition_price=300.0,
            )
        )
    )
    card_id = add_out["card_id"]
    await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id=card_id,
            pricing_provider="pricecharting",
            pricing_listing_id="12345",
        )
    )

    # Manually inject snapshots: one ungraded ($100), one PSA 10 ($2,800).
    db = get_db()
    db.add_pricing_snapshot(
        {
            "provider": "pricecharting",
            "listing_id": "12345",
            "grade": None,
            "currency": "USD",
            "market": 100.0,
        }
    )
    db.add_pricing_snapshot(
        {
            "provider": "pricecharting",
            "listing_id": "12345",
            "grade": "PSA 10",
            "currency": "USD",
            "market": 2800.0,
        }
    )

    val = json.loads(
        await tcg_collection_value_with_market(CollectionValueWithMarketInput())
    )
    item = val["items"][0]
    assert item["market_price"] == 2800.0   # graded match, not ungraded
    assert item["unrealized"] == 2500.0
