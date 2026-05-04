"""Tests for the sealed-product helper."""

from __future__ import annotations

import json

from tcg_mcp.server import (
    CollectionAddSealedInput,
    CollectionListInput,
    tcg_collection_add_sealed,
    tcg_collection_list,
)


async def test_add_etb_creates_one_row_per_quantity() -> None:
    out = json.loads(
        await tcg_collection_add_sealed(
            CollectionAddSealedInput(
                product_type="etb",
                set_name="Surging Sparks",
                year="2024",
                quantity=3,
                acquisition_price=49.99,
            )
        )
    )
    assert out["ok"] is True
    assert len(out["card_ids"]) == 3


async def test_sealed_appears_in_collection_list() -> None:
    await tcg_collection_add_sealed(
        CollectionAddSealedInput(
            product_type="booster_box",
            set_name="151",
            quantity=1,
        )
    )
    rows = json.loads(await tcg_collection_list(CollectionListInput()))
    assert rows["count"] == 1
    item = rows["items"][0]
    assert item["product_type"] == "booster_box"
    assert "151" in (item["subject"] or "")


async def test_subject_string_is_human_readable() -> None:
    out = json.loads(
        await tcg_collection_add_sealed(
            CollectionAddSealedInput(
                product_type="upc",
                set_name="Charizard ex",
                quantity=1,
            )
        )
    )
    cid = out["card_ids"][0]
    rows = json.loads(await tcg_collection_list(CollectionListInput()))
    [item] = [r for r in rows["items"] if r["id"] == cid]
    assert "Charizard ex UPC" in item["subject"]
