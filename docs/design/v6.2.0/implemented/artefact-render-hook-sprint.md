# Sprint Plan: ARTEFACT-REVIEW-HOOK — v6.2.0 Sprint 2.13

## Summary

Add a pluggable content-review hook to the artefact render pipeline,
as designed in [artefact-render-hook.md](artefact-render-hook.md).
Forks plug their own `ArtefactReviewer` (e.g. AIPLA's static-analysis
ruleset). The platform ships a permissive default — existing demos
render unchanged.

This is the third AIPLA template-extension after sprint 2.11
([anonymous-group-id-auth.md](implemented/anonymous-group-id-auth.md))
and sprint 2.12
([budget-enforcement.md](implemented/budget-enforcement.md)).
Smaller blast radius than its predecessors — frontend-leaning, no new
runtime gate hot-path.

**Duration:** 0.5 focused day + 0.2d buffer
**Scope:** TS Protocol + permissive default + router consult + refusal/warn UI + Python Protocol mirror + optional proxy interception + howto + smoke
**Dependencies:**
- [MCPAppToolCallRouter.tsx](../../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx) — sprint 1.7 shipped; this hook lives between resource resolve and `<AppRenderer>`
- [backend/protocols/mcp_proxy.py](../../../../backend/protocols/mcp_proxy.py) — sprint 1.7 M3 shipped; currently a dumb byte-forwarder. Server-side interception inspects JSON-RPC `resources/read` replies before forwarding
- The sandbox proxy + CSP layer — already in place. **This hook is ABOVE the sandbox** (defence-in-depth, not replacement)

**Risk Level:** Low-medium — small surface, well-bounded. Main risk is the M3 proxy patch: need to JSON-RPC-parse responses without breaking the current opaque-bytes pass-through. Mitigation: consult only when a server-side reviewer is registered AND the request method is `resources/read`; everything else passes through unchanged.

**Design Doc:** [artefact-render-hook.md](artefact-render-hook.md) — full Protocol contract, axiom alignment (+5 net), security considerations, performance budget (≤100ms per consult).

**Deadline:** AIPLA need-by week 6 ≈ early July 2026 (~6 weeks out). 0.5-day estimate; massive slack.

## Velocity Context (recent sprint days)

- Sprint 2.11 (anon-group-auth): 2d est / ~0.5d actual code + 2340 LOC actual vs 1490 estimated (+57%)
- Sprint 2.12 (budget-enforcement): 1.5d est / ~0.4d actual code + 2605 LOC actual vs 1845 estimated (+41%)
- Sprint 2.13's gate surface is smaller (no 8-gate matrix — just 4 paths: approve / warn / block / reviewer-crash). Expect overshoot ~+15-25% rather than 40-60%.
- **Implication for 2.13:** ~770 LOC raw — expect ~900-1100 actual. Time should land at ~0.2-0.3 days.

## Milestone Breakdown

### M1 — Frontend Protocol + permissive default + tests (`frontend`, ~0.15d)

**Files**
- `frontend/src/components/protocols/ArtefactReviewer.ts` (~80 LOC) — `ArtefactReviewer` TS interface (async `review(input: ArtefactReview): Promise<ArtefactDecision>`); `ArtefactReview` shape (toolName, serverId, resourceUri, html, csp, structuredContent, invocationId); `ArtefactDecision` discriminated union (`{action:"approve"}` / `{action:"warn", message, reasonCode}` / `{action:"block", message, reasonCode, appealUrl?}`); registry (`setArtefactReviewer` / `getArtefactReviewer` / `clearArtefactReviewer`); shipped `PermissiveArtefactReviewer` that returns approve.
- `frontend/src/components/protocols/__tests__/ArtefactReviewer.test.ts` (~80 LOC) — registry get/set; default-when-unregistered; clearArtefactReviewer resets to default; async review path; discriminated-union narrowing.

**Acceptance criteria**
1. `ArtefactReviewer` is a TypeScript interface with `async review(input: ArtefactReview): Promise<ArtefactDecision>` — duck-typed; forks implement without inheritance
2. `ArtefactDecision` is a discriminated union — `decision.action === "block"` narrows to the block variant with `message + reasonCode + appealUrl?`
3. `PermissiveArtefactReviewer` returns `{action: "approve"}` for any input — preserves current behaviour (existing Cesium demo unaffected)
4. `getArtefactReviewer()` returns the permissive default when nothing is registered
5. `setArtefactReviewer(impl)` replaces the default; `clearArtefactReviewer()` resets to default
6. `ArtefactReview` shape mirrors the Python `ArtefactReview` shape exactly (camelCase ⟷ snake_case on the wire; same field set)

**Risk:** None — pure types + permissive default. Existing demos use the permissive default by absence.

### M2 — Router consult + refusal/warn UI (`frontend`, ~0.2d)

**Files**
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` patch (~40 LOC) — after `setResource({ html, csp })` resolves but BEFORE rendering `<AppRenderer>`, consult `getArtefactReviewer().review({...})` with the resolved html + metadata; switch on `decision.action`:
  - `approve` → `<AppRenderer>` unchanged
  - `warn` → `<ArtefactWarningStripe message={decision.message}>` wrapping `<AppRenderer>`
  - `block` → `<ArtefactRefused decision={decision}>` instead of `<AppRenderer>` (no iframe mount)
  - reviewer throws → catch + fall back to approve + `console.error` (dev-only) + render normally; sandbox is the safety net
  - reviewer takes >500ms → log at warn level via `Promise.race` + `setTimeout`; degrade to approve
- `frontend/src/components/protocols/ArtefactRefused.tsx` (~80 LOC) — renders block message + reasonCode chip + optional appeal link; on mount fires audit POST (`/api/sessions/{id}/surface-action` with `event_type: "artefact_blocked"`); `role="alert"` + `aria-live="assertive"` for screen reader announcement
- `frontend/src/components/protocols/ArtefactWarningStripe.tsx` (~50 LOC) — yellow-bordered wrapper component; renders the message above the child artefact; `role="status"` + `aria-live="polite"`
- `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.review.test.tsx` (~120 LOC) — 4 paths × renders correctly + 2 fallback paths (crash + timeout)

**Acceptance criteria**
1. **Approve path:** reviewer returns `{action:"approve"}` → `<AppRenderer>` renders unchanged; no warning stripe, no refusal panel
2. **Warn path:** reviewer returns `{action:"warn", message, reasonCode}` → `<ArtefactWarningStripe>` wraps `<AppRenderer>`; both are mounted; stripe shows the message verbatim
3. **Block path:** reviewer returns `{action:"block", message, reasonCode, appealUrl?}` → `<ArtefactRefused>` renders; `<AppRenderer>` is NOT mounted (iframe never loads — verified via DOM query for `iframe` not present)
4. **Reviewer-crash path:** reviewer throws → fall back to approve + `console.error` in dev; `<AppRenderer>` renders normally. Sandbox is the safety net; a buggy reviewer must NOT break the platform
5. **Slow-reviewer degradation:** if `review()` exceeds 500ms, log at warn level + degrade to approve (soft budget, not a hard timeout — Promise.race style)
6. **ArtefactRefused audit POST:** on mount, fires `POST /api/sessions/{id}/surface-action` with `{event_type: "artefact_blocked", tool_name, server_id, reason_code}` — block path is the only record of what was refused
7. **Accessibility:** ArtefactRefused has `role="alert"` + `aria-live="assertive"` (the user MUST notice); ArtefactWarningStripe has `role="status"` + `aria-live="polite"` (informational)
8. **Back-compat:** with no reviewer registered (default permissive), the existing Cesium-map MCP-app demo still renders unchanged (regression test using the existing fixture)

**Risk:** The 500ms soft timeout via `Promise.race` is the only non-trivial pattern; verify the cleanup of the lingering reviewer Promise (no zombie state mutations after timeout).

### M3 — Backend Protocol + optional proxy interception (`backend`, ~0.15d)

**Files**
- `backend/protocols/artefact_review.py` (~110 LOC) — Python `typing.Protocol` `ArtefactReviewer` (`@runtime_checkable`, async `review`); `ArtefactReview` + `ArtefactDecision` frozen dataclasses (Literal["approve"|"warn"|"block"] on action); `BlockedArtefactError` exception carrying the decision; registry (`register_artefact_reviewer` / `get_registered_artefact_reviewer` / `clear_registered_artefact_reviewer`)
- `backend/protocols/mcp_proxy.py` patch (~80 LOC) — in `_forward`, after the upstream response lands BUT before returning: if `get_registered_artefact_reviewer()` is set AND the request body's JSON-RPC method is `"resources/read"` AND the upstream response is a JSON-RPC result with `result.contents[].mimeType ~= "text/html"`, run the reviewer on the HTML content; on block, return a structured `403` with `{type: "artefact_blocked", message, reason_code, appeal_url?}` body (the frontend handles this as if it were a client-side block). Everything else passes through unchanged.
- `backend/tests/unit/test_artefact_review.py` (~80 LOC) — Protocol conformance (`runtime_checkable` isinstance against duck types); registry set/get/clear; dataclass shape; `BlockedArtefactError` carries decision
- `backend/tests/api_tests/test_mcp_proxy_artefact_review.py` (~120 LOC) — server-side reviewer NOT registered → bytes pass through unchanged (back-compat); reviewer registered + approve → response forwarded; reviewer registered + block on `resources/read` → 403 with structured decision; non-`resources/read` JSON-RPC ignores reviewer entirely (only HTML artefacts are subject to content review)

**Acceptance criteria**
1. `ArtefactReviewer` is a `runtime_checkable` `typing.Protocol` — `isinstance(impl, ArtefactReviewer) == True` for duck-typed impls with just `async def review(...)`
2. `ArtefactReview` + `ArtefactDecision` are frozen dataclasses; Literal action accepts exactly `"approve" | "warn" | "block"`
3. `register_artefact_reviewer` validates Protocol shape (raises `TypeError` if not satisfied — same pattern as `register_budget_enforcer`)
4. **Back-compat:** with no reviewer registered, `mcp_proxy._forward` is byte-identical to current behaviour (regression test against the existing proxy test fixtures)
5. **Approve path (server-side):** registered reviewer returns approve → response forwarded unchanged
6. **Block path (server-side):** registered reviewer returns block on `resources/read` response → 403 with body `{type:"artefact_blocked", message, reason_code, appeal_url?}` (parseable by the frontend's ArtefactRefused mount handler)
7. **Scope guard:** only JSON-RPC methods named `resources/read` AND responses with `mimeType` starting with `text/html` trigger the reviewer. Tool calls, prompts, etc. pass through untouched.
8. **Reviewer crash fails open:** if `await reviewer.review(...)` raises, log the exception + forward the original response (sandbox is the safety net; same fail-open posture as the frontend)
9. **Performance log:** if `review()` exceeds 100ms, emit a warn-level log line `{tool_name, server_id, html_size, duration_ms}` so fork operators can tune their impls

**Risk:** JSON-RPC body parsing. The mcp_proxy currently treats bodies as opaque bytes. M3 needs to parse the REQUEST body to read `method`, and the RESPONSE body to read `result.contents[]`. Use `json.loads` with try/except — non-JSON or malformed bodies short-circuit to pass-through (back-compat preserved).

### M4 — Howto + smoke + audit-table flip + implemented/ move (`fullstack`, ~0.1d)

**Files**
- `docs/integrations/artefact-review-hooks.md` (~200 LOC) — fork adoption howto: TS interface, registering a reviewer at app bootstrap, AIPLA-style static-analysis sketch (htmlparser2 with `eval`/`fetch`/inline-handler bans + tag allow-list + size limit), client-side vs server-side trade-off (client = low overhead, server = unbypassable), audit log shape, performance budget, soft-degradation rules
- `docs/talks/ai-ui-protocol-stack.md` — flip the sprint 2.13 audit-row from 📋 → ✅ with verification-log entry mirroring sprint 2.12's row
- `docs/design/v6.2.0/SEQUENCE.md` — row 2.13 marked shipped; doc link → `implemented/`
- `docs/design/v6.2.0/artefact-render-hook.md` + `-sprint.md` → moved to `implemented/`; relative-path depth fixed (`../../../` → `../../../../` for backend refs)
- `.dev-logs/artefact-review-hook-smoke.txt` — chain-of-evidence summary (the test suite IS the smoke; documenting why a chrome screenshot would re-prove the test coverage)

**Acceptance criteria**
1. **Smoke A (block on `<script>` tag):** plug a stub reviewer that blocks any HTML containing `<script>` → run an existing MCP-App test fixture → observe `<ArtefactRefused>` rendered + `<AppRenderer>` NOT mounted (Vitest)
2. **Smoke B (approve baseline):** with the permissive default → existing Cesium-map demo renders unchanged (regression)
3. **Smoke C (server-side block via 403):** register a backend reviewer that blocks → frontend receives 403 with structured body → renders ArtefactRefused (matches client-side block UX)
4. Howto covers: TS + Python interface mirror, registering a reviewer at bootstrap, AIPLA-style static-analysis sketch, client-side vs server-side trade-off, audit log shape, performance budget rules
5. Talk-doc audit row flipped 📋 → ✅ with verification-log entry
6. SEQUENCE.md row 2.13 marked shipped; doc link → `implemented/`
7. Design docs moved to `implemented/`; relative-path depth fixed

**Risk:** Chrome-devtools smoke isn't strictly necessary — the Vitest cases for the 4 paths + the backend pytest for the proxy patch cover the wire end-to-end. Documenting "test suite IS the smoke" follows the precedent set by sprint 2.12.

## Day-by-Day (0.5-day plan)

| Half-day | Slot | Work |
|---|---|---|
| Day 1 AM | 1 | M1: Protocol + types + registry + PermissiveArtefactReviewer + tests; close M1 |
| Day 1 AM | 2 | M2: Router consult + ArtefactRefused + ArtefactWarningStripe + 4-path + fallback tests; close M2 |
| Day 1 PM | 1 | M3: Backend Protocol + mcp_proxy patch + pytest; close M3 |
| Day 1 PM | 2 | M4: howto + smoke + audit-row flip + implemented/ move; close sprint |
| (buffer) | | 0.2d for the unknown — most likely the M3 JSON-RPC body parsing |

## Quality Gates (per milestone close)

```bash
# Frontend (M1 + M2)
cd frontend && npm run quality:check

# Backend (M3)
cd backend && make lint && make test-fast

# Full pre-push parity (M4)
cd backend && make lint && make test-fast
cd frontend && npm run quality:check
```

## Push Policy

- Commit at each milestone close (4 commits expected).
- DO NOT push until user confirms — per project pre-push review convention.
- Smoke verification via the test suite (chrome-devtools optional, not load-bearing).

## Open Questions (carried from design doc)

1. **Headless-render preview?** AIPLA mentioned it; heavier ask (Playwright in Docker). Punt to v2; the hook supports it as one impl shape.
2. **Async + cancellable?** If a reviewer takes 30s, can the user cancel? Tie to existing `AbortController` plumbing in `useSkillAgent.stop`. Document but defer the wire-up.
3. **Allow-list of registered reviewers?** Should the platform refuse to set a reviewer not in a known-good registry? Recommend: no — registration is a code call inside the fork's bootstrap, not a runtime config.

Resolution: none of these blocks AIPLA's early-July need-by.

## Out of Scope (explicitly)

- **AIPLA's specific ruleset.** Their static-analysis rules go in their fork; the platform ships only the hook + permissive default.
- **Replacing the sandbox proxy.** The hook is ABOVE the sandbox — it inspects, then the sandbox runs. Both layers stay.
- **Reviewing A2UI specs.** They're inert JSON; the SDK validator + v0.9 schema already covers them.
- **Server-side mandatory review.** Protocol is offered both client- and server-side; forks pick where their reviewer runs.
- **Headless-render preview.** Heavier infrastructure (browser-in-Docker); v2.
