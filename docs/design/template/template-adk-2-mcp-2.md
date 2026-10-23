# ADK 2 upgrade · 06 — mcp 1.x → 2.x

**Status**: Proposed (optional; the last Phase 1 step and may trail Phase 2)
**Phase**: 1, after 03b
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Last Updated**: 2026-10-16

## Why

- **It fixes the concurrent-close hang at the source.** mcp 2.x wakes waiters synchronously when a session is cancelled (`_fan_out_closed`; python-sdk [#2005](https://github.com/modelcontextprotocol/python-sdk/pull/2005), [#3047](https://github.com/modelcontextprotocol/python-sdk/pull/3047)). The 1.x backport ([#3441](https://github.com/modelcontextprotocol/python-sdk/issues/3441)) is still open.
- ADK 2.9+ supports both majors (changelog line 614), and has an opt-in modern-protocol path (256).
- ADK MCP features that come with 2.x:
  - idle session eviction (2.8)
  - rebuilding sessions the server dropped (457)
  - reusing the server's tool list (999)
  - `elicitation_callback` (1009)
  - OAuth PKCE (1985)
  - MCP HTTP log records (808)

## Breaking changes that hit us

| Change | Where |
|---|---|
| `mcp.server.fastmcp` removed → `MCPServer` | `backend/protocols/mcp_server.py` (`PlatformMCP(FastMCP)`). `streamable_http_path='/'` is load-bearing; keep the equivalent. |
| Unknown fields outside `_meta` are dropped | Any tool result or annotation carrying extra keys, including the MCP Apps UI capability (`UI_CAPABILITY_HEADER`) |
| MCP HTTP calls lose OTel httpx auto-tracing | `adk/mcp_observability.py`. Use ADK's MCP HTTP log records (808), or instrument explicitly. |
| ~80 other items | Follow the [migration guide](https://py.sdk.modelcontextprotocol.io/migration/) line by line. |

## Keep

**Keep the `TaggedMcpToolset` in-flight lease (PR #20).** `Runner.close()` still closes shared toolsets as of 2.10. Re-check 2.12, and whether upstream [adk-python PR #7045](https://github.com/google/adk-python/pull/7045) has merged, before deciding.

## Tests

- The scripted concurrency check: two turns, one closes while the other has an MCP call in flight (`test_mcp_toolset_shared_close.py` + the live repro).
- MCP cold start; a tool call longer than 30s.
- Re-test every remote MCP server we ship or connect to: maps, docs, browser, MCP Toolbox, AILANG, and the per-user personal-token MCP server.
- An MCP Apps artefact still renders, and still sizes itself.
