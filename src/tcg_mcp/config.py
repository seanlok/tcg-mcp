"""Configuration loaded from environment variables.

We use pydantic-settings so users can configure the server with environment
variables only — no config file required. Each provider's credentials are
optional; missing creds simply disable that provider.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_db_path() -> str:
    """Default SQLite path: ~/.tcg-mcp/tcg.db (per-user, persistent)."""
    home = Path(os.path.expanduser("~"))
    return str(home / ".tcg-mcp" / "tcg.db")


class Settings(BaseSettings):
    """Environment-driven configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Storage ------------------------------------------------------------

    tcg_db_path: str = Field(
        default_factory=_default_db_path,
        description=(
            "Filesystem path to the SQLite DB used for collection / watchlist / "
            "pricing snapshots. Default: ~/.tcg-mcp/tcg.db. "
            "Override with TCG_DB_PATH to keep the DB inside a project folder."
        ),
    )

    # ---- Grading providers --------------------------------------------------

    # PSA — required to enable the PSA provider.
    psa_api_token: str | None = Field(
        default=None,
        description=(
            "PSA Public API bearer token. Obtain at https://www.psacard.com/publicapi. "
            "If unset, the PSA provider is disabled."
        ),
    )
    psa_api_base_url: str = Field(
        default="https://api.psacard.com/publicapi",
        description="PSA API base URL. Override only for testing.",
    )

    # CGC — placeholder; CGC provider is a stub for v0.2.
    cgc_api_token: str | None = None

    # BGS / Beckett — placeholder; BGS provider is a stub for v0.2.
    bgs_api_token: str | None = None

    # ---- Pricing providers (Stage 2 — placeholders) ------------------------

    pokemontcg_api_key: str | None = Field(
        default=None,
        description="Pokemon TCG API (pokemontcg.io) key. Optional; raises rate limits.",
    )
    pricecharting_token: str | None = Field(
        default=None,
        description="PriceCharting API token. Required to enable PriceCharting provider.",
    )

    # ---- HTTP behavior ------------------------------------------------------

    http_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)


def get_settings() -> Settings:
    """Return a fresh Settings instance.

    Kept as a function (not a module-level singleton) so tests can override env
    vars without import-order pain.
    """
    return Settings()
