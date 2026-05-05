"""SQLite database access layer.

Design choices:
- **Synchronous sqlite3** under the hood. SQLite is fast enough for an
  interactive single-user MCP server, and `sqlite3` ships with the stdlib
  (no extra dep). We expose ergonomic helpers; tools call them via
  `asyncio.to_thread` if blocking becomes a concern (it won't, at this scale).
- **Forward-only migrations** via `schema.sql`. The file is the source of
  truth; `apply_migrations` runs it idempotently because every statement is
  `CREATE ... IF NOT EXISTS`.
- **Row factories** return dicts so callers don't deal with positional tuples.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tcg_mcp.config import Settings, get_settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class Database:
    """Thin wrapper around sqlite3 with helpers for our tables.

    Usage:
        db = Database(path="/path/to/tcg.db")
        db.apply_migrations()
        with db.connect() as conn:
            ...
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- Lifecycle ---------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), isolation_level=None)  # autocommit
        conn.row_factory = _row_to_dict
        # Enable foreign keys + write-ahead log for better concurrency
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    def apply_migrations(self) -> None:
        """Idempotent — run on every server startup."""
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(sql)
            # Bump schema_version row if missing
            cur = conn.execute("SELECT MAX(version) as v FROM schema_version")
            current = cur.fetchone()["v"]
            if current is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (1)")

            # Stage 3 column additions on owned_cards. SQLite doesn't have
            # ALTER TABLE ADD COLUMN IF NOT EXISTS, so we check via PRAGMA
            # and add only if missing. This keeps the migration idempotent.
            self._ensure_column(conn, "owned_cards", "product_type", "TEXT DEFAULT 'single'")
            self._ensure_column(conn, "owned_cards", "pricing_provider", "TEXT")
            self._ensure_column(conn, "owned_cards", "pricing_listing_id", "TEXT")

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        """Add `column` to `table` if it's not already present.

        Using PRAGMA table_info as the existence check keeps this safe to call
        on every startup. The `definition` is the column-type clause that
        follows the column name in `ALTER TABLE ... ADD COLUMN <name> <def>`.
        """
        cur = conn.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cur.fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # ---- owned_cards CRUD --------------------------------------------------

    def add_owned_card(self, fields: dict[str, Any]) -> str:
        """Insert a new owned card. Returns the new row's id (UUID)."""
        new_id = fields.pop("id", None) or uuid.uuid4().hex

        # Coerce JSON fields
        if "tags" in fields and not isinstance(fields["tags"], (str, type(None))):
            fields["tags"] = json.dumps(fields["tags"])
        if "provider_raw" in fields and not isinstance(
            fields["provider_raw"], (str, type(None))
        ):
            fields["provider_raw"] = json.dumps(fields["provider_raw"])

        cols = ["id"] + list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        values: list[Any] = [new_id] + list(fields.values())
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO owned_cards ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
        return new_id

    def get_owned_card(self, card_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.execute("SELECT * FROM owned_cards WHERE id = ?", (card_id,))
            return cur.fetchone()

    def find_by_cert(self, provider: str, cert_number: str) -> dict[str, Any] | None:
        """Look up an owned card by (grading_provider, cert_number)."""
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM owned_cards WHERE grading_provider = ? AND cert_number = ?",
                (provider, cert_number),
            )
            return cur.fetchone()

    def list_owned(
        self,
        *,
        status: str | None = "owned",
        provider: str | None = None,
        subject_like: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if provider is not None:
            clauses.append("grading_provider = ?")
            params.append(provider)
        if subject_like:
            clauses.append("subject LIKE ?")
            params.append(f"%{subject_like}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM owned_cards {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, params + [limit, offset])
            return list(cur.fetchall())

    def update_owned_card(self, card_id: str, fields: dict[str, Any]) -> bool:
        """Update an owned card. Returns True if a row was updated."""
        if not fields:
            return False
        if "tags" in fields and not isinstance(fields["tags"], (str, type(None))):
            fields["tags"] = json.dumps(fields["tags"])

        # Always bump updated_at
        fields = {**fields, "updated_at": "datetime('now')"}
        # We have to inline the datetime() call since it's a SQL expression.
        set_parts = []
        params: list[Any] = []
        for k, v in fields.items():
            if k == "updated_at":
                set_parts.append("updated_at = datetime('now')")
            else:
                set_parts.append(f"{k} = ?")
                params.append(v)
        params.append(card_id)
        sql = f"UPDATE owned_cards SET {', '.join(set_parts)} WHERE id = ?"
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0

    def remove_owned_card(self, card_id: str, *, hard: bool = False) -> bool:
        """Remove a card. By default this is a soft delete (status='sold').

        Pass hard=True to actually DELETE the row (loses history).
        """
        with self.connect() as conn:
            if hard:
                cur = conn.execute("DELETE FROM owned_cards WHERE id = ?", (card_id,))
            else:
                cur = conn.execute(
                    "UPDATE owned_cards SET status = 'sold', "
                    "updated_at = datetime('now') WHERE id = ?",
                    (card_id,),
                )
            return cur.rowcount > 0

    # ---- pricing_snapshots --------------------------------------------------

    def add_pricing_snapshot(self, fields: dict[str, Any]) -> str:
        """Insert one pricing-snapshot row. Returns the new id."""
        new_id = fields.pop("id", None) or uuid.uuid4().hex
        if "raw" in fields and not isinstance(fields["raw"], (str, type(None))):
            fields["raw"] = json.dumps(fields["raw"])
        cols = ["id"] + list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        values: list[Any] = [new_id] + list(fields.values())
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO pricing_snapshots ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
        return new_id

    def latest_snapshot(
        self, provider: str, listing_id: str, *, grade: str | None = None
    ) -> dict[str, Any] | None:
        """Most recent snapshot for a (provider, listing_id, grade) tuple.

        `grade=None` returns the most recent raw/ungraded snapshot.
        """
        with self.connect() as conn:
            if grade is None:
                cur = conn.execute(
                    "SELECT * FROM pricing_snapshots "
                    "WHERE provider = ? AND listing_id = ? AND grade IS NULL "
                    "ORDER BY captured_at DESC LIMIT 1",
                    (provider, listing_id),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM pricing_snapshots "
                    "WHERE provider = ? AND listing_id = ? AND grade = ? "
                    "ORDER BY captured_at DESC LIMIT 1",
                    (provider, listing_id, grade),
                )
            return cur.fetchone()

    def list_pricing_snapshots(
        self,
        *,
        provider: str,
        listing_id: str,
        grade: str | None = None,
        days: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return the time series of pricing snapshots, oldest -> newest.

        Args:
            provider: pricing provider name (e.g. "pokemontcg")
            listing_id: provider-specific listing ID
            grade: optional grade filter; None means raw/ungraded only.
                Pass an explicit string ("PSA 10") for graded series.
            days: only return snapshots from the last N days
            limit: cap on rows returned
        """
        clauses = ["provider = ?", "listing_id = ?"]
        params: list[Any] = [provider, listing_id]
        if grade is None:
            clauses.append("grade IS NULL")
        else:
            clauses.append("grade = ?")
            params.append(grade)
        if days is not None and days > 0:
            clauses.append("captured_at >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM pricing_snapshots WHERE {where} "
            "ORDER BY captured_at ASC LIMIT ?"
        )
        params.append(limit)
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return list(cur.fetchall())

    def search_owned(
        self,
        query: str,
        *,
        status: str | None = "owned",
        is_graded: bool | None = None,
        language: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Free-text search across owned_cards (v0.4).

        Searches across `subject`, `set_name`, `brand`, `variety`, `notes`,
        and `tags` with case-insensitive substring matching. Provides richer
        discovery than `list_owned(subject_like=...)` which only hits one
        column.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if query:
            like = f"%{query}%"
            clauses.append(
                "("
                "LOWER(COALESCE(subject,'')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(set_name,'')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(brand,'')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(variety,'')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(notes,'')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(tags,'')) LIKE LOWER(?)"
                ")"
            )
            params.extend([like] * 6)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if is_graded is not None:
            clauses.append("is_graded = ?")
            params.append(1 if is_graded else 0)
        if language is not None:
            clauses.append("language = ?")
            params.append(language)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM owned_cards {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, params + [limit, offset])
            return list(cur.fetchall())

    def list_owned_with_pricing(
        self,
        *,
        provider: str | None = None,
        status: str | None = "owned",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Owned cards that have a pricing listing attached, for bulk operations.

        Filters out rows where pricing_provider or pricing_listing_id is NULL.

        Args:
            provider: optional pricing-provider filter ("pokemontcg", etc.)
            status: collection status; defaults to 'owned'
            limit: optional row cap (None = no cap)
        """
        clauses = ["pricing_provider IS NOT NULL", "pricing_listing_id IS NOT NULL"]
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if provider is not None:
            clauses.append("pricing_provider = ?")
            params.append(provider)
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM owned_cards WHERE {where} ORDER BY created_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return list(cur.fetchall())

    # ---- watchlist CRUD ----------------------------------------------------

    def add_watchlist(self, fields: dict[str, Any]) -> str:
        new_id = fields.pop("id", None) or uuid.uuid4().hex
        cols = ["id"] + list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        values: list[Any] = [new_id] + list(fields.values())
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO watchlist ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
        return new_id

    def get_watchlist(self, watchlist_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM watchlist WHERE id = ?", (watchlist_id,)
            )
            return cur.fetchone()

    def list_watchlist(
        self,
        *,
        horizon: str | None = None,
        open_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if horizon is not None:
            clauses.append("horizon = ?")
            params.append(horizon)
        if open_only:
            clauses.append("closed_at IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM watchlist {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, params + [limit, offset])
            return list(cur.fetchall())

    def update_watchlist(self, watchlist_id: str, fields: dict[str, Any]) -> bool:
        if not fields:
            return False
        set_parts = [f"{k} = ?" for k in fields.keys()]
        params: list[Any] = list(fields.values())
        params.append(watchlist_id)
        sql = f"UPDATE watchlist SET {', '.join(set_parts)} WHERE id = ?"
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0

    def close_watchlist(
        self, watchlist_id: str, *, reason: str
    ) -> bool:
        """Mark a watchlist row as closed (bought / thesis_invalidated / manual)."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE watchlist SET closed_at = datetime('now'), closed_reason = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (reason, watchlist_id),
            )
            return cur.rowcount > 0

    # ---- pop_snapshots ------------------------------------------------------

    def add_pop_snapshot(self, fields: dict[str, Any]) -> str:
        new_id = fields.pop("id", None) or uuid.uuid4().hex
        if "raw" in fields and not isinstance(fields["raw"], (str, type(None))):
            fields["raw"] = json.dumps(fields["raw"])
        cols = ["id"] + list(fields.keys())
        placeholders = ",".join("?" for _ in cols)
        values: list[Any] = [new_id] + list(fields.values())
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO pop_snapshots ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
        return new_id

    def list_pop_snapshots(
        self,
        *,
        grading_provider: str,
        spec_id: str,
        grade: str | None = None,
        days: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return pop snapshots for a (provider, spec_id) ordered oldest -> newest.

        Args:
            grading_provider: "psa" / "cgc" / "bgs"
            spec_id: provider-specific spec ID
            grade: optional grade filter (e.g. "GEM MT 10")
            days: only return snapshots from the last N days
        """
        clauses = ["grading_provider = ?", "spec_id = ?"]
        params: list[Any] = [grading_provider, spec_id]
        if grade is not None:
            clauses.append("grade = ?")
            params.append(grade)
        if days is not None and days > 0:
            clauses.append("captured_at >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM pop_snapshots WHERE {where} "
            "ORDER BY captured_at ASC LIMIT ?"
        )
        params.append(limit)
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return list(cur.fetchall())

    # ---- pricing attachment + live valuation -------------------------------

    def attach_pricing(
        self,
        card_id: str,
        *,
        pricing_provider: str,
        pricing_listing_id: str,
    ) -> bool:
        """Set pricing_provider + pricing_listing_id on an owned card."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE owned_cards SET pricing_provider = ?, "
                "pricing_listing_id = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (pricing_provider, pricing_listing_id, card_id),
            )
            return cur.rowcount > 0

    def collection_valuation(self) -> dict[str, Any]:
        """Live valuation across owned cards.

        For each owned (status='owned') card with a pricing attachment, we
        look up the latest pricing snapshot:
          - graded card: latest snapshot for (provider, listing_id, grade)
          - raw card:    latest snapshot for (provider, listing_id, grade=NULL)

        Cards with no attachment or no snapshot land in `unpriced_count`.
        Returns aggregate cost basis, market value, unrealized P&L, and a
        per-card breakdown.
        """
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT id, subject, is_graded, grade, "
                "acquisition_price, acquisition_currency, "
                "pricing_provider, pricing_listing_id "
                "FROM owned_cards WHERE status = 'owned'"
            )
            rows = cur.fetchall()

        items: list[dict[str, Any]] = []
        unpriced = 0
        total_cost = 0.0
        total_market = 0.0

        for row in rows:
            cost = float(row.get("acquisition_price") or 0.0)
            total_cost += cost

            provider = row.get("pricing_provider")
            listing = row.get("pricing_listing_id")
            grade = row.get("grade") if row.get("is_graded") else None

            market: float | None = None
            snapshot_at: str | None = None
            if provider and listing:
                snap = self.latest_snapshot(provider, listing, grade=grade)
                if snap is None and grade is not None:
                    # Fall back to ungraded snapshot
                    snap = self.latest_snapshot(provider, listing, grade=None)
                if snap is not None:
                    market = snap.get("market")
                    snapshot_at = snap.get("captured_at")

            if market is None:
                unpriced += 1
            else:
                total_market += float(market)

            # Round per-item floats to 2dp to avoid 25.340000000000003 noise.
            market_rounded = round(float(market), 2) if market is not None else None
            unrealized_rounded = (
                round(market_rounded - cost, 2)
                if market_rounded is not None
                else None
            )

            items.append(
                {
                    "card_id": row["id"],
                    "subject": row.get("subject"),
                    "is_graded": bool(row.get("is_graded")),
                    "grade": row.get("grade"),
                    "acquisition_price": cost or None,
                    "market_price": market_rounded,
                    "unrealized": unrealized_rounded,
                    "snapshot_provider": provider,
                    "snapshot_listing_id": listing,
                    "snapshot_at": snapshot_at,
                }
            )

        return {
            "owned_count": len(rows),
            "priced_count": len(rows) - unpriced,
            "unpriced_count": unpriced,
            "total_cost_basis": round(total_cost, 2),
            "total_market": round(total_market, 2),
            "unrealized_total": round(total_market - total_cost, 2),
            "currency": "USD (mixed; no FX conversion applied)",
            "items": items,
        }

    # ---- Aggregate ---------------------------------------------------------

    def collection_summary(self) -> dict[str, Any]:
        """Return basic counts + cost-basis totals (no live pricing).

        For market valuation, see `collection_valuation` which joins owned
        cards against the most recent pricing snapshots.
        """
        with self.connect() as conn:
            owned = conn.execute(
                "SELECT COUNT(*) as c FROM owned_cards WHERE status = 'owned'"
            ).fetchone()["c"]
            graded = conn.execute(
                "SELECT COUNT(*) as c FROM owned_cards "
                "WHERE status = 'owned' AND is_graded = 1"
            ).fetchone()["c"]
            total_cost = conn.execute(
                "SELECT COALESCE(SUM(acquisition_price), 0) as t "
                "FROM owned_cards WHERE status = 'owned' AND acquisition_price IS NOT NULL"
            ).fetchone()["t"]
            sold = conn.execute(
                "SELECT COUNT(*) as c FROM owned_cards WHERE status = 'sold'"
            ).fetchone()["c"]

        return {
            "owned_count": owned,
            "graded_count": graded,
            "raw_count": owned - graded,
            "sold_count": sold,
            "total_cost_basis": round(float(total_cost or 0), 2),
            "currency": "USD (mixed; assumes acquisition_currency='USD' for sums)",
        }


# Module-level cache, reset by reset_db() in tests.
_db: Database | None = None


def get_db(settings: Settings | None = None) -> Database:
    """Return a process-wide Database instance, applying migrations on first use."""
    global _db
    if _db is None:
        s = settings or get_settings()
        _db = Database(s.tcg_db_path)
        _db.apply_migrations()
    return _db


def reset_db() -> None:
    """Test hook — forces the next get_db() call to re-init."""
    global _db
    _db = None
