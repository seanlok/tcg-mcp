"""Tests for tcg_pricing_snapshot_collection (v0.3 bulk-snapshot tool)."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.server import (
    CollectionAddCardInput,
    CollectionAttachPricingInput,
    PricingSnapshotCollectionInput,
    tcg_collection_add_card,
    tcg_collection_attach_pricing,
    tcg_pricing_snapshot_collection,
)
from tcg_mcp.storage.db import get_db

from .test_pricing_pokemontcg import SAMPLE_CARD


def _wire_pokemontcg_at_mock() -> None:
    import tcg_mcp.pricing as pricing_mod
    from tcg_mcp.pricing.pokemontcg import PokemonTCGProvider

    reset_pricing_registry()
    pricing_mod.get_pricing_registry()
    pricing_mod._registry["pokemontcg"] = PokemonTCGProvider(
        api_key=None, base_url="https://mock.pokemontcg.test/v2"
    )


async def _add_card_with_pricing(
    subject: str, listing_id: str, *, price: float | None = 100.0
) -> str:
    """Helper — add a raw card and attach pokemontcg pricing to it."""
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(subject=subject, acquisition_price=price)
        )
    )
    card_id = add_out["card_id"]
    await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id=card_id,
            pricing_provider="pokemontcg",
            pricing_listing_id=listing_id,
        )
    )
    return card_id


async def test_bulk_empty_collection_returns_zeros() -> None:
    out = json.loads(
        await tcg_pricing_snapshot_collection(PricingSnapshotCollectionInput())
    )
    assert out["ok"] is True
    assert out["summary"]["owned_with_pricing"] == 0
    assert out["summary"]["snapshotted"] == 0
    assert out["snapshots"] == []
    assert out["failures"] == []


async def test_bulk_skips_cards_without_pricing_attachment() -> None:
    # Add a card with no pricing attached.
    await tcg_collection_add_card(CollectionAddCardInput(subject="Bulbasaur"))
    out = json.loads(
        await tcg_pricing_snapshot_collection(PricingSnapshotCollectionInput())
    )
    # Card exists but has no pricing attachment, so it's not counted at all
    # by `owned_with_pricing`.
    assert out["summary"]["owned_with_pricing"] == 0


async def test_bulk_snapshots_attached_card(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    await _add_card_with_pricing("Charizard", "base1-4")

    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )

    out = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(max_age_hours=0)
        )
    )
    assert out["ok"] is True
    assert out["summary"]["owned_with_pricing"] == 1
    assert out["summary"]["snapshotted"] == 1
    assert out["summary"]["failed"] == 0
    assert out["by_provider"]["pokemontcg"]["snapshotted"] == 1

    snap = out["snapshots"][0]
    assert snap["provider"] == "pokemontcg"
    assert snap["listing_id"] == "base1-4"
    assert len(snap["snapshot_ids"]) == 1


async def test_bulk_skips_recent_snapshots(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    await _add_card_with_pricing("Charizard", "base1-4")

    # First call — snapshot it.
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    first = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(max_age_hours=24)
        )
    )
    assert first["summary"]["snapshotted"] == 1

    # Second call — should skip because the snapshot is fresh.
    second = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(max_age_hours=24)
        )
    )
    assert second["summary"]["snapshotted"] == 0
    assert second["summary"]["skipped_recent"] == 1
    assert second["by_provider"]["pokemontcg"]["skipped_recent"] == 1


async def test_bulk_dry_run_does_not_call_or_persist(
    httpx_mock: HTTPXMock,
) -> None:
    _wire_pokemontcg_at_mock()
    await _add_card_with_pricing("Charizard", "base1-4")

    out = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(dry_run=True, max_age_hours=0)
        )
    )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["summary"]["snapshotted"] == 1
    # No HTTP call should have happened.
    assert httpx_mock.get_request() is None
    # And no snapshot row should be in the DB.
    db = get_db()
    assert db.latest_snapshot("pokemontcg", "base1-4") is None


async def test_bulk_provider_filter(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    await _add_card_with_pricing("Pokemon-A", "base1-4")
    # Add a card on a different provider.
    add_out = json.loads(
        await tcg_collection_add_card(CollectionAddCardInput(subject="Pokemon-B"))
    )
    await tcg_collection_attach_pricing(
        CollectionAttachPricingInput(
            card_id=add_out["card_id"],
            pricing_provider="pricecharting",
            pricing_listing_id="99999",
        )
    )

    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )

    out = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(provider="pokemontcg", max_age_hours=0)
        )
    )
    # Filter excluded the pricecharting card entirely.
    assert out["summary"]["owned_with_pricing"] == 1
    assert out["summary"]["snapshotted"] == 1
    assert "pokemontcg" in out["by_provider"]
    assert "pricecharting" not in out["by_provider"]


async def test_bulk_failure_is_recorded_not_fatal(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    # Two cards — one will succeed, one will fail.
    await _add_card_with_pricing("Pokemon-A", "base1-4")
    await _add_card_with_pricing("Pokemon-B", "missing-id")

    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/base1-4",
        json={"data": SAMPLE_CARD},
    )
    httpx_mock.add_response(
        url="https://mock.pokemontcg.test/v2/cards/missing-id",
        status_code=500,
        json={"error": "boom"},
        is_reusable=True,
    )

    out = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(max_age_hours=0)
        )
    )
    assert out["summary"]["owned_with_pricing"] == 2
    assert out["summary"]["snapshotted"] == 1
    assert out["summary"]["failed"] == 1
    assert len(out["failures"]) == 1
    assert out["failures"][0]["provider"] == "pokemontcg"


async def test_bulk_limit_caps_processing(httpx_mock: HTTPXMock) -> None:
    _wire_pokemontcg_at_mock()
    await _add_card_with_pricing("Pokemon-A", "base1-4")
    await _add_card_with_pricing("Pokemon-B", "base1-5")
    await _add_card_with_pricing("Pokemon-C", "base1-6")

    out = json.loads(
        await tcg_pricing_snapshot_collection(
            PricingSnapshotCollectionInput(dry_run=True, max_age_hours=0, limit=2)
        )
    )
    assert out["summary"]["owned_with_pricing"] == 2
    assert out["summary"]["snapshotted"] == 2
