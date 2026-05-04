# Architecture

A short tour of how `grading_mcp` is wired together.

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

## Why provider stubs in v0.1

CGC and BGS are registered as stubs (always present, always raise
`NotSupportedError`) instead of being absent from the registry. This:
- Lets `grading_list_providers` surface them as known but unsupported,
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
