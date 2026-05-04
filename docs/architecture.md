# Architecture

A short tour of how `tcg_mcp` is wired together.

## Layers

```
+----------------------+
|    MCP client        |  Claude Desktop, Claude Code, Cursor, etc.
+----------+-----------+
           | stdio (JSON-RPC)
+----------v-----------+
|   server.py          |  FastMCP — tool registration, input validation,
|   (tool layer)       |  output formatting (markdown / JSON)
+----------+-----------+
           | calls get_provider("psa") etc.
+----------v-----------+
|  providers/__init__  |  Registry. Builds providers from env vars.
+----------+-----------+
           |
   +-------+-------+----------+
   |               |          |
+--v--+         +--v--+   +---v---+
| psa |         | cgc |   |  bgs  |
+--+--+         +-----+   +-------+
   | httpx
+--v---------+
| PSA Public |
| API        |
+------------+
```

## Why a Protocol, not an ABC

`GradingProvider` is defined as a `typing.Protocol` (with
`@runtime_checkable`) rather than an abstract base class. Two reasons:

1. **Structural typing.** Anyone can write a class that satisfies the
   interface without inheriting from us — useful for third-party providers
   shipped as separate packages.
2. **No diamond on optional capabilities.** We use a small `BaseProvider`
   helper class (also concrete, not abstract) to provide default
   `NotSupportedError` implementations for the optional methods. Concrete
   providers inherit `BaseProvider` for ergonomics, but the *interface*
   they're conforming to is the Protocol.

## Why per-call httpx clients

`PSAProvider._client()` constructs a fresh `httpx.AsyncClient` on each tool
invocation. For an stdio MCP server with low call rate, this is the simplest
correct option — no connection-pool lifetime to manage, no leaked sockets if
something goes wrong upstream.

If we ever move to streamable HTTP transport (where calls become much higher
frequency), we should promote the client to a FastMCP lifespan-managed
singleton so we keep the connection pool across calls.

## Why we don't use `pokemontcgsdk`

There's an official Python SDK for the Pokemon TCG API at
[`PokemonTCG/pokemon-tcg-sdk-python`](https://github.com/PokemonTCG/pokemon-tcg-sdk-python),
distributed on PyPI as `pokemontcgsdk`. We deliberately call the API
directly via `httpx` instead of pulling in the SDK. Three reasons:

1. **The SDK is synchronous; the rest of our pricing layer is async.**
   Mixing sync calls into async tool handlers means thread-pool offloads
   for every request and worse latency. The whole `pricing/` package is
   built on `httpx.AsyncClient`, so direct calls fit the existing pattern.
2. **We're already translating to canonical models.** Every response gets
   normalized into our `PriceQuote` / `CardListing` shapes anyway, so the
   SDK's own data classes don't carry through to the tool layer. We'd
   write the same field-extraction code either way; the SDK just adds an
   extra layer to debug through when something changes upstream.
3. **Throttle integration.** Each pricing provider plugs into our shared
   `TokenBucket` rate limiter (`pricing/throttle.py`). Wrapping the SDK
   would either bypass that bucket — burning quota — or require another
   layer of plumbing equivalent to what we already have.

If the SDK ever ships an async API or starts exposing endpoints we want
that aren't in the public API directly (catalog browsing, `Type.all()`,
etc.), this decision is worth revisiting. Until then, direct HTTP is
simpler.

The same reasoning applies to PriceCharting — we hit `/api/product`
directly rather than depending on a third-party wrapper.

## Pricing layer mirrors the grading abstraction

Pricing has its own provider Protocol at `tcg_mcp.pricing.base.PricingProvider`,
its own registry at `tcg_mcp.pricing.__init__`, and the same "register-only-if-
credentials-are-present" pattern. This means:

- Pokemon TCG API works without `POKEMONTCG_API_KEY` (just at lower rate).
- PriceCharting only registers if `PRICECHARTING_TOKEN` is set; otherwise
  the tool returns a clean "not enabled" error pointing at the env var.
- SNKRDUNK is registered as a stub (no public API yet) — same shape as
  CGC/BGS on the grading side.

The bulk-snapshot tool (`tcg_pricing_snapshot_collection`) iterates
`owned_cards` rows, groups by pricing provider, and serially fetches
through each provider — natural rate-limit behavior because the
`TokenBucket` lives on the provider instance, not on the tool.

## Why provider stubs in v0.1

CGC and BGS are registered as stubs (always present, always raise
`NotSupportedError`) instead of being absent from the registry. This:
- Lets `tcg_list_providers` surface them as known but unsupported,
  giving the LLM a coherent answer when asked "can this server look up CGC
  certs?"
- Keeps the `ProviderName` literal stable so adding their real
  implementation later is a one-file change.

## Error contract for tools

Every tool catches its own exceptions and returns a string starting with
`"Error: "`. We deliberately do **not** raise out of tool functions — MCP
clients render tool exceptions less helpfully than tool return values, and
agents do better when error text is shaped like guidance ("you need to set
PSA_API_TOKEN, get one at ...").

## Logging

`logging.basicConfig` writes to **stderr**, never stdout. The stdio
transport uses stdout for JSON-RPC frames; any stray print or default
`logging` config that targets stdout will corrupt the protocol.
