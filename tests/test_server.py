"""Server-level tests — registry behavior and tool dispatch with the new
tcg_<namespace>_* tool names.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from tcg_mcp.errors import ProviderNotEnabledError
from tcg_mcp.providers import (
    get_provider,
    list_provider_names,
)

from .conftest import SAMPLE_PSA_CERT_RESPONSE


def test_psa_provider_disabled_without_token() -> None:
    with pytest.raises(ProviderNotEnabledError):
        get_provider("psa")


def test_psa_provider_enabled_with_token(psa_token: str) -> None:
    p = get_provider("psa")
    assert p.name == "psa"


def test_cgc_and_bgs_are_always_listed() -> None:
    names = list_provider_names()
    assert "cgc" in names
    assert "bgs" in names


def test_psa_appears_in_list_when_token_set(psa_token: str) -> None:
    assert "psa" in list_provider_names()


async def test_tcg_psa_get_cert_returns_markdown(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=SAMPLE_PSA_CERT_RESPONSE,
    )

    from tcg_mcp.server import PSAGetCertInput, tcg_psa_get_cert

    out = await tcg_psa_get_cert(PSAGetCertInput(cert_number="79721014"))
    assert "Charizard" in out
    assert "GEM MT 10" in out
    assert "PSA" in out


async def test_tcg_psa_get_cert_returns_json(
    psa_token_with_mock_base: str, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.test/publicapi/cert/GetByCertNumber/79721014",
        json=SAMPLE_PSA_CERT_RESPONSE,
    )

    from tcg_mcp.models import ResponseFormat
    from tcg_mcp.server import PSAGetCertInput, tcg_psa_get_cert

    out = await tcg_psa_get_cert(
        PSAGetCertInput(cert_number="79721014", response_format=ResponseFormat.JSON)
    )
    parsed = json.loads(out)
    assert parsed["provider"] == "psa"
    assert parsed["found"] is True
    assert parsed["grade"] == "GEM MT 10"


async def test_tcg_psa_get_cert_handles_missing_token() -> None:
    from tcg_mcp.server import PSAGetCertInput, tcg_psa_get_cert

    out = await tcg_psa_get_cert(PSAGetCertInput(cert_number="79721014"))
    assert out.startswith("Error:")
    assert "PSA_API_TOKEN" in out


async def test_tcg_cgc_get_cert_is_stubbed() -> None:
    from tcg_mcp.server import CGCGetCertInput, tcg_cgc_get_cert

    out = await tcg_cgc_get_cert(CGCGetCertInput(cert_number="79721014"))
    assert out.startswith("Error:")
    assert "CGC" in out or "stub" in out.lower()


async def test_tcg_bgs_get_cert_is_stubbed() -> None:
    from tcg_mcp.server import BGSGetCertInput, tcg_bgs_get_cert

    out = await tcg_bgs_get_cert(BGSGetCertInput(cert_number="79721014"))
    assert out.startswith("Error:")


async def test_tcg_list_providers() -> None:
    from tcg_mcp.server import ListProvidersInput, tcg_list_providers

    out = await tcg_list_providers(ListProvidersInput())
    parsed = json.loads(out)
    assert "grading" in parsed
    assert "cgc" in parsed["grading"]
    assert "bgs" in parsed["grading"]
    # Pricing exists as a section even though Stage 2 isn't implemented
    assert "pricing" in parsed
