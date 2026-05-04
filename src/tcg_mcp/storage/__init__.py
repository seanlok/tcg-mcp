"""SQLite-backed local storage for collection, watchlist, and pricing snapshots."""

from tcg_mcp.storage.db import Database, get_db

__all__ = ["Database", "get_db"]
