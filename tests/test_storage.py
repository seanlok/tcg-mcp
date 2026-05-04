"""Tests for the SQLite storage layer."""

from __future__ import annotations

from tcg_mcp.storage.db import get_db


def test_migrations_create_tables_idempotently() -> None:
    db = get_db()
    # Calling twice should not raise.
    db.apply_migrations()
    db.apply_migrations()
    with db.connect() as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = [row["name"] for row in cur.fetchall()]
    assert "owned_cards" in tables
    assert "watchlist" in tables
    assert "pricing_snapshots" in tables
    assert "schema_version" in tables


def test_add_get_round_trip() -> None:
    db = get_db()
    new_id = db.add_owned_card(
        {
            "is_graded": 1,
            "grading_provider": "psa",
            "cert_number": "79721014",
            "subject": "Charizard",
            "grade": "GEM MT 10",
            "grade_numeric": 10.0,
            "year": "1999",
            "brand": "Pokemon Game",
            "card_number": "4",
            "acquisition_price": 250.0,
            "acquisition_currency": "USD",
            "tags": ["chase", "vintage"],
        }
    )
    row = db.get_owned_card(new_id)
    assert row is not None
    assert row["subject"] == "Charizard"
    assert row["cert_number"] == "79721014"
    assert row["acquisition_price"] == 250.0
    # tags JSON-encoded round-trip
    assert "chase" in row["tags"]


def test_unique_cert_constraint() -> None:
    db = get_db()
    db.add_owned_card(
        {
            "is_graded": 1,
            "grading_provider": "psa",
            "cert_number": "11111111",
            "subject": "Pikachu",
        }
    )
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db.add_owned_card(
            {
                "is_graded": 1,
                "grading_provider": "psa",
                "cert_number": "11111111",
                "subject": "Pikachu Duplicate",
            }
        )


def test_find_by_cert() -> None:
    db = get_db()
    new_id = db.add_owned_card(
        {
            "is_graded": 1,
            "grading_provider": "psa",
            "cert_number": "22222222",
            "subject": "Mew",
        }
    )
    found = db.find_by_cert("psa", "22222222")
    assert found is not None
    assert found["id"] == new_id
    assert db.find_by_cert("psa", "00000000") is None


def test_list_with_filters() -> None:
    db = get_db()
    db.add_owned_card(
        {"is_graded": 0, "subject": "Charizard", "year": "1999"}
    )
    db.add_owned_card(
        {"is_graded": 1, "grading_provider": "psa", "cert_number": "33333333", "subject": "Mewtwo"}
    )
    db.add_owned_card({"is_graded": 0, "subject": "Mew", "status": "owned"})

    all_owned = db.list_owned()
    assert len(all_owned) == 3

    only_psa = db.list_owned(provider="psa")
    assert len(only_psa) == 1
    assert only_psa[0]["subject"] == "Mewtwo"

    only_charizard = db.list_owned(subject_like="char")
    assert len(only_charizard) == 1


def test_update_card() -> None:
    db = get_db()
    new_id = db.add_owned_card(
        {"is_graded": 0, "subject": "Bulbasaur", "acquisition_price": 5.0}
    )
    assert db.update_owned_card(new_id, {"acquisition_price": 7.5, "notes": "regraded"})
    row = db.get_owned_card(new_id)
    assert row["acquisition_price"] == 7.5
    assert row["notes"] == "regraded"


def test_soft_delete_keeps_row_changes_status() -> None:
    db = get_db()
    new_id = db.add_owned_card({"is_graded": 0, "subject": "Squirtle"})
    db.remove_owned_card(new_id, hard=False)
    row = db.get_owned_card(new_id)
    assert row is not None
    assert row["status"] == "sold"


def test_hard_delete_removes_row() -> None:
    db = get_db()
    new_id = db.add_owned_card({"is_graded": 0, "subject": "Eevee"})
    db.remove_owned_card(new_id, hard=True)
    assert db.get_owned_card(new_id) is None


def test_collection_summary() -> None:
    db = get_db()
    db.add_owned_card(
        {
            "is_graded": 1,
            "grading_provider": "psa",
            "cert_number": "10101010",
            "subject": "A",
            "acquisition_price": 100.0,
        }
    )
    db.add_owned_card({"is_graded": 0, "subject": "B", "acquisition_price": 25.0})
    sold_id = db.add_owned_card({"is_graded": 0, "subject": "C", "acquisition_price": 9.0})
    db.remove_owned_card(sold_id)

    s = db.collection_summary()
    assert s["owned_count"] == 2
    assert s["graded_count"] == 1
    assert s["raw_count"] == 1
    assert s["sold_count"] == 1
    assert s["total_cost_basis"] == 125.0
