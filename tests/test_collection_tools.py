"""Tests for the tcg_collection_* MCP tools."""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from tcg_mcp.server import (
    CollectionAddCardInput,
    CollectionGetInput,
    CollectionListInput,
    CollectionRemoveInput,
    CollectionUpdateInput,
    CollectionValueInput,
    PSAAddToCollectionInput,
    tcg_collection_add_card,
    tcg_collection_get,
    tcg_collection_list,
    tcg_collection_remove,
    tcg_collection_update,
    tcg_collection_value,
    tcg_psa_add_to_collection,
)

from .conftest import SAMPLE_PSA_CERT_RESPONSE


async def test_add_raw_card_then_list() -> None:
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(
                subject="Charizard",
                year="1999",
                brand="Pokemon Game",
                card_number="4",
                acquisition_price=120.0,
            )
        )
    )
    assert add_out["ok"] is True
    card_id = add_out["card_id"]

    list_out = json.loads(
        await tcg_collection_list(CollectionListInput())
    )
    assert list_out["count"] == 1
    assert list_out["items"][0]["id"] == card_id


async def test_graded_card_requires_provider_and_cert_number() -> None:
    out = await tcg_collection_add_card(
        CollectionAddCardInput(is_graded=True, subject="Mew")
    )
    assert out.startswith("Error:")


async def test_get_returns_added_card() -> None:
    add_out = json.loads(
        await tcg_collection_add_card(CollectionAddCardInput(subject="Pikachu"))
    )
    card_id = add_out["card_id"]

    get_out = json.loads(
        await tcg_collection_get(CollectionGetInput(card_id=card_id))
    )
    assert get_out["subject"] == "Pikachu"


async def test_get_unknown_card_returns_error() -> None:
    out = await tcg_collection_get(CollectionGetInput(card_id="does-not-exist"))
    assert out.startswith("Error:")


async def test_update_card_changes_field() -> None:
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(subject="Mewtwo", acquisition_price=50.0)
        )
    )
    card_id = add_out["card_id"]

    upd_out = json.loads(
        await tcg_collection_update(
            CollectionUpdateInput(card_id=card_id, acquisition_price=75.5, notes="re-bought")
        )
    )
    assert upd_out["ok"] is True
    assert upd_out["card"]["acquisition_price"] == 75.5
    assert upd_out["card"]["notes"] == "re-bought"


async def test_remove_soft_deletes_with_sold_data() -> None:
    add_out = json.loads(
        await tcg_collection_add_card(
            CollectionAddCardInput(subject="Eevee", acquisition_price=10.0)
        )
    )
    card_id = add_out["card_id"]

    out = json.loads(
        await tcg_collection_remove(
            CollectionRemoveInput(
                card_id=card_id,
                sold_price=18.0,
                sold_currency="USD",
                sold_date="2026-05-04",
            )
        )
    )
    assert out["ok"] is True
    assert out["hard_deleted"] is False

    row = json.loads(await tcg_collection_get(CollectionGetInput(card_id=card_id)))
    assert row["status"] == "sold"
    assert row["sold_price"] == 18.0


async def test_remove_hard_deletes() -> None:
    add_out = json.loads(
        await tcg_collection_add_card(CollectionAddCardInput(subject="Snorlax"))
    )
    card_id = add_out["card_id"]
    await tcg_collection_remove(CollectionRemoveInput(card_id=card_id, hard=True))
    err = await tcg_collection_get(CollectionGetInput(card_id=card_id))
    assert err.startswith("Error:")


async def test_collection_value_summary() -> None:
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="A", acquisition_price=20.0)
    )
    await tcg_collection_add_card(
        CollectionAddCardInput(subject="B", acquisition_price=30.0)
    )
    out = json.loads(await tcg_collection_value(CollectionValueInput()))
    assert out["owned_count"] == 2
    assert out["total_cost_basis"] == 50.0


async def test_psa_add_to_collection_workflow(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=SAMPLE_PSA_CERT_RESPONSE,
    )

    out = json.loads(
        await tcg_psa_add_to_collection(
            PSAAddToCollectionInput(
                cert_number="79721014",
                acquisition_price=300.0,
                acquisition_date="2026-05-01",
            )
        )
    )
    assert out["ok"] is True
    assert "Charizard" in out["summary"]


async def test_psa_add_to_collection_rejects_duplicate(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    # Both calls hit the same mock; pytest-httpx by default replays one match.
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=SAMPLE_PSA_CERT_RESPONSE,
        is_reusable=True,
    )

    first = await tcg_psa_add_to_collection(
        PSAAddToCollectionInput(cert_number="79721014", acquisition_price=300.0)
    )
    assert json.loads(first)["ok"] is True

    second = await tcg_psa_add_to_collection(
        PSAAddToCollectionInput(cert_number="79721014", acquisition_price=400.0)
    )
    assert second.startswith("Error:")
    assert "already in your collection" in second
