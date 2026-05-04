"""PSA provider tests with mocked HTTP responses (pytest-httpx)."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tcg_mcp.providers.psa import PSAProvider, _parse_grade_numeric

from .conftest import (
    SAMPLE_PSA_CERT_RESPONSE,
    SAMPLE_PSA_IMAGES_RESPONSE,
    SAMPLE_PSA_NO_DATA_RESPONSE,
)


@pytest.fixture
def provider() -> PSAProvider:
    return PSAProvider(token="fake", base_url="https://api.example.test/publicapi")


@pytest.mark.parametrize(
    "label, expected",
    [
        ("GEM MT 10", 10.0),
        ("MINT 9", 9.0),
        ("PR 1.5", 1.5),
        ("EX-MT 6", 6.0),
        ("AUTHENTIC", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_grade_numeric(label: str | None, expected: float | None) -> None:
    assert _parse_grade_numeric(label) == expected


async def test_get_cert_happy_path(provider: PSAProvider, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetByCertNumber/79721014",
        method="GET",
        json=SAMPLE_PSA_CERT_RESPONSE,
    )

    result = await provider.get_cert("79721014")

    assert result.provider == "psa"
    assert result.found is True
    assert result.cert_number == "79721014"
    assert result.year == "1999"
    assert result.brand == "Pokemon Game"
    assert result.category == "TCG Cards"
    assert result.subject == "Charizard"
    assert result.card_number == "4"
    assert result.variety == "1st Edition Holo"
    assert result.grade == "GEM MT 10"
    assert result.grade_numeric == 10.0
    assert result.total_population == 1234
    assert result.population_higher == 0
    assert result.is_dual_cert is False
    # The raw payload must be preserved.
    assert result.raw == SAMPLE_PSA_CERT_RESPONSE


async def test_get_cert_not_found(provider: PSAProvider, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetByCertNumber/00000000",
        json=SAMPLE_PSA_NO_DATA_RESPONSE,
    )

    result = await provider.get_cert("00000000")

    assert result.found is False
    assert result.provider == "psa"
    # We return a clean CertResult, not an exception.
    assert result.cert_number == "00000000"


async def test_get_cert_sends_bearer_header(
    provider: PSAProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetByCertNumber/123",
        json=SAMPLE_PSA_CERT_RESPONSE,
    )

    await provider.get_cert("123")
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["authorization"] == "bearer fake"


async def test_get_images_returns_normalized_list(
    provider: PSAProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetImagesByCertNumber/79721014",
        json=SAMPLE_PSA_IMAGES_RESPONSE,
    )

    images = await provider.get_images("79721014")
    assert len(images) == 2
    fronts = [i for i in images if i.is_front]
    backs = [i for i in images if not i.is_front]
    assert len(fronts) == 1 and len(backs) == 1
    assert all(i.cert_number == "79721014" for i in images)


async def test_get_images_handles_empty(provider: PSAProvider, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetImagesByCertNumber/old",
        json=[],
    )
    images = await provider.get_images("old")
    assert images == []


def test_provider_requires_token() -> None:
    with pytest.raises(ValueError):
        PSAProvider(token="")


# A bare-data response — no IsValidRequest / ServerMessage envelope, fields
# under "PSACert" with the cert directly. Confirmed real shape from PSA's
# live API for valid certs.
SAMPLE_PSA_BARE_RESPONSE = {
    "PSACert": {
        "CertNumber": "79721014",
        "SpecID": 8599972,
        "Year": "2023",
        "Brand": "BOWMAN CHROME PROSPECT AUTOGRAPHS",
        "Category": "Sports Cards",
        "CardGrade": "GEM MT 10",
        "TotalPopulation": 50,
        "PopulationHigher": 0,
        "Subject": "JUNIOR CAMINERO",
        "CardNumber": "BCP-15",
        "IsDualCert": False,
    },
}


# Even barer — fields directly at the root (no PSACert wrapper).
SAMPLE_PSA_FLAT_RESPONSE = {
    "CertNumber": "11111111",
    "Year": "1999",
    "Brand": "Pokemon Game",
    "CardGrade": "MINT 9",
    "Subject": "Pikachu",
    "TotalPopulation": 100,
    "PopulationHigher": 5,
}


async def test_get_cert_handles_bare_data_envelope(
    provider: PSAProvider, httpx_mock: HTTPXMock
) -> None:
    """Real PSA responses sometimes omit the IsValidRequest envelope."""
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetByCertNumber/79721014",
        json=SAMPLE_PSA_BARE_RESPONSE,
    )
    result = await provider.get_cert("79721014")
    assert result.found is True
    assert result.subject == "JUNIOR CAMINERO"
    assert result.grade == "GEM MT 10"
    assert result.grade_numeric == 10.0
    assert result.total_population == 50


async def test_get_cert_handles_flat_root_response(
    provider: PSAProvider, httpx_mock: HTTPXMock
) -> None:
    """Some PSA responses put cert fields directly at the root, no PSACert key."""
    httpx_mock.add_response(
        url="https://api.example.test/publicapi/cert/GetByCertNumber/11111111",
        json=SAMPLE_PSA_FLAT_RESPONSE,
    )
    result = await provider.get_cert("11111111")
    assert result.found is True
    assert result.subject == "Pikachu"
    assert result.grade == "MINT 9"


def test_psa_429_error_mentions_token_and_quota() -> None:
    """v0.3 — PSA returns 429 for both invalid token AND rate limiting, so
    the error message must mention both possibilities."""
    import httpx

    from tcg_mcp.errors import format_http_error

    req = httpx.Request("GET", "https://api.example.test/publicapi/cert/GetByCertNumber/1")
    resp = httpx.Response(429, request=req)
    err = httpx.HTTPStatusError("rate limited", request=req, response=resp)

    msg = format_http_error(err, provider="psa")
    assert "PSA" in msg
    # v0.2 said only "rate limit"; v0.3 must mention BOTH causes.
    assert "PSA_API_TOKEN" in msg or "token" in msg.lower()
    assert "quota" in msg.lower() or "rate" in msg.lower()


def test_non_psa_429_keeps_simple_message() -> None:
    """Non-PSA providers don't have the dual-meaning 429, so message stays simple."""
    import httpx

    from tcg_mcp.errors import format_http_error

    req = httpx.Request("GET", "https://example.test/foo")
    resp = httpx.Response(429, request=req)
    err = httpx.HTTPStatusError("rate limited", request=req, response=resp)

    msg = format_http_error(err, provider="pricecharting")
    assert "PRICECHARTING" in msg
    assert "rate limit" in msg.lower()
    # Should NOT mention PSA-specific token disambiguation.
    assert "PSA_API_TOKEN" not in msg
