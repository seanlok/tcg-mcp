"""BGS / Beckett provider — STUB.

Beckett Grading Services (BGS) does not publish an official public API.
Population reports live at https://www.beckett.com/grading/pop-report (login
required) and individual cert lookups at
https://www.beckett.com/grading/card-lookup.

Future implementation paths mirror the CGC strategy: polite HTML scraping or
the paid GemRate aggregator.
"""

from __future__ import annotations

from tcg_mcp.errors import NotSupportedError
from tcg_mcp.models import CertResult, ImageResult
from tcg_mcp.providers.base import BaseProvider


class BGSProvider(BaseProvider):
    name = "bgs"

    async def get_cert(self, cert_number: str) -> CertResult:
        raise NotSupportedError(
            "BGS provider is a stub in v0.1. Beckett has no official API; "
            "scraping support is planned for a future release."
        )

    async def get_images(self, cert_number: str) -> list[ImageResult]:
        raise NotSupportedError(
            "BGS provider is a stub in v0.1. BGS image fetching is not yet implemented."
        )
