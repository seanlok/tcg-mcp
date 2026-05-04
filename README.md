# tcg-mcp

<!-- mcp-name: io.github.seanlok/tcg-mcp -->

> A Pokemon TCG MCP server. Looks up graded cards (PSA today, CGC/BGS
> stubbed), manages your owned collection in a local SQLite DB, queries
> pricing providers (Pokemon TCG API + PriceCharting), tracks a watchlist
> with target prices, and snapshots PSA pop counts so you can see trends
> over time.

[![PyPI](https://img.shields.io/pypi/v/tcg-mcp.svg)](https://pypi.org/project/tcg-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/tcg-mcp.svg)](https://pypi.org/project/tcg-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/tcg-mcp.svg)](https://pypistats.org/packages/tcg-mcp)
[![CI](https://github.com/seanlok/tcg-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/seanlok/tcg-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`tcg-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io)
server. Install it once, wire it into Claude Desktop / Claude Code / Cursor /
any MCP client, and your assistant gains 25 tools for working with PSA cert
data, your personal collection, and live market prices.

---

## What it does — 25 tools, namespaced

**PSA grading (`tcg_psa_*`)** — cert lookup, front/back images, snapshot pop
data over time, plus a workflow tool that looks up a cert and records it as
owned in one call.

**CGC / BGS (`tcg_cgc_*`, `tcg_bgs_*`)** — stubs in v0.2; no public API exists
for either grader. Listed for routing parity; will route cleanly if either
grader publishes an API.

**Collection (`tcg_collection_*`)** — add raw or graded cards (or sealed
products: ETBs, booster boxes, UPCs, tins), list with filters, update cost
basis, soft-delete (mark sold) or hard-delete, attach a card to a pricing
listing, get a cost-basis summary or a live market valuation that joins
against the most recent pricing snapshots.

**Pricing (`tcg_pricing_*`)** — search a provider, get a full price quote
(top-level market/low/high plus per-variant breakdown for Pokemon TCG API,
plus per-grade levels for PriceCharting), and persist snapshots into the
local DB for later trend queries.

**Watchlist (`tcg_watchlist_*`)** — add target buy prices with thesis text,
list by horizon (flip / hold / sealed), update, and close with a reason
(bought / thesis_invalidated / manual).

**Meta (`tcg_list_providers`)** — discovery tool that shows which grading +
pricing providers are enabled, what env var each needs, and what tools are
in the namespace.

For the full tool list run `tcg_list_providers` after install or read the
[architecture doc](docs/architecture.md).

---

## Prerequisites

1. **Python 3.10 or newer** (3.13 recommended).
2. **An MCP client** — Claude Desktop, Claude Code, Cursor, Continue, etc.
3. *(Optional, for PSA tools only)* **A PSA Public API token** — free, sign
   up at [psacard.com/publicapi](https://www.psacard.com/publicapi).

The server works without any tokens — Pokemon TCG API queries, collection,
and watchlist tools all function on a fresh install with zero credentials.

---

## Install

### Option A — `uvx` (recommended, zero install)

```bash
# install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# run the server (auto-installs the package on first use)
uvx tcg-mcp --help
```

### Option B — `pipx`

```bash
pipx install tcg-mcp
tcg-mcp --help
```

### Option C — `pip`

```bash
python3 -m pip install tcg-mcp
```

### Option D — from source (for contributors)

```bash
git clone https://github.com/seanlok/tcg-mcp.git
cd tcg-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest        # run the test suite
```

---

## Configure

Copy `.env.example` to `.env` and fill in any tokens you have:

```bash
cp .env.example .env
$EDITOR .env
```

Or set the env vars however your client supports it (most clients let you
specify `env` per MCP server in the config).

| Env var | Required for | Notes |
|---|---|---|
| `PSA_API_TOKEN` | PSA tools | [Get one](https://www.psacard.com/publicapi) — free |
| `POKEMONTCG_API_KEY` | Higher Pokemon TCG API rate limit (optional) | [Get one](https://dev.pokemontcg.io/) — free |
| `PRICECHARTING_TOKEN` | PriceCharting tools | Paid subscription required |
| `TCG_DB_PATH` | Local DB location | Default `~/.tcg-mcp/tcg.db` |

---

## Wire it into your MCP client

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS),
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), or
`~/.config/Claude/claude_desktop_config.json` (Linux):

```json
{
  "mcpServers": {
    "tcg-mcp": {
      "command": "uvx",
      "args": ["tcg-mcp"],
      "env": {
        "PSA_API_TOKEN": "your-token-here",
        "TCG_DB_PATH": "~/Documents/tcg-mcp.db"
      }
    }
  }
}
```

Quit Claude Desktop fully (⌘Q on macOS — closing the window isn't enough)
and relaunch.

### Claude Code

```bash
claude mcp add tcg-mcp -- uvx tcg-mcp
```

Then export the tokens you have in the shell that runs `claude`.

### Cursor / Continue / etc.

Same shape — point the client at:

```
command: uvx
args:    ["tcg-mcp"]
env:     PSA_API_TOKEN=...    # optional
```

---

## Smoke-test it

After wiring up the client, ask it:

> "Use tcg-mcp to list providers."

You should see `pokemontcg` enabled, plus `psa` enabled if your token is
set, and stubs for the others.

Then try a real lookup:

> "Search Pokemon TCG API for Charizard ex from Obsidian Flames."

> "Add a 1999 Pokemon Base Set Charizard #4 (raw) to my collection — paid $250 on 2026-04-15."

> "Add Charizard ex Surging Sparks to my watchlist with a target buy price of $180."

> "What's my collection cost basis?"

---

## Architecture, briefly

```
+----------------------+
|     MCP client       |  Claude Desktop, Cursor, etc.
+----------+-----------+
           | stdio (JSON-RPC)
+----------v-----------+
|     server.py        |  FastMCP — tool registration, validation
+----------+-----------+
           |
   +-------+-------+----------------+
   |               |                |
+--v--+         +--v--+        +----v----+
| psa |  ...    |pricing|      | storage |  SQLite — collection,
+--+--+         +--+----+      +----+----+  watchlist, pop trends,
   | httpx         | httpx          |       pricing snapshots
+--v---------------v----+      +----v----+
|  PSA / Pokemon TCG    |      | tcg.db  |
|  API / PriceCharting  |      +---------+
+-----------------------+
```

Provider abstractions ([`providers/base.py`](src/tcg_mcp/providers/base.py),
[`pricing/base.py`](src/tcg_mcp/pricing/base.py)) make adding a new grader
or pricing source a single-file change. See
[`docs/adding-a-provider.md`](docs/adding-a-provider.md).

---

## Local SQLite database

All your personal data — collection, watchlist, pricing snapshots, pop
snapshots — lives in a single SQLite file. Default location is
`~/.tcg-mcp/tcg.db`. Point `TCG_DB_PATH` at any path you prefer.

The file format is plain SQLite, so you can inspect or back up the data
directly:

```bash
sqlite3 ~/.tcg-mcp/tcg.db
.tables
SELECT subject, grade, acquisition_price FROM owned_cards WHERE status='owned';
```

Schema is in [`src/tcg_mcp/storage/schema.sql`](src/tcg_mcp/storage/schema.sql).
Migrations are forward-only and idempotent (safe to run on every startup).

---

## Known limits

- **PSA images** only exist for cards graded after **October 2021**. Older
  slabs return an empty image list — that's the upstream API, not a bug.
- **PSA's `Brand` field** is the closest thing to a clean "set name" in
  their schema. We surface it as `set_name`; for finer-grained set parsing,
  reach into the raw payload.
- **CGC / BGS** providers are stubs in v0.2. They're listed for discovery
  but raise `NotSupportedError` if called. Implementation depends on either
  grader publishing a public API or an explicit decision to support polite
  scraping.
- **Rate limits** on the PSA free tier aren't publicly documented. If you
  see "PSA API rate limit exceeded", wait or upgrade your plan.
- **PriceCharting** is paid-only. Without a `PRICECHARTING_TOKEN` the
  provider is registered as `disabled` and graded-card prices aren't
  available — but Pokemon TCG API still gives you raw market prices.

---

## Roadmap

- v0.3 — `tcg_pricing_snapshot_collection` (snapshot every attached card in
  one call, respecting per-provider rate limits)
- v0.4 — CGC scraping path or GemRate provider
- v0.5 — BGS scraping path
- v0.6 — eBay sold-comp provider for raw market data

---

## Contributing

Contributions welcome. To add a new grading or pricing provider, see
[`docs/adding-a-provider.md`](docs/adding-a-provider.md). The contract is
intentionally small: implement a Protocol method, register it conditionally
based on credentials, write a mock-httpx test.

```bash
# Run tests + lint locally
pytest
ruff check .
```

---

## Disclaimers

This project is **independent**. It is not affiliated with PSA, CGC,
Beckett, The Pokemon Company, Nintendo, TCGPlayer, Cardmarket,
PriceCharting, or any other organization. Each external API call is
subject to that provider's Terms of Service.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Sources / further reading

- [Model Context Protocol](https://modelcontextprotocol.io) — official site
- [MCP Registry](https://modelcontextprotocol.io/registry) — discovery
- [Pokemon TCG API](https://docs.pokemontcg.io/) — pricing + catalog
- [PSA Public API](https://www.psacard.com/publicapi/documentation)
- [PriceCharting API](https://www.pricecharting.com/api-documentation)
