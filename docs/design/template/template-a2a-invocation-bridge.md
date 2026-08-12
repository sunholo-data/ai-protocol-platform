# Template A2A Invocation Bridge (G45)

**Status**: Implemented
**Priority**: P0
**Estimated**: 1-2d ; ~3h actual (Sprint A2A-INVOKE, 2026-06-08)
**Scope**: Backend + Frontend + Scripts + Cloud Build + Tests
**Dependencies**: G43 (A2A discovery spec compliance — shipped 2026-06-07)
**Created**: 2026-06-08
**Last Updated**: 2026-06-08
**Source items**: gde-ap-agent fork A2A-INVOKE sprint, 2026-06-07 (commits abe9bc9 → 5774cb0); see `<local-path>` §5 (5a–5h) + Frictions 25, 26, 27. **Friction 25** (Next.js owns ingress; `/a2a/*` falls through to its catch-all 404), **Friction 27** (`to_a2a()` Starlette app loses lifespan when mounted on FastAPI), and **§2** (URL path preservation in well-known proxy) are the three coupled fork-blocking findings this doc closes.

## Problem

G43 (shipped 2026-06-07) brought the template to A2A-**discovery**-compliant: peers can fetch the agent card, Gemini Enterprise accepts the card for registration, the card validates against Discovery Engine's strict schema. But a registered card whose `url` field returns HTTP 405 on `POST /` is registered-but-broken: peers see the agent, click invoke, get a hard error.

The gap between "discovery" and "invocation" is the missing JSON-RPC endpoint where peers POST `message/send`, `message/sendSubscribe`, `tasks/get`, `tasks/cancel` per the A2A v0.2 spec. ADK ships `google.adk.a2a.utils.agent_to_a2a.to_a2a()` to fill this gap — but mounting it on a FastAPI sub-app silently 404s every request because Starlette doesn't propagate lifespan events to sub-apps, and `to_a2a()` registers its routes via lifespan.

Three coupled fork-blocking findings the gde-ap-agent fork hit during their 5h A2A-INVOKE sprint, each invisible to unit tests:

1. **Friction 27** — `to_a2a(agent)` returns a Starlette app whose A2A routes are registered via a **lifespan event**. Mounted on FastAPI as a sub-app, the lifespan never fires; every `POST /a2a/*` returns 404. ADK's docstring suggests mount should work; it doesn't.

2. **Friction 25** — Next.js owns the public ingress on Cloud Run multi-container deploys. `/a2a/*` falls through to its catch-all 404 before FastAPI sees it. Symptom: backend mount healthy, env vars correct, every external POST 404s with `x-nextjs-cache: HIT` in the response headers.

3. **§2 (URL path preservation in well-known proxy)** — The G43 proxy at `frontend/src/app/.well-known/agent.json/route.ts` rewrites `card.url` with bare `publicOrigin(req)`, stripping any path. The backend's new `url = base_url/a2a` gets silently truncated to `url = https://host` at the ingress; peers POST to the wrong place.

## Goal

End-state: peer agents and Gemini Enterprise can POST strict A2A `message/send` JSON-RPC against the platform's deployed card and have it execute through ADK on our existing sessions/memory/artifact storage.

Success metric: `python3 scripts/simulate-a2a-peer.py` shows green at Step 4 ("HTTP 200 — strict A2A invocation works") against the deployed URL.

## Axiom Alignment (+8)

| Axiom | Score | Reasoning |
|---|---|---|
| INSTANT FEEL | +1 | Streaming preserved via `message/sendSubscribe`; same backing services (no extra hop) |
| EARNED TRUST | +2 | "Registered but broken" is a worse trust signal than "not registered" — closing the gap removes a class of post-registration silent failure |
| SKILLS NOT FEATURES | +1 | Skills become callable from outside the platform's UI, not just from inside it |
| RIGHT MODEL RIGHT MOMENT | 0 | Orthogonal |
| GRACEFUL DEGRADATION | +1 | Mount wrapped in try/except; partial init degrades to discovery-only, doesn't kill /api/* |
| PROTOCOL OVER CUSTOM | +2 | A2A v0.2 message/send is the spec; ADK's A2aAgentExecutor + a2a-sdk's A2AStarletteApplication are the canonical primitives |
| API FIRST | +1 | This IS the API — JSON-RPC at /a2a |
| OBSERVABLE | 0 | OTel traces still flow through Runner; nothing new |
| SECURE BY CONSTRUCTION | +1 | A2AAuthMiddleware enforces Bearer auth on invocation paths; returns JSON-RPC error envelopes (not HTML 401) so strict clients can parse the failure |
| THIN CLIENT FAT PROTOCOL | 0 | Orthogonal |
| **Net** | **+8** | Strong signal |

## Design

### Three-layer fix

```
PEER AGENT / Gemini Enterprise
        │
        │  POST https://<host>/a2a    (JSON-RPC message/send)
        ▼
NEXT.JS INGRESS (frontend/next.config.mjs)
        │   ← G45 layer 1: async rewrites() catches /a2a + /a2a/:path*
        │     → http://127.0.0.1:1956/a2a/:path*  (sidecar passthrough)
        ▼
FASTAPI SIDECAR (backend/fast_api_app.py)
        │   ← G45 layer 2: env-gated app.mount("/a2a", build_a2a_app(...))
        │     mount block AFTER include_router calls (order matters)
        ▼
STARLETTE SUB-APP (backend/protocols/a2a_invocation.py)
        │   ← G45 layer 3: build_a2a_app() — synchronous setup that
        │     wires A2AStarletteApplication + DefaultRequestHandler +
        │     A2aAgentExecutor at construction time (NOT via lifespan
        │     — works around Friction 27). Wraps A2AAuthMiddleware so
        │     auth failures return JSON-RPC envelopes, not HTML 401.
        ▼
ADK RUNNER (existing — same session/memory/artifact singletons)
        │
        └─→ root_agent (existing — backend/app.py)
```

### Why not use `to_a2a()` directly?

`to_a2a()` uses a Starlette lifespan event to call `setup_a2a()` which creates the `A2AStarletteApplication` and calls `add_routes_to_app(app)`. Starlette does NOT propagate lifespan to mounted sub-apps. The lifespan never fires, the routes never register, every request returns 404.

`build_a2a_app()` replicates the same setup synchronously using the same `a2a-sdk` building blocks ADK itself uses (`A2AStarletteApplication`, `DefaultRequestHandler`, `InMemoryTaskStore`, `InMemoryPushNotificationConfigStore`, ADK's `A2aAgentExecutor`). All routes register at construction time, mounting works as expected.

### Card consistency: discovery card vs invocation card

The discovery card at `/.well-known/agent.json` is what peers fetch FIRST. ADK's `to_a2a` also serves its own card at `/a2a/.well-known/agent.json` for backwards-compat. The two cards must be byte-identical or peers see drift. Solution:

- `_build_card_dict(base_url)` — the canonical wire-shape, single source of truth
- `_build_card_model(base_url)` — pydantic-validated wrap of the dict, passed to `A2AStarletteApplication(agent_card=...)` so the ADK-mounted card matches

The `iconUrl`, `protocolVersion`, AgentExtension descriptors, and skill catalogue all stay in sync.

### Auth: JSON-RPC error envelopes, not HTML 401

A strict A2A client expects JSON-RPC 2.0 envelopes (`{jsonrpc, id, error: {code, message}}`) even at the auth layer. A bare HTTP 401 with an HTML body would crash a peer that's parsing every response as JSON-RPC. `A2AAuthMiddleware` (Starlette `BaseHTTPMiddleware`) runs `get_current_user` on invocation paths; on auth failure returns a proper JSON-RPC envelope with code -32000 (server error reserved range — A2A v0.2 doesn't define a dedicated auth code).

Discovery paths (`/.well-known/agent.json`, `/.well-known/agent-card.json`) skip auth per A2A spec. Gated by `A2A_INVOCATION_REQUIRE_AUTH` (default true; set false in dev / for some Gemini Enterprise auth modes that inject service identity differently).

## Implementation

| Item | File | LOC | Notes |
|---|---|---|---|
| Sync `build_a2a_app()` + `A2AAuthMiddleware` | `backend/protocols/a2a_invocation.py` (NEW) | 261 | Verbatim port from gde-ap-agent fork; imports + singleton names already align |
| Card url → `{base}/a2a` | `backend/protocols/a2a.py` | 1-line | Constants + helpers (`A2A_INVOCATION_PATH`, `_build_card_dict`, `_build_card_model`) prepped as dead code in da937aa (2026-06-07); G45 wires them |
| Env-gated mount | `backend/fast_api_app.py` | 24 | Mount block AFTER all include_router calls; try/except around degraded-mode |
| Path-preserving URL rewrite | `frontend/src/app/.well-known/agent.json/route.ts` | 22 | Parse upstream URL, swap origin only, preserve path |
| Next.js `/a2a` rewrites | `frontend/next.config.mjs` | 11 | Friction 25 fix; rewrites are streaming-safe |
| Cloud Build env vars | `cloudbuild.yaml` | 6 | `ENABLE_A2A_INVOCATION=true`, `A2A_INVOCATION_REQUIRE_AUTH=false`, `PUBLIC_BASE_URL=<deployed host>` |
| 6 integration tests | `backend/tests/api_tests/test_a2a_invocation.py` (NEW) | 226 | Critical test: mount via FastAPI TestClient, assert /a2a/.well-known/agent.json returns 200 (regression guard for Friction 27) |
| Peer probe | `scripts/simulate-a2a-peer.py` (NEW) | 250 | 6-step stdlib-only peer simulation; AP_URL env var |
| POST probe | `scripts/verify-a2a.sh` | +54 | Existing discovery-only probe gains a POST invocation check at tail |
| Route.ts path test | `frontend/src/app/.well-known/agent.json/__tests__/route.test.ts` | +48 | Existing FAKE_CARD url updated to include /a2a; +2 new tests (positive + defensive fallback) |

Total: ~900 LOC code + ~300 LOC tests / docs across 10 files in commit `1f743d8`.

## Testing

### Pre-deploy

- Backend `make lint && make test-fast` — **1416 passed**, no regression from G43's 11 tests
- Frontend `npm run quality:check` (full, CI parity) — **574 passed**, lint + typecheck + production build green

### Local

```bash
make dev    # backend :1956 + frontend :3456 + Next.js rewrite

# Direct backend (sidecar):
curl http://localhost:1956/a2a/.well-known/agent.json   # → 200, url ends in /a2a

# Through Next.js proxy (the actual production path):
curl http://localhost:3456/a2a/.well-known/agent.json   # → 200 (rewrites pass through)
curl http://localhost:3456/.well-known/agent.json       # → 200, url ends in /a2a (path preserved)

# /api/* still works (mount order didn't swallow):
curl http://localhost:1956/api/local-mode-status         # → 200
```

### Post-deploy

```bash
# Discovery + invocation probe in one shot:
AP_URL=https://your-service-url.example ./scripts/verify-a2a.sh

# Full peer simulation (the gold-standard "does this work end-to-end" test):
AP_URL=https://your-service-url.example python3 scripts/simulate-a2a-peer.py
# → expect Step 4 green: "HTTP 200 — strict A2A invocation works"
```

### CI

The two new tests in `test_a2a_invocation.py` — particularly `test_build_a2a_app_returns_mountable_starlette_app` — are the regression guards. Without them, removing the synchronous `add_routes_to_app(a2a_app)` call would silently re-introduce Friction 27.

## Migration / fork impact

**Forks on the template** — no breaking change. If `ENABLE_A2A_INVOCATION` env var is unset (default `false`), the mount block is skipped; everything works exactly as G43. Forks opt in by adding the three env vars to their cloudbuild.yaml.

**Forks already using the gde-ap-agent pattern** — this template now ships the same primitives. They can drop their fork-local `a2a_invocation.py` in favour of `from protocols.a2a_invocation import build_a2a_app` and stay in sync with future template updates.

**Workshop attendees (July 2026)** — every fresh `git clone` of the public template now ships the A2A invocation bridge. Their first-deploy Gemini Enterprise registration becomes invocable, not just discoverable — closing the demo gap that today's G43 left.

## Related Documents

- [template-a2a-spec-compliance.md](template-a2a-spec-compliance.md) — G43 discovery layer (this doc builds on it)
- [template-fork-ergonomics.md](template-fork-ergonomics.md) — covers G44 (dual-cloudbuild discoverability, the deploy-model precondition)
- [docs/integrations/gemini-enterprise.md](../../integrations/gemini-enterprise.md) — operator's guide for registering against Gemini Enterprise; Friction 28 (agents-cli dedupe bug) workaround noted in Troubleshooting
- [docs/ops/deployment-models.md](../../ops/deployment-models.md) — the single-service-sidecar vs paired-services choice; A2A invocation makes the choice less reversible (peers cache the card.url path)
- Source reference: `<local-path>` (1170 lines, end-to-end fork-perspective brief)

## Implementation Report (Sprint A2A-INVOKE)

| Metric | Estimated | Actual |
|---|---|---|
| Duration | 1-2d | ~3h |
| Total LOC | 1080 | 1303 |
| Milestones | 4 | 4 (M1+M2 by sub-agent, M3+M4 by parent) |
| Commits | 2-3 | 2 (M1-M3 bundle as 1f743d8; M4 docs as follow-up) |

The 20% LOC overrun came from the design doc + sprint plan being longer than estimated (the gde-ap-agent brief is comprehensive; faithful adaptation to our context produced more docs, not less). All implementation milestones came in under estimate — the gde-ap-agent fork's identical port path meant zero design re-decisions and no adapter glue.
