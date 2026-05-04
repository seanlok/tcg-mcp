"""Smoke tests for the canonical models — they should round-trip cleanly."""

from __future__ import annotations

from tcg_mcp.models import CertResult, ImageResult, PopReport, PopRow


def test_cert_result_minimum_fields() -> None:
    c = CertResult(provider="psa", cert_number="123", found=True)
    assert c.provider == "psa"
    assert c.found is True
    assert c.is_dual_cert is False
    assert c.raw == {}


def test_cert_result_round_trip_through_json() -> None:
    c = CertResult(
        provider="psa",
        cert_number="79721014",
        found=True,
        year="1999",
        grade="GEM MT 10",
        grade_numeric=10.0,
        total_population=1234,
        population_higher=0,
    )
    payload = c.model_dump_json()
    c2 = CertResult.model_validate_json(payload)
    assert c2 == c


def test_image_result_basic() -> None:
    img = ImageResult(
        provider="psa",
        cert_number="79721014",
        url="https://example.com/x.jpg",
        is_front=True,
    )
    assert img.is_front is True


def test_pop_report_with_rows() -> None:
    rep = PopReport(
        provider="psa",
        spec_id="12345",
        rows=[PopRow(grade="10", count=1234), PopRow(grade="9", count=5000)],
        total=6234,
    )
    assert rep.total == 6234
    assert len(rep.rows) == 2
