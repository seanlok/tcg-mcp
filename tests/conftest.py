"""Shared pytest fixtures.

We isolate the env-var space, point the SQLite DB at a temp file per test, and
reset both the provider registry and the DB singleton between tests so state
doesn't leak.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tcg_mcp.pricing import reset_pricing_registry
from tcg_mcp.providers import reset_registry
from tcg_mcp.storage.db import reset_db


@pytest.fixture(autouse=True)
def clean_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Strip provider env vars by default and isolate the DB to a temp file."""
    for var in (
        "PSA_API_TOKEN",
        "PSA_API_BASE_URL",
        "CGC_API_TOKEN",
        "BGS_API_TOKEN",
        "POKEMONTCG_API_KEY",
        "PRICECHARTING_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TCG_DB_PATH", str(tmp_path / "test.db"))
    reset_registry()
    reset_pricing_registry()
    reset_db()
    yield
    reset_registry()
    reset_pricing_registry()
    reset_db()


@pytest.fixture
def psa_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Inject a fake PSA token so the PSA provider is registered."""
    token = "fake-psa-token-for-tests"
    monkeypatch.setenv("PSA_API_TOKEN", token)
    reset_registry()
    return token


@pytest.fixture
def psa_token_with_mock_base(monkeypatch: pytest.MonkeyPatch) -> str:
    """Token + base URL pointed at the pytest-httpx mock host."""
    monkeypatch.setenv("PSA_API_TOKEN", "fake-psa-token-for-tests")
    monkeypatch.setenv("PSA_API_BASE_URL", "https://mock.test/publicapi")
    reset_registry()
    return "fake-psa-token-for-tests"


@pytest.fixture
def pricecharting_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Inject a fake PriceCharting token so the provider is registered."""
    monkeypatch.setenv("PRICECHARTING_TOKEN", "fake-pricecharting-token")
    reset_pricing_registry()
    return "fake-pricecharting-token"


# Sample PSA payloads — focused on the fields the provider normalizes.
SAMPLE_PSA_CERT_RESPONSE = {
    "PSACert": {
        "CertNumber": "79721014",
        "SpecID": 12345,
        "SpecNumber": "150",
        "LabelType": "Standard",
        "Year": "1999",
        "Brand": "Pokemon Game",
        "Category": "TCG Cards",
        "CardGrade": "GEM MT 10",
        "GradeDescription": "GEM MINT",
        "TotalPopulation": 1234,
        "PopulationHigher": 0,
        "Subject": "Charizard",
        "CardNumber": "4",
        "Variety": "1st Edition Holo",
        "IsDualCert": False,
    },
    "IsValidRequest": True,
    "ServerMessage": "Request successful",
}


SAMPLE_PSA_NO_DATA_RESPONSE = {
    "PSACert": None,
    "IsValidRequest": True,
    "ServerMessage": "No data found",
}


SAMPLE_PSA_IMAGES_RESPONSE = [
    {"ImageURL": "https://images.psacard.com/cert/79721014-front.jpg", "IsFrontImage": True},
    {"ImageURL": "https://images.psacard.com/cert/79721014-back.jpg", "IsFrontImage": False},
]
