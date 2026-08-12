# Template A2A Spec Compliance

**Status**: Planned (Sprint G43, 2026-06-07)
**Priority**: P1 — every fork registering with Gemini Enterprise hits this
**Estimated**: 1d
**Scope**: Backend (`backend/protocols/a2a.py`, `tests/api_tests/test_a2a.py`), Frontend (`frontend/src/app/.well-known/agent.json/route.ts`), new `scripts/verify-a2a.sh`
**Dependencies**: G39 (Next ingress `/.well-known/agent.json` proxy — the route this design extends already exists)
**Created**: 2026-06-07
**Last Updated**: 2026-06-07
**Source items**: G43 / Frictions 22-24 — gde-ap-agent fork attempted Gemini Enterprise registration on 2026-06-07 against a real Discovery Engine app; three coupled A2A spec violations rejected the card. All three sailed past 13 passing pytest cases in the existing `test_a2a.py` — Discovery Engine's JSON-schema validator caught what local tests missed. Source commits: gde-ap-agent `236fdcb` (URL rewrite), `dbc5856` (protocolVersion + tests + probe).

## Problem Statement

The platform's A2A discovery surface (`/.well-known/agent.json`) **passes
unauthenticated discovery** — every existing test runs green, the route
returns 200 with a well-formed body. But it **fails Gemini Enterprise
registration** because the spec compliance is only schema-shallow. Three
coupled bugs:

### Friction 22 — the `url` field leaks `localhost` behind a proxy

```bash
$ curl -s https://<fork-host>/.well-known/agent.json | jq -r .url
http://localhost:1956   ← Gemini Enterprise would store this and try to invoke it
```

Root cause: the FastAPI backend has no way to know its public URL — it's a
sidecar behind the Next.js ingress. `backend/protocols/a2a.py` falls back
to `os.getenv("PUBLIC_BASE_URL", "http://localhost:1956")`; Cloud Run
deploys don't set the env var. The card is technically valid but
**unreachable**: peer A2A clients (and Gemini Enterprise) can discover the
agent but can't actually invoke any skill on it.

### Friction 23 — A2A spec v0.2 requires `protocolVersion`; we don't emit it

```bash
$ agents-cli register-gemini-enterprise --registration-type a2a \
    --agent-card-url https://<fork-host>/.well-known/agent.json \
    --gemini-enterprise-app-id <...>
Error: 400 INVALID_ARGUMENT: required property 'protocolVersion' not found in object
```

A2A v0.2+ requires a top-level `protocolVersion` field on the agent card.
The platform's `_build_card()` doesn't emit it. Permissive A2A clients
(humans curl-ing the card, simple peer agents) tolerate the omission;
Discovery Engine's strict validator rejects it.

### Friction 24 — `capabilities.extensions[]` must carry `AgentExtension` objects, not strings

(Latent — would surface as the next failure if/when the platform starts
advertising extensions.) The current platform doesn't emit any extensions
at all; the moment we add capability advertisement (required for Gemini
Enterprise to know which protocol extensions an agent supports), we'll hit
the schema rejection that bit gde-ap-agent:

```
INVALID_ARGUMENT: At /capabilities/extensions/0 of "a2ui-v0.9" -
unexpected instance type
```

A2A v0.2 schema defines `capabilities.extensions[]` as an array of
`AgentExtension` objects, each with at least a `uri` field. The fork
emitted bare strings; Discovery Engine rejected them.

### Why this matters

The entire point of the template's A2A surface is "another agent (or an
enterprise tool registry) can discover and coordinate with this one." All
three bugs make that discovery technically succeed AND practically fail.
The template's existing tests didn't catch any of them because they only
checked schema-shallow properties — `field in card`, `type(extensions) is
list`, etc. The real compliance gate is Discovery Engine's JSON-schema
validator, which we hit at registration time, not at unit-test time.

## Goals

**Primary Goal:** A fork following the template's deploy guide can register
with Gemini Enterprise on first try, no patch needed — and any new spec
violations surface at CI-time via a live `verify-a2a.sh` probe, not at
registration-time via a 400.

**Success Metrics:**
- `agents-cli register-gemini-enterprise --registration-type a2a
  --agent-card-url <fork-host>/.well-known/agent.json` returns HTTP 200
  on a freshly-deployed fork.
- The card's `url` field reflects the fork's public origin (no localhost,
  no internal Cloud Run hostname).
- `protocolVersion: "0.2.0"` appears at the card root.
- `capabilities.extensions[]` contains `AgentExtension` objects with `uri`,
  `description`, `required` per spec.
- `X-A2A-Extensions` request header → backend negotiates → response header
  echoes the negotiated set; `Vary: X-A2A-Extensions` is set so caches
  don't merge negotiated responses.
- `scripts/verify-a2a.sh` exits 0 on a spec-compliant card and 1 on any
  regression; suitable for CI gates.
- Backend test suite catches each of the three failure modes pinned above
  before the next regression.

**Non-Goals:**
- A2A task-handler surface. This doc only covers discovery + extension
  advertisement. Task handling is a separate later doc.
- A2A v0.3 migration. The spec is moving; we ship v0.2 today (matches
  Discovery Engine's current validator) and bump when Gemini Enterprise's
  validator does.
- Auto-deriving the fork's public URL at startup. The Next.js proxy layer
  is the only one that knows; baking that into the backend would couple
  the backend to the ingress, breaking the sidecar topology.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No request-path latency change |
| 2 | EARNED TRUST | +1 | Forks that claim "this agent is discoverable" actually deliver — registration works on first try |
| 3 | SKILLS, NOT FEATURES | +1 | Skill discoverability is core to the SKILL.md → public marketplace → A2A pipeline; spec-compliance closes the last gap |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Not model-routing |
| 5 | GRACEFUL DEGRADATION | +1 | URL-rewrite fallback chain (X-Forwarded-Proto → req.nextUrl); empty cache returns a card with empty skills instead of 500 |
| 6 | PROTOCOL OVER CUSTOM | +2 | This is the protocol-compliance fix. Replaces "shallow valid" with "Discovery-Engine-validator valid". |
| 7 | API FIRST | +1 | The A2A wire format IS the API contract; getting it right is the whole goal |
| 8 | OBSERVABLE BY DEFAULT | +1 | `verify-a2a.sh` is a 12-check spec-compliance probe runnable in CI; failures surface BEFORE the operator tries a Gemini Enterprise registration |
| 9 | SECURE BY CONSTRUCTION | 0 | No security surface change (the card is already unauthenticated discovery) |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Trusts the protocol's negotiation primitive (`X-A2A-Extensions`); doesn't invent any custom signalling |
| | **Net Score** | **+8** | Strong alignment — proceed |

**Conflict Justifications:** None (no -1 scores).

## Design

### Part 1 — Backend: extension info table + protocolVersion + negotiation

**File:** `backend/protocols/a2a.py`

The platform doesn't currently advertise any extensions or negotiate. We
add the full A2A v0.2 spec compliance surface in one place:

#### 1a. `SUPPORTED_EXTENSION_INFO` — single source of truth

```python
SUPPORTED_EXTENSION_INFO: dict[str, tuple[str, str]] = {
    "a2ui-v0.9": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/v0.9.md",
        "A2UI v0.9 declarative UI surfaces",
    ),
    "a2ui-basic-catalog-v0.9": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/basic-catalog-v0.9.md",
        "A2UI BasicCatalog component set",
    ),
    "a2ui-inline-pattern": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/inline-pattern.md",
        "A2UI inline-rendered surfaces (in-chat)",
    ),
    "a2ui-decoupled-pattern": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/decoupled-pattern.md",
        "A2UI decoupled surfaces (separate pane)",
    ),
    "a2a-v0.2": (
        "https://a2aproject.github.io/A2A/v0.2",
        "A2A protocol v0.2 (this card complies with this version)",
    ),
    "mcp-apps-v1": (
        "https://modelcontextprotocol.io/specification/draft/server/apps",
        "MCP Apps v1 sandboxed iframe artefacts",
    ),
    "adk-workflow-v1": (
        "https://google.github.io/adk-docs/agents/workflow-agents/",
        "ADK workflow agents (SequentialAgent / ParallelAgent / LoopAgent)",
    ),
}
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(SUPPORTED_EXTENSION_INFO.keys())
```

Single source of truth keyed by extension ID — the negotiation header uses
just the IDs, the card body needs full descriptors. Derived tuple stays the
canonical iteration order.

#### 1b. `_extension_descriptor()` — AgentExtension shape

```python
def _extension_descriptor(ext_id: str) -> dict[str, Any]:
    """Wrap a supported extension ID as an A2A AgentExtension object.

    Falls back to a urn:-style URI if the ID is missing from the info table
    — defence in depth so a fork adding a new extension without updating
    SUPPORTED_EXTENSION_INFO still produces a card that passes schema
    validation rather than crashing the endpoint.
    """
    uri, description = SUPPORTED_EXTENSION_INFO.get(
        ext_id,
        (f"urn:sunholo:a2a-extension:{ext_id}", ext_id),
    )
    return {"uri": uri, "description": description, "required": False}
```

#### 1c. `_parse_client_extensions()` + `_negotiate_extensions()`

The `X-A2A-Extensions` request header is a comma-separated list of bare IDs
the client supports. The negotiated set is the **intersection** with our
supported set, preserving the client's order so the client's preference
wins where ours allows.

```python
def _parse_client_extensions(header_value: str | None) -> list[str]:
    """Parse the X-A2A-Extensions request header into a list of bare IDs."""
    if not header_value:
        return []
    return [token.strip() for token in header_value.split(",") if token.strip()]


def _negotiate_extensions(client_ids: list[str]) -> list[str]:
    """Intersect client-supported IDs with our supported set, preserving
    client order. Empty client list = advertise everything we support
    (the discovery-default behaviour)."""
    if not client_ids:
        return list(SUPPORTED_EXTENSIONS)
    supported = set(SUPPORTED_EXTENSIONS)
    return [cid for cid in client_ids if cid in supported]
```

#### 1d. `_build_card()` adds `protocolVersion` + `capabilities.extensions`

```python
return {
    "protocolVersion": "0.2.0",       # NEW — required by Discovery Engine validator
    "name": os.getenv("A2A_AGENT_NAME", "..."),
    # ... existing fields ...
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": False,
        "extensions": [_extension_descriptor(e) for e in ext_ids],  # NEW — descriptor objects
    },
    # ...
}
```

`ext_ids` is the negotiated list; passed through from the route handler.

#### 1e. Route handler returns `Response` with negotiation headers

```python
@router.get("/.well-known/agent.json")
def agent_card(request: Request) -> Response:
    """A2A agent card. Unauthenticated."""
    client_ids = _parse_client_extensions(request.headers.get("X-A2A-Extensions"))
    negotiated = _negotiate_extensions(client_ids)
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:1956")
    card = _cached_card(base_url, _time_bucket(), tuple(negotiated))
    return JSONResponse(
        content=card,
        headers={
            "X-A2A-Extensions": ", ".join(negotiated),
            "Vary": "X-A2A-Extensions",
        },
    )
```

Cache key extended to include the negotiated tuple — different clients can
get different cards from the same TTL window without poisoning each other.

### Part 2 — Frontend: rewrite the `url` field to the public origin

**File:** `frontend/src/app/.well-known/agent.json/route.ts`

The G39 route already proxies headers byte-for-byte. We extend it to JSON-parse
the upstream response and rewrite the `url` field to the public origin. The
backend can't know its public URL when it's a sidecar; the Next.js layer is
the only place that does.

Public-origin derivation (in priority order):
1. `X-Forwarded-Proto` + `X-Forwarded-Host` headers (Cloud Run GFE always
   sets these — strict edge of the topology)
2. `Host` header + scheme from `req.nextUrl.protocol`
3. `req.nextUrl.origin` as final fallback

Non-JSON / non-2xx responses pass through untouched so error bodies
aren't accidentally rewritten.

### Part 3 — `scripts/verify-a2a.sh` — 12-check compliance probe

A standalone shell script that fetches the card with a negotiation header
and asserts:

1. HTTP 200 on unauthenticated discovery
2. `X-A2A-Extensions` echoed on response
3. `Vary` advertises `X-A2A-Extensions`
4-10. Required card fields (`protocolVersion`, `name`, `description`,
      `url`, `version`, `capabilities`, `skills`)
11. URL is NOT `localhost` / `127.0.0.1`
12. `capabilities.extensions[]` are `AgentExtension` objects with `uri`
13. At least one extension matches `a2a-v0.2`
14. `skills[]` is non-empty (warning if empty — may be intentional in early fork)

Skip-don't-fail on missing `curl`/`jq`. Exit code 1 on any assertion failure
→ CI-gate ready.

### Part 4 — Test strengthening

`backend/tests/api_tests/test_a2a.py` gets:
- `_extension_ids()` helper that reverse-lookups URIs to IDs via
  `SUPPORTED_EXTENSION_INFO` — centralised so a future v0.3 shape change
  edits one place.
- Updated `test_agent_card_returns_minimum_a2a_fields` requires
  `protocolVersion == "0.2.0"`.
- New `test_agent_card_advertises_extensions_as_descriptors` asserts each
  entry is `{uri, description, required}`.
- New `test_agent_card_negotiates_extensions_via_header` asserts the
  response header echoes the negotiated subset + Vary is set.
- New `test_agent_card_default_advertises_all_extensions` covers the
  empty-client-header → advertise-all default.

### CLI Surface

No new commands. `make verify-a2a` target added to the root Makefile.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add SUPPORTED_EXTENSION_INFO + tuple + descriptor + negotiation helpers in `protocols/a2a.py` | 30min |
| 2 | Add protocolVersion + extensions to `_build_card`; update cache key signature | 20min |
| 3 | Update route to FastAPI `Request`/`JSONResponse`; emit Vary + X-A2A-Extensions response headers | 20min |
| 4 | Update tests: existing assertion strengthening + 3 new tests | 40min |
| 5 | Frontend route: rewrite `url` to public origin via `X-Forwarded-Proto`/`X-Forwarded-Host` | 30min |
| 6 | Frontend tests: assert url-rewrite + JSON-only / non-2xx pass-through | 30min |
| 7 | `scripts/verify-a2a.sh` + Makefile target | 30min |
| 8 | New design doc + SEQUENCE.md row | 20min |

**Total: ~3.5h** (actual run-time).

## Testing Strategy

**Backend** (`tests/api_tests/test_a2a.py`):
- `test_agent_card_returns_minimum_a2a_fields` — strengthened to require `protocolVersion == "0.2.0"`.
- `test_agent_card_advertises_extensions_as_descriptors` — every `capabilities.extensions[]` entry is `dict` with `uri`.
- `test_agent_card_negotiates_extensions_via_header` — passing `X-A2A-Extensions: a2ui-v0.9, a2ui-decoupled-pattern` filters the response to those two; `Vary: X-A2A-Extensions` set.
- `test_agent_card_default_advertises_all_extensions` — no client header → response carries all `SUPPORTED_EXTENSIONS`.
- `test_agent_card_negotiation_ignores_unsupported_extensions` — client requests `a2ui-v0.9, made-up-protocol` → response contains only `a2ui-v0.9`.

**Frontend** (`frontend/src/app/.well-known/agent.json/__tests__/route.test.ts`):
- `test_route_rewrites_url_to_x_forwarded_origin` — pass `X-Forwarded-Proto: https` + `X-Forwarded-Host: example.com`; assert `card.url === "https://example.com"`.
- `test_route_falls_back_to_host_header_when_x_forwarded_absent` — no X-Forwarded; uses `Host` header + `req.nextUrl.protocol`.
- `test_route_passes_non_json_response_through_unchanged` — backend returns 502 with HTML body; assert response is the original bytes.
- `test_route_preserves_x_a2a_extensions_round_trip` — regression on G39.

**Integration** (`scripts/verify-a2a.sh`):
- Run against a local `make dev` → exit 0.
- Run against a deployed dev URL → exit 0.
- Tampered backend (deliberately remove `protocolVersion`) → exit 1 with the missing-field message.

**Live test** (manual, requires gcloud auth + Discovery Engine app):
```bash
agents-cli register-gemini-enterprise \
  --registration-type a2a \
  --agent-card-url https://<fork-host>/.well-known/agent.json \
  --gemini-enterprise-app-id projects/<num>/locations/global/collections/default_collection/engines/<engine-id> \
  --display-name "<your agent>" \
  --deployment-target cloud_run
# → expect: HTTP 200 + registration confirmation.
```

## Success Criteria

- [ ] `protocolVersion: "0.2.0"` at the card root.
- [ ] `capabilities.extensions[]` is `AgentExtension[]`; every entry has `uri`.
- [ ] Frontend route rewrites `url` to the public origin via
      `X-Forwarded-Proto` + `X-Forwarded-Host`.
- [ ] `X-A2A-Extensions` request header negotiates the response's
      advertised set; `Vary: X-A2A-Extensions` is set.
- [ ] `scripts/verify-a2a.sh` exits 0 on the platform's deployed dev card.
- [ ] All 6 existing test_a2a.py cases still pass; 4 new ones cover
      protocolVersion, descriptors, negotiation, and the all-extensions
      default.
- [ ] Frontend route tests cover the url-rewrite + pass-through paths.
- [ ] `make verify-a2a` target exists in the root Makefile.
- [ ] **Live test**: `agents-cli register-gemini-enterprise` against a
      Discovery Engine app returns 200 on first try.
- [ ] **Template sync pending**: next `aitana-template-publish` run
      propagates to `sunholo-data/ai-protocol-platform`.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — G43 row
- [template-fork-ergonomics.md G39](./template-fork-ergonomics.md) — the
  Next ingress proxy this design extends
- [template-agui-terminal-dedup.md G41](./template-agui-terminal-dedup.md) —
  sibling "fork registration / discovery failure surfaced only at the
  integration boundary" doc
- gde-ap-agent fork commits `236fdcb` + `dbc5856` — source of the URL-rewrite
  pattern + protocolVersion fix + verify-a2a.sh
