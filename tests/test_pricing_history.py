"""Tests for tcg_pricing_get_history (v0.3 time-series query)."""

from __future__ import annotations

import json

from tcg_mcp.server import PricingHistoryInput, tcg_pricing_get_history
from tcg_mcp.storage.db import get_db


async def test_history_empty_returns_zero_count() -> None:
    out = json.loads(
        await tcg_pricing_get_history(
            PricingHistoryInput(provider="pokemontcg", listing_id="base1-4")
        )
    )
    assert out["count"] == 0
    assert out["snapshots"] == []


async def test_history_returns_oldest_first() -> None:
    db = get_db()
    db.add_pricing_snapshot(
        {
            "provider": "pokemontcg",
            "listing_id": "base1-4",
            "grade": None,
            "currency": "USD",
            "market": 100.0,
        }
    )
    db.add_pricing_snapshot(
        {
            "provider": "pokemontcg",
            "listing_id": "base1-4",
            "grade": None,
            "currency": "USD",
            "market": 120.0,
        }
    )
    out = json.loads(
        await tcg_pricing_get_history(
            PricingHistoryInput(provider="pokemontcg", listing_id="base1-4")
        )
    )
    assert out["count"] == 2
    markets = [row["market"] for row in out["snapshots"]]
    assert markets == [100.0, 120.0]   # oldest first


async def test_history_filters_by_grade() -> None:
    db = get_db()
    db.add_pricing_snapshot(
        {
            "provider": "pricecharting",
            "listing_id": "12345",
            "grade": None,
            "currency": "USD",
            "market": 50.0,
        }
    )
    db.add_pricing_snapshot(
        {
            "provider": "pricecharting",
            "listing_id": "12345",
            "grade": "PSA 10",
            "currency": "USD",
            "market": 1500.0,
        }
    )

    raw = json.loads(
        await tcg_pricing_get_history(
            PricingHistoryInput(provider="pricecharting", listing_id="12345")
        )
    )
    assert raw["count"] == 1
    assert raw["snapshots"][0]["market"] == 50.0

    psa10 = json.loads(
        await tcg_pricing_get_history(
            PricingHistoryInput(
                provider="pricecharting", listing_id="12345", grade="PSA 10"
            )
        )
    )
    assert psa10["count"] == 1
    assert psa10["snapshots"][0]["market"] == 1500.0


async def test_history_does_not_leak_other_listings() -> None:
    db = get_db()
    db.add_pricing_snapshot(
        {
            "provider": "pokemontcg",
            "listing_id": "base1-4",
            "currency": "USD",
            "market": 7800.0,
        }
    )
    db.add_pricing_snapshot(
        {
            "provider": "pokemontcg",
            "listing_id": "sv3-223",
            "currency": "USD",
            "market": 105.0,
        }
    )

    out = json.loads(
        await tcg_pricing_get_history(
            PricingHistoryInput(provider="pokemontcg", listing_id="base1-4")
        )
    )
    assert out["count"] == 1
    assert out["snapshots"][0]["listing_id"] == "base1-4"
