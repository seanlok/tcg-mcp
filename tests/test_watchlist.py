"""Tests for the watchlist tools."""

from __future__ import annotations

import json

from tcg_mcp.server import (
    WatchlistAddInput,
    WatchlistCloseInput,
    WatchlistGetInput,
    WatchlistListInput,
    WatchlistUpdateInput,
    tcg_watchlist_add,
    tcg_watchlist_close,
    tcg_watchlist_get,
    tcg_watchlist_list,
    tcg_watchlist_update,
)


async def test_add_then_list_then_get() -> None:
    add_out = json.loads(
        await tcg_watchlist_add(
            WatchlistAddInput(
                card_descriptor="Charizard ex SIR — Surging Sparks",
                horizon="hold",
                target_price=180.0,
                thesis="Anniversary year tailwind + low pop expected.",
            )
        )
    )
    assert add_out["ok"] is True
    wid = add_out["watchlist_id"]

    list_out = json.loads(await tcg_watchlist_list(WatchlistListInput()))
    assert list_out["count"] == 1
    assert list_out["items"][0]["id"] == wid

    get_out = json.loads(await tcg_watchlist_get(WatchlistGetInput(watchlist_id=wid)))
    assert get_out["card_descriptor"] == "Charizard ex SIR — Surging Sparks"
    assert get_out["target_price"] == 180.0
    assert get_out["closed_at"] is None


async def test_list_filters_by_horizon() -> None:
    await tcg_watchlist_add(
        WatchlistAddInput(card_descriptor="Flip 1", horizon="flip")
    )
    await tcg_watchlist_add(
        WatchlistAddInput(card_descriptor="Hold 1", horizon="hold")
    )
    await tcg_watchlist_add(
        WatchlistAddInput(card_descriptor="Sealed 1", horizon="sealed")
    )

    flips = json.loads(await tcg_watchlist_list(WatchlistListInput(horizon="flip")))
    assert flips["count"] == 1
    assert flips["items"][0]["card_descriptor"] == "Flip 1"


async def test_update_target_price() -> None:
    add_out = json.loads(
        await tcg_watchlist_add(WatchlistAddInput(card_descriptor="X", target_price=50.0))
    )
    wid = add_out["watchlist_id"]
    upd = json.loads(
        await tcg_watchlist_update(
            WatchlistUpdateInput(watchlist_id=wid, target_price=42.5, thesis="lower the bid")
        )
    )
    assert upd["ok"] is True
    assert upd["entry"]["target_price"] == 42.5
    assert upd["entry"]["thesis"] == "lower the bid"


async def test_close_with_reason_excludes_from_open_only_list() -> None:
    add_out = json.loads(
        await tcg_watchlist_add(WatchlistAddInput(card_descriptor="Y"))
    )
    wid = add_out["watchlist_id"]

    close = json.loads(
        await tcg_watchlist_close(
            WatchlistCloseInput(watchlist_id=wid, reason="bought")
        )
    )
    assert close["ok"] is True
    assert close["entry"]["closed_at"] is not None
    assert close["entry"]["closed_reason"] == "bought"

    # open-only list should not include it
    open_list = json.loads(
        await tcg_watchlist_list(WatchlistListInput(open_only=True))
    )
    ids_open = {item["id"] for item in open_list["items"]}
    assert wid not in ids_open

    # but open_only=False does
    all_list = json.loads(
        await tcg_watchlist_list(WatchlistListInput(open_only=False))
    )
    ids_all = {item["id"] for item in all_list["items"]}
    assert wid in ids_all


async def test_close_idempotent() -> None:
    add_out = json.loads(
        await tcg_watchlist_add(WatchlistAddInput(card_descriptor="Z"))
    )
    wid = add_out["watchlist_id"]
    await tcg_watchlist_close(WatchlistCloseInput(watchlist_id=wid, reason="manual"))
    second = await tcg_watchlist_close(
        WatchlistCloseInput(watchlist_id=wid, reason="manual")
    )
    assert second.startswith("Error:")
    assert "already closed" in second


async def test_close_unknown_id() -> None:
    out = await tcg_watchlist_close(
        WatchlistCloseInput(watchlist_id="nonexistent-id", reason="manual")
    )
    assert out.startswith("Error:")
