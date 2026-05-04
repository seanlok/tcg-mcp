"""PSA Public API provider.

Hits the official PSA Public API (https://www.psacard.com/publicapi).

Confirmed endpoints:
    GET /cert/GetByCertNumber/{certNumber}     — cert details
    GET /cert/GetImagesByCertNumber/{certNumber} — front/back images

Auth: bearer token in `Authorization` header.

The PSA payload uses PascalCase keys; we normalize into the snake_case
canonical models defined in `tcg_mcp.models`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from tcg_mcp.errors import format_http_error
from tcg_mcp.models import CertResult, ImageResult
from tcg_mcp.providers.base import BaseProvider

log = logging.getLogger(__name__)

# Match the leading numeric portion of a grade label like "GEM MT 10"
# or "MINT 9.5". Returns the float (10.0, 9.5) or None.
_GRADE_NUMERIC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*$")


def _parse_grade_numeric(grade: str | None) -> float | None:
    if not grade:
        return None
    m = _GRADE_NUMERIC_RE.search(grade)
    return float(m.group(1)) if m else None


class PSAProvider(BaseProvider):
    """Talk to the PSA Public API."""

    name = "psa"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.psacard.com/publicapi",
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ValueError("PSAProvider requires a non-empty bearer token")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Build a fresh httpx.AsyncClient with auth + timeout configured.

        We do NOT keep a long-lived client because FastMCP tool calls are
        short-lived and a per-call client keeps the code simple. If perf
        becomes a concern we can promote this to a lifespan-managed singleton.
        """
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"bearer {self._token}"},
            timeout=self._timeout,
        )

    # ---- GradingProvider implementation -------------------------------------

    async def get_cert(self, cert_number: str) -> CertResult:
        path = f"/cert/GetByCertNumber/{cert_number}"
        try:
            async with self._client() as client:
                resp = await client.get(path)
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json()
        except httpx.HTTPError as e:
            # Surface as a generic exception so the tool layer can translate
            # it into an actionable message via errors.format_http_error.
            log.warning("PSA get_cert(%s) failed: %s", cert_number, e)
            raise

        # The PSA Public API responds in two slightly different shapes:
        #   1. With an envelope: {"IsValidRequest": true, "ServerMessage": "...",
        #      "PSACert": {...}}.
        #   2. Without an envelope: just the PSACert fields at the root, OR
        #      {"PSACert": {...}} alone with no envelope keys.
        # Earlier versions of this provider over-trusted the envelope and
        # returned `found=False` for the no-envelope variant. Now we treat
        # the presence of populated PSACert data as the source of truth and
        # only fall back to envelope checks for explicit "no data" signals.

        # Explicit "no data" signal in the envelope shape.
        if payload.get("ServerMessage") == "No data found":
            return CertResult(
                provider="psa",
                cert_number=cert_number,
                found=False,
                raw=payload,
            )

        # If IsValidRequest is explicitly False, trust that.
        if payload.get("IsValidRequest") is False:
            return CertResult(
                provider="psa",
                cert_number=cert_number,
                found=False,
                raw=payload,
            )

        # PSACert may be nested under "PSACert" or, for some endpoints/auth
        # states, the cert fields are at the top level. Detect either shape.
        cert: dict[str, Any] = payload.get("PSACert") or {}
        if not cert and payload.get("CertNumber"):
            cert = payload  # bare-data shape

        if not cert:
            return CertResult(
                provider="psa",
                cert_number=cert_number,
                found=False,
                raw=payload,
            )

        return CertResult(
            provider="psa",
            cert_number=str(cert.get("CertNumber") or cert_number),
            found=True,
            year=_str_or_none(cert.get("Year")),
            brand=_str_or_none(cert.get("Brand")),
            category=_str_or_none(cert.get("Category")),
            # PSA doesn't have a clean "set name" — Brand is the closest
            # field (e.g. "Pokemon Game", "Topps Chrome"). Callers needing
            # the cleaner set name should fall back to `raw`.
            set_name=_str_or_none(cert.get("Brand")),
            card_number=_str_or_none(cert.get("CardNumber")),
            subject=_str_or_none(cert.get("Subject")),
            variety=_str_or_none(cert.get("Variety")),
            grade=_str_or_none(cert.get("CardGrade") or cert.get("GradeDescription")),
            grade_numeric=_parse_grade_numeric(cert.get("CardGrade")),
            qualifier=_str_or_none(cert.get("Qualifier")),
            total_population=_int_or_none(cert.get("TotalPopulation")),
            population_higher=_int_or_none(cert.get("PopulationHigher")),
            is_dual_cert=bool(cert.get("IsDualCert", False)),
            autograph_grade=_str_or_none(cert.get("AutographGrade")),
            primary_signers=_str_or_none(cert.get("PrimarySigners")),
            raw=payload,
        )

    async def get_images(self, cert_number: str) -> list[ImageResult]:
        path = f"/cert/GetImagesByCertNumber/{cert_number}"
        try:
            async with self._client() as client:
                resp = await client.get(path)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as e:
            log.warning("PSA get_images(%s) failed: %s", cert_number, e)
            raise

        # The endpoint returns either a list of {ImageURL, IsFrontImage} dicts
        # or, for older slabs, an empty list / a wrapped envelope.
        if isinstance(payload, dict):
            items = payload.get("Images") or payload.get("PSAImages") or []
        else:
            items = payload or []

        out: list[ImageResult] = []
        for item in items:
            url = item.get("ImageURL")
            if not url:
                continue
            out.append(
                ImageResult(
                    provider="psa",
                    cert_number=cert_number,
                    url=url,
                    is_front=bool(item.get("IsFrontImage", True)),
                )
            )
        return out


# ---- helpers ----------------------------------------------------------------


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# Re-export for convenience in error formatting
__all__ = ["PSAProvider", "format_http_error"]
