"""The provider abstraction.

Every grading service (PSA, CGC, BGS, ...) implements this Protocol. The MCP
tool layer is provider-agnostic — it dispatches on the `provider` argument,
fetches the matching `GradingProvider` from the registry, and calls into it.

To add a new provider:
1. Subclass nothing — just satisfy the Protocol.
2. Register your instance in `providers/__init__.py`'s `_build_registry()`.
3. Add the literal name to `ProviderName` in `models.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tcg_mcp.errors import NotSupportedError
from tcg_mcp.models import (
    CardSearchResult,
    CertResult,
    ImageResult,
    PopReport,
    ProviderName,
)


@runtime_checkable
class GradingProvider(Protocol):
    """Uniform interface for a card-grading data source.

    Implementations should be async, idempotent, and read-only. Optional
    capabilities (search, population) may raise NotSupportedError if the
    underlying service does not expose them.
    """

    name: ProviderName
    """Short identifier — must match a value in models.ProviderName."""

    async def get_cert(self, cert_number: str) -> CertResult:
        """Look up a single cert by its certificate number.

        Implementations MUST return a CertResult with `found=False` when the
        cert is not in the database, rather than raising. Reserve exceptions
        for transport/auth/server failures.
        """
        ...

    async def get_images(self, cert_number: str) -> list[ImageResult]:
        """Return any front/back images attached to the cert.

        May return an empty list if the provider has no images for this cert
        (PSA, for example, only has images for cards graded after Oct 2021).
        """
        ...

    async def search_cards(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CardSearchResult]:
        """Search the provider's card catalog. OPTIONAL — may raise NotSupportedError."""
        ...

    async def get_population(self, spec_id: str) -> PopReport:
        """Fetch a population report for a card spec. OPTIONAL — may raise NotSupportedError."""
        ...


class BaseProvider:
    """Convenience base that fulfils the optional-method contract.

    Concrete providers can inherit this and override only what they actually
    support. The default implementations raise NotSupportedError, which the
    MCP tool layer translates into a clean error message.
    """

    name: ProviderName

    async def search_cards(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CardSearchResult]:
        raise NotSupportedError(
            f"Provider '{self.name}' does not support card search."
        )

    async def get_population(self, spec_id: str) -> PopReport:
        raise NotSupportedError(
            f"Provider '{self.name}' does not support population lookup by spec_id."
        )
