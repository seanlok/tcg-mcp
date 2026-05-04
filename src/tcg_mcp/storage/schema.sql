-- tcg-mcp local SQLite schema (v1).
--
-- This is the authoritative source for the DB shape. Migrations are simple
-- forward-only `CREATE TABLE IF NOT EXISTS` blocks; if you need to change
-- column types or add NOT NULL constraints, write an explicit ALTER step in
-- db.py's migration runner.

-- ---- Schema version tracking ----------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- Cards owned ----------------------------------------------------------
-- One row per physical card. A graded card uses (provider, cert_number) as
-- its natural key; a raw card uses an auto-generated UUID.
CREATE TABLE IF NOT EXISTS owned_cards (
    id              TEXT PRIMARY KEY,           -- UUID4
    -- Identity (raw or graded)
    is_graded       INTEGER NOT NULL DEFAULT 0, -- 0 = raw, 1 = graded
    grading_provider TEXT,                      -- "psa" | "cgc" | "bgs" | NULL for raw
    cert_number     TEXT,                       -- only set when graded
    grade           TEXT,                       -- "GEM MT 10", "9", "PSA 10", etc.
    grade_numeric   REAL,                       -- 10.0, 9.0 — for sorting/queries

    -- Card metadata (from the grader response or user input)
    year            TEXT,
    brand           TEXT,                       -- "Pokemon Game" / "Topps Chrome"
    set_name        TEXT,                       -- best-effort; rarely clean
    card_number     TEXT,
    subject         TEXT,                       -- "Charizard"
    variety         TEXT,                       -- "1st Edition Holo"
    language        TEXT DEFAULT 'EN',          -- "EN" | "JP" | "KO" | ...

    -- Cost basis & ownership
    acquisition_date    TEXT,                   -- ISO 8601 date
    acquisition_price   REAL,                   -- in acquisition_currency
    acquisition_currency TEXT DEFAULT 'USD',
    acquisition_source  TEXT,                   -- "ebay" | "tcgplayer" | "lcs" | ...

    -- Disposition
    status          TEXT NOT NULL DEFAULT 'owned', -- "owned" | "sold" | "lost" | "graded_out"
    sold_date       TEXT,
    sold_price      REAL,
    sold_currency   TEXT,

    -- Free-form
    notes           TEXT,
    tags            TEXT,                       -- JSON array of strings

    -- Bookkeeping
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),

    -- Provider-native blob (whatever PSA returned, etc.) for power lookups
    provider_raw    TEXT,                       -- JSON

    UNIQUE (grading_provider, cert_number)
);

CREATE INDEX IF NOT EXISTS idx_owned_subject ON owned_cards (subject);
CREATE INDEX IF NOT EXISTS idx_owned_status  ON owned_cards (status);
CREATE INDEX IF NOT EXISTS idx_owned_provider_cert
    ON owned_cards (grading_provider, cert_number);

-- ---- Watchlist (Stage 3 — table created early so migrations stay simple) --
CREATE TABLE IF NOT EXISTS watchlist (
    id              TEXT PRIMARY KEY,
    card_descriptor TEXT NOT NULL,    -- free-form: "Charizard ex SIR — Surging Sparks"
    horizon         TEXT,             -- "flip" | "hold" | "sealed"
    target_price    REAL,
    target_currency TEXT DEFAULT 'USD',
    thesis          TEXT,             -- markdown
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at       TEXT,             -- when removed from watchlist
    closed_reason   TEXT              -- "bought" | "thesis_invalidated" | "manual"
);

-- ---- Pricing snapshots (Stage 2) ------------------------------------------
CREATE TABLE IF NOT EXISTS pricing_snapshots (
    id              TEXT PRIMARY KEY,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
    provider        TEXT NOT NULL,    -- "pokemontcg" | "pricecharting" | ...
    listing_id      TEXT NOT NULL,    -- provider-specific ID
    grade           TEXT,             -- NULL = raw
    currency        TEXT NOT NULL,
    market          REAL,
    low             REAL,
    high            REAL,
    raw             TEXT              -- JSON
);

CREATE INDEX IF NOT EXISTS idx_pricing_listing
    ON pricing_snapshots (provider, listing_id, captured_at);

-- ---- Pop-report snapshots (Stage 3) ---------------------------------------
-- Tracks PSA (and future CGC/BGS) population data over time. Keyed by spec_id
-- (PSA's `SpecID`) so trends can be queried even if the user looks up
-- different cert numbers from the same card.
CREATE TABLE IF NOT EXISTS pop_snapshots (
    id                  TEXT PRIMARY KEY,
    captured_at         TEXT NOT NULL DEFAULT (datetime('now')),
    grading_provider    TEXT NOT NULL,    -- "psa" | "cgc" | "bgs"
    spec_id             TEXT NOT NULL,    -- provider-specific card-spec ID
    cert_number         TEXT,             -- the cert that triggered this snapshot (optional)
    grade               TEXT,             -- grade label, e.g. "GEM MT 10"
    total_at_grade      INTEGER,
    population_higher   INTEGER,
    raw                 TEXT              -- JSON
);

CREATE INDEX IF NOT EXISTS idx_pop_spec_time
    ON pop_snapshots (grading_provider, spec_id, captured_at);
