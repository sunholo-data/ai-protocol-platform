# Sprint A2A-INVOKE — A2A `message/send` invocation bridge

**Status**: Planned
**Sprint ID**: `A2A-INVOKE`
**G-number**: G45 (G44 collision avoided — G44 = dual-cloudbuild docs in commit `aa98031`)
**Duration**: 1–2 days
**Scope**: Backend + Frontend + Scripts + Tests + Cloud Build + Docs
**Source brief**: `<local-path>` §5 + Frictions 25, 26, 27
**Created**: 2026-06-07
**Workshop alignment**: 🔥 High — A2A invocation is the only way peer agents / Gemini Enterprise users can actually CALL platform skills. Today's G43 ships DISCOVERY only.

## Goal

Bring the platform from "A2A-discovery-compliant" (G43, shipped this
morning) to "A2A-invocation-compliant" by mounting an A2A JSON-RPC
endpoint at `/a2a` that handles `message/send`, `message/sendSubscribe`,
`tasks/get`, `tasks/cancel`. Peers / Gemini Enterprise can then invoke
the platform's skills directly through the strict A2A wire format.

End-state matches gde-ap-agent: `python3 scripts/simulate-a2a-peer.py`
shows green at Step 4 (strict A2A `message/send` works), agent's tools
become callable in the Gemini Enterprise UI.

## Reference implementation

gde-ap-agent has shipped this end-to-end (HEAD `d44f982`). Sprint is
mostly a PORT, not net-new design. Source files live at:

- `backend/protocols/a2a_invocation.py` (262 LOC, the new file)
- `backend/protocols/a2a.py` (already partially adapted in our `da937aa`)
- `backend/fast_api_app.py` (mount block, ~20 LOC addition)
- `backend/tests/api_tests/test_a2a_invocation.py` (6 tests)
- `frontend/src/app/.well-known/agent.json/route.ts` (url-path preservation)
- `frontend/next.config.mjs` (/a2a rewrites)
- `scripts/simulate-a2a-peer.py` (~150 LOC peer probe)
- `scripts/verify-a2a.sh` (extend with POST probe)

Their design doc at
`<local-path>`
captures all six surface decisions (URL mount, card authoring, skill
selection, streaming overlap, sessions, auth) with explicit
alternatives.

## Milestones

### M1 — Backend invocation surface (TDD-first, ~3h)

**Scope:** backend (4 files, 1 new + 3 edits)
**LOC:** ~400 impl + ~150 tests

Tasks:
1. Create `backend/tests/api_tests/test_a2a_invocation.py` with 6 tests
   FIRST (tests will fail until M1 lands). Critical: the integration
   test that mounts the sub-app via FastAPI `TestClient` and asserts
   `/a2a/.well-known/agent.json` returns 200 — that's the regression
   guard for Friction 27 (lifespan-loss-on-mount).
2. Create `backend/protocols/a2a_invocation.py` (~250 LOC). Port from
   gde-ap-agent verbatim except: import `APP_NAME` from our
   `adk.agui`, use our `get_session_service` / `get_memory_service` /
   `get_artifact_service` singletons. Lazy imports for `a2a-sdk` +
   ADK A2A executor (keeps discovery-card path light).
3. Update `backend/protocols/a2a.py`: change `_build_card`'s `url`
   field from `base_url` to `f"{base_url.rstrip('/')}{A2A_INVOCATION_PATH}"`.
   The `A2A_INVOCATION_PATH = "/a2a"` constant + `_build_card_dict` +
   `_build_card_model` were added as dead code in commit `da937aa`;
   this wires the URL change so peers POST to the right place.
4. Run `cd backend && make lint && make test-fast` — all 6 new tests
   pass + the 11 existing a2a tests still pass (G43 regression guard).

**Acceptance:**
- 6 new tests pass; 11 existing a2a tests still green
- `_build_card("http://x.com")` returns `url == "http://x.com/a2a"`
- `build_a2a_app(agent, "http://x.com")` returns a `Starlette` app
  that mounts cleanly under FastAPI and serves
  `/a2a/.well-known/agent.json` with HTTP 200

**Risk:** ADK's `A2aAgentExecutor` is `@a2a_experimental` — pin
`google-adk` version in `backend/pyproject.toml`; re-verify on minor
bumps. Mitigation: lock to whatever version gde-ap-agent's working
deploy uses.

### M2 — Frontend ingress + mount (TDD-first, ~2h)

**Scope:** backend (1 file) + frontend (2 files)
**LOC:** ~50 impl + ~30 tests

Tasks:
1. Update `backend/fast_api_app.py`: import + mount block guarded by
   `os.environ.get("ENABLE_A2A_INVOCATION") in ("true", "1", "yes")`.
   Use `root_agent` from `app.py` as the A2A entry agent. Wrap in
   try/except so partial init doesn't break `/api/*` routes.
2. Update `frontend/src/app/.well-known/agent.json/route.ts`: parse the
   upstream card's `url`, rewrite ONLY the origin (proto+host),
   preserve the path. Today's code at line 118 does
   `card.url = publicOrigin(req)` which strips `/a2a`. Fix per §2 of
   the brief. Add a Vitest case for the path-preservation behaviour.
3. Update `frontend/next.config.mjs`: add `async rewrites()` returning
   `[{source: '/a2a', destination: '${backend}/a2a/'}, {source: '/a2a/:path*', destination: '${backend}/a2a/:path*'}]`.
   Friction 25 — without this, Next.js's catch-all 404s every
   `/a2a/*` request before FastAPI sees it.
4. Run `make dev`, hit `http://localhost:3456/a2a/.well-known/agent.json` —
   expect 200, body `url` ends in `/a2a`. Then
   `curl http://localhost:3456/.well-known/agent.json` — expect 200,
   body `url` ends in `/a2a` (not the bare host).
5. Run `cd frontend && npm run quality:check` (CI parity, not :fast).

**Acceptance:**
- Local: `curl localhost:3456/a2a/.well-known/agent.json` returns 200 + ADK-served card
- Local: `curl localhost:3456/.well-known/agent.json | jq -r .url` ends in `/a2a`
- Frontend test: route.ts preserves path when rewriting origin
- Frontend `npm run quality:check` green

**Risk:** Mount order matters in FastAPI — sub-app mount must happen
AFTER all `app.include_router(...)` calls. If not, the catch-all in
the A2A sub-app may swallow `/api/*` traffic.

### M3 — Deploy wiring + end-to-end verification (~2h)

**Scope:** Cloud Build + Scripts
**LOC:** ~150 new + ~30 edits

Tasks:
1. Update `backend/cloudbuild.yaml`'s `--set-env-vars` with
   `ENABLE_A2A_INVOCATION=true`, `A2A_INVOCATION_REQUIRE_AUTH=true`,
   `PUBLIC_BASE_URL=https://your-service-url.example`.
   Pre-existing cosmetic bash error at file tail (exit 127 after
   deploy succeeds) — investigate ONLY if it blocks; otherwise file
   as follow-up since it doesn't actually fail the deploy.
2. Port `scripts/simulate-a2a-peer.py` from gde-ap-agent. Keep
   generic (`AP_URL` env var). 6-step probe: fetch card → resolve
   url → POST `message/send` → assert response shape → poll task →
   render output.
3. Extend `scripts/verify-a2a.sh` with a POST invocation probe at the
   end: fetch card, POST minimal `message/send`, assert HTTP 200 +
   valid JSON-RPC envelope. Today's version is discovery-only.
4. Push to `dev`, wait for Cloud Build, then run
   `AP_URL=https://your-service-url.example python3 scripts/simulate-a2a-peer.py` —
   expect Step 4 green ("HTTP 200 — strict A2A invocation works").

**Acceptance:**
- Cloud Build green on both frontend + backend triggers
- `verify-a2a.sh` against deployed URL: all checks pass INCLUDING
  the new POST probe
- `simulate-a2a-peer.py` against deployed URL: 6/6 steps green
- Gemini Enterprise console: the agent's tools become "callable"
  (no longer 405 on invocation)

**Risk:** ADK's A2A executor may not accept the empty `Runner` if our
session-service singletons aren't fully initialised at mount time.
Mitigation: try/except in M2 task 1 logs the exception and continues
without /a2a mounted — degraded mode that still serves discovery.

### M4 — Docs + sprint close (~1h)

**Scope:** Docs only
**LOC:** ~200 new + ~30 edits

Tasks:
1. Create `docs/design/template/template-a2a-invocation-bridge.md`
   (the G45 design doc). Status: Planned → Implemented on close.
   Score axioms. Cover the 4 fork-blocking findings: Friction 27
   (lifespan loss), Friction 25 (Next.js ingress), Friction 26 (dual
   cloudbuild fork-residue — different from our two-service model
   but worth documenting), §2 (URL path preservation in well-known).
2. Update `docs/design/template/SEQUENCE.md`: add G45 entry. Verify
   G44 still resolves to commit `aa98031` (dual-cloudbuild docs).
3. Add Friction 28 dedupe workaround note to
   `docs/integrations/gemini-enterprise.md` Troubleshooting section
   (cite our open bug report to Google for `agents-cli`).
4. Sunholo Gemini Enterprise cleanup: delete the existing agent
   `5808413650389691505` (registered today for discovery-only),
   re-register with the new `/a2a` URL so it can actually be invoked.
   Manual API delete (per Friction 28 workaround) since `agents-cli`
   dedupe doesn't work.
5. Commit. Push. Mark sprint Implemented.

**Acceptance:**
- Design doc lands in `docs/design/template/`
- SEQUENCE.md has G45 entry pointing at this sprint's commits
- Sunholo Gemini Enterprise shows the agent with working tools
  (replace, not duplicate)

## Day-by-day plan

### Day 1 (4-6h) — M1 + M2

- Morning: M1 (TDD-first; 6 tests then port a2a_invocation.py)
- Afternoon: M2 (mount + Next.js rewrite + route.ts fix + local dev smoke)
- Validation gate before EOD: `make lint && make test-fast` + `npm run quality:check` + local dev `curl localhost:3456/a2a/...` returns 200

### Day 2 (3-4h) — M3 + M4

- Morning: M3 (cloudbuild env vars, push, wait deploy, run
  simulate-a2a-peer.py against deployed)
- Afternoon: M4 (design doc + SEQUENCE.md + Sunholo GE re-register
  cleanup)
- Final gate: `python3 scripts/simulate-a2a-peer.py` shows green
  Step 4 against `https://your-service-url.example`

## Success criteria

- [ ] All 6 new backend tests pass; 11 existing a2a tests still green
- [ ] `verify-a2a.sh` against deployed: all checks pass + new POST probe green
- [ ] `simulate-a2a-peer.py` against deployed: 6/6 steps green (esp. Step 4 — strict A2A `message/send`)
- [ ] Sunholo Gemini Enterprise: agent's tools callable (not 405)
- [ ] Design doc + SEQUENCE.md entry land in same sprint as code
- [ ] No regression: G43 discovery still works; existing `/api/*` routes still respond

## Scope cuts (if Day 2 proves tight)

In priority order — what to ship vs defer:

1. **Defer:** the cosmetic bash error in `backend/cloudbuild.yaml` tail
   (exit 127 after deploy succeeds). File as follow-up; deploy works
   despite it.
2. **Defer:** Friction 26 (dual cloudbuild) treatment in the new
   design doc — already covered by today's G44 doc commit
   (`docs/ops/deployment-models.md`). The design doc can just
   reference it.
3. **Defer:** Sunholo GE re-register cleanup (M4 task 4). The
   duplicate-agent state is annoying but not blocking; can be
   cleaned up any time post-sprint.
4. **Last resort:** Ship the design doc as a follow-up. The
   gde-ap-agent brief is comprehensive; we can write our adapted
   version after the code is live. But avoid this — design docs are
   how the next fork learns.

## Friction 28 dedupe workaround (sprint close-out)

After deploying, the existing Sunholo Gemini Enterprise registration
(agent `5808413650389691505`) will have a stale `url` field pointing
at the discovery-only host root. Re-registering with the new
`/a2a`-pointing card will create a SECOND agent (per Friction 28),
not update the existing one. Manual cleanup:

```bash
TOKEN=$(gcloud auth print-access-token)
ENGINE_BASE="https://discoveryengine.googleapis.com/v1alpha/projects/863281350736/locations/global/collections/default_collection/engines/gemini-enterprise-17808672_1780867216189"

# List
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: sunholo-gemini-enterprise" \
  "$ENGINE_BASE/assistants/default_assistant/agents" \
  | jq -r '.agents[] | "\(.displayName): \(.name | split("/") | last)"'

# Delete the stale one (agent 5808413650389691505)
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: sunholo-gemini-enterprise" \
  "$ENGINE_BASE/assistants/default_assistant/agents/5808413650389691505"

# Re-register with new card
agents-cli register-gemini-enterprise \
  --registration-type a2a \
  --agent-card-url https://your-service-url.example/.well-known/agent.json \
  --gemini-enterprise-app-id projects/863281350736/locations/global/collections/default_collection/engines/gemini-enterprise-17808672_1780867216189 \
  --display-name "Sunholo AI Protocol Platform" \
  --deployment-target cloud_run
```

## G-number housekeeping (one-time)

Commit `aa98031` (2026-06-07) claimed G44 for the dual-cloudbuild docs
(`docs/ops/deployment-models.md` + `backend/cloudbuild.yaml` preamble).
This sprint takes **G45** to avoid collision. The G44 commit is
docs-only and doesn't need to change.

## Risks summary

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ADK A2A executor version drift | Medium | Sprint blocked | Pin `google-adk` version |
| Mount order swallows `/api/*` traffic | Low | Production outage | Test order in M2 task 1; integration test catches |
| `simulate-a2a-peer.py` fails against deployed (env-var mismatch) | Medium | M3 delayed | Local dev smoke FIRST in M2 |
| Sunholo dedupe leaves stale agent | High | Cosmetic console clutter | Manual delete in M4 task 4 |
| Cloudbuild bash error blocks deploy | Low | Sprint blocked | Pre-existing — already deploys successfully |
