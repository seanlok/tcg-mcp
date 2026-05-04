"""PriceCharting provider tests with mocked httpx."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tcg_mcp.pricing.models import cents_to_dollars
from tcg_mcp.pricing.pricecharting import PriceChartingProvider

# A faux PriceCharting response for a Pokemon card.
# Prices are in INTEGER PENNIES per their docs.
SAMPLE_PC_RESPONSE = {
    "status": "success",
    "id": "12345",
    "product-name": "Charizard #4 - Base Set",
    "console-name": "Pokemon Base Set",
    "release-date": "1999-01-09",
    "loose-price": 12500,        # $125.00
    "cib-price": 18000,          # $180.00
    "new-price": 25000,          # $250.00
    "graded-price": 60000,       # $600.00 — historical PSA 9 convention
    "bgs-10-price": 350000,      # $3,500.00
    "condition-17-price": 280000, # PSA 10 = $2,800.00
    "condition-18-price": 90000,  # PSA 9 = $900.00
}

SAMPLE_PC_NO_DATA_RESPONSE = {
    "status": "error",
    "error-message": "No products found",
}


@pytest.fixture
def provider() -> PriceChartingProvider:
    return PriceChartingProvider(
        token="fake-pc-token",
        base_url="https://mock.pc.test/api",
    )


def test_cents_to_dollars_rounds_correctly() -> None:
    assert cents_to_dollars(1732) == 17.32
    assert cents_to_dollars(0) == 0.0
    assert cents_to_dollars(None) is None
    assert cents_to_dollars("not a number") is None


def test_provider_requires_token() -> None:
    with pytest.raises(ValueError):
        PriceChartingProvider(token="")


async def test_search_returns_single_match(
    provider: PriceChartingProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pc-token&q=Charizard",
        json=SAMPLE_PC_RESPONSE,
    )
    listings = await provider.search("Charizard")
    assert len(listings) == 1
    item = listings[0]
    assert item.provider == "pricecharting"
    assert item.listing_id == "12345"
    assert "Charizard" in (item.name or "")
    assert item.set_name == "Pokemon Base Set"
    assert item.url and item.url.endswith("/12345")


async def test_search_returns_empty_on_error(
    provider: PriceChartingProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pc-token&q=Bogus",
        json=SAMPLE_PC_NO_DATA_RESPONSE,
    )
    out = await provider.search("Bogus")
    assert out == []


async def test_get_price_extracts_top_level_and_graded(
    provider: PriceChartingProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pc-token&id=12345",
        json=SAMPLE_PC_RESPONSE,
    )
    quote = await provider.get_price("12345")

    # Loose-price as primary "market"
    assert quote.market == 125.0
    assert quote.low == 125.0
    # CIB price beats new-price for "high" when both present
    assert quote.high == 180.0
    assert quote.currency == "USD"

    # All graded levels surfaced
    grades = {lvl.grade: lvl.market for lvl in quote.graded_levels}
    assert grades.get("PSA 10") == 2800.0
    assert grades.get("PSA 9") == 900.0
    assert grades.get("BGS 10") == 3500.0
    # Historical "graded-price" → "PSA 9" convention
    psa9_levels = [lvl for lvl in quote.graded_levels if lvl.grade == "PSA 9"]
    assert len(psa9_levels) == 2  # condition-18-price + graded-price


async def test_get_price_handles_error_status(
    provider: PriceChartingProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pc-token&id=99999",
        json=SAMPLE_PC_NO_DATA_RESPONSE,
    )
    quote = await provider.get_price("99999")
    assert quote.market is None
    assert quote.graded_levels == []


async def test_token_passed_in_query_param(
    provider: PriceChartingProvider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://mock.pc.test/api/product?t=fake-pc-token&id=1",
        json={"status": "error"},
    )
    await provider.get_price("1")
    req = httpx_mock.get_request()
    assert req is not None
    assert "t=fake-pc-token" in str(req.url)
