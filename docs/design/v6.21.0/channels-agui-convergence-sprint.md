# Sprint Plan: CHANNELS-AGUI — Channels AG-UI Convergence (Phases 2–4)

## Summary

Turn the channel layer into one shared AG-UI renderer behind per-adapter
sinks, project A2UI into Discord embeds, and give channel users real
(authoritatively-sourced) identity — so a new channel is a renderer, not a
rewrite.

**Duration:** 3 days
**Scope:** Backend (+ one CLI flag)
**Dependencies:** Phase 1 complete — shipped `3c116e0`, pushed to `dev`
**Risk Level:** Medium (M1 refactors live streaming semantics; M3 changes an access decision)
**Design Doc:** [channels-agui-convergence.md](channels-agui-convergence.md)

## Current Status Analysis

### Recent Velocity
- **89 commits / 184 files / 14,767 insertions** over the last 14 days (ARTIFACT-PROMOTION sprint, docs-heavy)
- Recent milestone completion rate: **5/5** (ARTIFACT-PROMOTION M1–M5, all landed)
- Phase 1 of this design doc: ~250 LOC impl + ~200 LOC tests in one session, lint + 2709 tests green
- Estimated capacity: **~400–500 LOC/day** of tested backend code

### Existing Implementation
- **206 tests** in `backend/tests/channels/`, all passing
- `BaseChannel` framework carries identity / commands / attachments / dispatch
- Phase 1 landed the seam this sprint builds on: `stream_skill_events` +
  `collect_reply` in `_skill_invoke.py`, `supports_streaming` branch and a
  default `send_streaming` in `base.py`
- Discord adapter (607 LOC) holds the coalescing/chunking logic M1 extracts
- `aiplatform a2ui render` already exists (`cli/aiplatform/commands/a2ui.py`) —
  M2 adds a flag, not a command group
- A2UI Basic catalog vocabulary: `agent-protocols` skill,
  `references/a2ui-v0.9-basic-catalog.md` — read it, don't re-derive

## Proposed Milestones

### Milestone 1: Extract the shared renderer
**Scope:** backend
**Goal:** One module owns AG-UI event→presentation for all channels; Discord's inline event loop disappears.
**Estimated:** ~270 LOC implementation + ~180 LOC tests = ~450 LOC
**Duration:** 1 day

**Tasks:**
- [ ] `channels/_agui_render.py` — `AGUIChannelRenderer` + `ChannelSink` protocol (~180)
- [ ] `ChannelSink` defaults: `show_progress` no-op, `render_surface` → text summary, `show_error` → text (~40)
- [ ] `CollectingSink`; reimplement `collect_reply` on it, preserving Phase 1 behaviour exactly (~60)
- [ ] `DiscordSink`; `discord.py` sheds its inline event loop (−80 / +60)
- [ ] `_demo_cli` terminal sink + `supports_streaming = True` (~30)
- [ ] Drift-guard test: every event type emitted by `adk/agui.py` has renderer coverage (~50)
- [ ] Renderer unit tests: per-event sink calls, throttling, chunk boundaries (~130)

**Files to Create/Modify:**
- `backend/channels/_agui_render.py` (new, ~220)
- `backend/channels/_skill_invoke.py` (modify, `collect_reply` → `CollectingSink`)
- `backend/channels/discord.py` (modify, −80/+60)
- `backend/channels/_demo_cli.py` (modify, ~+30)
- `backend/tests/channels/test_agui_render.py` (new, ~180)

**Acceptance Criteria:**
- [ ] No adapter contains an AG-UI event-type conditional outside `_agui_render.py`
- [ ] All 206 existing channel tests still pass **unmodified** (behaviour-preserving refactor)
- [ ] Drift guard fails when a new event type is added without a channel decision
- [ ] `python -m channels._demo_cli` streams text + progress with no credentials
- [ ] `make lint && make test-fast` clean

**Risks:**
- **Behaviour drift during extraction.** Mitigation: existing tests must pass *unmodified* — any test edit is a signal the refactor changed semantics; justify or revert.
- **Discord edit rate limits.** Mitigation: move `STREAM_EDIT_INTERVAL_SEC` coalescing verbatim; do not "clean it up" while moving it.

### Milestone 2: A2UI → Discord embeds
**Scope:** backend + CLI
**Goal:** A2UI reaches a channel for the first time; proves the sink boundary before Block Kit.
**Estimated:** ~160 LOC implementation + ~110 LOC tests = ~270 LOC
**Duration:** 1 day

**Tasks:**
- [ ] Read `references/a2ui-v0.9-basic-catalog.md` in the `agent-protocols` skill first
- [ ] Basic-catalog → Discord embed projection (title/description/fields/footer/color) (~120)
- [ ] Text-summary fallback for unmappable components — degrade, never vanish (~40)
- [ ] `aiplatform a2ui render --channel discord` (~40)
- [ ] Projection tests incl. unmappable → text, and embed field limits (~110)

**Files to Create/Modify:**
- `backend/channels/_a2ui_discord.py` (new, ~160)
- `backend/channels/discord.py` (modify, wire `render_surface`)
- `cli/aiplatform/commands/a2ui.py` (modify, ~+40)
- `backend/tests/channels/test_a2ui_discord.py` (new, ~110)

**Acceptance Criteria:**
- [ ] An `A2UI_SURFACE` event renders as a Discord embed
- [ ] Unmappable components degrade to readable text, never silently drop
- [ ] Discord embed limits respected (25 fields, 1024/field, 6000 total)
- [ ] `aiplatform a2ui render --mapping <m> --result <f> --channel discord` prints the projection

**Risks:**
- **Catalog scope creep.** Mitigation: Basic catalog only, read-only. Interactive components are explicitly a later doc.
- **Confidential content into a third-party message history.** Mitigation: per the design doc's security section this is a per-deployment decision; M2 ships the mechanism, not a default-on for tagged skills.

### Milestone 3: Channel identity — authoritative claims only
**Scope:** backend
**Goal:** Channel users reach group-tagged skills without channels becoming a privilege path.
**Estimated:** ~120 LOC implementation + ~130 LOC tests = ~250 LOC
**Duration:** 1 day

> **Design corrected during planning.** The original Phase 4 said to read
> `channel_identities.group_tags` into `User`. That violates the invariant at
> [`identity.py:22-24`](../../../backend/channels/identity.py#L22-L24) — the
> mirror is advisory *precisely* so it cannot grant access. Tags must come
> from the Firebase custom claim fetched by UID, unioned with
> `clients/{domain}.derived_group_tags`, exactly as `get_current_user` does.

**Tasks:**
- [ ] Fetch authoritative custom claims by UID (Firebase Admin SDK) (~50)
- [ ] Reuse `_apply_derived_group_tags` for the domain union — do not fork it (~20)
- [ ] Short-TTL cache (one Admin call per inbound message otherwise) (~30)
- [ ] `CHANNEL_IDENTITY_ENRICHMENT` flag; default off (~20)
- [ ] Tests: allow path, deny path, **mirror-tampering grants nothing**, cache expiry, flag-off preserves today's behaviour (~130)

**Files to Create/Modify:**
- `backend/channels/_skill_invoke.py` (modify, `_build_channel_user`)
- `backend/auth/firebase_auth.py` (modify — export claim lookup if not already reusable)
- `backend/tests/channels/test_channel_identity.py` (new, ~130)

**Acceptance Criteria:**
- [ ] A channel user with an authoritative tag reaches a tagged skill
- [ ] A user without one is still denied (fail-closed)
- [ ] **Writing tags into the `channel_identities` mirror grants nothing** — explicit test
- [ ] Admin SDK lookup failure → today's restricted `User`, never a permissive default
- [ ] Flag off → byte-identical behaviour to today

**Risks:**
- **This is the security-critical milestone.** Mitigation: the mirror-tampering test is non-negotiable; evaluate on a different model than the executor.
- **Admin SDK latency per message.** Mitigation: short-TTL cache, measured before merge.
- **Discord guild ≠ employment.** Mitigation: per-guild allowlist (`channel_routes/discord/{guild_id}`) stays mandatory for any tag-granting identity; assert it.

## Model Assignment

<!-- Rubric: .claude/skills/sprint-planner/resources/model-assignment.md
     NOTE: that rubric's lineup table is stale (lists Opus 4.8 / Sonnet 4.6).
     IDs below verified against the current lineup: Opus 5, Fable 5, Sonnet 5,
     Haiku 4.5. -->

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-5`, effort high | Decomposition + interactive iteration; the design doc holds the hard thinking. Caught the `identity.py` invariant conflict at this stage. |
| Execute M1 (renderer) | `claude-fable-5`, effort xhigh | High subtlety (streaming/concurrency semantics, throttling, a behaviour-preserving refactor of live code) + complete spec + long autonomous horizon — the rubric's exact case for Fable. |
| Execute M2 (A2UI embeds) | `claude-opus-5`, effort xhigh | Mostly mapping code against a published catalog. Moderate subtlety, shorter interactive loop. |
| Execute M3 (identity) | `claude-fable-5`, effort xhigh | Security-critical gate where wrong-but-plausible passes shallow tests — the highest-stakes milestone in the sprint. |
| Evaluation (all rounds) | `claude-opus-5` + report-everything prompt | Cross-model diversity: different model than M1/M3's executor. Must instruct "report every issue including low-confidence/low-severity" — the evaluator withholds findings under severity filters. |
| Sub-agents (search, test loops) | `claude-sonnet-5` / `claude-haiku-4-5` | Procedural fan-out. |

## Day-by-Day Breakdown

### Day 1 — M1: the renderer
- **Focus:** Extract `_agui_render.py`; Discord and collecting paths both route through it
- **Tasks:** Renderer + sink protocol → CollectingSink → DiscordSink → demo-CLI sink → drift guard
- **Checkpoint:** 206 existing tests pass **unmodified**; `python -m channels._demo_cli` streams live

### Day 2 — M2: A2UI on a channel
- **Focus:** Basic-catalog → Discord embeds, with honest degradation
- **Tasks:** Read the catalog reference → projection → fallback → CLI flag → tests
- **Checkpoint:** An `A2UI_SURFACE` event renders as an embed; unmappable input still readable

### Day 3 — M3 + real-guild verification
- **Focus:** Authoritative identity, then prove the whole sprint in a real guild
- **Tasks:** Claim lookup → derived-tag union → cache → flag → security tests → **live Discord run**
- **Checkpoint:** Tagged skill reachable from Discord with the flag on; mirror-tampering test green; live guild shows streaming, tool progress, an embed, and a visible error

## Quality Gates

After each milestone:
```bash
cd backend && make lint && make test-fast
```

Before the sprint closes:
```bash
cd backend && make test           # full suite incl. slow
cd cli && make test               # a2ui --channel flag
```

## Success Metrics
- [ ] All backend tests passing (`make test-fast`, currently 2709)
- [ ] Lint + format clean (`make lint`)
- [ ] Zero AG-UI event conditionals outside `_agui_render.py`
- [ ] Drift-guard test present and green
- [ ] **Real Discord guild run** — streaming, tool progress, an A2UI embed, and an induced error all visibly correct
- [ ] Mirror-tampering grants no access
- [ ] Design doc moved to `implemented/` only after the guild run

## Dependencies
- Phase 1 (`3c116e0`) — landed
- A Discord guild + bot token for acceptance. **This is the sprint's only external dependency and it gates closure** — without it the sprint ends at "unit-tested", which is the exact failure mode that let the original code ship dead.

## Open Questions
- **Does a test guild + bot token exist, or does one need provisioning?** Blocks Day 3 acceptance, nothing earlier.
- Should `A2UI_SURFACE` render inline or as a link back to the web workbench for confidential-content skills? M2 ships inline; the default for tagged skills is a deliberate follow-up decision.
- `CHANNEL_IDENTITY_ENRICHMENT` on in dev only, or dev+test? Recommend dev only until the guild run passes.

## Notes
- **Not in scope:** Slack, Teams, interactive A2UI on channels, the CopilotKit Channels SDK (rejected in the design doc's Alternatives).
- The Teams-as-MCP-Apps-surface question stays open and deserves its own spike before anyone writes `teams.py`.
- M1's guard rail is that existing tests pass *unmodified*. If the executor edits a channel test during M1, that is a signal to stop and justify — the Phase 1 experience showed a test can encode the same wrong assumption as the code.
