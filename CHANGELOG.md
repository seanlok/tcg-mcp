# Changelog

All notable changes to `tcg-mcp` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-05-04

### Added
- **`tcg_pricing_snapshot_collection`** — bulk-snapshot every owned card
  with an attached pricing listing in one call. Skips cards whose latest
  snapshot is younger than `max_age_hours` (default 24h). Per-provider rate
  limits are respected via each provider's existing token bucket. Errors
  are reported per-card; one failure no longer aborts the rest. Supports
  `provider` filtering, `dry_run`, and `limit`.
- **`tcg_pricing_get_history`** — query the time series of saved pricing
  snapshots for a `(provider, listing_id, grade?)` tuple. Mirrors the
  existing `tcg_psa_pop_trend` shape so prompts about pop trends and price
  trends use the same pattern.
- `db.list_owned_with_pricing()` and `db.list_pricing_snapshots()` —
  storage helpers backing the two new tools.

### Changed
- `tcg_collection_value_with_market` per-item `market_price` and
  `unrealized` fields are now rounded to 2 decimal places, matching the
  aggregate fields. Eliminates float-drift artifacts like
  `25.340000000000003`.
- PSA `429` error message now mentions BOTH possible causes (invalid token
  OR exhausted quota), since the PSA API uses the same status code for
  both. Saves users debugging the wrong end first.

## [0.2.2] - 2026-05-04

### Added
- Automated MCP Registry publishing via `mcp-publisher login github-oidc`
  in `publish.yml`. PyPI + Registry now both publish from a single
  GitHub Release click.
- Version-mismatch guards in the publish workflow that fail-fast if
  `pyproject.toml`, `server.json`, or the tag drift apart.
- PyPI propagation poll (up to 150s) so the registry publish step runs
  only after the new wheel is queryable.

### Changed
- `ci.yml` and `publish.yml` switched off the dead
  `astral-sh/setup-uv@v3` action; both now use the official
  `actions/setup-python@v5` + stock `pip`.

## [0.2.1] - 2026-05-04

### Added
- `mcp-name: io.github.seanlok/tcg-mcp` marker in `README.md`, required
  by the MCP Registry's PyPI ownership-validation step.

### Fixed
- PSA provider now correctly handles bare-data API responses (cert fields
  at the root or under `PSACert` without the surrounding `IsValidRequest`
  envelope). v0.2.0 misreported valid certs as "not found".

## [0.2.0] - 2026-05-04

### Added
- Initial public release.
- 25 tools across six namespaces: `tcg_psa_*`, `tcg_cgc_*` (stub),
  `tcg_bgs_*` (stub), `tcg_pricing_*`, `tcg_collection_*`,
  `tcg_watchlist_*`, plus `tcg_list_providers`.
- PSA grading lookups, front/back images, pop snapshots and trend queries.
- Local SQLite-backed collection — raw + graded + sealed product, cost
  basis, soft/hard delete, pricing-listing attachment, cost-basis summary
  and live market valuation.
- Pokemon TCG API and PriceCharting pricing providers, plus a SNKRDUNK
  stub for future implementation.
- Watchlist with target buy prices, horizon (flip/hold/sealed), and
  thesis text.

[Unreleased]: https://github.com/seanlok/tcg-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/seanlok/tcg-mcp/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/seanlok/tcg-mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/seanlok/tcg-mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/seanlok/tcg-mcp/releases/tag/v0.2.0
