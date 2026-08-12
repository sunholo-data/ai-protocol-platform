# Template MCP Strict Resolution

**Status**: ✅ All 3 parts implemented in platform (2026-06-06) — **template sync pending**. Part (a) shipped as a template-shipped utility helper (`derive_in_process_mcp_base_url()`) + a gotchas.md entry rather than a literal port of the fork's `_seed_in_process_mcp_servers` patch (the fork added that function itself; the platform doesn't have an auto-seed step today). Any fork adopting the auto-seed pattern uses the helper and inherits the correct loopback default for free.
**Priority**: P1 (silent misconfig → silent broken agent at runtime)
**Estimated**: 0.5d planned; ~1h actual
**Scope**: Backend (`backend/adk/tools.py`, `backend/tools/mcp/registry.py`, `backend/scripts/seed_mcp_servers.py`)
**Dependencies**: None
**Created**: 2026-06-06
**Last Updated**: 2026-06-06
**Source items**: G42 / Friction 23 — gde-ap-agent fork (2026-06-06), MCP tool calls silently produced no tools because the SKILL.md declared a server-id that wasn't in the deployed environment's Firestore. Three-part fix needed.

## Problem Statement

The platform's MCP-tool wiring has a silent-misconfiguration trap. When a SKILL.md declares:

```yaml
toolConfigs:
  mcp:
    servers: [vendor-master, erp-posting]
```

…and one or both server-ids are missing from the deployed environment's `mcp_servers/` Firestore collection (or the docs exist but have malformed configs), the pre-G42 `resolve_mcp_tools` silently returned a partial list. The agent built with FEWER MCP tools than the SKILL.md asked for, then at run-time behaved as if it had no tool for that operation — said "I can't help with that" or invented a plausible-looking but wrong answer.

The user has no visible error. Logs show INFO/WARN lines from the silent skip ("server 'vendor-master' not found in Firestore; skipping") buried in startup chatter. The skill author thinks "the LLM ignored my prompt"; the SRE thinks "the deployment is fine"; days are lost.

**Three trigger paths:**

a. **In-process MCP seed wired against the wrong default URL.** A fork-shipped fix added an in-process MCP server seeded at app startup; the seed targets a `BACKEND_BASE_URL` that defaulted to `localhost`. Node's DNS can resolve `localhost` to `::1` (IPv6) while the backend binds `0.0.0.0` (IPv4-only) — silent fetch-failed at run-time, MCP server returns no tools, agent silently misbehaves. **Fork-specific patch lives in `backend/fast_api_app.py`; will land via template-publish.**

b. **`seed_mcp_servers.py` default URL ambiguous.** The seed script that populates `mcp_servers/` Firestore docs accepts a `--url` flag whose default was `http://localhost:3001/mcp`. Same IPv6 trap as (a); plus there's no audit trail when an operator runs the script — a stray `--url <some-public-cloud-run-url>` invocation silently re-targeted the wrong environment.

c. **`resolve_mcp_tools` silently returns partial.** When the agent factory called `resolve_mcp_tools(tool_configs)` and some declared servers didn't resolve, it logged + returned `[resolved_subset]`. Agent built with fewer tools than declared; user-visible behaviour was an agent that "ignored" parts of its SKILL.md.

This is the same shape as Friction 7 ("SKILL.md tool drift") — the agent silently lost tools between SKILL.md declaration and runtime invocation. Friction 7's symptom was the audit pane showing nothing for `emit_*`; G42's symptom is the agent never calling any MCP tool.

## Goals

**Primary Goal:** Any MCP misconfiguration that would cause an agent to silently misbehave at runtime instead fails LOUDLY at agent-build time, with a diff naming exactly which server-ids didn't resolve.

**Success Metrics:**
- Declaring `mcp.servers: [name]` in a SKILL.md against a Firestore that doesn't have a `mcp_servers/name` doc raises `McpServerResolutionError` at agent build (NOT a silent zero-tool agent at runtime).
- The exception message lists the declared count, resolved count, and the specific missing server-ids — enough for the operator to fix the seed without grepping logs.
- `seed_mcp_servers.py` defaults to `127.0.0.1:3001/mcp` (NOT `localhost`) so the IPv6 DNS trap can't strike.
- A non-loopback target requires the explicit `--public-url` flag so accidental wrong-env seeds leave an audit trail in stdout.
- Backwards-compat invariant: admin scripts and test fixtures using the legacy `get_mcp_tools` keep getting the silently-skip behaviour (the strict layer is at `resolve_mcp_tools`, not at `get_mcp_tools`).

**Non-Goals:**
- Auto-seeding missing servers. Out of scope — the seed is operator-controlled per environment.
- Validating that each MCP server actually returns ≥1 tool at runtime. That's a runtime check; the user mentioned the idea as a future extension. For now, agent-build-time validation that the toolset was constructed (which requires a valid `url`) is sufficient signal.
- Patching the fork-specific `fast_api_app.py` in-process MCP seed. The user shipped that fix in their own fork; will land via the next template-publish if they provide the diff.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Build-time-only failure (no runtime path change for valid configs) |
| 2 | EARNED TRUST | +1 | Users see honest "agent build failed: missing servers" instead of "agent ignored my prompt" |
| 3 | SKILLS, NOT FEATURES | +1 | The SKILL.md → agent-build contract is now strict — what the skill declared is what the agent gets, or nothing |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Not model-routing |
| 5 | GRACEFUL DEGRADATION | -1 | INTENTIONAL conflict: pre-G42 was a degraded-running agent; G42 is fail-loud. The trade-off favours honest failure over silent partial degradation. See justification below. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Enforces the SKILL.md / `tool_configs.mcp.servers` contract precisely — protocol invariant. |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | +2 | Build-time failure message names the missing IDs + points at the fix command (`seed_mcp_servers.py`) + cross-references the design doc. Also: `seed_mcp_servers.py` prints `target url = X` so operator runs leave a clear audit line. |
| 9 | SECURE BY CONSTRUCTION | 0 | No security surface |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend correctness fix |
| | **Net Score** | **+4** | Acceptable — proceed |

**Conflict justification (#5 GRACEFUL DEGRADATION at -1):**
The pre-G42 silently-skip behaviour was a "graceful" degradation in the literal sense — the agent ran, just with fewer tools. But the resulting agent silently misbehaved at runtime, which is worse than not running at all because the operator can't tell the difference between "agent is broken" and "user asked a question the agent legitimately can't answer." Fail-loud at build time gives the operator a clear signal AND an actionable fix. The trade-off favours honest failure.

## Design

### Part (a) — in-process MCP seed loopback default

**Status:** ✅ shipped 2026-06-06 as a template-shipped utility helper, not as a literal port of the fork's patch.

**Why not a literal port:** the fork's `ccf9843` modifies a function (`_seed_in_process_mcp_servers`) that doesn't exist in the platform — the fork added the auto-seed step itself in its earlier `0494cf0`. The platform's `fast_api_app.py` mounts FastMCP at `/mcp` but doesn't auto-seed any `mcp_servers/` Firestore docs at startup. So porting the literal diff would require first porting the auto-seed pattern from the fork (out of scope: the platform doesn't have a concrete use case for auto-seeded in-process MCP servers; downstream forks add their own based on need).

**What the platform DOES ship:** a small helper plus documentation that any future fork adopting the auto-seed pattern can use to inherit the correct loopback default without rediscovering the public-URL trap.

**`backend/tools/mcp/registry.py::derive_in_process_mcp_base_url()`:**

```python
def derive_in_process_mcp_base_url() -> str:
    """Return the base URL a fork should seed when registering one of
    THIS service's in-process MCP servers. Loopback only — never the
    public Cloud Run URL. See [gotchas.md] for the failure mode."""
    override = os.environ.get("MCP_INTERNAL_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    port = os.environ.get("PORT", "1956")
    return f"http://127.0.0.1:{port}"
```

Three properties this enforces:

1. **Loopback default.** `127.0.0.1:<PORT>` — NOT the public Cloud Run URL. The public hostname routes to the frontend container; the in-process FastMCP mount lives only on the backend sidecar at the loopback bind.
2. **127.0.0.1, not `localhost`.** Node's DNS can resolve `localhost` to `::1` (IPv6) while uvicorn binds IPv4-only on `0.0.0.0`. Same trap as the `seed_mcp_servers.py` script's default — fixed at both layers.
3. **`MCP_INTERNAL_BASE_URL` override + ops audit.** Ops can override for test fixtures or alternate binds. Empty-string is treated as "no override" (Cloud Run delivers absent declared vars as `""`, which `??` doesn't catch — same lesson as G20).

**Documentation:** [`docs/ops/gotchas.md`](../../ops/gotchas.md) gets a new "In-process MCP servers must be seeded with loopback URLs, NEVER the public Cloud Run URL (G42)" entry with the symptom-cascade (Tool not found → slow first-click → ADK crash → RUN_FINISHED-after-RUN_ERROR, now suppressed by G41), the fix recipe, and the external-MCP carve-out.

**Tests:** 5 new cases in `test_mcp_registry.py::TestDeriveInProcessMcpBaseUrl` covering default behaviour, Cloud Run's `PORT` injection, the `MCP_INTERNAL_BASE_URL` override, the trailing-slash normalisation, and the empty-string falls-through-to-port-logic invariant.

### Part (b) — `seed_mcp_servers.py` 127.0.0.1 default + explicit `--public-url`

**File:** [`backend/scripts/seed_mcp_servers.py`](../../../backend/scripts/seed_mcp_servers.py)

Two changes:

1. **`DEFAULT_LOCAL_URL`** changes from `http://localhost:3001/mcp` → `http://127.0.0.1:3001/mcp`. Comment above the constant explains the Node DNS-resolves-localhost-to-::1 trap; the IPv4 loopback dodges it explicitly.

2. **Two mutually-exclusive URL flags:**
   - `--url <X>` — kept for backwards-compat but marked DEPRECATED for non-loopback URLs.
   - `--public-url <X>` — new flag for any non-loopback URL. The split forces the operator to be explicit about which environment they're targeting. A stray `--url https://prod-foo.run.app/mcp` can no longer silently re-target.

3. **Audit log line:** the script prints `seed_mcp_servers: target url = <X>` on every run so grep-able logs show exactly which URL was seeded.

### Part (c) — strict resolution at agent build

**Files:** [`backend/tools/mcp/registry.py`](../../../backend/tools/mcp/registry.py), [`backend/adk/tools.py`](../../../backend/adk/tools.py)

Three pieces:

1. **New `get_mcp_tools_with_status(server_ids) → (resolved, missing)` in `tools/mcp/registry.py`.** Same resolution logic as the legacy `get_mcp_tools`, but tracks which server-ids failed to resolve (Firestore-not-found OR Firestore-error OR `_build_toolset` returned None). Returns both lists so the caller can decide how to react.

2. **`McpServerResolutionError` in `adk/tools.py`.** A `RuntimeError` subclass with a docstring explaining the G42 context. The exception type lets agent-build callers catch this specific error class without sniffing message strings.

3. **`resolve_mcp_tools` now uses the strict resolver.** Calls `get_mcp_tools_with_status`; if `missing` is non-empty, raises `McpServerResolutionError` with a structured message:

   ```
   SKILL.md declares 2 MCP server(s) (['resolved-srv', 'missing-srv'])
   but only 1 resolved. Missing: ['missing-srv'].
   Common causes:
     (1) the server doc doesn't exist in Firestore mcp_servers/ —
         re-run scripts/seed_mcp_servers.py for the target environment;
     (2) the Firestore doc is missing the 'url' field;
     (3) the SKILL.md typoed the server name.
   See docs/design/template/template-mcp-strict-resolution.md.
   ```

4. **Backwards-compat path:** the legacy `get_mcp_tools(server_ids)` keeps its silently-skip behaviour. Admin scripts (status dashboards, dev probes) and test fixtures rely on it. The strict gate is at the agent-build layer (`resolve_mcp_tools`), which is where the SKILL.md contract is enforced.

### CLI Surface

No new commands. The seed script gets a `--public-url` flag (additive — doesn't break existing scripted invocations).

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add `get_mcp_tools_with_status` to `tools/mcp/registry.py`; refactor `get_mcp_tools` to delegate | 15min |
| 2 | Add `McpServerResolutionError` + strict path in `adk/tools.py::resolve_mcp_tools` | 20min |
| 3 | Update `seed_mcp_servers.py`: 127.0.0.1 default, `--public-url` flag, audit log | 15min |
| 4 | 8 new tests in `test_mcp_registry.py` (6 covering `get_mcp_tools_with_status` paths, 2 covering the strict `resolve_mcp_tools` raise paths) + 1 updated test for the new resolver wire-up | 20min |
| 5 | New design doc + SEQUENCE.md row | 15min |

**Total: ~85 min ≈ 1h** (actual run-time).

## Testing Strategy

**`backend/tests/tool_tests/test_mcp_registry.py`** — new + updated cases:

`TestGetMcpToolsWithStatus` (6 cases):
1. **`test_returns_resolved_and_empty_missing_when_all_succeed`** — happy path.
2. **`test_tracks_server_not_in_firestore_as_missing`** — `get_document` returns None.
3. **`test_tracks_server_without_url_as_missing`** — doc exists but is malformed.
4. **`test_tracks_firestore_error_as_missing`** — `get_document` raises.
5. **`test_partial_resolution_reports_both_sides`** — 1 resolved + 1 missing; both lists populated.
6. **`test_legacy_get_mcp_tools_still_skips_silently`** — backwards-compat invariant.

`TestResolveMcpTools` (updated/new):
7. **`test_calls_get_mcp_tools_with_status_and_returns_resolved`** — replaces the old patch-and-assert-call test; happy path with empty missing.
8. **`test_g42_raises_when_some_declared_servers_dont_resolve`** — partial-resolution path. Asserts the exception message contains declared count, resolved count, missing IDs, and `seed_mcp_servers` pointer.
9. **`test_g42_raises_when_all_declared_servers_fail_to_resolve`** — all-miss path. Asserts all missing IDs appear in the message.

Test infra: patches `tools.mcp.registry.get_document` for the registry tests (Firestore boundary) and `tools.mcp.registry.get_mcp_tools_with_status` for the resolver tests (so the resolver tests don't accidentally exercise the Firestore boundary).

## Success Criteria

- [x] `get_mcp_tools_with_status(server_ids)` returns `(resolved_toolsets, missing_server_ids)` per the documented contract.
- [x] `resolve_mcp_tools` raises `McpServerResolutionError` with a clear diff when any declared server fails to resolve.
- [x] Legacy `get_mcp_tools(server_ids)` keeps silently-skip behaviour (backwards compat).
- [x] `seed_mcp_servers.py` defaults to `http://127.0.0.1:3001/mcp`; `--public-url` flag required for non-loopback targets; `target url = X` audit log on every run.
- [x] 8 new + 1 updated test in `test_mcp_registry.py`; 23/23 pass.
- [ ] **Template sync pending**: next `aitana-template-publish` run propagates to `sunholo-data/ai-protocol-platform`.
- [ ] **Fork-specific (a) patch** for `backend/fast_api_app.py` loopback default — user to provide.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — G42 row
- [template-protocol-defaults.md G24](./template-protocol-defaults.md) — Friction 7 ("SKILL.md tool drift") — G42 also prevents this class of bug by making the SKILL.md → agent-build contract strict.
- [template-agui-terminal-dedup.md](./template-agui-terminal-dedup.md) — sibling "universal trap that every fork hits" doc (G41). Both are template-shipped backend code where the failure is silent until a real demo surfaces it.
- [adk/tools.py](../../../backend/adk/tools.py) and [tools/mcp/registry.py](../../../backend/tools/mcp/registry.py) — the files under test.

## Cloud Run failure-path interaction with G41

On a Cloud Run deployment, the strict check at agent-build time raises
`McpServerResolutionError`, which propagates up through `ag_ui_adk`'s
background task and gets emitted as `RUN_ERROR` via the queue path.
Without [G41](./template-agui-terminal-dedup.md) the subsequent spurious
`RUN_FINISHED` from `ag_ui_adk`'s outer try-block would trip the
`@ag-ui/client` state machine with *"Cannot send event type 'RUN_FINISHED'"*.
G41 suppresses that duplicate terminal, so the user sees a SINGLE
clean error chip carrying the G42 fix-it message
(*"SKILL.md declares N MCP server(s) but only K resolved…"*) — exactly
the failure mode the user can act on. G42 and G41 are intentionally
co-designed; shipping G42 without G41 would surface the misconfig
correctly but leave the user with a confusing client-side crash on top
of it.
