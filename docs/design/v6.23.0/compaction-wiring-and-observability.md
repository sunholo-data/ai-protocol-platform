# Compaction Wiring & Observability — the config was never connected

**Status**: Planned (spike complete, failing guard committed)
**Priority**: P0 — supersedes the compaction half of [conversation-context-fidelity](conversation-context-fidelity.md), whose root cause is **disproved**
**Estimated**: ~3.5 days (Spike ✅ done · Phase 1 ~0.5d · **Phase 1b ~1d, added 2026-08-06** · Phase 2 ~1d · Phase 3 ~1d)
**Scope**: Backend
**Dependencies**: None. Touches `backend/adk/agui.py`, `backend/app.py`, `backend/adk/callbacks.py`.
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

**Conversation history is never compacted on the chat path. It has never been
compacted. The entire compaction configuration is dead code.**

`backend/app.py` builds an `App` carrying `events_compaction_config`, and
`backend/adk/session.py` maintains a careful per-model tuning table feeding it.
Neither has ever affected a single chat turn.

### Why (confirmed in pinned google-adk 1.31.1 + ag_ui_adk)

The AG-UI chat path builds its agent in
[`adk/agui.py`](../../../backend/adk/agui.py) via `build_agui_adk_agent`, which
calls `ADKAgent(adk_agent=..., ...)`. `ag_ui_adk` populates its internal `_app`
**only** from the `from_app()` classmethod (`adk_agent.py:395`). On our path
`_app` stays `None`, so `_create_runner` takes the component branch:

```python
if self._app is not None:
    request_app = self._app.model_copy(update={'root_agent': adk_agent})
    return Runner(app=request_app, **service_kwargs)
else:
    # ours
    return Runner(app_name=app_name, agent=adk_agent, **service_kwargs)
```

A Runner built that way has `runner.app is None`, which disables **both**
compaction triggers:

```python
runners.py:622   if self.app and self.app.events_compaction_config:            # -> False
runners.py:1480  events_compaction_config=(self.app.events_compaction_config if self.app else None)  # -> None
```

The `App` in `app.py` is consumed by ADK's own FastAPI surface, not by our
AG-UI stream. Two separate paths; only one has the config.

### Evidence (measured, then pinned)

**Live A/B, 2026-08-06, real backend, `general-assistant`:** a 25-turn
conversation driven at `compaction_interval=10` produced **100 events across 25
invocations and zero compaction events**. A working sliding window must have
fired at turns 10 and 20. Running the same 25 turns under the new
token-threshold config produced an identical result — because the config was
inert in both arms. *The A/B is what exposed this;* a single-arm run would have
been read as a pass.

**Hermetic guard**, `tests/unit/test_compaction_reaches_chat_runner.py`
(`make adk-conformance`), currently **3 failed, 1 passed**:

| Assertion | Now |
|---|---|
| chat Runner carries an App | FAIL — `runner.app is None` |
| its App has `events_compaction_config` | FAIL |
| that config matches `app.py`'s | FAIL |
| Runner still gets the real services | PASS *(control — services ARE wired; the failures are specific to the App)* |

### What this corrects

[conversation-context-fidelity.md](conversation-context-fidelity.md) attributed
Tomas's context loss to `compaction_interval=10` discarding turns 1–10. **That
root cause is disproved** — compaction never ran, so it discarded nothing. That
doc's Phase 1/2 shipped a better tuning table and fixed a genuine `app.py`
hardcode, but both are **inert for chat** until this doc's Phase 1 lands.

The failure mode was: correct reading of ADK source, never checked that the
wiring reached it. A unit test asserting `get_compaction_config` returns the
right values passes happily for a config nobody reads.

### The impact is the opposite of what we assumed

We assumed over-aggressive compaction. The reality is **no compaction at all**,
so context grows unbounded and the only backstop is the model's own limit. That
fits Dana's UAT complaint far better than Tomas's:

> "The most common error we were having was a limitation error when you were
> trying to analyze a big document or a set of documents that were exceeding the
> size limit."

We told her ADK's compaction would handle that. It is not connected.

**Impact:**
- **Who:** every user in a long conversation or working over large documents.
- **How significant:** P0. A hard context-limit failure is a dead end mid-task,
  and it is the failure ONE reported most often on v5.
- **Also:** `runner.app is None` disables *every* App-level ADK feature —
  plugins, resumability, context caching — not only compaction.

## Goals

**Primary Goal:** The chat path's Runner carries the deployment's `App`, so
compaction (and every other App-level config) actually applies — and when a
compaction happens, it is observable.

**Success Metrics:**
- `make adk-conformance` passes all four assertions (today 3 fail).
- A 25-turn conversation at a deliberately low threshold produces ≥1 compaction
  event — the direct inverse of today's measured zero.
- A long conversation degrades by summarising rather than by hitting a context limit.
- Chat history still persists (Vertex sessions unchanged) — the primary risk.

**Non-Goals:**
- Re-tuning thresholds. Numbers land in the sibling doc; this is about wiring.
- Explaining Tomas's context loss. Its cause is now **unknown again** — see Open Questions.
- Adopting plugins/resumability just because `from_app` exposes them.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Compaction adds an occasional summarisation call but shrinks prompts thereafter; roughly neutral. |
| 2 | EARNED TRUST | +1 | A hard context-limit failure mid-task is the sharpest possible breach; graceful degradation replaces it. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure under every skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Per-model context budgets finally take effect. |
| 5 | GRACEFUL DEGRADATION | +1 | The axiom, literally: summarise instead of failing. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses `ADKAgent.from_app()`, the library's own supported seam, rather than reaching past it. |
| 7 | API FIRST | 0 | No new endpoint. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Phase 3 makes compaction visible; today it is undetectable without reading raw session events. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access; summaries share the session's gate. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Entirely backend. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Build the AG-UI agent with `ADKAgent.from_app(app, ...)` so the App reaches the
Runner; verify against the failing guard; then emit a compaction event so the
behaviour is observable rather than inferred.

**Observability is sequenced FIRST after the wiring**, deliberately. Every wrong
turn in this investigation — a weak canary, a mis-attributed root cause, hours
against the wrong backend — traced to compaction being invisible. We should not
re-tune anything we cannot watch.

### Phase 1 — wire the App (`adk/agui.py`)

Replace `ADKAgent(adk_agent=agent, ...)` with `ADKAgent.from_app(app, ...)`,
keeping every explicit service argument.

`from_app` copies the App per request with the skill's agent swapped in
(`model_copy(update={'root_agent': adk_agent})`), which is exactly the shape we
need: one declared App, per-skill root agent.

**The risk that matters.** `build_agui_adk_agent` exists to stop `ag_ui_adk`
falling back to silent in-memory services, and it carries two load-bearing
settings — `delete_session_on_cleanup=False` and `session_timeout_seconds=86400`
— whose comment records a real incident (19 of 75 conversations permanently
deleted on test). **A regression here loses chat history, which is far worse
than the bug being fixed.** `from_app` must be verified to preserve all of it;
the guard's fourth assertion covers services, and Phase 1 adds one for the
cleanup settings.

Importing `app` into `agui.py` also risks an import cycle (`app.py` imports from
`adk/`). If so, take the App via a parameter or accessor rather than a module
import — do not paper over it with a deferred import inside the function without
checking what else that drags in.

### Phase 1b — the summarizer (added 2026-08-06, after reading the implementation)

Turning compaction on means adopting a summarisation strategy, and ADK's
default one is wrong for this platform in two specific ways. Both were found by
reading `google/adk/apps/llm_event_summarizer.py` and confirmed at runtime;
neither was known when this doc was first written.

**What the strategy actually is.** One LLM call over the events being
compacted, flattened to `"{author}: {text}"` lines, with a fixed prompt asking
for something "concise" that captures "the essence". The resulting summary
event REPLACES those raw turns in every later request. The model defaults to
`agent.canonical_model` — the compacting skill's own.

**Problem 1 — tool results are dropped entirely.**
`_format_events_for_prompt` keeps only `part.text`; `function_call` and
`function_response` parts are skipped. On this platform the substance IS the
tool output: extracted clauses, contract comparisons, obligation timelines,
BigQuery results. Compaction as-shipped would summarise the conversation
*around* an analysis and discard the analysis. For ONE that is the whole value
of the turn.

**Problem 2 — the default summarizer mutates a shared config.**
`_ensure_compaction_summarizer` does `config.summarizer = LlmEventSummarizer(
llm=agent.canonical_model)` **in place**. Our configs are module-level
singletons in `adk/session.py`, and `from_app` shallow-copies the App per
request, so the object is shared. Verified at runtime: the mutation leaks into
later callers AND into `app.events_compaction_config`. Consequence — the first
skill to compact pins its model as the summarizer for every skill afterwards; a
`lite` front door would leave Claude and `pro` skills summarising on flash-lite
for the container's lifetime.

**Fix.** Supply an explicit summarizer (`adk/compaction_summarizer.py`):
- subclass `LlmEventSummarizer` and override only `_format_events_for_prompt`
  to include tool calls and results (capped, and *labelled* when capped — a
  silent truncation would let the summariser state a partial finding
  confidently);
- a prompt that preserves specifics over brevity, because concision is exactly
  wrong for contract review: a strike price paraphrased away is
  indistinguishable from one never said;
- pinned to `pro` via `resolve_model_chain`, so history condenses the same way
  regardless of which skill is answering when the threshold trips, with retry +
  fallback like every other model call (backend/CLAUDE.md);
- setting it explicitly also makes ADK's mutating branch return early, closing
  Problem 2. `get_compaction_config` additionally returns a `model_copy` so the
  singleton can never be mutated even if the summarizer fails to build.

**Non-goal:** replacing ADK's compaction. We override one method and supply one
config field (Axiom #6).

### Phase 2 — verify against the guard, then live

Guard green, then re-run the live A/B with `COMPACTION_TOKEN_THRESHOLD` set low
enough to force firing, and assert compaction events appear in the session. The
inverse measurement to today's zero is the acceptance test.

### Phase 3 — never-silent compaction (`adk/callbacks.py`)

Emit an AG-UI event when a compaction lands, carrying the turn range and
triggering token count, rendered as an Activity marker ("History summarised —
turns 1–18 condensed at 251K tokens"). Metadata only, never summary text, so it
is safe for a lower-trust group session.

### CLI Surface

```
aiplatform session compaction <session-id>   # did this session compact, when, at what token count
```

The triage tool this investigation needed and did not have.

## Implementation Plan

### Spike (~done)
- [x] Establish empirically that compaction never fires (live 25-turn A/B)
- [x] Identify the seam (`_app` is None → component-based Runner)
- [x] Commit the failing hermetic guard under `make adk-conformance`

### Phase 1: Wire the App (~0.5 day)
- [ ] `build_agui_adk_agent` → `ADKAgent.from_app(app, ...)` (~40 LOC)
- [ ] Resolve/avoid the `app.py` ↔ `adk/agui.py` import cycle
- [ ] Guard assertion for `delete_session_on_cleanup` / `session_timeout_seconds` (~40 LOC)
- [ ] `make adk-conformance` fully green (remove the `xfail(strict=True)` marker)

### Phase 1b: Summarizer (~1 day)
- [ ] `adk/compaction_summarizer.py` — tool-aware formatter + fidelity prompt (~120 LOC)
- [ ] `get_compaction_config` returns a copy carrying the explicit summarizer (~30 LOC)
- [ ] Test: tool calls/results appear in the summariser's input (the Problem-1 guard)
- [ ] Test: the shared config is never mutated across callers (the Problem-2 guard)
- [ ] Test: an unresolvable model degrades to ADK's default, never raises

### Phase 2: Verify (~1 day)
- [ ] Live A/B with a forced low threshold; assert compaction events appear
- [ ] Confirm chat history still persists and resumes across turns
- [ ] Confirm no TTFT regression from the per-request App copy

### Phase 3: Observability (~1 day)
- [ ] Compaction event through the existing activity path (~50 LOC)
- [ ] `ActivityPanel` marker (~40 LOC)
- [ ] `aiplatform session compaction` (~60 LOC)

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** `COMPACTION_TOKEN_THRESHOLD` (already shipped) tunes or
effectively disables the token trigger without a redeploy.

**Rollback Plan:** Phase 1 is a single construction-site change; revert restores
today's behaviour exactly (no compaction). No persisted state changes shape, so
sessions written under either version stay readable.

**Environment Variables:** None new.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `make adk-conformance` — all four assertions (the spike guard)
- [ ] Cleanup settings preserved through `from_app` (history-loss regression)
- [ ] A session crossing the threshold emits a compaction event
- [ ] Existing session/agui suites unaffected

### Manual / live verification (non-negotiable)
Unit-green is what let this ship inert in the first place — `test_session_factories.py`
was green throughout, for a config nobody read.
- [ ] Force a low threshold; drive 25 turns; assert ≥1 compaction event in the session
- [ ] Same conversation retains early-turn detail through the compaction
- [ ] Chat history resumes correctly after a restart

## Security Considerations

Summaries derive from customer conversation content and inherit its
confidentiality — same session store, same auth gate, never logged outside the
GCP project edge. The compaction event carries **metadata only** (turn range,
token count), never summary text.

## Performance Considerations

Compaction currently costs nothing because it never runs; enabling it adds an
occasional summarisation call, offset by smaller prompts afterwards. The
per-request `App.model_copy` is on the hot path — cheap, but measure TTFT before
and after rather than assuming.

## Success Criteria

- [ ] `make adk-conformance` green
- [ ] Backend suite green (`make test-fast`), lint clean
- [ ] ≥1 compaction event on a forced-threshold 25-turn run (inverse of today's zero)
- [ ] Chat history still persists and resumes — no repeat of the deleted-sessions incident
- [ ] Compaction visible in Activity
- [ ] No TTFT regression

## Open Questions

- ~~**What actually caused Tomas's context loss?**~~ **ANSWERED 2026-08-06 —
  and it was already fixed before the UAT.** It is the ag_ui_adk SessionManager
  sweep (`delete_session_on_cleanup=True`, 20-min idle timeout), fixed by
  `44ca9b6` on 2026-08-05 and deployed to dev that evening. Tomas's session was
  90 minutes over 12 turns; a >20-minute gap is near-certain, the sweep deleted
  the Vertex session mid-conversation, and the next turn created an empty one
  under the same threadId. He described it as deletion himself in the meeting.
  This is the same root cause as the blank-thread-on-resume report.

  Everything else was eliminated by inspection first: no naive truncation exists
  in our code or `ag_ui_adk`; delegates are wired as `sub_agents` (ADK transfer,
  same session) not `AgentTool` (which *would* isolate — fresh
  `InMemorySessionService`, state-only forwarding — but is used solely for
  search/code tools); branch segregation only sub-divides under `parallel_agent`,
  so transfer keeps the branch; and `include_contents` is never set, so it
  defaults to full history.

  **Confirmation still owed:** Tomas offered in the meeting to re-run the exact
  journey that afternoon. His result is the acceptance test — not a code review.
- **Does `from_app` change resumability or plugin behaviour by default?** It
  exposes both. We want compaction only; verify neither activates implicitly.
- **Should the A2A path (`protocols/a2a_invocation.py:203`) get the same App?**
  It builds its own Runner and likely has the identical gap.
- ~~**Is ADK's default summariser adequate for legal content?**~~ **Answered: no.**
  It drops tool results and optimises for concision. Addressed in Phase 1b.
  What remains open is whether the *replacement* is good enough — that needs a
  real compacted PPA conversation judged by someone at ONE, not a unit test.
- **Should `event_retention_size` / `overlap_size` be raised now that we know
  what the summariser loses?** Retention is the only thing guaranteed verbatim.
  Revisit once Phase 2 can measure a real compaction.

## Related Documents

- [conversation-context-fidelity.md](conversation-context-fidelity.md) — the superseded root cause; tuning table still valid
- [adk-contract-checklist.md](../v6.17.0/adk-contract-checklist.md) — the custom↔ADK seam class this belongs to
- UAT source record (internal notes)
