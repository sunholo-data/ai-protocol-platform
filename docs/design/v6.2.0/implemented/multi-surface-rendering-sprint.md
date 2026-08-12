# Sprint Plan: MULTI-SURFACE-A2UI — v6.2.0 Sprint 2.9

## Summary

Adopt A2UI's first-class `surfaceId` semantic across backend + frontend so the agent can direct components to named surfaces (`chat`, `workspace`, `sidebar`, `modal`) instead of always rendering inline in the chat bubble. Strengthens workshop W6 demo, unblocks AIPLA fork's ADR-015, optional adoption path for a tutoring fork's teacher dashboard.

**Duration:** 4 calendar days (3 focused days + 0.5d for the A2UI-SDK extensibility spike + 0.5d buffer)
**Scope:** Backend schema + skill config extension (~25%) + frontend SurfaceRegistry + portal routing + patch semantics (~75%)
**Dependencies:** [a2ui-tool-delivery (1.0 ✅)](../../v6.1.0/implemented/a2ui-tool-delivery.md), [chat-message-rendering (1.1 ✅)](../../v6.1.0/implemented/chat-message-rendering.md), [document-ui layout (1.10 ✅)](../../v6.1.0/document-ui.md), `a2ui-agent-sdk` (current pin)
**Risk Level:** Medium — pivot risk on M1 (A2UI SDK may not support surface_id natively; wrapper-toolset fallback designed)
**Design Doc:** [multi-surface-rendering.md](multi-surface-rendering.md)

## Current Status Analysis

### Recent Velocity (last 7 days, 36 commits)
- 14087 insertions across 110 files — most of that the CHANNELS-FRAMEWORK sprint via parallel Task sub-agents
- Solo focused-day velocity (M1 LOCAL_MODE, M1 CHANNELS): ~1100-1700 LOC/day backend + tests
- Sub-agent parallel velocity (M2+M3 CHANNELS, M4+M5 CHANNELS): 2× to 3× compression possible for independent frontend/backend work
- Last sprint hit rate: 5/5 milestones PASS evaluator round 1

### Baseline Test State
- Backend: **1067 passed, 1 skipped** (`make test-fast`)
- Frontend: **391/391 passed** (`npm run test:run`)
- `make lint` clean

### Existing Implementation
- [`backend/adk/a2ui.py`](../../../../backend/adk/a2ui.py) — `make_a2ui_toolset()` returns `SendA2uiToClientToolset` from external `a2ui-agent-sdk` v0.2.1+
- [`frontend/src/components/protocols/A2UIRenderer.tsx`](../../../../frontend/src/components/protocols/A2UIRenderer.tsx) — 70 LOC, `<A2UIViewer>` from `@a2ui/react`, accepts `spec` + `onAction`, no surface concept
- [`frontend/src/components/chat/MessageBubble.tsx`](../../../../frontend/src/components/chat/MessageBubble.tsx) — mounts `A2UIRenderer` inline inside the chat bubble that produced the tool call
- [`frontend/src/app/chat/[...path]/page.tsx`](../../../../frontend/src/app/chat/[...path]/page.tsx) — split-pane layout: `aside w-64` (sidebar) + `w-1/2 DocumentPanel` + chat. Static regions, not addressable from agent.
- **Zero hits** on `surfaceId` / `surface_id` / `targetSurface` across `frontend/src/` and `backend/` — confirms gap

### Estimated Capacity
- Sprint target: ~2200-3000 LOC across 5 milestones
- 3 focused days × ~1100 LOC/day = ~3300 LOC budget — fits with buffer
- Parallelism opportunity: M2 (frontend SurfaceRegistry) and M1 (backend schema) are independent — runnable as parallel Task sub-agents

## Proposed Milestones

### M1: Backend Schema + Wrapper Toolset
**Scope:** backend
**Goal:** A2UI tool calls carry an optional `surface_id` + `update_mode`. Skills can declare a default surface in `SkillConfig.tool_configs.a2ui.default_surface`.
**Estimated:** ~280 LOC implementation + ~200 LOC tests = ~480 LOC
**Duration:** 0.5 day (after spike completes; if spike result is "wrapper needed" add 2-3h)

**Spike (~1-2h, BEFORE writing implementation code):**
- [ ] Read `a2ui-agent-sdk` source for `SendA2uiToClientToolset`. Can we pass extra params through the tool call? Is there a `_meta` field on the schema? Can we subclass cleanly?
- [ ] Three possible outcomes:
  - (a) SDK accepts extra fields → set `surface_id` directly on the payload
  - (b) SDK has a `_meta` extension point → put `surface_id` there
  - (c) Neither → build a `SurfaceAwareA2uiToolset` wrapper that exposes our extended tool signature + delegates emission
- [ ] Document decision in `backend/adk/a2ui.py` docstring

**Tasks:**
- [ ] Spike: confirm SDK extensibility approach (see above) (~1-2h)
- [ ] Extend `backend/adk/a2ui.py` to carry `surface_id` + `update_mode` per the chosen approach (~80 LOC + ~60 LOC tests)
- [ ] Extend `SkillConfig.tool_configs.a2ui` schema in `backend/db/models/` with optional `default_surface: str | None = None` (~30 LOC + ~40 LOC tests)
- [ ] Wire `default_surface` into the toolset factory so skills can declare it once and have it propagate (~50 LOC + ~50 LOC tests)
- [ ] Validation: `update_mode="patch"` requires `surface_id is not None and != "chat"` — reject at schema level (~20 LOC + ~30 LOC tests)
- [ ] Update one reference skill (e.g., the workshop's geo-map template skill) to demo `default_surface="workspace"` — no behaviour change yet since frontend doesn't honour it; lands the round-trip (~20 LOC)
- [ ] `cd backend && make lint && make test-fast` clean (CI parity)

**Files to Create/Modify:**
- `backend/adk/a2ui.py` (modify, ~80 LOC delta) — surface-aware toolset
- `backend/db/models/skill_config.py` or wherever `tool_configs.a2ui` lives (modify, ~30 LOC delta)
- `backend/skills/templates/<geo-map or similar>.yaml` (modify, ~5 LOC delta) — optional demo
- `backend/tests/unit/test_a2ui_surface_schema.py` (new, ~120 LOC)
- `backend/tests/unit/test_skill_config_a2ui_surface.py` (new, ~80 LOC) — exercise the SkillConfig path

**Acceptance Criteria:**
- [ ] `SendA2uiToClientToolset`-equivalent (direct or wrapped) emits `surface_id` and `update_mode` on the tool call payload when set
- [ ] `surface_id=None` produces a payload backwards-compatible with current frontend (proves backwards-compat at the schema level)
- [ ] `update_mode="patch"` with `surface_id=None` raises a validation error
- [ ] Skill with `default_surface="workspace"` in its `tool_configs.a2ui` propagates to the toolset
- [ ] All 7 new unit tests pass
- [ ] Existing 1067 backend tests still pass
- [ ] `make lint` clean

**Risks:**
- A2UI SDK doesn't accept extra fields → wrapper-toolset adds ~80 LOC and ~2h extra. Mitigation: spike first, choose approach with eyes open.
- `tool_configs.a2ui` schema extension breaks existing skill loads → Pydantic `default=None` keeps existing configs valid; add migration test that loads a no-surface skill.

---

### M2: Frontend `SurfaceRegistry` + `A2UISurfaceMount`
**Scope:** frontend
**Goal:** React context that maps `surfaceId → mount point`, with per-surface policies (persistence + patch acceptance + user-gesture requirement). `A2UISurfaceMount` is the layout primitive that declares a named mount.
**Estimated:** ~280 LOC implementation + ~220 LOC tests = ~500 LOC
**Duration:** 0.75 day (independent of M1 — can run **parallel** via Task sub-agent)

**Tasks:**
- [ ] `frontend/src/providers/SurfaceRegistry.tsx` — context + provider; per-surface state owner (current tree + last update timestamp + source tool-call id) (~150 LOC)
- [ ] `frontend/src/components/protocols/A2UISurfaceMount.tsx` — registry-binding component with optional policy override (~50 LOC)
- [ ] Built-in default surfaces table (chat / workspace / sidebar / modal) with policies (~30 LOC)
- [ ] `useSurfaceRegistry()` + `useSurfaceMount(id)` + `useSurfaceState(id)` hooks (~50 LOC)
- [ ] Vitest: register/unregister; get by id; double-mount error; policy override; useSurfaceState updates on registry write (~220 LOC)
- [ ] `npm run quality:check` clean

**Files to Create:**
- `frontend/src/providers/SurfaceRegistry.tsx` (new, ~280)
- `frontend/src/providers/__tests__/SurfaceRegistry.test.tsx` (new, ~140)
- `frontend/src/components/protocols/A2UISurfaceMount.tsx` (new, ~50)
- `frontend/src/components/protocols/__tests__/A2UISurfaceMount.test.tsx` (new, ~80)

**Acceptance Criteria:**
- [ ] `SurfaceRegistry.register("workspace", ref, policy)` makes the mount retrievable via `getMount("workspace")`
- [ ] Double-register with a different ref logs an error and refuses
- [ ] `useSurfaceState("workspace")` re-renders when the registry sets a new tree for that surface
- [ ] All 4 default surfaces (chat / workspace / sidebar / modal) have their policy in the built-in table
- [ ] Modal policy has `requiresUserGesture: true`
- [ ] All new Vitest pass; existing 391 tests still pass
- [ ] `npm run quality:check` clean (tsc + lint + tests + build)

**Risks:**
- React `useRef` lifecycle vs registry register/unregister race when the layout re-renders → mitigate with `useLayoutEffect` for registration; test with rapid re-mount cycle.

---

### M3: `A2UIRenderer` Portal Routing + Layout Mounts
**Scope:** frontend
**Goal:** `A2UIRenderer` reads `surface_id` from the tool-call payload, `createPortal`s into the registered mount, or falls back to inline-in-chat with a dev warning. Chat layout declares mounts for workspace + sidebar + modal.
**Estimated:** ~220 LOC implementation + ~250 LOC tests = ~470 LOC
**Duration:** 0.5 day (depends on M2)

**Tasks:**
- [ ] Extend `A2UIRenderer` to accept `surfaceId` + `updateMode` props (~40 LOC)
- [ ] Portal-or-inline branch: `surfaceId === "chat" || surfaceId === undefined` → existing inline path; otherwise `createPortal` to registry mount (~50 LOC)
- [ ] Missing-mount fallback: render inline + `console.warn` in dev + emit OTEL event in prod (~30 LOC)
- [ ] Update `MessageBubble.tsx` to extract `surface_id` + `update_mode` from the parsed A2UI tool result and pass through to `A2UIRenderer` (~30 LOC)
- [ ] Update `frontend/src/app/chat/[...path]/page.tsx` layout to add `<A2UISurfaceMount surfaceId="workspace" ... />` etc. — but only when a surface is *intended* to be live (avoid creating empty `<div>`s in DOM if no surface emit has happened yet) (~70 LOC layout integration)
- [ ] Wrap the chat page in `<SurfaceRegistryProvider>` (~5 LOC)
- [ ] Vitest: portal routing happy path; fallback on missing mount; backwards compat (no surfaceId → inline) (~250 LOC)
- [ ] chrome-devtools MCP smoke per `aitana-frontend-verify` skill: emit a fake A2UI tool result with `surface_id=workspace` from an existing skill — confirm it lands in the workspace pane, not the chat bubble (~documented in test plan)
- [ ] `npm run quality:check` clean

**Files to Create:**
- `frontend/src/components/protocols/__tests__/A2UIRenderer.surface.test.tsx` (new, ~150)
- `frontend/src/components/chat/__tests__/MessageBubble.surface.test.tsx` (new, ~100)

**Files to Modify:**
- `frontend/src/components/protocols/A2UIRenderer.tsx` (~100 LOC delta)
- `frontend/src/components/chat/MessageBubble.tsx` (~30 LOC delta) — extract surface_id from tool result
- `frontend/src/app/chat/[...path]/page.tsx` (~75 LOC delta) — surface mounts + provider wrap

**Acceptance Criteria:**
- [ ] A tool-call payload `{surface_id: "workspace", ...}` renders A2UI tree in the workspace mount, NOT in the chat bubble
- [ ] A tool-call payload without `surface_id` renders inline in the chat bubble (zero-migration backwards compat)
- [ ] Missing mount (e.g., `surface_id: "nonexistent"`) falls back to chat with `console.warn` in dev
- [ ] Modal surface ignores agent-initiated invocations unless `data-user-initiated="true"` flag is set on the request
- [ ] chrome-devtools verifies a workspace surface mounts in the workspace pane (manual smoke per `aitana-frontend-verify`)
- [ ] All new Vitest pass; existing 391 + M2 tests still pass

**Risks:**
- `createPortal` to an unmounted target → defensive null check; fall back to inline.
- Portal-mounted A2UI tree re-renders on every chat scroll because of context propagation → memoise the rendered subtree.
- Layout DOM bloat if every surface is always mounted as an empty div → only render `<A2UISurfaceMount>` for surfaces the fork actually uses.

---

### M4: Patch Semantics + Per-Surface Persistence
**Scope:** frontend (some fullstack — adds an AG-UI event subscriber)
**Goal:** `update_mode="patch"` on a `workspace`/`sidebar` surface merges into the existing tree's data model without remounting components. Per-surface lifecycle policies enforce when surfaces clear (turn / session / on-action).
**Estimated:** ~180 LOC implementation + ~250 LOC tests = ~430 LOC
**Duration:** 0.75 day (depends on M3)

**Tasks:**
- [ ] Patch handler — shallow merge `payload.data` into the existing surface's `tree.data`, preserve component IDs (~80 LOC)
- [ ] Component identity test fixture: a `ref-counting` stub component that increments on mount + warns on remount; use to assert patch preserves identity (~60 LOC test infra)
- [ ] Per-surface lifecycle wiring:
  - `chat`: turn-scoped — clear bubble-local tree on next `RUN_FINISHED` (already the behaviour; verify)
  - `workspace`, `sidebar`: session-scoped — `SurfaceRegistry` subscribes to session-id changes from `AGUIProvider` or equivalent; clears on transition
  - `modal`: turn-scoped + auto-dismiss on action
- [ ] Validation: a `patch` request when no tree exists yet → treat as `replace` + log a recoverable warning (don't fail loudly; user-facing skills shouldn't crash on edge cases) (~30 LOC)
- [ ] Vitest: patch preserves identity; second patch on same surface chains correctly; session change clears workspace; modal dismisses on action; replace mode unconditionally swaps tree (~250 LOC)

**Files to Modify:**
- `frontend/src/providers/SurfaceRegistry.tsx` (~80 LOC delta) — patch handler + session subscription
- `frontend/src/components/protocols/A2UIRenderer.tsx` (~20 LOC delta) — dispatch replace vs patch
- `frontend/src/providers/__tests__/SurfaceRegistry.surface_lifecycle.test.tsx` (new, ~250)

**Acceptance Criteria:**
- [ ] `replace` mode swaps the surface's tree entirely
- [ ] `patch` mode merges `data` onto existing tree; component identity preserved (test uses ref-counting stub)
- [ ] Patch with no prior tree → treated as replace + warning logged (no crash)
- [ ] Workspace surface clears on session-id change
- [ ] Modal surface clears on action (user clicked something on the modal subtree)
- [ ] All new Vitest pass; backwards-compat (no surface_id) unaffected

**Risks:**
- Component identity preservation requires `@a2ui/react` library to use stable React keys based on `componentId` — verify with the spike in M1. If the library remounts on any prop change, we'd need to either fork the library or limit patch to simpler updates. Spike outcome decides.
- Session-id subscription source needs care — must be the same source-of-truth as the chat's session reset path (probably `useSessionId()` from the AG-UI provider).

---

### M5: Workshop W6 Demo Upgrade + Skill Author Howto
**Scope:** fullstack (small) + docs
**Goal:** Update the geo-map workshop skill to target `surface_id=workspace`; ship `docs/integrations/multi-surface-rendering.md` as a short howto for skill authors. The howto IS the validation that skill authors can adopt surfaces in ~30 minutes.
**Estimated:** ~120 LOC code + ~250 LOC docs = ~370 LOC
**Duration:** 0.5 day (after M4)

**Tasks:**
- [ ] Update the geo-map skill (or whichever skill the workshop uses) to set `tool_configs.a2ui.default_surface="workspace"` + opt into `update_mode="patch"` on subsequent calls (~30 LOC)
- [ ] Add a sample "two-turn" workshop demo: `show me Munich` → `zoom to the old town` — verify the second turn patches the map without remount, end-to-end via chrome-devtools (~documented in test plan + ~20 LOC integration test if feasible)
- [ ] `docs/integrations/multi-surface-rendering.md` — short howto for skill authors (~250 LOC):
  1. Pick a surface (chat default; workspace for dashboards; sidebar for context; modal for confirmations)
  2. Set `default_surface` in `SkillConfig` OR pass `surface_id` per tool call
  3. Use `update_mode="patch"` for live updates (preserves component identity)
  4. Modal requires user gesture — can't pop unprompted
  5. Forks can declare custom surfaces via `<A2UISurfaceMount>`
- [ ] Cross-link from [a2ui-tool-delivery.md](../../v6.1.0/implemented/a2ui-tool-delivery.md) + [multi-surface-rendering.md](multi-surface-rendering.md)
- [ ] Update the workshop talk's W6 section in [docs/talks/ai-ui-protocol-stack.md](../../../talks/ai-ui-protocol-stack.md) with the new demo flow

**Files to Create:**
- `docs/integrations/multi-surface-rendering.md` (new, ~250)

**Files to Modify:**
- `backend/skills/templates/<geo-map>.yaml` or equivalent (~5 LOC delta)
- `docs/design/v6.1.0/implemented/a2ui-tool-delivery.md` (~3 LOC delta) — forward link
- `docs/design/v6.2.0/multi-surface-rendering.md` (~5 LOC delta) — implementation status stamps
- `docs/talks/ai-ui-protocol-stack.md` (~30 LOC delta) — W6 demo upgrade

**Acceptance Criteria:**
- [ ] Workshop W6 two-turn demo runs end-to-end against the geo-map skill: first turn renders map in workspace; second turn patches in place
- [ ] Howto doc is self-contained — a skill author can follow it without reading multi-surface-rendering.md
- [ ] All linked docs cross-reference correctly
- [ ] No regressions: full backend + frontend test suites pass; lint clean

---

## Day-by-Day Breakdown

### Day 1 — M1 spike + M2 parallel start
- **Morning track A (backend):** M1 spike on `a2ui-agent-sdk` — decide direct/`_meta`/wrapper. Then implement schema additions + SkillConfig extension + validation. Tests.
- **Morning track B (frontend, can run as Task sub-agent in parallel):** M2 `SurfaceRegistry` + `A2UISurfaceMount` + their unit tests.
- **End-of-day gate:** M1 backend tests green; M2 frontend tests green; both ready to commit on their branches. Backend baseline: 1067 → ~1074. Frontend baseline: 391 → ~395.

### Day 2 — M3 portal routing
- **Focus:** wire `A2UIRenderer` to read `surface_id` from tool-call payload, `createPortal` to registry mount, fall back to chat on missing mount with warning. Update `MessageBubble`. Layout mounts.
- **Morning:** renderer changes + MessageBubble extraction + tests
- **Afternoon:** layout integration on `chat/[...path]/page.tsx` + chrome-devtools manual smoke
- **End-of-day gate:** `surface_id=workspace` route lands a fake A2UI tree in the workspace pane, not the chat bubble. Backwards-compat verified — existing inline rendering unchanged. Frontend tests: ~395 → ~405.

### Day 3 — M4 patch + persistence
- **Focus:** dataModelUpdate patch handler with component identity preservation; per-surface lifecycle (turn/session/on-action); validation edge cases.
- **Morning:** patch handler in SurfaceRegistry + identity-preservation test fixture
- **Afternoon:** session-id subscription; modal-dismiss-on-action; full integration test for the two-turn flow against a fake skill
- **End-of-day gate:** ref-counting stub assertion passes (patch does NOT remount); session change clears workspace; modal dismisses on action. Frontend tests: ~405 → ~415.

### Day 4 — M5 demo + docs + cross-stack QA
- **Morning:** update geo-map skill; write the howto doc; cross-link
- **Afternoon:** full-stack quality gates — `cd backend && make lint && make test-fast` clean; `cd frontend && npm run quality:check` clean; chrome-devtools verification of the two-turn workshop demo
- **End-of-day gate:** sprint JSON status flips to complete; ready for evaluator round

### Buffer day (Day 4 afternoon → optional Day 5)
- If M1 spike took longer than expected → catch-up here
- If chrome-devtools surfaces an unexpected layout bug → fix here
- If evaluator round 1 returns FAIL with actionable feedback → address in the buffer

## Quality Gates

After each milestone:

```bash
# Backend milestones (M1)
cd backend && make lint && make test-fast    # CI parity per pre-push rule

# Frontend milestones (M2, M3, M4)
cd frontend && npm run quality:check         # lint + typecheck + tests + build

# Fullstack milestones (M5)
cd backend && make lint && make test-fast
cd frontend && npm run quality:check
```

After all milestones:

```bash
# Full integration verification
cd backend && make lint && make test-fast
cd frontend && npm run quality:check
# Plus chrome-devtools manual smoke of the two-turn workshop demo per
# the `aitana-frontend-verify` skill.
```

**Pre-push discipline reminder:** `quality:check:fast` (no tests) and `make lint` (no tests) are NOT enough. Use the parity commands at every milestone close. The CHANNELS-FRAMEWORK sprint had 9 red CI commits because of the fast variants — same gotcha applies here.

## Success Metrics

- [ ] Backend: ~1067 → ~1074 tests passing (M1's 7 new)
- [ ] Frontend: 391 → ~415 tests passing (M2 + M3 + M4 = ~24 new tests across 5 files)
- [ ] `cd backend && make lint && make test-fast` clean
- [ ] `cd frontend && npm run quality:check` clean
- [ ] Two-turn workshop demo (Munich → zoom to old town) lands the map in workspace + patches in place (chrome-devtools verified)
- [ ] Skills without `surface_id` continue to render inline-in-chat unchanged (backwards-compat regression test passes)
- [ ] AIPLA fork can adopt by wrapping their layout with `<SurfaceRegistryProvider>` + declaring `<A2UISurfaceMount>` for each named surface — verified against the howto doc
- [ ] All milestones PASS evaluator round 1 (threshold: 70/100; target: 90+)

## Dependencies

- `a2ui-tool-delivery` (v6.1.0 1.0 ✅) — the protocol primitive we extend
- `chat-message-rendering` (v6.1.0 1.1 ✅) — current inline A2UI mount point we keep backwards-compat with
- `document-ui` layout (v6.1.0 1.10 ✅) — the split-pane shell we extend with surface mounts
- `a2ui-agent-sdk` (backend) — external package, current pinned version. M1 spike confirms extensibility.
- `@a2ui/react` (frontend) — external package; we control the mount, not the library internals. M4 component-identity preservation depends on stable React keys inside the library — verify with the spike.

## Open Questions

1. **Spike outcome for `a2ui-agent-sdk` extensibility.** Direct field? `_meta` extension? Wrapper toolset? — Day 1 morning decides.
2. **Session-id source for cross-turn clear.** Confirm we use the AG-UI provider's session-id, not a parallel state.
3. **Mobile layout.** Defer — first consumers are desktop-primary. Document as v3.0 concern in the howto.
4. **Fork-level surface naming convention.** Encourage fork-specific prefixes (`aipla:teacher-grid`, `playground:group-1`) to avoid collisions with built-in surface names — note in the howto.

## Notes

- Push policy: commit at every milestone, do NOT push until user reviews diff between milestones, then push as batch. Same as CHANNELS-FRAMEWORK.
- M2 and M3 are explicit candidates for Task sub-agent parallelisation if Day 1 timing slips; cleanly partitioned by file (M2 = new files in providers/+protocols/; M3 = modifications to existing renderer+messagebubble+page).
- The howto in M5 is the validation that the API surface is right. If writing it surfaces friction, fix the API before merging M5 — same pattern as CHANNELS-FRAMEWORK M5 worked.
- Backwards-compat is the contract: every existing skill that doesn't set `surface_id` continues to render inline-in-chat unchanged. Regression test in M3 pins this.
