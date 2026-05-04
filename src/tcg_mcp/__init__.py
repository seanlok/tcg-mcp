"""tcg_mcp — Pokemon TCG MCP server.

Tool namespaces:
    tcg_psa_*         — PSA grading lookups
    tcg_cgc_*         — CGC stubs (deferred)
    tcg_bgs_*         — Beckett stubs (deferred)
    tcg_collection_*  — local SQLite-backed inventory + sealed product
    tcg_pricing_*     — Pokemon TCG API + PriceCharting (+ SNKRDUNK stub)
    tcg_watchlist_*   — buy candidates with target prices
    tcg_list_providers — capability discovery
"""

__version__ = "0.2.2"
__all__ = ["__version__"]
