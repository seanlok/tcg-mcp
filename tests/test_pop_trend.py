"""Tests for tcg_psa_snapshot_pop / tcg_psa_pop_trend."""

from __future__ import annotations

import copy
import json

from pytest_httpx import HTTPXMock

from tcg_mcp.server import (
    PSAPopTrendInput,
    PSASnapshotPopInput,
    tcg_psa_pop_trend,
    tcg_psa_snapshot_pop,
)
from tcg_mcp.storage.db import get_db

from .conftest import SAMPLE_PSA_CERT_RESPONSE


def _cert_with_pop(total: int, higher: int = 0) -> dict:
    payload = copy.deepcopy(SAMPLE_PSA_CERT_RESPONSE)
    payload["PSACert"]["TotalPopulation"] = total
    payload["PSACert"]["PopulationHigher"] = higher
    return payload


async def test_snapshot_pop_persists_row(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=_cert_with_pop(1234),
    )
    out = json.loads(
        await tcg_psa_snapshot_pop(PSASnapshotPopInput(cert_number="79721014"))
    )
    assert out["ok"] is True
    assert out["spec_id"] == "12345"
    assert out["total_at_grade"] == 1234
    assert out["grade"] == "GEM MT 10"


async def test_snapshot_pop_handles_missing_token() -> None:
    out = await tcg_psa_snapshot_pop(PSASnapshotPopInput(cert_number="79721014"))
    assert out.startswith("Error:")
    assert "PSA_API_TOKEN" in out


async def test_pop_trend_returns_snapshots_in_order(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    # Two snapshots over time — different pop counts.
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=_cert_with_pop(1000),
        is_reusable=True,
    )
    await tcg_psa_snapshot_pop(PSASnapshotPopInput(cert_number="79721014"))

    # Manually insert a "later" snapshot with a higher pop to simulate growth.
    db = get_db()
    db.add_pop_snapshot(
        {
            "grading_provider": "psa",
            "spec_id": "12345",
            "cert_number": "79721014",
            "grade": "GEM MT 10",
            "total_at_grade": 1500,
            "population_higher": 0,
        }
    )

    trend = json.loads(
        await tcg_psa_pop_trend(
            PSAPopTrendInput(spec_id="12345", grade="GEM MT 10", days=365)
        )
    )
    assert trend["count"] == 2
    pops = [s["total_at_grade"] for s in trend["snapshots"]]
    # Oldest first
    assert pops == [1000, 1500]


async def test_pop_trend_can_resolve_spec_from_cert_number(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=_cert_with_pop(800),
    )
    await tcg_psa_snapshot_pop(PSASnapshotPopInput(cert_number="79721014"))

    out = json.loads(
        await tcg_psa_pop_trend(
            PSAPopTrendInput(cert_number="79721014", days=365)
        )
    )
    assert out["spec_id"] == "12345"
    assert out["count"] == 1


async def test_pop_trend_errors_when_neither_provided() -> None:
    out = await tcg_psa_pop_trend(PSAPopTrendInput())
    assert out.startswith("Error:")


async def test_pop_trend_errors_when_cert_never_snapshotted() -> None:
    out = await tcg_psa_pop_trend(PSAPopTrendInput(cert_number="00000000"))
    assert out.startswith("Error:")
    assert "snapshot" in out.lower()


async def test_pop_trend_filters_by_grade(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    db = get_db()
    db.add_pop_snapshot(
        {
            "grading_provider": "psa",
            "spec_id": "spec-A",
            "cert_number": "11",
            "grade": "GEM MT 10",
            "total_at_grade": 100,
        }
    )
    db.add_pop_snapshot(
        {
            "grading_provider": "psa",
            "spec_id": "spec-A",
            "cert_number": "12",
            "grade": "MINT 9",
            "total_at_grade": 500,
        }
    )

    only_10 = json.loads(
        await tcg_psa_pop_trend(
            PSAPopTrendInput(spec_id="spec-A", grade="GEM MT 10", days=365)
        )
    )
    assert only_10["count"] == 1
    assert only_10["snapshots"][0]["grade"] == "GEM MT 10"

    all_grades = json.loads(
        await tcg_psa_pop_trend(PSAPopTrendInput(spec_id="spec-A", days=365))
    )
    assert all_grades["count"] == 2
