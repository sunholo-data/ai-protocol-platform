# Compaction Tuning Console — levers you can actually pull

**Status**: Planned
**Priority**: P1 — compaction is now live and unmeasured; the tuning values are educated guesses nobody can change without a deploy
**Estimated**: ~2 days (M1 ~0.75d runtime config, M2 ~0.75d admin UI, M3 ~0.5d per-skill override)
**Scope**: Fullstack
**Dependencies**: [compaction-wiring-and-observability](compaction-wiring-and-observability.md) M1/M2/M4 — all shipped. This is only useful *because* compaction now runs and is observable.
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

Compaction is a deep well. It has at least six interacting knobs, its behaviour
depends on conversation shape rather than on any single number, and the only way
to find good values is to run real conversations and look. Right now every one
of those knobs is a **code constant**, so an experiment costs a commit, a build,
and a deploy — and the values currently shipped are educated guesses that have
never been measured against a real workload.

Two of them are already known to be wrong-ish, discovered *while implementing
them*:

- `event_retention_size=60` was chosen to be generous with verbatim history. It
  turns out to set a **floor on whether compaction fires at all**:
  `_events_to_compact_for_token_threshold` returns `[]` when
  `len(candidate_events) <= event_retention_size`, regardless of token pressure.
  So no conversation compacts under ~15 turns however large it is, and when it
  does fire it condenses only the oldest few events (measured live: summaries of
  360 and 985 chars).
- `token_threshold=250_000` has never been crossed by a real conversation, so
  whether it is the right pressure point is unknown.

Neither can be explored without a redeploy. That is the actual problem: **not
that the numbers are wrong, but that finding the right ones is currently
expensive.**

**Impact:**
- **Who:** us, tuning; and ONE indirectly, since compaction quality determines
  whether a long expert conversation keeps its detail.
- **How significant:** medium — no user-facing breakage, but it blocks the
  measurement the wiring work was supposed to enable.

## Goals

**Primary Goal:** Change any compaction lever from the admin panel and see the
effect on the next conversation, with no deploy.

**Success Metrics:**
- Every runtime-controllable lever changeable in <1 minute, no restart.
- A tuning experiment (change → drive a conversation → read the Activity marker)
  takes minutes, not a deploy cycle.
- Per-skill override, so an experiment on `one-ppa-expert` can't degrade every
  other skill.
- Bad input cannot break chat — an invalid value falls back loudly to the coded
  default.

**Non-Goals:**
- Exposing this to end users. Admin-only; it changes answer quality.
- Auto-tuning. Humans read the results and decide.
- Making `compaction_interval` / `overlap_size` runtime-controllable — see the
  constraint below; they are backstops now and not worth the machinery.

## What levers exist (the inventory)

| Lever | What it controls | Today | Runtime-able? |
|---|---|---|---|
| `token_threshold` | prompt size that triggers compaction | code + `COMPACTION_TOKEN_THRESHOLD` env (needs restart) | ✅ |
| `event_retention_size` | raw events kept verbatim after a token compaction | code | ✅ |
| **summarizer prompt** | *what the summary is told to preserve* | code constant | ✅ |
| **summarizer model** | which model condenses (pinned `pro`) | code | ✅ |
| compaction_interval | turn-count backstop trigger | code | ❌ App-level |
| overlap_size | invocations kept raw (sliding window) | code | ❌ App-level |
| enabled | compaction off entirely | — | ✅ (unset threshold) |

The two that are *not* runtime-able are the sliding-window pair, and that is
acceptable: since the wiring work the token trigger is the primary mechanism and
the interval is a deliberate backstop (40/20). If they ever need tuning, the env
var + restart path still exists.

**The most interesting lever is the prompt**, and it is the one nobody would
guess is a lever. What the summariser is *told to preserve* determines what
survives far more than any threshold does — and for contract review it is the
difference between "discussed pricing" and "strike price EUR 48.62/MWh indexed
to CPI".

## The constraint, and why this is nonetheless cheap

`EventsCompactionConfig` lives on the `App`, and `App` is built **once at
import**. A Firestore change cannot reach it without a restart. That is the
reason this looks harder than it is.

The way through was found while implementing the wiring:
`invocation_context.events_compaction_config` is a **real, mutable,
per-invocation field** (`agents/invocation_context.py:205`), seeded from the App
at `runners.py:1480`, and it is what the pre-request `CompactionRequestProcessor`
reads (`flows/llm_flows/compaction.py:39`). And `_handle_before_agent_callback`
runs at `base_agent.py:291`, **before** `_run_async_impl` and its request
processors.

So a `before_agent_callback` can overwrite the per-request compaction config
from live settings, and the token-threshold path will honour it. We already
compose a `before_agent_callback` into every agent (`make_before_agent`), and
`ReadonlyContext` exposes `_invocation_context`. **No ADK fork, no new
mechanism.**

Everything else already exists too:
- `config/platform_config.py` — Firestore-backed settings with a 60s TTL cache,
  `invalidate_cache()`, and `update_platform_config()`.
- `admin/platform_config_routes.py` + `/admin/settings` — an admin plane with a
  working save → "Last edited by X" pattern.

This is an extension of two proven surfaces, not new plumbing.

## Design

### M1 — runtime config (backend)

Add a `compaction` block to `PlatformConfig`:

```python
class CompactionSettings(BaseModel):
    enabled: bool = True
    token_threshold: int | None = None      # None → coded per-model default
    event_retention_size: int | None = None
    summarizer_model: str | None = None     # tier or registry id
    summarizer_prompt: str | None = None    # must contain {conversation_history}
```

`make_before_agent` reads the live settings and, when any are set, replaces
`ctx._invocation_context.events_compaction_config` with a `model_copy` carrying
the overrides. Per-request copy — never mutate the shared config (that was M2's
lesson: ADK's own code mutates it in place, and the leak reached
`app.events_compaction_config`).

**Validation is load-bearing.** A prompt missing `{conversation_history}` makes
`str.format` raise *inside compaction*, i.e. inside a user's turn. A threshold of
0 is rejected by ADK's validator. Both must be caught at **write** time in the
admin route AND defended at read time — the Trap-22 lesson from
`gotcha_skill_list_500_blank_switcher`: an unvalidated admin write took down a
whole list endpoint. Invalid stored config logs loudly and falls back to the
coded default rather than failing turns.

### M2 — admin UI

A "Conversation compaction" section on `/admin/settings`, following the existing
preamble pattern (checkbox + fields + save + last-edited-by):

- **Enabled** toggle
- **Token threshold** — number, with the per-model default shown as placeholder
- **Raw events retained** — number, with the floor explained inline: *"compaction
  cannot fire until a conversation exceeds this many events"*, because that
  interaction is genuinely surprising and cost us a wrong M3 result
- **Summarizer model** — tier dropdown from the registry
- **Summarizer prompt** — textarea, defaulting to the shipped fidelity prompt,
  with a validation error if `{conversation_history}` is missing

Copy matters here: this panel changes **answer quality**, silently, for everyone.
The section needs a plain warning saying so — compaction's whole hazard is that
its effects are invisible.

### M3 — per-skill override

Experimentation wants "try this on `one-ppa-expert` only". Add the same optional
block to `SkillMetadata`; the before-agent callback prefers skill → platform →
coded default. Without this, every experiment is a production-wide change, which
in practice means nobody runs one.

### CLI

```
aiplatform compaction show [--skill X]     # effective config after precedence
aiplatform compaction set --token-threshold N [--skill X]
```

`show` matters more than `set`: with three precedence layers, "what is actually
in effect for this skill" stops being obvious, and a wrong answer there would
waste a whole experiment.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Config read is cached (60s); no per-turn cost. |
| 2 | EARNED TRUST | +1 | Lets us tune toward keeping detail rather than guessing at it. |
| 3 | SKILLS, NOT FEATURES | +1 | M3 makes compaction a per-skill property, like model and thinking tier. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Summarizer model becomes a real choice instead of a hardcoded pin. |
| 5 | GRACEFUL DEGRADATION | +1 | Invalid config degrades to the coded default; never fails a turn. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK's own per-invocation config seam and our existing platform-config plane. |
| 7 | API FIRST | +1 | Admin route + CLI over the same settings. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Only useful because M4 made compaction visible; the two compose. |
| 9 | SECURE BY CONSTRUCTION | 0 | Admin-only, existing auth. The prompt field is operator-authored, not user input. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client renders a form; all precedence resolved server-side. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

## Implementation Plan

### M1 — runtime config (~0.75d)
- [ ] `CompactionSettings` on `PlatformConfig` (~40)
- [ ] `before_agent_callback` applies overrides to the per-invocation config (~50)
- [ ] Write-time validation in the admin route + read-time fallback (~40)
- [ ] Tests: override applies; invalid config falls back loudly; shared config never mutated (~90)

### M2 — admin UI (~0.75d)
- [ ] Compaction section on `/admin/settings` (~120)
- [ ] Inline explanation of the retention floor; quality warning (~20)
- [ ] Tests incl. the missing-placeholder validation error (~70)

### M3 — per-skill override + CLI (~0.5d)
- [ ] `SkillMetadata.compaction`; skill → platform → default precedence (~50)
- [ ] `aiplatform compaction show/set` (~60)
- [ ] Precedence test (~40)

## Testing Strategy

- [ ] Backend: override reaches `invocation_context`; precedence correct; invalid values fall back
- [ ] Backend: the shared module config is never mutated (M2's regression, re-asserted here)
- [ ] Frontend: validation blocks a prompt with no `{conversation_history}`
- [ ] **Live**: change the threshold in admin, drive a conversation, see the
      `HISTORY_COMPACTED` marker change accordingly — the acceptance test, and
      only possible because M4 shipped

## Security Considerations

Admin-only, behind the existing admin plane. The summarizer prompt is
operator-authored and is **not** user input — but it is injected into a model
call over customer conversation content, so it must never be exposed to a
non-admin surface or accepted from a non-admin route. Compaction settings
themselves are metadata; the `HISTORY_COMPACTED` event stays metadata-only
regardless of configuration.

## Open Questions

- **What should `event_retention_size` actually be?** The question this doc
  exists to let us answer. High protects verbatim fidelity but delays compaction;
  low compacts eagerly and loses detail.
- **Should the summarizer prompt be per-skill from the start?** A PPA prompt and
  a code-assistant prompt want different things. M3 makes it possible; whether
  it's used is a product call.
- **Do we need a dry-run** ("summarise this session with these settings and show
  me the result" without mutating it)? That would make tuning dramatically
  faster and is the natural M4 if this proves useful.

## Related Documents

- [compaction-wiring-and-observability](compaction-wiring-and-observability.md) — the prerequisite; M4's marker is the feedback signal
- [conversation-context-fidelity](conversation-context-fidelity.md) — the tuning table these levers override
- UAT source record (internal notes)
