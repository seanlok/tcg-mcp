# Research: Multi-Provider Grading MCP Server

**Status:** Phase 1 — research complete, scaffolding underway
**Author:** Lok / Claude (Cowork mode)
**Date:** 2026-05-04
**Project package name:** `grading_mcp` (Python)
**Repo folder:** `psa-mcp-server/` (kept for continuity; the package itself is provider-agnostic)

---

## 0. Goal

Build a Model Context Protocol (MCP) server that lets an LLM look up trading-card grading data — PSA today, CGC and Beckett (BGS) later — and open-source it on GitHub so anyone can install and run it locally.

The server must:
- Speak MCP over stdio (default Claude Desktop / Claude Code transport).
- Expose a small, well-documented tool surface (cert lookup, images, search, population).
- Hide each provider behind a uniform `GradingProvider` interface so adding a new grader is mostly a matter of writing one new module.
- Be installable in one command (`uvx grading-mcp` or `pip install grading-mcp`).
- Have a clean public-facing setup guide.

---

## 1. How to create an MCP server and open-source it on GitHub

### 1.1 Stack choice

**Language:** Python — the user picked this; FastMCP (now folded into the official `mcp` SDK) is a pleasant decorator-based API and the HTTP/scraping ecosystem (`httpx`, `BeautifulSoup`, `Playwright`) is what we'll need for non-PSA providers.

**Transport:** stdio. Local-first, single user, runs as a subprocess of Claude Desktop / Claude Code / Cursor. We can add streamable HTTP later if there's demand for a hosted multi-tenant version.

**Packaging:** `pyproject.toml` (PEP 621, modern). Build backend: `hatchling`. Distribution: PyPI. Run via `uvx grading-mcp` (preferred; isolated env, fast) or `pipx run grading-mcp`.

### 1.2 Repository layout (target)

```
psa-mcp-server/
├── pyproject.toml             # PEP 621 metadata, deps, console script
├── README.md                  # public setup guide
├── LICENSE                    # TBD (MIT or Apache 2.0)
├── server.json                # MCP Registry metadata
├── .gitignore
├── .github/
│   └── workflows/
│       ├── ci.yml             # ruff + pytest on PRs
│       └── publish.yml        # PyPI publish on tag
├── src/
│   └── grading_mcp/
│       ├── __init__.py        # exposes __version__
│       ├── __main__.py        # `python -m grading_mcp`
│       ├── server.py          # FastMCP app + tool registration
│       ├── config.py          # env-var loading (Pydantic Settings)
│       ├── models.py          # canonical Pydantic models (Cert, Image, PopReport)
│       ├── errors.py          # _handle_api_error helper, custom exceptions
│       └── providers/
│           ├── __init__.py    # provider registry
│           ├── base.py        # GradingProvider Protocol + abstract helpers
│           ├── psa.py         # PSA Public API implementation
│           ├── cgc.py         # stub — raises NotImplementedError
│           └── bgs.py         # stub — raises NotImplementedError
├── tests/
│   ├── conftest.py
│   ├── test_models.py
│   ├── test_providers_psa.py  # mocked httpx responses
│   └── test_server.py         # validates tool listing, basic dispatch
└── docs/
    ├── research.md            # this file
    ├── architecture.md
    └── adding-a-provider.md   # step-by-step for contributors
```

### 1.3 Open-source publishing checklist

| Step | What | Notes |
|---|---|---|
| 1 | Pick a license | MIT (max adoption) or Apache 2.0 (explicit patent grant). Currently deferred per user. |
| 2 | Create GitHub repo | Public, with README, LICENSE, `.gitignore` (Python). |
| 3 | Add CI | GitHub Actions matrix on Python 3.10/3.11/3.12: `ruff check` + `pytest`. |
| 4 | Tag a release | Semantic version (`v0.1.0`). |
| 5 | Publish to PyPI | OIDC trusted publishing (no token in repo) via `pypa/gh-action-pypi-publish`. Project name: `grading-mcp`. |
| 6 | Verify | `uvx grading-mcp --help` works on a clean machine. |
| 7 | Submit to MCP Registry | See §1.4. |

### 1.4 MCP Registry submission (modelcontextprotocol/registry)

The official MCP Registry launched September 2025 and is now the standard discovery point for clients. To list there:

1. **Install the publisher CLI** (one-time):
   ```bash
   brew install mcp-publisher
   # or curl-download from https://github.com/modelcontextprotocol/registry/releases
   ```

2. **Author `server.json`** at the repo root. For our case (Python on PyPI):
   ```json
   {
     "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
     "name": "io.github.<your-username>/grading-mcp",
     "description": "MCP server for trading-card grading data (PSA, with CGC/BGS extensibility).",
     "title": "Grading MCP",
     "repository": {
       "url": "https://github.com/<your-username>/psa-mcp-server",
       "source": "github"
     },
     "version": "0.1.0",
     "packages": [
       {
         "registryType": "pypi",
         "registryBaseUrl": "https://pypi.org",
         "identifier": "grading-mcp",
         "version": "0.1.0",
         "runtimeHint": "uvx",
         "transport": { "type": "stdio" },
         "environmentVariables": [
           {
             "name": "PSA_API_TOKEN",
             "description": "PSA Public API bearer token from https://www.psacard.com/publicapi",
             "isRequired": false,
             "isSecret": true
           }
         ]
       }
     ]
   }
   ```

   The `name` **must** match the namespace owner. With GitHub auth that means `io.github.<your-github-username>/...`.

3. **Authenticate** with the registry:
   ```bash
   mcp-publisher login github
   ```
   This kicks off a device-code flow (open the URL, paste the code).

4. **Publish to PyPI first** — the registry hosts metadata only, not artifacts. So `pip install grading-mcp` must already work before submission.

5. **Publish metadata**:
   ```bash
   mcp-publisher publish
   ```

6. **Verify** via the registry API:
   ```bash
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=grading-mcp"
   ```

### 1.5 FastMCP server skeleton

The minimum viable shape:

```python
# src/grading_mcp/server.py
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("grading_mcp")

@mcp.tool(name="grading_get_cert", annotations={"readOnlyHint": True, ...})
async def grading_get_cert(params: GetCertInput) -> str:
    ...

def main() -> None:
    mcp.run()  # stdio by default
```

`pyproject.toml` exposes the entrypoint:

```toml
[project.scripts]
grading-mcp = "grading_mcp.server:main"
```

---

## 2. PSA Public API surface

### 2.1 Authentication

- **Endpoint:** `https://api.psacard.com/publicapi/...`
- **Auth scheme:** OAuth 2 *password grant* used by PSA to mint a long-lived bearer token. Practically, you just paste the token they give you on `https://www.psacard.com/publicapi`.
- **Header on every request:**
  ```
  Authorization: bearer <YOUR_TOKEN>
  ```
- **Account model:** Free token tier exists (low daily quota). Higher quotas via paid plans — contact PSA. Quota numbers are **not** in the public docs, so we should expose remaining quota in error messages and respect 429s.

### 2.2 Confirmed endpoints (from a working OSS reference implementation)

These are confirmed against [`brad-newman/fetch-psa-api`](https://github.com/brad-newman/fetch-psa-api) which calls the live API in production.

| Method | Path | Purpose |
|---|---|---|
| GET | `/cert/GetByCertNumber/{certNumber}` | Full cert details (card metadata + grade + dual-grade info) |
| GET | `/cert/GetImagesByCertNumber/{certNumber}` | Front/back images (only for cards graded after Oct 2021) |

The Swagger UI at `https://api.psacard.com/publicapi/swagger/ui/index` documents the rest but is Cloudflare-gated to authenticated browsers. Other endpoints rumored from community posts: `GetPSASetCall` (set lookup), pop-by-spec endpoints. **We will design the provider so that adding these later is a single function added to `providers/psa.py`.**

### 2.3 Confirmed response shape

`GetByCertNumber` returns a JSON envelope:

```json
{
  "PSACert": {
    "CertNumber": "12345678",
    "SpecID": 12345,
    "SpecNumber": "150",
    "LabelType": "Standard",
    "ReverseBarCode": false,
    "Year": "1999",
    "Brand": "Pokemon Game",
    "Category": "TCG Cards",
    "CardGrade": "GEM MT 10",
    "GradeDescription": "GEM MINT",
    "TotalPopulation": 1234,
    "TotalPopulationWithQualifier": 1240,
    "PopulationHigher": 0,
    "Subject": "Charizard",
    "CardNumber": "4",
    "Variety": "1st Edition Holo",
    "IsPSADNA": false,
    "IsDualCert": false
  },
  "IsValidRequest": true,
  "ServerMessage": "Request successful"
}
```

For dual-graded autographs (`IsDualCert: true`), the response additionally includes:

```json
{
  "PSACert": {
    "...": "...",
    "PrimarySigners": "Author Name",
    "AutographGrade": "10"
  }
}
```

When the cert isn't found, `IsValidRequest` is still `true` but `ServerMessage` is `"No data found"`.

### 2.4 Failure modes to handle

| Scenario | Status | Handling |
|---|---|---|
| Bad token | 401 | "PSA API token is invalid. Get one at https://www.psacard.com/publicapi" |
| Quota exceeded | 429 | "PSA API rate limit hit. Wait or upgrade your plan." |
| Cert not found | 200, `IsValidRequest=true`, `ServerMessage="No data found"` | Return a clean empty result, not an error |
| Cloudflare block / network | 5xx / timeout | Retry once with backoff, then surface |

### 2.5 Image quirk

PSA only began attaching images to certs in **October 2021**. Older slabs return a 200 with an empty list. The MCP tool description should state this so the LLM doesn't panic when a 1999 Charizard returns no images.

---

## 3. CGC and Beckett (BGS) — extensibility plan

### 3.1 Reality check

Neither CGC nor Beckett ship a documented public API. Both expose:

- A **cert verification page** (look up a single cert number on the web) — scrapeable.
- A **population report** behind a free login wall — scrapeable but ToS-sensitive.

Aggregators exist:
- **GemRate** (`gemrate.com`) — Universal Search aggregates PSA/BGS/SGC/CGC pop data. Has a paid developer API. Reasonable hedge against scraping fragility.
- **CardGrade.io** — free cert-lookup web tool covering PSA/BGS/CGC/SGC/TAG; no documented API.

### 3.2 Design implication

We must **not** hardcode HTTP-API assumptions into the core. The `GradingProvider` interface will treat the *transport* as an implementation detail — `psa.py` calls a JSON API; `cgc.py` and `bgs.py` may eventually call a scraper, GemRate, or whatever.

### 3.3 Phased rollout

| Phase | Provider | Mechanism | Notes |
|---|---|---|---|
| 0.1 | PSA | Official API | Ship now |
| 0.2 | CGC | Stub (`NotImplementedError`) | Tool surface listed, returns "not yet supported" |
| 0.3 | CGC | Cert lookup via cgccards.com page parse | ToS review first |
| 0.4 | BGS | Cert lookup via beckett.com page parse | ToS review first |
| 0.5+ | GemRate | Optional `GEMRATE_API_KEY` | Paid; gives unified pop data without scraping |

### 3.4 ToS posture

Before shipping any scrape-based provider:
- Read each grader's ToS (Beckett, CGC) — terms can prohibit automated access.
- Default to **rate-limited, polite scraping** (1 req/s, identifying User-Agent, respect robots.txt).
- Make the user provide their own login cookie if the provider requires authentication; do not bundle one.
- Document the legal posture in `docs/provider-tos.md`.

---

## 4. Provider abstraction — design

### 4.1 The Protocol

```python
# src/grading_mcp/providers/base.py
from typing import Protocol, runtime_checkable
from grading_mcp.models import CertResult, ImageResult, PopReport, CardSearchResult

@runtime_checkable
class GradingProvider(Protocol):
    """Uniform interface every grading-service implementation must satisfy."""

    name: str  # "psa" | "cgc" | "bgs" — used in tool routing

    async def get_cert(self, cert_number: str) -> CertResult: ...
    async def get_images(self, cert_number: str) -> list[ImageResult]: ...

    # Optional capabilities — providers can raise NotSupportedError
    async def search_cards(
        self, query: str, limit: int = 20, offset: int = 0
    ) -> list[CardSearchResult]: ...

    async def get_population(self, spec_id: str) -> PopReport: ...
```

### 4.2 Canonical models (provider-agnostic)

```python
# src/grading_mcp/models.py — abridged
class CertResult(BaseModel):
    provider: Literal["psa", "cgc", "bgs"]
    cert_number: str
    found: bool
    year: str | None
    brand: str | None
    category: str | None        # "TCG Cards", "Sports Cards", ...
    set_name: str | None
    card_number: str | None
    subject: str | None
    variety: str | None
    grade: str | None           # "GEM MT 10"
    grade_numeric: float | None # 10.0
    qualifier: str | None       # e.g. "OC" (PSA off-center)
    total_population: int | None
    population_higher: int | None
    is_dual_cert: bool = False
    autograph_grade: str | None = None
    raw: dict                   # provider-specific blob for power users
```

The `raw` field is the escape hatch — every provider also returns the original payload so a downstream tool or LLM can reach into provider-specific fields.

### 4.3 Tool routing

The MCP tools are provider-aware via a `provider` parameter (default `"psa"`):

```python
class GetCertInput(BaseModel):
    cert_number: str = Field(..., min_length=4, max_length=16)
    provider: Literal["psa", "cgc", "bgs"] = "psa"
    response_format: ResponseFormat = ResponseFormat.MARKDOWN

@mcp.tool(name="grading_get_cert", annotations={"readOnlyHint": True, ...})
async def grading_get_cert(params: GetCertInput) -> str:
    provider = get_provider(params.provider)  # registry lookup
    result = await provider.get_cert(params.cert_number)
    return format_cert(result, params.response_format)
```

A provider registry inside `providers/__init__.py` maps the literal to an instance:

```python
_PROVIDERS: dict[str, GradingProvider] = {}

def register(p: GradingProvider) -> None: _PROVIDERS[p.name] = p
def get_provider(name: str) -> GradingProvider:
    if name not in _PROVIDERS:
        raise ValueError(f"Provider '{name}' is not enabled. Available: {list(_PROVIDERS)}")
    return _PROVIDERS[name]
```

Providers self-register at import time iff their required env vars are present. Missing creds = silently disabled, with a clear error if the user tries to use them.

### 4.4 Adding a new provider — what a contributor does

This is what `docs/adding-a-provider.md` will spell out:

1. Create `src/grading_mcp/providers/<name>.py`.
2. Implement the `GradingProvider` Protocol (`name`, `get_cert`, `get_images`; optional methods may `raise NotSupportedError`).
3. Register in `providers/__init__.py` if env vars present.
4. Add the literal to `Provider = Literal["psa", "cgc", "bgs", "<name>"]` in `models.py`.
5. Add a mocked-httpx test in `tests/test_providers_<name>.py`.
6. Document any new env vars in the README.

That's it — no changes to the MCP tool layer, no changes to the canonical models.

---

## 5. Tool surface (v0.1)

| Tool | Inputs | Returns |
|---|---|---|
| `grading_get_cert` | cert_number, provider, response_format | Cert metadata + grade + pop |
| `grading_get_cert_images` | cert_number, provider | List of front/back image URLs |
| `grading_search_cards` | query, provider, limit, offset | Card search results (provider-dependent) |
| `grading_get_population` | spec_id, provider | Population by grade |
| `grading_list_providers` | — | Which providers are currently enabled and what creds they need |

All tools are read-only (`readOnlyHint: true`, `destructiveHint: false`). Pagination on the search tool. Markdown is the default output format; JSON available via `response_format`.

---

## 6. Open questions to resolve before v0.1 release

- [ ] **License** — MIT vs Apache 2.0 (deferred per user)
- [ ] **PSA paid tier** — confirm the quotas before recommending a tier in the README
- [ ] **PSA `GetPSASetCall` and pop-by-spec** — get an account, hit them, document the response shapes
- [ ] **Repo + Python package name finalization** — `grading-mcp` is good; the GitHub repo name (`psa-mcp-server`) may want to follow

---

## 7. Sources

- [PSA Public API Documentation](https://www.psacard.com/publicapi/documentation)
- [PSA Public API landing page](https://www.psacard.com/publicapi)
- [`brad-newman/fetch-psa-api` (working OSS reference)](https://github.com/brad-newman/fetch-psa-api)
- [PSA Swagger UI](https://api.psacard.com/publicapi/swagger/ui/index)
- [CGC Cards Population Report](https://www.cgccards.com/population-report/)
- [Beckett Grading Population Report](https://www.beckett.com/grading/pop-report)
- [GemRate Universal Search](https://www.gemrate.com/universal-search)
- [Official MCP Registry](https://registry.modelcontextprotocol.io/)
- [MCP Registry: Quickstart Publishing Guide](https://modelcontextprotocol.io/registry/quickstart)
- [MCP Registry: server.json spec](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)
- [Python MCP SDK / FastMCP](https://github.com/modelcontextprotocol/python-sdk)
- [PrefectHQ / FastMCP](https://github.com/jlowin/fastmcp)
