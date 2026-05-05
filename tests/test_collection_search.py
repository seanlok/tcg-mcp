"""Tests for tcg_collection_search (v0.4)."""

from __future__ import annotations

import json

from tcg_mcp.server import (
    CollectionAddCardInput,
    CollectionSearchInput,
    tcg_collection_add_card,
    tcg_collection_search,
)


async def test_search_finds_by_subject() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Charizard", set_name="Base")
    )
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Pikachu", set_name="Surging Sparks")
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="Charizard"))
    )
    assert out["count"] == 1
    assert out["items"][0]["subject"] == "Charizard"


async def test_search_finds_by_set_name() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Pikachu", set_name="Surging Sparks")
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="Surging"))
    )
    assert out["count"] == 1


async def test_search_finds_by_variety() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Mew", variety="1st Edition Holo")
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="1st Edition"))
    )
    assert out["count"] == 1


async def test_search_finds_by_notes() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(
            subject="Bulbasaur", notes="Plan to grade with PSA next month"
        )
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="grade"))
    )
    assert out["count"] == 1


async def test_search_finds_by_tags() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(
            subject="Eevee", tags=["sir", "anniversary-target"]
        )
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="anniversary"))
    )
    assert out["count"] == 1


async def test_search_is_case_insensitive() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Mewtwo")
    )
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="MEWTWO"))
    )
    assert out["count"] == 1


async def test_search_filter_is_graded() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="Card-A", is_graded=False)
    )
    await tcg_collection_add_card(
        CollectionAddCardInput(
            subject="Card-A",
            is_graded=True,
            grading_provider="psa",
            cert_number="11111111",
            grade="GEM MT 10",
        )
    )
    out = json.loads(
        await tcg_collection_search(
            CollectionSearchInput(query="Card-A", is_graded=True)
        )
    )
    assert out["count"] == 1
    assert out["items"][0]["is_graded"] == 1


async def test_search_returns_zero_for_no_match() -> None:
    await tcg_collection_add_card(CollectionAddCardInput(subject="Snorlax"))
    out = json.loads(
        await tcg_collection_search(CollectionSearchInput(query="nonexistent"))
    )
    assert out["count"] == 0
    assert out["items"] == []
