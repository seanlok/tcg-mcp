"""Canonical, provider-agnostic data models.

Every provider normalizes its raw API response into these models so the MCP
tool layer never has to care which grader the data came from.

The `raw` field on each model preserves the provider-specific payload so power
users can still reach into provider-only fields when needed.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["psa", "cgc", "bgs"]


class ResponseFormat(str, Enum):
    """How an MCP tool should serialize its return value."""

    MARKDOWN = "markdown"
    JSON = "json"


class CertResult(BaseModel):
    """One graded card / slab, normalized across providers."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: ProviderName = Field(..., description="Which grading service this came from")
    cert_number: str = Field(..., description="The cert / certificate number")
    found: bool = Field(
        ...,
        description=(
            "True when the provider returned a record. "
            "False = lookup succeeded but no data."
        ),
    )

    # Card identity
    year: str | None = Field(default=None, description="Year on the slab label, e.g. '1999'")
    brand: str | None = Field(
        default=None, description="Manufacturer / publisher, e.g. 'Pokemon Game'"
    )
    category: str | None = Field(
        default=None, description="Top-level category, e.g. 'TCG Cards', 'Sports Cards'"
    )
    set_name: str | None = Field(default=None, description="Set name, e.g. 'Base Set Shadowless'")
    card_number: str | None = Field(default=None, description="Card number within the set")
    subject: str | None = Field(default=None, description="Player / character / subject name")
    variety: str | None = Field(
        default=None, description="Variety or parallel, e.g. '1st Edition Holo'"
    )

    # Grade
    grade: str | None = Field(default=None, description="Full grade label, e.g. 'GEM MT 10'")
    grade_numeric: float | None = Field(
        default=None, description="Numeric grade for sorting, e.g. 10.0"
    )
    qualifier: str | None = Field(
        default=None,
        description="Grade qualifier (PSA: 'OC', 'PD', 'MK', etc.); None if no qualifier",
    )

    # Population
    total_population: int | None = Field(
        default=None, description="Count of slabs at this exact grade"
    )
    population_higher: int | None = Field(
        default=None, description="Count graded HIGHER than this slab"
    )

    # Dual-cert (autograph) fields — PSA-specific but generalizable
    is_dual_cert: bool = Field(
        default=False,
        description="True when slab has both card + auto grade",
    )
    autograph_grade: str | None = Field(default=None, description="Auto grade if dual-cert")
    primary_signers: str | None = Field(default=None, description="Signer name(s) if dual-cert")

    # Escape hatch
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-native payload, in case tool callers need a field "
            "we did not normalize"
        ),
    )


class ImageResult(BaseModel):
    """A single front/back image attached to a cert."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: ProviderName
    cert_number: str
    url: str = Field(..., description="Direct image URL")
    is_front: bool = Field(..., description="True for front, False for back")


class CardSearchResult(BaseModel):
    """One row in a card-search response. Provider-dependent fidelity."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: ProviderName
    spec_id: str | None = Field(
        default=None,
        description="Provider-specific spec/ID for fetching pop reports for this exact card",
    )
    year: str | None = None
    brand: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    subject: str | None = None
    variety: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PopRow(BaseModel):
    """Population count at one grade level."""

    grade: str = Field(..., description="Grade label, e.g. '10', '9', 'AUTH'")
    count: int = Field(..., ge=0, description="How many slabs at this grade")
    qualifier: str | None = Field(default=None, description="Grade qualifier if any")


class PopReport(BaseModel):
    """Population breakdown for a single card spec."""

    model_config = ConfigDict(str_strip_whitespace=True)

    provider: ProviderName
    spec_id: str
    set_name: str | None = None
    card_number: str | None = None
    subject: str | None = None
    rows: list[PopRow] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)
