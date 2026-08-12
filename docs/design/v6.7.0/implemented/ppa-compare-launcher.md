# PPA Contract Compare — Launcher & Config

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: 2.5 days
**Scope**: Fullstack (frontend-heavy)
**Dependencies**: [action-triggered-agent-turn.md](../v6.1.0/implemented/action-triggered-agent-turn.md) (surface-action-run loop), [a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md), the shipped M1 compare-rendering fix (`PpaWorkspacePanel`, `KeyDifferencesPanel` filters, `extract→extract→compare` skill flow)
**Created**: 2026-07-09
**Last Updated**: 2026-07-09

## Problem Statement

The `one-doc-compare` (PPA Contract Compare) skill now renders correctly (M1: the
comparison completes, the Key Differences panel and clause cards mount on the
Workspace tab, and the run populates progressively). But **starting** a comparison
is still clumsy, and it exposes zero configuration.

**Current State:**

- To compare, the user pre-selects two PDFs in the doc-tabs bar, then must **type**
  a free-text intent ("compare these two ppas"). The skill parses the attachments
  and runs. Selection and intent are two disjoint steps; a first-time user doesn't
  know the magic phrase.
- The comparison always runs with hardcoded defaults: all 12 standard clauses, all
  severities, `other_clauses` capped at 20. The user cannot say "only compare
  settlement + price" or "show me material differences only" *before* the (slow,
  Gemini-Pro) diff runs — they can only filter **after** via the M1 client-side
  panel, having already paid the full comparison cost.
- There is no affordance in the workbench that says "you can compare here". The
  Workspace tab is empty until a run produces output.

**Impact:**

- The flagship ONE demo relies on the operator remembering to type the right phrase.
- Users burn a full two-doc extraction + Pro-tier comparison before discovering they
  only cared about two clauses — wasted latency and tokens on every exploratory run.
- Selection state lives in the doc-tabs bar but is invisible as "comparison input",
  so the mental model ("pick two → compare") isn't reinforced by the UI.

## Goals

**Primary Goal:** Let a user start a correctly-scoped PPA comparison from the
workbench in one click — pick two contracts, optionally narrow what to compare, hit
"Compare" — with no typed intent, using the existing surface-action-run loop.

**Success Metrics:**

- Zero-typing path: selecting two docs + clicking "Compare contracts" starts a run
  (measured: a `surface-action-run` POST fires with both doc_ids and no chat message).
- Config reduces work: choosing a clause subset before running measurably shrinks the
  comparison (assert the selected subset reaches the tools).
- First-token budget on the action-triggered run matches the chat path (<3 s with
  tools), inherited from the surface-action-run pipeline.

**Non-Goals:**

- The side-by-side block-diff viewer (`SideBySideDocViewer`) — still deferred; out of
  scope here.
- Comparing >2 documents (pairwise only, matching the tool contract).
- A new comparison model/algorithm — this is orchestration + UI only.
- Replacing the M1 post-render client-side filters — those stay; this adds *pre-run*
  config. The two are complementary (pre-run bounds cost; post-render bounds view).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | One click to a scoped run; pre-run clause selection cuts the slowest path (Pro-tier diff over fewer clauses). Action-triggered run inherits the <3 s first-token budget. |
| 2 | EARNED TRUST | +1 | Config is explicit and defaulted sensibly; the run echoes what it compared. Selection → run is transparent (composer shows a "Comparing A vs B" system line). No hidden scope changes. |
| 3 | SKILLS, NOT FEATURES | +1 | Everything is expressed as the `one-doc-compare` skill's A2UI surfaces + tool_configs; no bespoke app screen. A fork inherits the launcher by declaring the same config. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Reinforces M1 tiering (extraction=lite, comparison=pro). Narrowing clauses pre-run keeps the Pro call small. |
| 5 | GRACEFUL DEGRADATION | +1 | If a skill hasn't opted into `allow_action_triggered_runs`, the launcher falls back to composing a normal chat intent (today's behaviour). Config form is optional; defaults always work. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Launcher + config are A2UI v0.9 surfaces; the run uses the existing surface-action-run transport (already filed upstream as a2ui#1570). Selection payload rides `forwardedProps` (spec-permitted). No new wire format. |
| 7 | API FIRST | +1 | No new backend endpoint — reuses `POST /api/skills/{id}/sessions/{sid}/surface-action-run`. New CLI verb `aiplatform skill compare` drives it headlessly. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Action-triggered runs already trace through the AG-UI pipeline; the `_action_trigger` forwardedProps make "launched from workbench" visible in traces. |
| 9 | SECURE BY CONSTRUCTION | +1 | Reuses the 8-gate access policy from surface-action-run (Firebase JWT → session → access policy → skill → a2ui config → context-writes opt-in → size cap → `allow_action_triggered_runs`). Doc selection validated against docs the user can already see. No new data path. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The client renders declarative A2UI and forwards a selection payload; all comparison logic stays server-side in the skill + tools. |

**Net score: +8** (threshold ≥ +4 ✅). No −1 scores; hard-fail rules satisfied (EARNED TRUST +1, SECURE BY CONSTRUCTION +1).

**Conflict Justifications:** None.

## Design

### Overview

Two A2UI surfaces owned by the `one-doc-compare` skill, both driven by the existing
surface-action-run loop:

1. **Workbench launcher** (Workspace tab, shown when no comparison output exists yet):
   a compact "Compare contracts" card listing the current selection / tenant PPA
   library with checkboxes, a "Compare" button, and a "Configure…" affordance. The
   button fires an `A2uiClientAction` (`name: "start_compare"`) carrying the two
   selected doc identities. `<A2UISurfaceMount triggerOnAction>` POSTs it to
   `surface-action-run`; the skill's next turn sees the selection in `forwardedProps`
   and runs `extract → extract → compare` (M1 flow), whose output replaces the
   launcher with `PpaWorkspacePanel`.

2. **Inline-chat config form** (chat surface): when the user clicks "Configure…" (or
   asks to "set up the comparison"), the skill emits an A2UI form **in chat** — clause
   multi-select (12 standard + "include non-standard"), severity focus
   (material / +moderate / all), and `other_clauses` depth — pre-filled with sensible
   defaults. Submitting fires `start_compare` with the config in the action payload.
   Richer input lives in chat (roomier, conversational) per the product decision;
   the workbench keeps only the one-click launcher + a "Configure…" link.

The comparison config becomes a typed object threaded from the action payload →
`forwardedProps` → skill instruction context → tool arguments.

### Frontend Changes

**New Components:**

- `src/components/workspace/CompareLauncher.tsx` — the workbench launcher card.
  Reads the current doc-tab selection + `skill.welcome.exampleDocuments` /
  bucket-browser list, renders checkboxes (max 2 selectable), a disabled-until-two
  "Compare contracts" button, and a "Configure…" link. On click, builds the
  `A2uiClientAction` payload and calls the action trigger.
- `src/components/workspace/CompareConfigForm.tsx` — the inline-chat config form.
  Clause checklist, severity segmented control, depth stepper; defaults = all clauses
  / all severities / depth 20. (May be pure A2UI basic-catalog instead of bespoke —
  see Open Questions.)

**Modified Components:**

- `src/components/chat/ChatShell.tsx` (`WorkbenchPane`) — when the skill exposes the
  compare launcher and there is no `ppaArtifacts.hasContent` yet, render
  `CompareLauncher` in the Workspace tab (slots in ahead of the existing
  `SkillExamplesPicker` fallback). Once a comparison lands, `PpaWorkspacePanel` takes
  over (M1 behaviour, unchanged).
- `src/components/protocols/A2UISurfaceMount.tsx` — already supports
  `triggerOnAction` (from action-triggered-agent-turn). Confirm the workspace mount
  passes it for this skill.

**State Management:**

- Launcher selection is local component state (`Set<docIdentity>`, max 2). It seeds
  from the doc-tabs selection so the two surfaces agree.
- Config lives in the A2UI surface data model; the submitted values ride the action
  payload — no new client store.

**UI/UX:**

- Launcher is the Workspace tab's empty-state replacement for this skill: instead of
  "structured outputs appear here", the user sees an actionable "pick two → Compare".
- "Configure…" is secondary; the default one-click path never requires it.

### Backend Changes

**New Endpoints:** None. Reuses
`POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run`.

**Modified Endpoints:** None.

**New Services/Modules:** None. The comparison config is parsed from
`forwardedProps._action_trigger` / the surface state in the existing
`wrap_with_a2ui_surface_context` InstructionProvider path.

**Data Model Changes:**

- `one-doc-compare` SKILL.md `tool_configs.a2ui`: add
  `allow_action_triggered_runs: true` (gate 8) and `allow_surface_context_writes:
  true` (already required for the loop). Re-seeded via `platform_seed`.
- New typed config carried through the skill turn (not persisted): a `CompareConfig`
  shape `{clauses: string[] | "all", severity_floor: "material"|"moderate"|"cosmetic",
  max_other_clauses: int}`. Documented in the skill instructions so the model maps it
  onto `extract_ppa_clauses` / `compare_ppa_contracts` arguments.
- `extract_ppa_clauses` / `compare_ppa_contracts`: accept an optional `clauses`
  filter + `max_other_clauses` override (the cap is already parameterised from M1;
  extend to a per-call argument). Extraction over a clause subset shrinks the prompt;
  the diff respects the same subset.

### API Changes

| Method | Endpoint | Change | Auth |
|--------|----------|--------|------|
| POST | `/api/skills/{skill_id}/sessions/{session_id}/surface-action-run` | **Reused** — action body `{surfaceId, componentId, name: "start_compare", payload: {left, right, config}}`; `forwardedProps` carries the surface snapshot. Gated by `allow_action_triggered_runs`. | Firebase Bearer |

**Action payload shape (`start_compare`):**

```json
{
  "name": "start_compare",
  "surfaceId": "workspace",
  "payload": {
    "left":  {"doc_id": "..."},
    "right": {"gs_url": "gs://.../B.pdf"},
    "config": {"clauses": ["settlement_type", "price_formula"],
               "severity_floor": "moderate", "max_other_clauses": 20}
  }
}
```

`left`/`right` accept the same `doc_id` | `gs_url` duality the tools already support
(mixed modes allowed).

### CLI Surface

Per the local-dev CLI heuristic (any new developer-facing action-trigger needs a typed
command), add to the existing `aiplatform skill` group:

- `aiplatform skill compare <skill_id> --left <doc_id|gs_url> --right <doc_id|gs_url>
  [--clauses settlement_type,price_formula] [--severity material|moderate|cosmetic]
  [--max-other N] [--session <sid>]` — mints a token, opens/uses a session, POSTs the
  `start_compare` action to `surface-action-run`, and streams the AG-UI result. Lets
  us smoke the whole launcher path headlessly without a browser. ~0.25 day (Click
  subcommand + httpx SSE consume + unit test). Backlink:
  [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

### Architecture Diagram

```
Workspace tab (no output yet)
  └─ CompareLauncher  ──click "Compare"──▶ A2uiClientAction{start_compare, {left,right,config}}
                                                    │ triggerOnAction
                                                    ▼
        POST /api/skills/{id}/sessions/{sid}/surface-action-run   (8 gates)
                                                    │  synthetic RunAgentInput
                                                    │  forwardedProps: selection + config
                                                    ▼
        one-doc-compare skill turn ── extract(left)  ─▶ card ─┐
                                    ── extract(right) ─▶ card ─┤ (M1 progressive fill)
                                    ── compare ─▶ KeyDifferencesPanel ┘
                                                    │ AG-UI SSE
                                                    ▼
        Workspace tab replaces launcher with PpaWorkspacePanel
```

## Implementation Plan

### Phase 1: Skill config + tool args (~0.75 day)
- Add `allow_action_triggered_runs` / `allow_surface_context_writes` to
  `one-doc-compare` SKILL.md; document `CompareConfig` + the `start_compare` action in
  the instructions (how to map payload → tool args). Re-seed.
- Extend `extract_ppa_clauses` / `compare_ppa_contracts` with optional `clauses`
  subset + `max_other_clauses` args (cap already parameterised in M1). Tests.

### Phase 2: Workbench launcher (~1 day)
- `CompareLauncher.tsx` + wire into `ChatShell` Workspace tab (empty-state slot).
- Fire `start_compare` via the action-trigger hook; selection seeds from doc-tabs.
- Vitest: two-select gating, payload shape, fallback-to-chat-intent when not opted in.

### Phase 3: Inline config form + CLI + polish (~0.75 day)
- `CompareConfigForm` (A2UI in chat) with defaults; "Configure…" opens it; submit →
  `start_compare` with config.
- `aiplatform skill compare` CLI verb + test.
- E2E verify in browser (chrome-devtools): pick two → Compare → progressive fill;
  configure → narrowed run.

## Migration & Rollout

**Database Migrations:** None (Firestore schemaless; `tool_configs.a2ui` gains two
booleans via re-seed).

**Feature Flags:** Gated implicitly by the skill's `allow_action_triggered_runs`
opt-in. Ship launcher behind that flag on `one-doc-compare` only; other skills
unaffected.

**Rollback Plan:** Remove the two `tool_configs.a2ui` booleans (re-seed) → launcher
falls back to composing a chat intent; nothing else changes. Frontend components are
additive and only render for the opted-in skill.

**Environment Variables:** None new (reuses `PPA_EXTRACTION_TIER` /
`PPA_COMPARISON_TIER` / `PPA_MAX_OTHER_CLAUSES` from M1).

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- `CompareLauncher`: disables "Compare" until exactly two selected; builds correct
  `start_compare` payload; seeds selection from doc-tabs; falls back to chat intent
  when the skill isn't opted in.
- `CompareConfigForm`: defaults (all clauses / all severities / depth 20); submitting
  a subset yields the right config in the payload.
- `ChatShell`: launcher shows in Workspace tab when opted-in + no comparison output;
  `PpaWorkspacePanel` replaces it once output arrives.

### Backend Tests (pytest)
- `extract_ppa_clauses` / `compare_ppa_contracts`: `clauses` subset restricts the
  extracted/diffed clauses; `max_other_clauses` override respected + still transparent.
- surface-action-run gate parity for `one-doc-compare` (reuses existing 8-gate suite).
- `aiplatform skill compare` unit test (mocked SSE).

### Manual Testing
- Browser (chrome-devtools skill): pick two ONE PPAs → Compare → cards then diff panel
  populate progressively; Configure → material-only → smaller diff; not-opted-in skill
  → chat-intent fallback.

## Security Considerations

- No new endpoint or data path — inherits the surface-action-run 8-gate policy.
- Doc selection is constrained to documents the user can already list (same access as
  the doc-tabs bar); the tools re-resolve identities server-side under the user's
  access context. **No derivative of restricted content is exposed** — the launcher
  only passes identifiers, and rendering stays behind the authed workspace (per
  CLAUDE.md security rule). Confirm the bucket-list for the launcher uses the authed
  `/api/buckets/.../list` proxy, never a public URL.

## Performance Considerations

- Pre-run clause narrowing is the main win: a 2-clause comparison sends a far smaller
  extraction prompt and a smaller Pro-tier diff than the full 12-clause default.
- The action-triggered run reuses the chat streaming pipeline — same first-token
  budget, no extra round-trips (write + run + stream in one POST).

## Success Criteria

- [x] Selecting two contracts + clicking "Compare contracts" starts a run with no typed message (a `surface-action-run` POST carries both identities). *(M2 — `CompareLauncher` fires `start_compare` via `useActionDrivenAgent`; covered by CompareLauncher tests.)*
- [x] The Workspace tab shows the launcher when opted-in and empty; A2UI artifact tabs (7.3/7.5 rendering model) replace it on output. *(M2 — `shouldShowCompareLauncher` predicate + ChatShell WorkbenchPane wiring.)*
- [x] "Configure…" opens a config form with sensible defaults; submitting a clause subset provably narrows the comparison (fewer clauses reach the tools). *(M3 — `CompareConfigForm` rendered INLINE in the launcher card, not chat; see Open Question 1 resolution. Config threads into `start_compare` context.config; covered by CompareConfigForm + CompareLauncher tests.)*
- [x] A skill not opted into `allow_action_triggered_runs` falls back to a chat-intent message (no regression). *(M2 — `optedIn=false` path calls `onCompareViaChat`; test-covered.)*
- [x] `aiplatform skill compare` drives the full path headlessly. *(M3 — new CLI verb bootstraps a session + POSTs `start_compare` + streams AG-UI; `test_cli_skill_compare.py`.)*
- [x] Reuses the surface-action-run gates (no new access path); no public exposure of restricted content. *(Reuses the existing endpoint; launcher forwards only doc_id/gs_url identities — test asserts no `storage.googleapis.com` URL is ever emitted.)*

## Open Questions

1. **Pure A2UI vs bespoke React for the config form?** ~~The M1 panels are bespoke React
   (not A2UI). The config form could be emitted as A2UI basic-catalog (roomier in
   chat, no new component) OR a bespoke `CompareConfigForm`. Lean A2UI to keep it in
   the protocol (Axiom #6) — decide in Phase 3.~~ **RESOLVED (M3): bespoke React
   `CompareConfigForm`, rendered INLINE in the workbench launcher card (NOT in chat).**
   Rationale: `one-doc-compare` is a Model-B skill — the model does not author A2UI in
   the chat area, and the out-of-model A2UI emitter is scoped to tool RESULTS, not
   pre-run input. A chat-emitted A2UI form therefore has no producer here. Keeping the
   config beside the one-click launcher also keeps selection + scope + run in one place
   on the workbench. The submitted config still rides the SAME `start_compare`
   context.config the one-click path uses; an all-default scope emits `{}` so the run
   reuses the legacy (non-variant) caches. (This supersedes the "inline-chat config
   form" language in the Design/Overview sections above.)
2. **Launcher doc source:** current doc-tabs selection only, or also inline the
   bucket-browser library so the user can pick from the full ONE library without
   pre-loading tabs? MVP = doc-tabs selection; stretch = embed the library list.
3. **Config persistence:** remember the user's last clause/severity choice per skill
   (session state) so repeat comparisons keep their scope? Deferred; defaults each run
   for now.

## Related Documents

- [action-triggered-agent-turn.md](../v6.1.0/implemented/action-triggered-agent-turn.md) — the surface-action-run loop this reuses
- [a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — surface → agent context injection
- [bucket-browser-and-doc-compare-files.md](../v6.5.0/bucket-browser-and-doc-compare-files.md) — doc-compare file selection precedent
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance backlink
- M1 (shipped this session): `PpaWorkspacePanel`, `KeyDifferencesPanel` filters, transparent clause cap, model-tier de-hardcoding, `extract→extract→compare` skill flow (commit `fix(ppa-compare): unblock comparison + wire workbench rendering + tier models`)
