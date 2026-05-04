"""tcg_mcp — local-first Pokemon TCG MCP server.

Tool namespaces:
    tcg_psa_*         — PSA grading lookups
    tcg_cgc_*         — CGC stubs (deferred)
    tcg_bgs_*         — Beckett stubs (deferred)
    tcg_collection_*  — local SQLite-backed inventory
    tcg_pricing_*     — Stage 2, not yet implemented
    tcg_list_providers — capability discovery
"""

__version__ = "0.2.0"
__all__ = ["__version__"]
