# Cross-Provider Model Fallback — make the Gemini safety net work for tool-using agents

**Status**: Planned
**Priority**: P1 (the fallback safety net is real only if it survives the exact case it exists for — a whole provider down)
**Estimated**: ~2–3 days
**Scope**: Backend (`adk/resilient_llm.py`, `config/models.yaml`, small `agent.py`)
**Dependencies**: the per-member `llm_request.model` rewrite (shipped 2026-07-16, commit 983fe1c) — routing is now correct; this doc is the next layer
**Created**: 2026-07-16
**Last Updated**: 2026-07-16
**Motivated by**: Mark — "I want the fallback to work cross provider if possible." When the Anthropic org usage cap took down *every* Claude-tier skill, the Gemini fallback fired but a tool-using thinking agent then crashed on a `tool_call_id` error. The safety net must survive a full-provider outage, which is the only time it truly matters.

## Problem Statement

`ResilientLlm` (MODEL-RELIABILITY M3, v6.7.0) walks a residency-gated fallback chain: on a classified failure it retries transient errors, then falls to the next model. Two bugs this week (both fixed) made the chain fire at all:
1. An Anthropic **org usage/billing cap** arrives as a 400 `BadRequest` (not a 429) and was mis-classified `fallbackable=False` → dead turn (fixed: capacity 4xx → fallbackable).
2. The **same `llm_request` was passed to every member**, and ADK stamps `llm_request.model` once with the primary. A Gemini fallback was called with `model=claude-opus-4-8` → Vertex routed it to its anthropic Model-Garden publisher → 404 (fixed: rewrite `llm_request.model = member.model` per member).

With routing correct, a **`MODEL_FALLBACK: claude-opus-4-8 → gemini-2.5-flash-lite`** now fires on the wire. But a **tool-using** thinking agent then fails with:

```
litellm.BadRequestError: AnthropicException - 'tool_call_id'
```

**Root cause (verified in ADK source, `google/adk/models/lite_llm.py`):** `_content_to_message_param` builds tool messages from `part.function_call.id` / `part.function_response.id`, via `tool_call_id = part.function_call.id or ""`. **Gemini's `functionCall` parts carry no id** (unlike OpenAI `tool_calls` and Anthropic `tool_use`, which require a matching id linking a call to its result). So once a Gemini fallback produces a tool call and its result enters the history, a *subsequent* Anthropic call (or the converse) sees an **empty / non-matching `tool_call_id`** — Anthropic requires every `tool_result` to reference a `tool_use` id, and litellm's transformer raises `'tool_call_id'`.

The three providers model tool exchanges differently and the ids don't round-trip:

| Provider | Call shape | Result shape | Id |
|---|---|---|---|
| **Anthropic** | `tool_use` block (`id`) | `tool_result` block (`tool_use_id`) | **required, must match** |
| **OpenAI** | `tool_calls[]` (`id`) | `role:"tool"` (`tool_call_id`) | **required, must match** |
| **Gemini** | `functionCall` (name+args) | `functionResponse` (name) | **none — matched by name/order** |

So a fallback that crosses providers **mid-tool-loop** carries a tool history one side can't validate. This is a **recurring pattern, not a one-off**: it will bite every cross-provider hop (Claude→Gemini, GPT→Gemini, Gemini→Claude) for any tool-using skill — which is most of the ONE specialists.

## Goals

**Primary goal:** the fallback chain produces a working answer **across providers** for a tool-using agent — the safety net survives a full-provider outage (the Anthropic org cap being the live example), not just a single-model blip.

**Success metrics:**
- A tool-using thinking skill (`one-ppa-expert`, thinking=claude-opus) whose Anthropic provider is fully capped **answers via the Gemini fallback** — no `tool_call_id` RUN_ERROR — verified on a real stream (`make elicitation-e2e`-style harness).
- Single-model failures (overload/rate-limit) stay **in-provider** (claude-opus→claude-sonnet) — no needless cross-provider hop, no quality cliff.
- No regression to the non-tool path (front door, plain chat) or to same-provider fallback.
- < 30s total failover (Axiom #5 budget) preserved.

## Axiom Alignment

| # | Axiom | Score | Note |
|---|-------|-------|------|
| 1 | INSTANT FEEL | 0 | Failover latency-bounded already; unchanged. |
| 2 | EARNED TRUST | +1 | A capped provider yields a real answer instead of a dead turn — the reliability promise holds under the worst case. |
| 3 | SKILLS, NOT FEATURES | +1 | One chain mechanism serves every skill; no per-skill fallback code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Same-provider ladder keeps quality close on a blip; cross-provider only on a true outage. |
| 5 | GRACEFUL DEGRADATION | +1 | The core of the doc: degrade to a working provider under a full outage, within the failover budget. |
| 6 | PROTOCOL OVER CUSTOM | 0 | We normalize to ADK's own `google-genai` Content contract + provider id conventions — not a new format. |
| 7 | API FIRST | +1 | Testable headlessly: unit tests on the sanitizer + a real cross-provider stream, before any UI. |
| 8 | OBSERVABLE BY DEFAULT | +1 | `MODEL_FALLBACK` already emits from/to + code; add a `cross_provider` + `sanitized` flag. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access; egress residency stays gated by `resolve_model_chain`. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend-only; client unaffected. |

**Net: +6** (threshold +4). No −1s. Hard-fail checks pass.

## Standards Compliance Check

No new format invented. The fix operates on **ADK's `google.genai.types.Content`** (the canonical in-request representation) and each provider's **documented tool-id convention** (Anthropic `tool_use`/`tool_result` id match; OpenAI `tool_call_id`; Gemini id-less, name-matched). Verified against installed ADK `lite_llm.py` (`_content_to_message_param`, `_function_call_to_part`) — the authoritative version in `backend/.venv`.

## Design

### Overview — a two-layer strategy that keeps cross-provider working

**Layer 1 — same-provider ladder first (avoid the problem for the common case).**
Reorder each smart/fast model's fallbacks so the FIRST rungs are same-provider
(claude-opus-4-8 → claude-sonnet-5 → claude-haiku), and a cross-provider Gemini
rung is LAST. A single-model overload/rate-limit (the common failure) then falls
to a sibling Anthropic model — same tool-id convention, zero format risk, minimal
quality drop. Cross-provider is reached only when the WHOLE provider is down
(the org cap) — which is exactly when we must cross.

**Layer 2 — sanitize the request at a cross-provider boundary (make crossing valid).**
When `ResilientLlm` is about to call a member whose provider differs from where
the tool history was produced, transform `llm_request.contents` so the tool
exchange validates for the target:
- **Backfill ids.** Give every `functionCall` Part a stable `id` (e.g.
  `call_{n}` by order) and set the matching `functionResponse.id` to the same —
  so ADK's `_content_to_message_param` emits a matched `tool_call_id` pair for
  OpenAI/Anthropic targets. (Gemini targets ignore ids, so this is a no-op there.)
- **Drop orphans.** Remove a `functionCall` with no following `functionResponse`
  (a mid-flight call the primary never got to answer) and vice-versa — a dangling
  tool_use with no tool_result is invalid for Anthropic.
- **Idempotent + typed.** Operates on a shallow copy of contents; never mutates
  the caller's request beyond the model id we already rewrite.

**Layer 3 — last-resort clean restart.** If sanitation can't yield a valid tool
history (e.g. a corrupt/partial exchange), strip tool turns entirely and keep the
user+model TEXT so the fallback answers from clean context. A weak text answer
beats a RUN_ERROR (never-silent). Emitted as `MODEL_FALLBACK reason=tool_history_stripped`.

```
ResilientLlm.generate_content_async(llm_request):
  for member in chain:
     if provider(member) != provider_of_history(llm_request):   # cross-provider hop
         llm_request = _sanitize_for_cross_provider(llm_request)  # backfill ids, drop orphans
     llm_request.model = member.model            # (shipped)
     try: yield from member.generate_content_async(llm_request) ; return
     except -> classify -> retry / next member
```

### Backend Changes

1. **`config/models.yaml`** — reorder fallbacks to same-provider-first:
   `claude-opus-4-8: [claude-sonnet-5, claude-haiku-*, gemini-flash-lite]` (the
   Gemini rung stays LAST; residency gate still drops non-EU rungs under
   eu-strict). Same for the OpenAI smart/fast entries.
2. **`adk/resilient_llm.py`** — add `_sanitize_for_cross_provider(llm_request)`
   (id backfill + orphan drop) invoked only when the member's provider differs
   from the history's; add the last-resort tool-history strip; extend the
   `MODEL_FALLBACK` event with `cross_provider: bool` + `sanitized: str`.
3. **`adk/model_errors.py`** — no change (capacity-4xx fix already shipped).
4. Provider-of-history detection: infer from the contents' most recent tool
   Part shape, or thread the primary provider through (the chain knows its
   members' providers).

### API / Frontend Changes

None. Backend-only. The `MODEL_FALLBACK` CUSTOM event already renders in the
Activity tab; the added flags are observability-only.

### Architecture Diagram

```
 chain: [claude-opus] → [claude-sonnet] → [claude-haiku] → [gemini-flash-lite]
          |  overload/rate-limit stays here ────────────┘        |
          |  ORG CAP (all Anthropic down) ───────────────────────┘ cross-provider
                                                                   ↳ _sanitize_for_cross_provider
                                                                     (backfill ids, drop orphans)
                                                                   ↳ if invalid: strip tool history (text-only)
```

## Implementation Plan

### Phase 1 — same-provider ladder (~0.5d)
Reorder `models.yaml` fallbacks (Anthropic + OpenAI smart/fast → sibling models
first, Gemini last). Unit test: chain for claude-opus-4-8 is
`[claude-opus, claude-sonnet, claude-haiku, gemini-flash-lite]` after residency
filtering. This alone removes the cross-provider hop for the common blip.

### Phase 2 — cross-provider sanitizer (~1–1.5d)
`_sanitize_for_cross_provider`: id backfill + orphan drop over `Content` parts.
Unit tests with real `google.genai.types.Content` fixtures (functionCall without
id + functionResponse → matched pair; dangling call dropped). Wire into the
ResilientLlm loop, gated on a provider change.

### Phase 3 — last-resort strip + verify (~0.5d)
Tool-history strip when sanitation can't validate; `cross_provider`/`sanitized`
event flags. **Real cross-provider stream verification** (mandatory, CLAUDE.md):
force the Anthropic provider down (the live org cap, or fault-inject) and confirm
`one-ppa-expert` answers via Gemini with no `tool_call_id` error. Add a
`make model-fallback-e2e` harness (sibling to `handoff-e2e`/`elicitation-e2e`).

## Migration & Rollout

Purely additive + behind the existing `ResilientLlm` path. The sanitizer only
runs on a cross-provider hop (rare); same-provider and no-fallback paths are
untouched. Rollback = revert the sanitizer + the models.yaml reorder; zero data
migration. Gate the sanitizer behind `MODEL_XPROVIDER_SANITIZE` (default on dev,
staged) so it can be cut if it misbehaves.

## Testing Strategy

### Backend (pytest)
- `_sanitize_for_cross_provider`: functionCall w/o id + response → matched
  `tool_call_id` pair; dangling call dropped; already-valid history unchanged;
  no-tool history unchanged.
- Chain order after residency filter (same-provider first).
- ResilientLlm: cross-provider fallback with a tool history now succeeds (the
  member sees a sanitized request); same-provider fallback is NOT sanitized.
- Regression: the shipped `test_fallback_rewrites_llm_request_model_to_member`
  still passes; `test_model_errors` capacity-cap case still fallbackable.

### E2E (real stream — jsdom/unit is NOT sufficient)
- `make model-fallback-e2e`: stream a complex prompt to `one-ppa-expert` with the
  Anthropic provider down; assert an answer + `MODEL_FALLBACK cross_provider=true`
  + no `tool_call_id`/`RUN_ERROR`. Run on deployed dev, then test.

## Security Considerations

Residency stays gated by `resolve_model_chain` (eu-strict drops non-EU rungs
regardless of skill config); the sanitizer never widens egress — it only
reshapes tool ids within the already-permitted request. No new data path.

## Performance Considerations

The sanitizer is O(parts) and runs ONLY on a cross-provider hop; it adds no
latency to the primary or same-provider path. Same-provider laddering can add one
extra in-provider hop before crossing, but each hop is bounded by the retry/
failover budget (<30s total, Axiom #5).

## CLI Surface

- `make model-fallback-e2e ENV=dev|test` — the acceptance harness (sibling to
  `handoff-e2e`, `elicitation-e2e`): real cross-provider stream, asserts a
  tool-using skill answers via the fallback provider. This IS the definition of
  done. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## Success Criteria

- [ ] Same-provider ladder: claude-opus-4-8 falls to claude-sonnet/haiku before Gemini.
- [ ] A tool-using skill answers via the Gemini fallback under a full Anthropic cap — no `tool_call_id` error (real stream).
- [ ] Sanitizer unit-tested (id backfill, orphan drop, idempotent, no-tool no-op).
- [ ] Last-resort tool-history strip yields a text answer, never a RUN_ERROR.
- [ ] No regression: non-tool path, same-provider fallback, residency gate, the two shipped fixes.

## Open Questions

- **OQ1 — same-provider rungs:** do we have a `claude-haiku` entry mounted, and is claude-sonnet-5 an acceptable smart-class first fallback for claude-opus? (Lean: yes — sonnet-5 is the balanced default.)
- **OQ2 — provider-of-history detection:** infer from the last tool Part vs thread the primary provider explicitly through `ResilientLlm`. (Lean: thread it — the chain already knows each member's provider.)
- **OQ3 — is id backfill enough for Anthropic, or does it also need thought-signature handling?** ADK's `_content_to_message_param` has `_THOUGHT_SIGNATURE_SEPARATOR` logic on tool ids (adaptive-thinking) — verify the backfilled ids don't collide with that. (Verify against `lite_llm.py` before Phase 2.)
- **OQ4 — the real lever:** the Anthropic org usage cap is the actual outage here; this doc makes the *safety net* work, but should we also alert/surface when a provider is capped (an admin signal) so it's fixed at the source? (Lean: a follow-up admin/observability item.)

## Related Documents

- [model-reliability.md](../v6.7.0/implemented/model-reliability.md) — the original ResilientLlm + residency-gated fallback chain (M2/M3) this extends.
- [complexity-graded-model-routing.md](../v6.8.0/complexity-graded-model-routing.md) — the tier/routing model that selects which chain runs.
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface backlink.
- Memory: `resilient_llm_rewrites_model_per_member` — the routing bug + the chain of red herrings that led here.
- ADK source: `google/adk/models/lite_llm.py::_content_to_message_param` (the `tool_call_id = part.function_call.id or ""` line — the crux).
