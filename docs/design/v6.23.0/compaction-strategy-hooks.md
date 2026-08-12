# Compaction Strategy Hooks — context management as a pluggable strategy

**Status**: Proposed
**Priority**: P1 — the tuning console (1b) exposes scalars; the interesting choices are not scalars
**Estimated**: ~3 days (M1 ~1d protocol + registry, M2 ~1d strategies, M3 ~1d selection/eval). Retrieval track scoped separately.
**Scope**: Backend, with a template-facing extension point
**Dependencies**: [compaction-wiring-and-observability](compaction-wiring-and-observability.md) (shipped) · composes with [compaction-tuning-console](compaction-tuning-console.md)
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

The tuning console makes numbers adjustable. But the numbers are the least
interesting part of context management. The real questions are structural:

- **What is kept verbatim?** All human turns? The last N? The first N *and* the
  last N, summarising the middle? Tool findings but not tool chatter?
- **What replaces the rest?** An LLM summary, an extracted fact list, or nothing?
- **Should some of it be retrievable rather than resident?** Semantic search over
  the conversation, so a compacted turn can be *fetched back* on demand instead
  of being permanently gone.
- **Where does memory sit?** Vertex Memory Bank already does cross-session
  recall via `load_memory_tool` / `preload_memory_tool`. Compaction is
  within-session. These two overlap and nobody has decided how.
- **And the one that makes it hard: compaction IS intelligence.** Deciding what
  matters in a 40-turn contract negotiation is a reasoning task, not a
  truncation. So there is a genuine quality/latency/cost trade in the *middle of
  a user's turn* — a `pro` summariser is better and slower, and it runs while
  someone waits.

None of these are expressible as a threshold. They are strategies, and today
there is exactly one, hardcoded.

**This also matters downstream.** The template ships to forks whose context
shape is nothing like ONE's — a tutoring fork's conversations are long and
chatty, a code assistant's are tool-heavy, a legal reviewer's are dense with
figures. A single shipped policy cannot serve all of them, and forking
`compaction_summarizer.py` is the wrong extension mechanism.

## The enabling constraint (why this is buildable)

ADK gives exactly one hook — `BaseEventsSummarizer.maybe_summarize_events(events)`
— and at first glance it looks too narrow: one method, returns one event.

It isn't, for two reasons found by reading `llm_event_summarizer.py`:

1. **`compacted_content` is an arbitrary `Content`.** Nothing requires it to be
   an LLM summary. A strategy may return verbatim turns, a hybrid of verbatim
   human messages plus a summary of the rest, an extracted fact list, or
   structured text. Whatever it returns *replaces* the compacted range in the
   model's request.
2. **Returning `None` declines the compaction.** A strategy can refuse — e.g.
   "never compact a conversation containing an unresolved obligation analysis".

So "keep all human turns verbatim, summarise the assistant's" is implementable
**inside the existing hook**, with no ADK fork.

**Be honest about the limit:** we do *not* choose which events ADK offers up.
That slicing is ADK's, governed by `event_retention_size` / `overlap_size` /
`compaction_interval`. We control the *replacement*, completely; we influence
the *selection* only through config. Any strategy needing different selection
(e.g. "always keep the first 3 turns regardless of age") must implement it by
including those turns verbatim in its output, not by changing the slice.

## Goals

**Primary Goal:** Context-management strategy is a named, swappable,
independently testable component — selectable per platform and per skill, and
extendable by a fork without touching platform code.

**Success Metrics:**
- ≥3 strategies shipped and switchable without a deploy.
- A fork can register a strategy from its own module with no platform edit.
- A/B two strategies over the same recorded conversation and compare, offline.
- Strategy choice, and the model it uses, visible in the `HISTORY_COMPACTED`
  event.

**Non-Goals:**
- Auto-selecting a strategy. Humans choose; we make choosing cheap.
- Replacing Memory Bank. Cross-session recall stays where it is; this doc says
  how the two relate.
- Forking ADK compaction. One hook, used well.

## Design

### The protocol

```python
class CompactionStrategy(Protocol):
    name: str
    async def compact(self, events: list[Event], ctx: StrategyContext) -> Content | None:
        """Return the content that REPLACES these events, or None to decline."""
```

`StrategyContext` carries the skill id, the resolved model chain, and the
settings block — so a strategy picks its own model rather than inheriting one.

Registered like every other extension point in this repo
(`a2ui_result_render.register` is the precedent, including `clear_registry()`
for tests and a listing accessor for the CLI):

```python
register_strategy(FidelitySummary())          # name: "fidelity-summary"
register_strategy(HumanTurnsVerbatim())       # name: "human-turns"
```

A single thin `StrategyEventSummarizer(BaseEventsSummarizer)` adapts the
selected strategy onto ADK's hook, keeping ADK's event construction and
timestamps. Strategies never see ADK's `Event` plumbing beyond the input list.

### Strategies to ship

| Name | Keeps | Model | For |
|---|---|---|---|
| `fidelity-summary` | LLM summary, specifics-preserving, tool findings included | `pro` | today's behaviour; the default |
| `human-turns` | **every human message verbatim**, assistant/tool turns summarised | `lite` | cheap and fast; the user's own words are usually the load-bearing part |
| `head-tail` | first N + last M verbatim, middle summarised | `lite` | conversations that establish premises early and drift |
| `facts-only` | extracted structured facts (identifiers, figures, dates, decisions) | `pro` | contract/legal work — the ONE shape |
| `none` | declines; never compacts | — | debugging, and a hard opt-out |

`human-turns` is the one worth arguing for: it is nearly free, needs no model
for the part that matters most, and directly addresses the failure Tomas
described — *he* wanted his own twelve prompts back, not the assistant's prose.

### Where the intelligence trade lives

Compaction runs **inside the user's turn**, so summariser latency is user-visible
latency. That makes model choice a real product decision, not a config detail:

- `lite` — sub-second, adequate when the strategy is mostly mechanical
  (`human-turns`, `head-tail`), where the model only condenses the parts nobody
  refers back to.
- `pro` — seconds, warranted when the strategy *is* the intelligence
  (`facts-only`, `fidelity-summary` over dense material).

The honest framing: **a cheap strategy with a good structure usually beats an
expensive strategy with a bad one.** `human-turns` on `lite` will likely
outperform `fidelity-summary` on `pro` for most conversations, because keeping
the user's actual words verbatim is a structural guarantee, not a model
judgement. That is a hypothesis this doc exists to let us test — not a claim.

### Retrieval — a different mechanism, deliberately scoped out

Semantic search over conversation history is **not** a compaction strategy, and
conflating them would be a design error. Compaction decides what stays resident;
retrieval fetches what didn't. They compose:

> compact aggressively (cheap, small context) **+** `search_conversation_history`
> so anything dropped is recoverable on demand

That needs a **tool**, not a summarizer hook: index session events, expose a
tool the agent calls when the user references something not in context. It is
strictly better than tuning thresholds — and strictly more work. It gets its own
doc; this one only guarantees strategies are free to assume it may exist (e.g. a
strategy may compact harder when retrieval is available).

### How memory relates (the decision nobody has made)

| | Scope | Mechanism | Status |
|---|---|---|---|
| Compaction | within a conversation | ADK summarizer hook | live |
| Memory Bank | across conversations | `load_memory_tool` / `preload_memory_tool` | live, default config |
| Conversation retrieval | within a conversation, on demand | *(a tool — doesn't exist)* | proposed |

The gap is the third row, and today compaction is silently doing that job badly:
when a user says "what did I say about indexation earlier", nothing retrieves —
they get whatever survived the summary. Worth stating plainly so it stops being
an accident. Note v6.22.0 already flags Memory Bank as running on unaudited
defaults; strategy work should not deepen the entanglement until that lands.

### Template / downstream story

The registry is the extension point, documented alongside the A2UI mapping
registry that forks already follow. A fork adds a module, calls
`register_strategy`, and selects it by name in config — no platform edit, no
rebase pain. The shipped strategies double as worked examples.

### Offline A/B — the piece that makes this pay off

```
aiplatform compaction strategies                 # list registered
aiplatform compaction replay <session-id> --strategy human-turns
aiplatform compaction compare <session-id> --strategies a,b
```

`replay` runs a strategy over a **recorded real session** and prints the content
it would produce, without touching the session. This is the difference between
tuning by deploy-and-hope and tuning by comparison. Everything learned this
sprint argues for it: the wrong root cause, the vacuous canary and the surprising
retention floor were all *measurement* failures, not implementation failures.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Makes the latency/quality trade explicit and selectable; a fast structural strategy becomes available where today only an LLM call exists. |
| 2 | EARNED TRUST | +1 | `human-turns` structurally guarantees the user's own words survive — no model judgement involved. |
| 3 | SKILLS, NOT FEATURES | +1 | Strategy becomes a per-skill property alongside model and thinking tier. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | The doc's core: strategy and model chosen together, per skill. |
| 5 | GRACEFUL DEGRADATION | +1 | A failing strategy falls back to the shipped default; `none` is an explicit opt-out. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK's own summarizer hook and this repo's existing registry convention — no third pattern. |
| 7 | API FIRST | +1 | Registry + CLI replay/compare. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Strategy name and model ride the existing `HISTORY_COMPACTED` event. |
| 9 | SECURE BY CONSTRUCTION | 0 | Strategies see conversation content, same trust boundary as the summarizer today; event stays metadata-only. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Entirely server-side. |
| | **Net Score** | **+9** | Threshold: >= +4 ✅ |

## Implementation Plan

### M1 — protocol + registry (~1d)
- [ ] `CompactionStrategy` protocol + `StrategyContext` (~60)
- [ ] `register_strategy` / listing / `clear_registry`, mirroring `a2ui_result_render` (~60)
- [ ] `StrategyEventSummarizer` adapter onto ADK's hook (~50)
- [ ] Tests incl. decline-to-compact and failure→default fallback (~100)

### M2 — the strategies (~1d)
- [ ] `fidelity-summary` (port existing), `human-turns`, `head-tail`, `facts-only`, `none` (~200)
- [ ] Per-strategy model selection via `resolve_model_chain` (~30)
- [ ] Tests: each strategy's structural guarantee (e.g. every human turn present verbatim) (~120)

### M3 — selection + replay (~1d)
- [ ] Strategy name in platform/skill settings (composes with the tuning console) (~50)
- [ ] Strategy + model in the `HISTORY_COMPACTED` event (~20)
- [ ] `aiplatform compaction strategies | replay | compare` (~120)
- [ ] Template docs for registering a fork strategy (~docs)

## Testing Strategy

- [ ] Each strategy's structural promise, asserted directly (`human-turns`: every human message appears verbatim)
- [ ] A strategy that raises falls back to the default and never fails a turn
- [ ] `none` genuinely declines (no compaction event appears)
- [ ] Registry: fork registration works without platform edits
- [ ] **Live**: run two strategies over the same real session via `replay` and diff — the acceptance test

## Open Questions

- **Is `human-turns` actually better than `fidelity-summary`?** The central
  hypothesis. Cheap, fast, structurally guaranteed — but loses the assistant's
  derived conclusions, which in a contract analysis may be the valuable part.
  `replay` over real ONE sessions decides it, not argument.
- **Should a strategy be able to influence ADK's slice?** Currently no. If
  `head-tail` proves valuable, "always keep the first N" would want real
  selection control, which ADK does not offer — it would mean carrying those
  turns verbatim forward through every compaction. Workable, but it grows.
- **Retrieval first instead?** If `search_conversation_history` existed,
  aggressive compaction becomes safe and most of this matters less. Genuinely
  arguable that retrieval is the higher-value track and strategies are a
  consolation prize for not having it.
- **Per-skill defaults for the template?** A tutor fork and a legal fork want
  different defaults out of the box; shipping one and documenting the rest may
  be enough.

## Related Documents

- [compaction-tuning-console](compaction-tuning-console.md) — scalars; strategy selection plugs into the same settings block
- [compaction-wiring-and-observability](compaction-wiring-and-observability.md) — the hook this builds on, and the event that reports it
- [gemini-enterprise-agent-platform-adoption](../v6.22.0/gemini-enterprise-agent-platform-adoption.md) — Memory Bank config track; overlaps the memory row above
