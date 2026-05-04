"""CGC provider — STUB.

CGC (Certified Guaranty Company) does not publish an official public API.
Population data lives at https://www.cgccards.com/population-report/ and cert
verification lives at https://www.cgccards.com/certlookup/.

Future implementation paths:
1. Polite HTML scraping of the public cert-lookup page.
2. Paid GemRate API (https://www.gemrate.com) which aggregates CGC pop data.

For v0.1 this provider is a placeholder so the MCP tool surface and the
provider registry can both reference 'cgc' as a known name without surprises.
"""

from __future__ import annotations

from tcg_mcp.errors import NotSupportedError
from tcg_mcp.models import CertResult, ImageResult
from tcg_mcp.providers.base import BaseProvider


class CGCProvider(BaseProvider):
    name = "cgc"

    async def get_cert(self, cert_number: str) -> CertResult:
        raise NotSupportedError(
            "CGC provider is a stub in v0.1. CGC has no official API; "
            "scraping support is planned for a future release."
        )

    async def get_images(self, cert_number: str) -> list[ImageResult]:
        raise NotSupportedError(
            "CGC provider is a stub in v0.1. CGC image fetching is not yet implemented."
        )
