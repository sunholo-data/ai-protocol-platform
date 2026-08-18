# Conversation Context Fidelity — stop losing history mid-conversation

> ## ⚠️ ROOT CAUSE DISPROVED — 2026-08-06, same day
>
> **This doc blames `compaction_interval=10` for discarding turns 1–10. That is
> wrong.** Compaction has never run on the chat path at all: the AG-UI Runner is
> built without an `App`, so `runner.app is None` and both compaction triggers
> are dead. It discarded nothing, because it never ran.
>
> Measured: a 25-turn conversation at `compaction_interval=10` produced 100
> events across 25 invocations and **zero** compaction events. Pinned by a
> failing guard in `tests/unit/test_compaction_reaches_chat_runner.py`.
>
> **Read [compaction-wiring-and-observability.md](compaction-wiring-and-observability.md) instead.**
>
> What survives from this doc:
> - **The tuning table** (token-pressure triggering, `event_retention_size`,
>   fail-safe default) — still the right configuration, and shipped.
> - **The `app.py` hardcode fix** — a genuine bug; the App did resolve its config
>   from a hardcoded `gemini-2-5-flash`.
> - **The ADK verification findings** below — accurate, and load-bearing for the
>   successor doc.
>
> What does NOT survive:
> - The root cause, the Problem Statement's mechanism, and any claim that
>   Phases 1–2 fixed Tomas's issue.
>
> **Tomas's actual cause, found later the same day:** the ag_ui_adk
> SessionManager sweep was permanently deleting sessions idle >20 minutes —
> already fixed by `44ca9b6` (2026-08-05), the night before the UAT. His session
> was 90 minutes over 12 turns, so the sweep hit it mid-conversation and the next
> turn started from an empty session. He called it deletion himself in the
> meeting. See [uat-triage-2026-08-06](uat-triage-2026-08-06.md) B2/B3.
>
> Kept rather than deleted because the reasoning error is instructive: every
> statement about ADK below was correct in isolation, and the conclusion was
> still wrong, because nothing checked that the config reached the code that
> reads it. A unit test asserting `get_compaction_config` returns the right
> values passes happily for a config nobody reads.

**Status**: Superseded (root cause disproved). Phases 1 + 2 shipped and remain valid as tuning, but are **inert for chat** until the successor doc's Phase 1 lands.
**Priority**: P0 (High) — the only UAT finding that damages answer *quality*, and the one blocking sign-off for the 1 Sept cutover
**Estimated**: ~2 days (Phase 1 ~0.5d, Phase 2 ~0.75d, Phase 3 ~0.75d)
**Scope**: Backend (+ a small Activity-tab surface in the frontend)
**Dependencies**: None. Touches `backend/app.py`, `backend/adk/session.py`, `backend/adk/agent.py`.
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

At the 2026-08-06 ONE user-acceptance session, Tomas described the single
worst outcome of the whole test — and it is a quality failure, not a crash:

> "I was interacting and because I had to add more information so she would
> understand and then reprocess the thing. After 10, 12 iterations, she got it
> spot on... And then I said 'okay, now from here, please write me a summary
> for a lawyer'... I asked to summarize the feedback of the previous 12 prompts
> and it was just not the right bits and pieces. **It was like she didn't have
> access to the prompt history, to the chat history.**"

That is the highest-value moment in the product — an hour and a half of expert
iteration converging on a correct answer — and the payoff step failed. He was
right about the cause, and the mechanism is now identified.

**This is NOT the same bug as the empty-thread issue.** Two distinct faults
were conflated during the meeting and must stay separated:

| | Symptom | Status |
|---|---|---|
| **A. Empty thread on resume** | Reopening a past thread showed *no messages at all* | Observed 2026-08-05, **not reproducing** as of 2026-08-06 AM. Not this doc. Tracked as an open question below. |
| **B. Mid-conversation context loss** | Transcript on screen looks complete; the *model* has lost the early turns | **Open. This doc.** |

Fault B is the dangerous one precisely because nothing looks wrong: the user
sees the full transcript in the UI and reasonably assumes the model sees it too.

### Root cause (confirmed in the pinned ADK source, not inferred)

ADK's events compaction replaces raw turns with an LLM summary once enough
turns accumulate. From `google/adk/apps/app.py`:

```python
compaction_interval: int
"""The number of *new* user-initiated invocations that, once
fully represented in the session's events, will trigger a compaction."""

overlap_size: int
"""The number of preceding invocations to include from the
end of the last compacted range."""
```

and from `google/adk/flows/llm_flows/contents.py` (`_process_compaction_events`):

> "A summary event is materialized at its compaction end timestamp, and **raw
> events inside any kept compaction range are filtered out**."

So the raw events *stay in the session store* — which is why the UI transcript,
the Firestore mirror and the Activity tab all still show every turn — but they
are **filtered out of the request sent to the model**. The model sees a summary
where the user sees a conversation. There is no mechanism by which the agent
can reach back for the raw turns.

We configure it in [`backend/adk/session.py`](../../../backend/adk/session.py):

```python
_COMPACTION_CONFIGS = {
    "gemini-": EventsCompactionConfig(compaction_interval=10, overlap_size=3),
    ...
}
```

At `compaction_interval=10`, Tomas's ~12-turn conversation compacted **exactly
once**, right before the summarise request: turns 1–10 collapsed into one
summary, turns 10–12 kept raw by `overlap_size=3`. He then asked the model to
"summarise the previous 12 prompts" — and it had 2 raw turns plus a generic
summary of the other 10 to work from. The reported behaviour ("not the right
bits and pieces") is precisely what that input produces.

### Three defects, one mechanism

**1. Turn-count compaction is the wrong trigger for a 1M-token model.**
Ten turns of short clarifying questions might be 8K tokens. We discard them
anyway, on a model with a 1M-token window, buying nothing and losing detail.
Compaction should be driven by *token pressure*, which is the actual
constraint. ADK already supports this and we simply never set it:

```python
token_threshold: Optional[int] = Field(default=None, gt=0)
"""Post-invocation token threshold trigger.
If set, ADK will attempt a post-invocation compaction when the most recently
observed prompt token count meets or exceeds this threshold."""
```

`_run_compaction` prefers token-threshold compaction when configured and only
falls through to the sliding window otherwise — so setting it changes the
trigger without needing to fight the existing path.

**2. The "model-aware" compaction config is dead code.** `get_compaction_config`
selects per model family, but [`backend/app.py:85`](../../../backend/app.py)
calls it with a hardcoded lookup:

```python
events_compaction_config=get_compaction_config(gemini_api_name_for("gemini-2-5-flash")),
```

`EventsCompactionConfig` lives on `App`, which is constructed once at import,
so **every session gets the Gemini 10/3 setting regardless of the model the
skill actually runs**. A Claude Opus skill (~200K window) silently gets the
setting designed for a 1M window — the tuning table has never once been
applied. Note this cuts the opposite way for Claude: too *little* compaction
against a smaller window, which is the shape of the context-overflow errors
Dana asked about in the same meeting ("the most common error we were having
was a limitation error"). One bug, both complaints.

**3. Compaction is invisible.** Nothing in the UI, the Activity tab or the
trace says "history was summarised here." The user cannot tell why an answer
degraded, and neither can we when triaging a report. This is a direct violation
of repo principle #8 (NEVER SILENT) applied to context rather than to actions.

**Impact:**
- **Who:** every user in any conversation longer than 10 turns. ONE's super-users
  (Tomas, Dana, long-stream) live in long conversations — that is the *product*.
- **How significant:** blocker for the 1 Sept cutover. It degrades exactly the
  expert, high-iteration workflow that the customer values most, and it does so
  invisibly.

## Goals

**Primary Goal:** A conversation of 30+ turns retains full fidelity of the
turns the model needs, and when history *is* compacted the user can see that it
happened.

**Success Metrics:**
- A scripted 25-turn conversation ending in "summarise everything we've
  discussed" cites specifics from turns 1–5. Baseline today: fails at ~12 turns.
- Zero compactions fire below 250K prompt tokens on a 1M-context model
  (today: fires at 10 turns regardless of size).
- The compaction config applied to a session matches that session's actual
  model family in 100% of runs (today: 0%).
- Every compaction emits an observable event; a triager can answer "was this
  session compacted, and when" from the Activity tab without reading logs.

**Non-Goals:**
- Unlimited context. A long enough conversation must still compact — the goal is
  that it compacts on the real constraint and says so.
- Retrieval over past turns (letting the agent search its own history). That is
  a bigger design and is listed under Open Questions.
- Fixing fault A (empty thread on resume). Different mechanism; see Open Questions.
- Replacing ADK compaction with a bespoke summariser. We tune ADK's, we do not
  fork it (Axiom #6).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Compacting less often means slightly larger prompts and marginally slower turns; the token ceiling keeps that bounded. Neutral trade, consciously made. |
| 2 | EARNED TRUST | +1 | The core of the fix. An answer that silently forgets what you told it five minutes ago is the sharpest trust failure the product has, and the user has no way to detect it. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure beneath every skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Makes per-model context budgets real for the first time — the tuning table exists but has never been applied. |
| 5 | GRACEFUL DEGRADATION | +1 | Compaction becomes a visible, bounded degradation rather than a silent quality cliff. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK's own `token_threshold` and per-App config rather than building a parallel history manager. |
| 7 | API FIRST | 0 | No new endpoint; a CLI probe is in scope (below). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Compaction goes from invisible to an emitted event visible in Activity and trace. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. Summaries live in the same session store as the raw events. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Fix is entirely backend; the client renders an event it is already able to render. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scored -1).

## ADK verification findings (2026-08-06, google-adk **1.31.1**)

The design below said not to trust it without checking ADK first. Checked —
against our pinned 1.31.1 source, not the ADK MCP index (which serves v1.24.1).
Three findings, one of which materially improves Phase 2 and one of which
corrects an omission in the original plan.

**1. The two triggers read config from DIFFERENT objects.** This is the finding
that unblocks per-model tuning:

| Trigger | When | Reads |
|---|---|---|
| `token_threshold` | pre-request, `CompactionRequestProcessor` (`flows/llm_flows/compaction.py:39`) | `invocation_context.events_compaction_config` |
| `token_threshold` | post-invocation, inside `_run_compaction_for_sliding_window` | `app.events_compaction_config` |
| `compaction_interval` | post-invocation, Runner (`runners.py:622`) | `app.events_compaction_config` |

`invocation_context.events_compaction_config` is a real, mutable per-invocation
field (`agents/invocation_context.py:205`), seeded from the App at
`runners.py:1480`. So **per-model token thresholds are reachable per session**
via the invocation context — the fail-safe "narrowest window wins" fallback the
doc hedged with is not needed for the trigger that matters. The sliding-window
interval remains App-global; that is acceptable because after Phase 1 it is only
a backstop.

**2. `token_threshold` cannot be set alone.** A model validator rejects it
without `event_retention_size`:

```
ValueError: token_threshold and event_retention_size must be set together.
```

`event_retention_size` keeps the last N **raw events** uncompacted when token
compaction fires — the token-mode analogue of `overlap_size`. Note *events*,
not turns: one turn is several events (user message, tool call, tool response,
model reply), so a tool-heavy turn can be 4–5 on its own. This knob was missing
from the original plan and is arguably the most important one in the file, since
it directly sets how much recent history the model still sees verbatim.

**3. `compaction_interval` counts user-initiated invocations**, confirming the
arithmetic on Tomas's session: at 10, his ~12-turn conversation compacted
exactly once, immediately before the summarise request.

### What shipped (Phases 1 + 2)

- `_COMPACTION_CONFIGS` now sets `token_threshold` + `event_retention_size` per
  family (1M tier: 250K/60 raw events; 200–400K tier: 120K/40), with
  `compaction_interval` raised to 40/20 so it is a backstop rather than the
  primary mechanism.
- Unknown models now resolve to the **smallest** window's config, not a
  mid-range guess — compacting too eagerly degrades an answer, overflowing the
  context fails the turn outright.
- `COMPACTION_TOKEN_THRESHOLD` env override, which fails **loudly** (logged
  warning, falls back to the family default) on a non-integer or non-positive
  value, and uses `model_copy` so it can't mutate the shared module-level config.
- [`app.py`](../../../backend/app.py) now derives the App config from
  `default_model()` instead of the hardcoded `gemini_api_name_for("gemini-2-5-flash")`.
  Verified: the deploy default resolves to `gemini-3.6-flash` → 250K/60/40/5.
  Deliberately *not* `gemini_api_name_for` — a deployment may legitimately
  default to Claude, and asserting Gemini here would fail the import on a valid
  deploy.
- 26 tests in `test_session_factories.py`, including a guard that a larger
  context window is never the more aggressive setting (catches a tier
  transposition, which is invisible in review and only shows up as mysteriously
  worse answers on the flagship model), and a guard that the App config is not
  hardcoded to one family.

Backend suite: **2865 passed**, ruff + format clean.

**Still owed and NOT done:** Phase 3 (the never-silent compaction event), the
25-turn live fidelity check, and per-session config via the invocation context
(now known to be feasible — finding 1). Phase 1+2 change *when* compaction
fires; they do not yet make it visible when it does.

> **Correction (same day).** The line above is wrong in one word: Phase 1+2
> change when compaction *would* fire. Nothing fires, because the chat Runner
> has no App. The live 25-turn check listed as "owed" was subsequently run —
> and it is what disproved the root cause. See the banner at the top and
> [compaction-wiring-and-observability.md](compaction-wiring-and-observability.md).
>
> Finding 1 below (the per-invocation config seam) also needs a caveat: it is
> real, but moot while `invocation_context.events_compaction_config` is seeded
> from `self.app` — which is `None` on this path. It becomes useful only after
> the App is wired.

## Design

### Overview

Three changes, independent and independently shippable: (1) make compaction
trigger on **token pressure** instead of turn count, (2) make the per-model
config **actually reach the session** it describes, and (3) **emit an event**
when compaction happens so it is never silent.

### Backend Changes

**Phase 1 — token-threshold trigger (`backend/adk/session.py`)**

Add `token_threshold` to each entry, sized to the model family's real window
rather than to a turn count. Keep the sliding window as the backstop it already
is (ADK prefers the token trigger when configured and falls through otherwise,
per `_run_compaction`), but raise the interval so it stops being the primary
mechanism:

```python
# Token thresholds are ~25% of the usable window: compact early enough that a
# single large turn can't blow the context, late enough that a normal expert
# conversation (ONE's is ~12 turns / tens of K) never compacts at all.
_COMPACTION_CONFIGS = {
    "gemini-": EventsCompactionConfig(
        compaction_interval=40, overlap_size=5, token_threshold=250_000,
    ),
    "gpt-5.4": EventsCompactionConfig(
        compaction_interval=40, overlap_size=5, token_threshold=250_000,
    ),
    "claude-": EventsCompactionConfig(
        compaction_interval=20, overlap_size=4, token_threshold=120_000,
    ),
    "gpt-5": EventsCompactionConfig(
        compaction_interval=20, overlap_size=4, token_threshold=120_000,
    ),
}
```

The numbers are a starting point to be validated by the Phase 3 eval, not a
claim of optimality. What matters structurally is that the *token* trigger is
the one that fires in practice and the turn count is a safety net.

**Phase 2 — make the per-model config reach the session (`backend/app.py`)**

`EventsCompactionConfig` is an `App`-level property and `App` is built once, so
a single global config cannot be model-aware. Resolve by building the App's
config from the **deployment's default model** rather than a hardcoded literal,
and — where a skill pins a different family — attach the matching config on the
per-skill `App` built in `create_agent`.

Phase 2 is deliberately scoped to *removing the hardcoded literal and proving
which config a given session actually got*. If it turns out ADK offers no
per-session seam in the pinned version, the fallback is to select the config
from the **narrowest** window across configured model tiers (fail safe: compact
too often rather than overflow), and record that as the accepted limitation.
Verify against the ADK MCP (`search_code` on `EventsCompactionConfig`,
`App.__init__`) before implementing — do not assume from this doc.

**Phase 3 — never-silent compaction (`backend/adk/callbacks.py`)**

Emit an AG-UI event when a compaction lands, carrying the turn range and the
token count that triggered it, and render it in the Activity tab as a marker
("History summarised — turns 1–18 condensed at 251K tokens"). This reuses the
existing activity-event path; it is not a new protocol surface.

The user-visible half matters as much as the telemetry: the whole failure mode
is that a degraded answer looks identical to a good one.

### CLI Surface

Long-conversation behaviour is currently only reproducible by hand-driving the
UI for twenty minutes, which is why this shipped unnoticed. Add:

```
aiplatform session compaction <session-id>     # did this session compact, when, at what token count
aiplatform skill probe --turns 25 <skill>      # drive an N-turn synthetic conversation, report fidelity
```

`session compaction` is the triage tool for the next customer report of this
shape; `skill probe --turns` is what makes the Phase 3 eval runnable in CI.
See [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## Implementation Plan

### Phase 1: Token-threshold trigger (~0.5 day)
- [ ] Add `token_threshold` to each `_COMPACTION_CONFIGS` entry; raise intervals (~15 LOC)
- [ ] Verify `token_threshold` semantics against ADK MCP before trusting this doc (~0 LOC)
- [ ] Extend `tests/unit/test_session_factories.py` for the new fields (~40 LOC)

### Phase 2: Config actually reaches the session (~0.75 day)
- [ ] Remove the hardcoded `gemini-2-5-flash` literal in `app.py` (~10 LOC)
- [ ] Resolve per-skill config in `create_agent` where the skill pins a family (~40 LOC)
- [ ] Test asserting a Claude-pinned skill gets the Claude config — the guard that would have caught this (~50 LOC)

### Phase 3: Never-silent + eval (~0.75 day)
- [ ] Emit a compaction event through the existing activity path (~50 LOC)
- [ ] Render the marker in `ActivityPanel` (~40 LOC)
- [ ] `aiplatform session compaction` + `skill probe --turns` (~80 LOC)
- [ ] 25-turn fidelity evalset asserting recall of early turns (~60 LOC)

## Migration & Rollout

**Database Migrations:** None. Compaction state lives in session events.

**Feature Flags:** `COMPACTION_TOKEN_THRESHOLD` env var overrides the computed
threshold, so dev can be tuned without a redeploy and prod can be reverted to
the old behaviour by setting it low.

**Rollback Plan:** Phases 1 and 2 are pure config resolution — revert the commit.
No persisted state changes shape, so a rollback cannot strand a session. Sessions
compacted under the new thresholds stay valid under the old code.

**Environment Variables:** `COMPACTION_TOKEN_THRESHOLD` (optional, all envs).

## Testing Strategy

### Backend Tests (pytest)
- [ ] `get_compaction_config` returns the token threshold per family
- [ ] A Claude-pinned skill resolves the Claude config, not the Gemini default (the Phase 2 regression guard)
- [ ] A synthetic 25-turn session does NOT compact below the token threshold
- [ ] A session that crosses the threshold DOES compact and emits the event

### Frontend Tests (Vitest)
- [ ] `ActivityPanel` renders the compaction marker with its turn range
- [ ] No marker rendered when no compaction occurred (guards a vacuous pass)

### Manual / live verification (non-negotiable)
Per both CLAUDE.mds: **unit-green is not proof**. This one is especially prone
to false confidence because unit tests construct sessions rather than
accumulate them.
- [ ] Drive a real 25-turn conversation against dev via `aiplatform`, then ask
      it to summarise, and confirm it cites turn-1 specifics
- [ ] Confirm the compaction event appears on the wire in a real AG-UI stream
- [ ] Re-run Tomas's actual journey (iterate to a correct answer, then ask for
      a lawyer summary) and have ONE confirm the result before cutover

## Security Considerations

Compaction summaries are derived from customer conversation content and inherit
its confidentiality: they live in the same session store, behind the same auth,
and must never be logged to any sink outside the GCP project edge (CLAUDE.md
security rule, Axiom #9). The new compaction event carries **metadata only** —
turn range and token count, never summary text — so it is safe for the Activity
tab and for a lower-trust group session.

## Performance Considerations

Larger prompts cost latency and tokens: a 250K-token prompt is materially slower
to first token than a compacted 20K one. This is a deliberate trade of speed for
correctness on long conversations, bounded by the threshold. Most conversations
never reach it and are unaffected — and today's behaviour spends compaction cost
(an LLM summarisation call) on conversations that did not need it at all, so
short-to-medium sessions should get *faster*.

## Success Criteria

- [ ] Backend tests passing (`cd backend && make test-fast`)
- [ ] Frontend tests passing (`npm run test:run`)
- [ ] Lint clean (`make lint`, `npm run quality:check`)
- [ ] A 25-turn conversation summarises with turn-1 fidelity, verified live on dev
- [ ] The compaction config applied matches the session's model family
- [ ] Compaction is visible in the Activity tab when it happens
- [ ] Tomas's original journey re-run and confirmed by ONE before 1 Sept

## Open Questions

- **Fault A — the empty thread on resume (2026-08-05, not reproducing 08-06).**
  Not reproducing is not the same as fixed. Before cutover, establish whether
  this was an ADK-session/Firestore-mirror divergence, a deploy-window artefact,
  or a genuine race. If no cause is found, it is a latent P0 and should get its
  own doc rather than be assumed healed.
- **Should the agent be able to retrieve its own compacted history on demand?**
  A `search_conversation_history` tool would let it reach past a summary when a
  user says "what did I say about X earlier". Strictly better than tuning
  thresholds, and strictly more work. Deferred, not dismissed.
- **Is ADK's default summariser good enough for legal/contract content?**
  A generic summariser may drop exactly the clause-level specifics ONE cares
  about. `EventsCompactionConfig.summarizer` accepts a custom `BaseEventsSummarizer`
  — worth evaluating once Phase 1 stops compaction from firing spuriously.
- **Dana's question, still unanswered:** summarisation vs. Vertex Search
  embeddings for large document sets. Related but separable — that is about
  *document* context, this is about *conversation* context. Needs its own eval.

## Related Documents

- UAT source record (internal notes) — the meeting this came from
- [`backend/adk/session.py`](../../../backend/adk/session.py) — where compaction is configured
- [`backend/CLAUDE.md`](../../../backend/CLAUDE.md) — model-reliability and verification-bar rules
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface this extends
