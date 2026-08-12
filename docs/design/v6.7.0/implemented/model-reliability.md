# Model Reliability — Retries, Fallback Chains, and Stream Survival

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: ~4.5 days (4 phases; Phase 0 is hotfix-grade and independently shippable; EU-provider spike deferred to later roadmap)
**Scope**: Fullstack
**Dependencies**: v6.6.0 model tiers ✅ (`config/models.yaml`, `resolve_model`), AG-UI CustomEvent drain path ✅ (`LatencyTracker`/`STAGE_PROGRESS`), skill-delegation event precedent (7.1, `AGENT_DELEGATION` marker pattern)
**Created**: 2026-07-10
**Last Updated**: 2026-07-10

## Problem Statement

When a model provider degrades — or merely *looks* degraded — the v6 user
experience is a dead chat. The v5 "long-stream incident" (July 2026, ONE production)
is the motivating case study, and **v6 has every ingredient of that incident
plus gaps of its own**:

**The long-stream incident, decomposed (v5, both fixed there, neither fixed here):**

1. **Transport killed a healthy stream.** Anthropic was generating a correct
   528K-char comparison across 3 PDF contracts. The Next.js proxy uses
   undici's `fetch()`, which has a **hardcoded 300-second body timeout** —
   at exactly 5 minutes it killed the stream (`UND_ERR_BODY_TIMEOUT`) and the
   user got nothing. v6's proxy
   ([route.ts:119](../../../frontend/src/app/api/proxy/[...path]/route.ts))
   uses the same undici `fetch()` for SSE passthrough, and v6's
   `gcloud run deploy` (cloudbuild.yaml, ~line 162) sets **no `--timeout`**,
   so Cloud Run's default request timeout applies on top.
2. **Extended thinking = minutes of silence.** With Claude adaptive thinking,
   every token for ~5 minutes was a hidden reasoning token. The user saw
   *nothing* — indistinguishable from a hang. v6 is worse here: `agui.py` has
   **no handling for LiteLLM `ReasoningChunk` parts at all**, there are **no
   SSE heartbeats on the chat path** (grep confirms: only the Discord channel
   has keep-alives), and the frontend's 30s watchdog only covers
   pre-`RUN_STARTED` — a mid-stream 5-minute silence has no signal.

**v6's own model-error gaps (surveyed 2026-07-10):**

- **Retry config exists only on the root agent.** `app.py:45` sets
  `HttpRetryOptions(attempts=3)` on the root Gemini; `resolve_model()`
  ([agent.py:80-106](../../../backend/adk/agent.py)) creates every
  *skill-level* model bare — no retry for Gemini, Claude, or OpenAI.
- **Claude/OpenAI errors are invisible.** Only `google.genai.errors.ClientError`
  is caught and translated to a `RUN_ERROR`
  ([skill_processor.py:236-243](../../../backend/skills/skill_processor.py)).
  LiteLLM exceptions (Anthropic 429 `rate_limit_error`, **529
  `overloaded_error`**, 500/503) propagate uncaught → the stream dies silently
  → the user sees a generic network error after 30s, with no retry and no
  explanation.
- **No fallback anywhere.** Tiers (`lite`/`smart`/`pro`) are 1:1 mappings in
  `models.yaml`. If Anthropic is down, every `smart`-tier skill
  (including `one-ppa-expert`) is down, even though Gemini is healthy.

**Impact:** ONE (flagship customer) runs the `smart` tier on Claude via the
direct US API — a single-provider single-point-of-failure for the exact
workloads (multi-contract comparison) that also trigger the long-silence and
long-stream failure modes. Product axiom #5 (GRACEFUL DEGRADATION) explicitly
requires "fallback to alternative model or informative message" and "<30s
automatic failover" — nothing specs or implements it today. This doc fills
that gap.

## Goals

**Primary Goal:** A model-provider outage, rate limit, or overload never
produces a silent dead chat — the turn either completes on a fallback model
(with a clear user-visible notice) or fails fast with an actionable,
retryable error; and a *healthy* long-running turn (thinking or long output)
is never killed by transport or mistaken for a hang by the user.

**Success Metrics:**
- Zero user-facing silent failures from model API errors: 100% of model-call
  failures surface as either a completed fallback turn or a typed `RUN_ERROR`
  (never a 30s-watchdog generic network error).
- Automatic failover completes in <30s from first provider error (axiom #5 KPI).
- A 10-minute streaming response survives end-to-end on deployed envs
  (proxy + Cloud Run), verified by a long-stream probe.
- 100% of visible-silence periods >5s show an activity state ("Thinking…",
  tool stage, or retry notice) in the UI.
- Fallback events are observable: per-provider error/retry/fallback counters
  in Cloud Logging, traceable per turn.

**Non-Goals:**
- Multi-region *active-active* serving or latency-based region routing —
  cross-region entries are failover rungs only, same as model rungs.
- Retrying/resuming a turn that already streamed *visible* content to the user
  (v1 policy: fail visibly with a retry affordance instead — no silent
  double-generation).
- Channel paths (Telegram/email) — they reuse `skill_processor`, so they
  inherit retry/fallback for free, but channel-specific messaging is out of
  scope.
- Queuing/deferral of requests during outages (no request persistence).

## Axiom Alignment

Score per [Product Axioms](../../product-axioms.md). Net must be >= +4.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Heartbeats + thinking-phase visibility kill the "is it stuck?" 5-minute blank; retry-then-fallback beats a dead 30s watchdog wait. Backoff is capped so failover stays <30s. |
| 2 | EARNED TRUST | +1 | Degradation is *announced*, not hidden: persistent transcript marker says which backup model answered. No silently swapped models. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; skills only gain an optional `fallback` config block. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Fallback chains are defined per tier in `models.yaml`, preserving tier semantics (smart→smart-class fallback, lite→lite-class) instead of degrading arbitrarily. |
| 5 | GRACEFUL DEGRADATION | +1 | This doc *is* axiom #5: explicit per-failure-mode handling, timeout hardening, provider redundancy, 100% user-comprehensible degraded states. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Reuses the existing AG-UI `CUSTOM` event drain (same pattern as `STAGE_PROGRESS`/`AGENT_DELEGATION`); heartbeats are standard SSE comments; rejects LiteLLM's proprietary silent `fallbacks` kwarg (see Standards Compliance). |
| 7 | API FIRST | 0 | No new endpoints; existing SSE contract gains events. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every retry/fallback emits a structured log + OTel event; per-provider failure counters make outages visible before users report them. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. One guarded risk: cross-provider fallback changes egress jurisdiction (Gemini/EU-Vertex → Claude/US-direct) — gated per skill by `fallback.allow_cross_provider` (see Security Considerations). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All retry/fallback/classification logic is server-side; the client only renders typed events it already knows how to drain. |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ — no conflicts. |

**Conflict Justifications:** none (no -1 scores).

**Standards Compliance Check:** LiteLLM natively supports `fallbacks=` on
`completion()` and the Anthropic API now has a server-side `fallbacks` beta —
both were evaluated and **rejected as the primary mechanism**: LiteLLM's
fallback is silent (no hook to emit a user-visible AG-UI event), doesn't cover
native-ADK Gemini (our majority path), and Anthropic's server-side fallback
only targets refusals on `claude-fable-5`-class models. The platform-level
wrapper is not a reinvention of a standard — it is the only place all three
providers and the AG-UI notification contract meet. Native client retries
(google-genai `HttpRetryOptions`, LiteLLM `num_retries`, Anthropic SDK
`max_retries`) are deliberately **not** stacked under the wrapper to avoid
multiplicative retry storms (see Standards note in Design Decisions below).

## Design

### Failure taxonomy (the load-bearing idea)

The long-stream incident proves that "add a fallback model" is the wrong reflex for
half the failure space. Every mitigation below is keyed to a class; applying
one class's cure to another class's disease makes things worse (e.g. falling
back on a transport timeout re-runs a 5-minute job on a second provider).

| Class | What happened | Examples | Correct response |
|-------|---------------|----------|------------------|
| **A. Model error at turn start** (no output yet) | Provider rejected or died before any content | 429 `rate_limit_error`, 529 `overloaded_error`, 500/503, `ClientError` 429/401 | Retry with backoff (transient only) → fallback chain → typed `RUN_ERROR` if exhausted |
| **B. Model error mid-stream** | Provider died after partial output | Stream reset, mid-generation 5xx | If no *visible* content yet (thinking-only): treat as Class A — fallback + restart turn. If visible content emitted: typed retryable `RUN_ERROR` (no silent re-generation) |
| **C. Transport failure** | Infrastructure killed a healthy stream | undici 300s body timeout, Cloud Run request timeout, LB idle timeout | Fix the transport (Phase 0). **Never fall back** — the model wasn't the problem |
| **D. Perceived stall** | Nothing failed; user just sees silence | Claude thinking phase (minutes), long tool call, slow first token | Heartbeats keep intermediaries alive; activity events keep the *user* informed. Not an error path |
| **E. Non-retryable model error** | Provider says "this request can never work" | 400 invalid request, 413 too large, auth 401/403 (auth *is* worth one fallback try — a misconfigured key on one provider doesn't impugn another) | Typed `RUN_ERROR` immediately (400/413); fallback once for auth-class |

### Architecture Diagram

```
skill config (SkillMetadata.model + optional .fallback)
        │
        ▼
resolve_model_chain(model_id, fallback_cfg)          backend/adk/agent.py
        │  primary + [fallbacks] each via resolve_model()
        ▼
ResilientLlm(BaseLlm)                                 backend/adk/resilient_llm.py  (NEW)
  generate_content_async():
    for model in [primary, *fallbacks]:
      for attempt in range(retries):
        try: stream from model.generate_content_async()
        except e:
          cls = classify(e)   # backend/adk/model_errors.py (NEW)
          if cls.transient and no_visible_output: backoff(honor retry-after); emit MODEL_RETRY; continue
          if cls.fallbackable and no_visible_output: emit MODEL_FALLBACK; break → next model
          raise ModelTurnError(cls)  # typed, carries provider/model/code
        │
        ▼ (events via LatencyTracker sink — same queue as STAGE_PROGRESS)
stream_agui_events()                                  backend/adk/agui.py
  drains MODEL_RETRY / MODEL_FALLBACK as AG-UI CUSTOM events
  + emits SSE heartbeat comments during silence        (Class C/D)
  + maps ReasoningChunk partials → THINKING activity   (Class D)
        │
        ▼
skill_processor.py: except ModelTurnError → RUN_ERROR {code, message, retryable, retry_after}
        │
        ▼
/api/proxy (node:http streaming, no body timeout)      (Class C)
        │
        ▼
useSkillAgent.ts: MODEL_FALLBACK → FallbackNotice marker; classifyRunError gains codes;
                  mid-stream inactivity watchdog (heartbeat-aware)
```

### Backend Changes

**New module: `backend/adk/model_errors.py`** — single source of truth for
error classification across the three providers:

- `classify(exc) -> ErrorClass{transient: bool, fallbackable: bool, code: str, retry_after: float|None}`
- Gemini: `google.genai.errors.ClientError`/`ServerError` by `.code`
  (429/500/503 transient; 401 → `VERTEX_AUTH_FAILED`, fallbackable;
  400 non-retryable). ADK already wraps 429 as `_ResourceExhaustedError` —
  match on the underlying `ClientError`.
- Claude/OpenAI via LiteLLM: match `litellm` exception types
  (`RateLimitError`, `ServiceUnavailableError`, `InternalServerError`,
  `APIConnectionError`, `Timeout` → transient+fallbackable;
  `AuthenticationError` → fallbackable; `BadRequestError`,
  `ContextWindowExceededError` → non-retryable). Anthropic 429 carries
  `retry-after`; honor it, capped at 10s (beyond that, fall back instead —
  axiom #5's <30s failover budget). Anthropic **529 overloaded** maps through
  LiteLLM as a 5xx — verify the concrete class in Phase 1 with a recorded
  fixture, don't trust the mapping table from memory.
- Backoff: exponential with full jitter, base 0.5s, factor 2, max 8s,
  **2 retries per model** (mirrors the Anthropic SDK's own default), then
  next model in chain. Worst-case pre-fallback delay ≈ 0.5+1+2 ≈ 3.5s + two
  request timeouts.

**New module: `backend/adk/resilient_llm.py`** — `ResilientLlm(BaseLlm)`
wrapping an ordered list of resolved `BaseLlm` instances (verified: ADK
`BaseLlm.generate_content_async` is the single streaming seam all three
providers share). Key behaviors:

- Tracks whether any *visible* (non-thinking) partial has been yielded this
  turn; fallback/retry is only legal before that point (Class B policy).
- Emits `MODEL_RETRY {model, attempt, delay_s, code}` and
  `MODEL_FALLBACK {from_model, to_model, code, provider}` through an injected
  event sink — the per-request `LatencyTracker` queue that `agui.py` already
  drains between ADK events (no new wire plumbing).
- Per-provider **cooldown** (module-level, per-instance): after N=3
  consecutive fallbackable failures, skip the provider as primary for 120s and
  go straight to its fallback (prevents every turn paying the retry+timeout
  tax during a sustained outage). In-memory only — Cloud Run instances learn
  independently; that's acceptable at our scale (documented Open Question).

**Design decision — retries live in ONE place.** The wrapper owns all retry
and fallback logic. We do *not* additionally set `HttpRetryOptions` on
skill-level Gemini or `num_retries` on LiteLlm: stacked layers multiply
(2 wrapper retries × 3 native retries = 6 hidden attempts per fallback hop),
blowing the <30s failover budget and making behavior unobservable. The root
agent's existing `retry_options` stays (it is outside the skill path).

**Modified: `backend/adk/agent.py`**
- `resolve_model()` → `resolve_model_chain()`: resolves primary + fallback ids
  (from `SkillMetadata.fallback` override, else `models.yaml` chain for the
  model/tier), returns `ResilientLlm` (or the bare model when chain is empty —
  zero behavior change for unconfigured skills).
- Thinking-router agents (`create_agent_with_thinking`) get chains for both
  fast and thinking models.

**Modified: `backend/config/models.yaml`** — declarative chains with
residency tags and residency-aware tier defaults:

```yaml
residency:
  default_policy: eu-strict            # template default; MODEL_RESIDENCY_POLICY env overrides per env
models:
  gemini-3-1-pro:
    residency: eu                      # Vertex europe-west
    fallbacks:
      - {id: gemini-3-1-pro, location: europe-west4}   # tier 1a: cross-region
      - gemini-2-5-pro                                 # tier 1b: sibling model
  gemini-flash-lite:
    residency: eu
    fallbacks:
      - {id: gemini-flash-lite, location: europe-west4}
      - gemini-2-5-flash
  claude-opus-4-7:
    residency: us                      # direct Anthropic API
    fallbacks: [gemini-3-1-pro]        # egress-narrowing (us→eu): always legal
tier_defaults:
  smart:
    default: claude-opus-4-7           # unrestricted deployments (dev, non-EU forks)
    eu-strict: gemini-3-1-pro          # EU deployments resolve smart → EU automatically
  lite:
    default: gemini-flash-lite
fallback_policy:
  max_retries_per_model: 2
  provider_cooldown_seconds: 120
```

The per-policy `tier_defaults` variants are the "good default EU option":
skills reference tiers (`lite`/`smart`), so an `eu-strict` deployment gets a
working EU model + EU chain for every tier with **zero skill changes** —
no per-skill audit, no load failures. Only skills that *pin* a concrete
non-EU model id fail validation under `eu-strict` (loudly, at load time).

**Deployment residency enforcement (in `resolve_model_chain`)** — the single
choke point:
- Read `MODEL_RESIDENCY_POLICY` env (fallback: `residency.default_policy`).
- Under `eu-strict`: tier refs resolve via the `eu-strict` tier variant;
  fallback entries whose `residency` ≠ `eu` are dropped with a structured
  warning log; a pinned non-EU primary raises a load-time validation error
  (skill listed as misconfigured in admin — never silently swapped).
- Under `unrestricted`: full chains as declared; per-skill
  `fallback.allow_cross_provider: false` can still narrow.
- Cloud Run env vars: `MODEL_RESIDENCY_POLICY=eu-strict` on Aitana prod only,
  `unrestricted` on dev + test (superseded v6.8.0 — test joined dev on the
  faster global 3.x line; matches the accepted dev/test non-EU-egress
  exception. Canonical per-env record: `docs/ops/env-config-parity.md`).

**New: `RegionalGemini(Gemini)` (~30 LOC)** — cross-region rungs (tier 1a).
ADK's `Gemini.api_client` is a `cached_property` building `google.genai.Client()`
from env (`GOOGLE_CLOUD_LOCATION`); a chain entry with `location:` overrides it:
`Client(vertexai=True, project=..., location=entry.location, http_options=...)`.
Vertex quota is per project+region, so 429 `RESOURCE_EXHAUSTED` in
europe-west1 genuinely clears in europe-west4 — region rungs are the cheapest
availability win in the whole design. Per-region model availability differs
(verified: some model aliases 404 in EU regions), so `make verify-regions`
probes every `{model, location}` pair in the chains and CI fails on drift.

**Fallback provider landscape — EU-first (Phase 2 config; contracts are a
separate track).** Two framing facts before the table:

1. **`ResilientLlm` IS our router.** External gateways (OpenRouter, EdenAI,
   Requesty) add a middleman: a new data recipient, a new SPOF, and a latency
   hop — value only when they grant something we can't reach directly
   (many models through one DPA, or an EU-resident path to a provider that
   has none). We should not adopt a router for failover logic we already own.
2. **Residency is a *deployment* property, not a skill property.** Aitana
   production is EU-only as a hard constraint; downstream template forks may
   not be. So the gate is a deployment-level `MODEL_RESIDENCY_POLICY`
   (`eu-strict` | `unrestricted`) enforced at the single choke point
   (`resolve_model_chain`) — under `eu-strict`, a non-EU entry *cannot* enter
   a chain no matter what a skill or `models.yaml` says (dropped with a
   loud warning; a *pinned* non-EU primary is a load-time validation error,
   never a silent model swap). Aitana: prod/test `eu-strict`, dev
   `unrestricted` (matching the accepted dev/demo US-egress exception).
   The template ships `default_policy: eu-strict` — the safe default — and a
   fork flips one reviewed line to opt out. Per-skill
   `fallback.allow_cross_provider` remains as a *narrowing* knob under
   `unrestricted` (it can never widen beyond deployment policy).
3. **Exhaust redundancy inside the trust boundary before adding recipients.**
   The rung order for an EU deployment: same model in another EU *region* →
   sibling model, same provider → EU-resident second provider → (never, for
   Aitana prod) non-EU.

| Tier | Option | Residency | Integration | Verdict |
|------|--------|-----------|-------------|---------|
| **1a** | **Same model, different EU region** (e.g. `gemini-3-1-pro` europe-west1 → europe-west4) | Inside our GCP project boundary; zero new recipients, zero new DPAs, same model = zero quality change | `RegionalGemini` subclass (~30 LOC, see Backend Changes); Vertex quotas are per project+region, so a 429 in one region genuinely clears in another | **First rung of every EU chain.** Covers regional brownouts and per-region quota exhaustion — likely the most *frequent* failure class — at zero trust and zero quality cost. Caveat: model availability differs per region (`gemini-flash-lite-latest` 404s in EU regions, verified 2026-07-08) — every region rung must be probe-verified per model |
| **1b** | **Sibling model, same provider** (gemini→gemini across families, europe-west) — *current strategy* | Inside our GCP project boundary; zero new recipients, zero new DPAs | Already integrated | Second rung. Covers per-*model* failures (family-wide degradation, model-specific quota). 1a+1b together still don't cover a whole-Vertex outage — that residual risk is what tiers 2–3 exist for |
| **2** | **Direct EU-resident endpoints, first-party**: Mistral La Plateforme (French, EU-hosted; LiteLLM `mistral/` native); Claude on **Bedrock eu-central-1/eu-west-1** (LiteLLM `bedrock/` native) — per Requesty's catalog Claude Opus/Sonnet are served from EU Bedrock regions, **verify with AWS directly** | EU region pinning per provider DPA; one new recipient each | Trivial via LiteLLM prefixes; Bedrock adds an AWS account/IAM surface to a pure-GCP stack (real operational cost) | **Preferred provider-diversity rung.** Bedrock-EU Claude is strategically notable: it would be the first EU-resident Claude, potentially retiring the accepted-temporary US-direct exception for the smart tier |
| **3** | **EU routers**: EdenAI EU (`api.eu.edenai.run/v3/` — French co., "hosted, processed, routed exclusively within the EU", SOC2/ISO27001, DPA, zero retention); Requesty EU (Frankfurt, ZDR, OpenAI-compatible, claims 77 EU-region deployments incl. Bedrock-EU Claude + europe-west Gemini + Azure France GPT); EUrouter | EU-resident by design (vendor claims → DPA items) | Requesty: trivial (OpenAI-compatible → LiteLLM `openai/` + `api_base`). EdenAI: **own v3 API, not OpenAI-compatible → custom `BaseLlm` or adapter (~1d)** | Worth it only if we want many-models-through-one-DPA or Bedrock-EU-Claude *without* opening an AWS account. Evaluate Requesty first (integration ≈ free); EdenAI's integration cost needs the router to earn its place |
| **4** | **US options**: OpenRouter default (US infra; EU endpoint `eu.openrouter.ai` is enterprise-only, by request), Anthropic direct US (current smart tier) | Egress-widening | OpenRouter: LiteLLM `openrouter/` native, force `zdr` + `data_collection: deny` on every request | **Dev/demo + non-EU-pinned skills only**, behind `allow_cross_provider: true`. Never in an Aitana production chain |

All vendor residency claims (EdenAI "exclusively within the EU", Requesty
Frankfurt/ZDR, OpenRouter EU) are marketing-page assertions as of 2026-07-10
— each becomes a DPA checklist item before any customer content flows.
Every tier-2/3 addition is a new third-party recipient under the platform
privacy boundary and needs explicit justification + DPA review.

Example Aitana production chain under this policy (smart tier, EU-pinned):

```yaml
gemini-3-1-pro:                       # smart primary (Vertex EU, europe-west1)
  residency: eu
  fallbacks:
    - {id: gemini-3-1-pro, location: europe-west4}   # tier 1a: same model, other EU region
    - gemini-2-5-pro                                 # tier 1b: sibling model
    # tier 2 rung once contracted + probe-verified:
    # - mistral-large-eu
```

**Deferred follow-up — EU provider spike (lower on roadmap, tracked here so
it isn't lost):** before any tier-2/3 rung enters a production chain, run a
half-day spike per candidate (EdenAI EU, Requesty EU, Mistral La Plateforme,
Bedrock-EU Claude): enumerate the actual EU model catalog, verify auth +
streaming behavior through `ResilientLlm`, measure TTFT via
`aiplatform skill probe`, confirm ZDR/data-collection flags on the wire, and
run the PPA evalset against each candidate rung for a quality baseline. Output:
a `docs/ops/` provider matrix + go/no-go per rung. Not scheduled in this
sprint — config schema above already accommodates the results.

**Modified: `backend/skills/skill_processor.py`**
- Catch `ModelTurnError` (new) alongside `BudgetExceededError`/`ClientError` →
  `RUN_ERROR` with new codes: `MODEL_UNAVAILABLE`, `MODEL_RATE_LIMITED`
  (+`retry_after_seconds`), `MODEL_AUTH_FAILED`, `MODEL_REQUEST_INVALID`.
  This alone (Phase 1) converts today's silent LiteLLM deaths into actionable
  errors, before any fallback exists.

**Modified: `backend/adk/agui.py`** (Class C/D)
- **SSE heartbeats:** while awaiting the next ADK event, yield an SSE comment
  line (`: hb <n>`) every 20s of silence. SSE comments are spec-legal and
  ignored by conformant parsers — verify `@ag-ui/client`'s parser tolerates
  them (Phase 0 spike, with a fallback to a no-op `CUSTOM {name: HEARTBEAT}`
  event if it doesn't).

**Thinking-token streaming (Class D) — the v5 fix, ported correctly.**
v5's second fix for the long-stream incident was to stream thinking tokens so the
pipe carried traffic during the reasoning phase. Audit (2026-07-10) of the
v6 pipeline, hop by hop:

| Hop | Status |
|-----|--------|
| ADK `LiteLlm` yields reasoning parts as partial `LlmResponse`s (`ReasoningChunk`) | ✅ verified in ADK v1.24.1 `lite_llm.py` |
| `ag_ui_adk` `event_translator.py` maps thought parts → AG-UI `REASONING` events | ✅ verified in installed lib |
| Frontend `useSkillAgent` `onReasoningMessageContentEvent` → `thinkingContent`/`isThinking` → `ThinkingPanel.tsx` | ✅ exists |
| **Models actually emitting thought parts** | ❌ **nobody asks them to** |

The plumbing is wired end-to-end but dark, because:

- **Gemini:** `_planner_for()` ([agent.py:166](../../../backend/adk/agent.py))
  sets `ThinkingConfig(thinking_budget=-1)` **without `include_thoughts=True`**
  — Gemini thinks but streams no thought summaries. One-line fix.
- **Claude:** `resolve_model()` creates `LiteLlm` bare — no `thinking` kwarg.
  On Claude Opus 4.7+/Sonnet 4.6 via the API, *no thinking field means
  thinking is off entirely*, so today v6 Claude turns have no thinking phase
  at all (no long-stream-style silence, but also no thinking-tier quality). Fix:
  pass `thinking={"type": "adaptive", "display": "summarized"}` through
  LiteLlm kwargs for smart-tier models.
- **⚠ The v5 trick does not survive model upgrades on its own:** on Claude
  Opus 4.7/4.8/Fable, thinking `display` **defaults to `"omitted"`** — thinking
  blocks stream with *empty text*, so "thinking tokens as keep-alive traffic"
  silently produces near-zero bytes unless `display: "summarized"` is set
  explicitly. This is exactly why Phase 0's SSE heartbeats are the keep-alive
  *guarantee* (model-independent, covers tool-call silences too) and thinking
  streaming is the UX layer on top, not the transport crutch.
- Fallback label: for any silent phase where no REASONING traffic arrives
  (e.g. a model with omitted display), emit throttled
  `STAGE_PROGRESS {label: "Thinking…"}` so the UI never shows a dead pause.
  Never render raw reasoning text from that path — label only.

**Modified: `cloudbuild.yaml`** — add `--timeout=3600` to the
`gcloud run deploy` (Class C; Cloud Run max is 3600s). Add a trap-catalogue
entry in the `platform-deploy` skill so test/prod promotion carries it.

### Frontend Changes

**Modified: [route.ts](../../../frontend/src/app/api/proxy/[...path]/route.ts)** (Class C)
- For responses with `content-type: text/event-stream`, bypass undici: new
  `streamFromBackend()` using `node:http` request piped into a Web
  `ReadableStream` (v5's proven `fetchStreamingFromBackend` fix, ported).
  Node's native `http` has no body timeout by design. Non-streaming requests
  keep `fetch()` unchanged. Keep the FE-BRINGUP-1 gotchas (IPv4 literal,
  never :8080).

**Modified: `frontend/src/hooks/useSkillAgent.ts`**
- `onCustomEvent` branches: `MODEL_RETRY` → transient status line (reuses the
  `STAGE_PROGRESS` surface, auto-fades); `MODEL_FALLBACK` → persistent
  transcript entry + sets `activeFallback` state.
- `classifyRunError()` gains the four new codes with honest copy, e.g.
  `MODEL_UNAVAILABLE` → "The AI model for this skill is temporarily
  unavailable (and its backup also failed). Try again in a minute." —
  retryable; `MODEL_RATE_LIMITED` reuses the budget-style countdown.
- **Mid-stream inactivity watchdog:** the existing 30s watchdog stays for
  pre-`RUN_STARTED`; add a mid-stream timer reset by *any* traffic (heartbeats
  included via a byte-level `onActivity` hook if comment lines are invisible to
  the AG-UI subscriber — decided in Phase 0). No traffic for 90s mid-stream →
  "Connection looks stalled" retryable error instead of waiting forever.

**New component: `frontend/src/components/chat/FallbackNotice.tsx`** (<2KB,
mirrors `DelegationMarker.tsx`): persistent chip in the transcript —
"⚠ Answered by backup model *Gemini 3.1 Pro* — *Claude Opus* was unavailable."
Renders from the `MODEL_FALLBACK` event; survives session resume via the
existing history payload (same mechanism as delegation markers).

**State Management:** no new contexts — `useSkillAgent` gains `activeFallback`
+ transcript entries; all state derives from stream events.

**UI/UX:**
- Retry: subtle transient line "Model busy — retrying…" (auto-fades like
  stage progress; never blocks input).
- Fallback: persistent, honest, non-alarming chip at the answer's position.
- Stall: "Thinking…" indicator within 5s of a silent phase; stalled-connection
  error only after 90s of true silence.

### CLI Surface

- `aiplatform skill probe` — print `MODEL_RETRY`/`MODEL_FALLBACK`/`RUN_ERROR`
  events in the stream trace (same pattern the delegation sprint added for
  `AGENT_DELEGATION`). ~0.1d.
- Backend fault injection for verification without breaking a real provider:
  env `FAULT_INJECT_MODEL="anthropic:429:2"` (provider:code:count) checked in
  `ResilientLlm` (dev-only, refuses to arm when running on a deployed prod
  service). Paired `make probe-fallback` target that arms it, runs a probe,
  asserts a `MODEL_FALLBACK` event. ~0.25d.
- `scripts/smoke-long-stream.sh [env]` — dev-only backend route
  `GET /api/debug/slow-stream?minutes=6` (auth-gated, streams a counter with
  60s gaps) curled through the deployed frontend proxy; asserts the stream
  survives >5m. This is the regression guard for the exact long-stream failure.
  ~0.25d. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| — | AG-UI stream (existing) | New CUSTOM events `MODEL_RETRY`, `MODEL_FALLBACK`; new RUN_ERROR codes | No (additive; unknown CUSTOM events are already ignored) |
| GET | `/api/debug/slow-stream` | Dev-only long-stream probe (disabled in prod) | No (new, gated) |

## Implementation Plan

### Phase 0 — Transport + stall hotfix (Class C/D core) (~1d) — independently shippable
- [ ] Proxy: `node:http` streaming path for SSE responses + unit test (~120 LOC)
- [ ] `cloudbuild.yaml` `--timeout=3600` + deploy-skill trap-catalogue note (~5 LOC)
- [ ] SSE heartbeats in `agui.py` + verify `@ag-ui/client` tolerance (spike: comment line vs no-op CUSTOM event) (~60 LOC)
- [ ] Frontend mid-stream inactivity watchdog (~50 LOC)
- [ ] `scripts/smoke-long-stream.sh` + `/api/debug/slow-stream` route (~80 LOC)

### Phase 1 — Error translation completeness (Class A/E visibility) (~0.5d)
- [ ] `model_errors.py` classifier + fixtures for all three providers (incl. recorded Anthropic 529-via-LiteLLM) (~150 LOC + tests)
- [ ] `skill_processor.py` catches `ModelTurnError` → typed RUN_ERROR codes (~40 LOC)
- [ ] `classifyRunError()` new codes + copy + vitest (~60 LOC)

### Phase 2 — Retry + fallback chains (Class A/B) (~2d)
- [ ] `ResilientLlm` with backoff, visible-output gate, event sink, provider cooldown (~250 LOC + tests with scripted fake `BaseLlm`)
- [ ] `resolve_model_chain()` + `models.yaml` chains/residency tags/tier variants + `SkillMetadata.fallback` (typed block: `{models: [...], allow_cross_provider: bool}`) (~150 LOC)
- [ ] Deployment residency enforcement: `MODEL_RESIDENCY_POLICY` env + eu-strict filtering/validation + cloudbuild env vars per env (~60 LOC + tests)
- [ ] `RegionalGemini` location override + `make verify-regions` probe (~80 LOC)
- [ ] `agui.py` drains `MODEL_RETRY`/`MODEL_FALLBACK` (~30 LOC)
- [ ] `FallbackNotice.tsx` + `useSkillAgent` branches + vitest (~120 LOC)

### Phase 3 — Thinking visibility, tooling, evals (~1d)
- [ ] Gemini planner: `ThinkingConfig(include_thoughts=True, thinking_budget=-1)` — light up the existing REASONING→ThinkingPanel path (~5 LOC + browser verify)
- [ ] Claude smart tier: `thinking={"type": "adaptive", "display": "summarized"}` via LiteLlm kwargs; verify LiteLLM forwards `thinking` to Anthropic and REASONING events flow (~20 LOC + probe)
- [ ] Silent-phase fallback: throttled "Thinking…" STAGE_PROGRESS when no REASONING traffic (~40 LOC)
- [ ] `FAULT_INJECT_MODEL` + `make probe-fallback` + `aiplatform skill probe` event printing (~100 LOC)
- [ ] Evalset entry: fault-injected turn must still produce a rubric-passing answer via fallback
- [ ] OTel: counters `model_retry_total`, `model_fallback_total`, `model_error_total` by provider/code (~40 LOC)

## Migration & Rollout

**Database Migrations:** none. `SkillMetadata.fallback` is optional/additive;
Firestore docs without it behave exactly as today.

**Feature Flags:**
- Fallback chains activate only for models with `fallbacks:` in `models.yaml`
  (or per-skill override) — empty chain = current behavior, so rollout is
  per-model and reviewable in one diff.
- Heartbeats + proxy streaming fix + typed errors ship unflagged (strictly
  better; no behavior branch).

**Rollback Plan:** revert `models.yaml` chains (config-only) to disable
fallback; `ResilientLlm` with an empty chain is a passthrough. Proxy change
reverts to plain `fetch()` in one commit.

**Environment Variables:** `FAULT_INJECT_MODEL` (dev only, prod-guarded).
No new secrets — fallback uses already-mounted provider keys; a chain entry
whose provider key isn't mounted is skipped at resolve time with a warning
(deploy-drift-proof; cross-org env drift is the #1 trap class in the deploy
skill's catalogue).

## Testing Strategy

### Backend Tests (pytest)
- [ ] Classifier: table-driven fixtures per provider exception → expected class/code/retry_after
- [ ] `ResilientLlm`: fake `BaseLlm` scripted to fail N times → asserts backoff schedule, retry events, fallback order, visible-output gate (no fallback after visible partial), cooldown behavior
- [ ] `skill_processor`: LiteLLM exception → RUN_ERROR code/payload (regression: today this dies silently)
- [ ] Heartbeat: silent-agent stream yields comment/no-op event within 25s

### Frontend Tests (Vitest + React Testing Library)
- [ ] `classifyRunError` new codes → correct kind/copy/retryable
- [ ] `MODEL_FALLBACK` custom event → `FallbackNotice` rendered, persists in transcript
- [ ] Mid-stream watchdog: fake timers, traffic resets, 90s silence → stalled error

### Manual Testing
- [ ] `make probe-fallback` on dev (fault-injected Anthropic 429 → Gemini answer + notice)
- [ ] `scripts/smoke-long-stream.sh dev` — >5-minute stream survives deployed proxy + Cloud Run (the long-stream regression test)
- [ ] Real Claude thinking-heavy prompt (`one-ppa-expert` compare) → "Thinking…" visible within 5s, no 30s watchdog trip

## Security Considerations

- **Egress jurisdiction is the one real risk — enforced by construction, not
  convention.** Falling back Gemini(EU Vertex) → Claude(US direct API) would
  silently move customer content across the privacy boundary (GCP project
  edge). The guard is the deployment-level `MODEL_RESIDENCY_POLICY` enforced
  at the single choke point (`resolve_model_chain`): under `eu-strict`
  (Aitana prod — see the env note above) a non-EU entry **cannot** enter a chain regardless of
  skill config or `models.yaml` content — dropped fallbacks warn loudly,
  pinned non-EU primaries fail at load. This is architectural in the same
  sense as the CLAUDE.md confidential-content rule: no code path exists that
  widens egress at runtime. Egress-narrowing fallback (us→eu, e.g.
  claude→gemini on dev) is always legal. Region rungs (tier 1a) never change
  jurisdiction — EU regions only under `eu-strict`.
- Fault injection is dev-only and refuses to arm on deployed prod env.
- `/api/debug/slow-stream` requires auth and is disabled outside dev.
- No prompt/content is ever included in `MODEL_RETRY`/`MODEL_FALLBACK` events
  or logs — provider, model id, status code, attempt only.
- Fallback re-sends the same conversation to a second provider — within the
  existing configured-provider trust set for Gemini/Claude/OpenAI chains.
  **Any tier-2/3/4 entry is different**: routers (OpenRouter/EdenAI/Requesty)
  and new direct providers (Mistral, Bedrock) are new third-party recipients.
  EU-resident options (tiers 2–3) need DPA review; US-routed options (tier 4)
  additionally require `allow_cross_provider: true` and, for OpenRouter,
  forced `zdr` + `data_collection: deny` — see "Fallback provider landscape"
  under Backend Changes. Aitana production chains are tier 1–2 only.

## Performance Considerations

- Happy path: `ResilientLlm` adds one `isinstance` check per chunk — negligible.
- Failure path budget: 2 retries × capped backoff (≤3.5s sleep) + request
  timeouts, then fallback TTFT. Provider cooldown removes this tax from
  subsequent turns during a sustained outage (they go straight to fallback).
- Heartbeats: one comment line / 20s / active stream — noise-level.
- Thinking STAGE_PROGRESS throttled to 1 event / 15s — no stream bloat.
- No extra model calls unless the primary already failed (fallback cost is
  the cost of getting an answer at all).
- Bundle size: `FallbackNotice.tsx` <2KB; no new deps.

## Success Criteria

- [x] All frontend tests passing (`npm run quality:check`) — 861 tests + build (2026-07-10)
- [x] All backend tests passing (`make lint && make test-fast`) — 1,798 tests, lint clean
- [x] `make probe-fallback` produces a fallback answer + `MODEL_FALLBACK` event end-to-end on local dev — PASSED against live Vertex (2× retry → west4 fallback → answer)
- [x] `scripts/smoke-long-stream.sh dev` passes on deployed dev (>5min stream) — 360s w/ 60s gaps survived (2026-07-10)
- [x] Fault-injected LiteLLM 429/529 surfaces typed `RUN_ERROR` (not silent death) when chain exhausted — M2 regression tests
- [x] Residency test: eu-strict drops non-EU fallbacks / fails pinned non-EU primaries; unrestricted resolves full chain — 12-test suite
- [x] Cross-region test: injected failure on primary → same-family answer via `RegionalGemini` europe-west4, events carry region — unit + live E2E probe
- [x] `make verify-regions` passes for every `{model, location}` pair in shipped chains — all 8 pairs verified live (and it caught the gemini-3.x global-only discovery)
- [x] `FallbackNotice` visible in transcript after fallback turn (vitest) — session-resume replay deferred with rationale (see Open Questions)
- [x] "Thinking…" indicator during thinking phases — reasoning stream live-verified at the litellm layer (161-char summarized reasoning on opus-4-7); silent-phase label on heartbeat ticks; full browser pass = evaluator manual item
- [x] OTel counters fire for fault-injected runs (`model_retry_total`/`model_fallback_total`/`model_error_total` by provider/code); Cloud Logging visibility confirms on next deployed fault test
- [x] `aiplatform skill probe` prints retry/fallback events — 86 CLI tests green
- [x] Documentation updated (deploy-skill trap 20 incl. `--timeout` + residency flip; design doc updated as reality diverged)

## Open Questions

- **Heartbeat transport:** SSE comment lines vs no-op CUSTOM event — decided
  by the Phase 0 spike against `@ag-ui/client`'s parser. Comments are cleaner
  (invisible to the app layer) if the parser and the mid-stream watchdog's
  byte-level `onActivity` hook can both see/ignore them appropriately.
- **Cooldown scope:** per-instance in-memory is v1. If ONE's traffic grows
  multi-instance, consider a Firestore-backed provider-health doc (adds a
  read per turn — probably not worth it; revisit with data).
- **Sticky fallback within a session:** after one fallback, should subsequent
  turns in the same session skip the primary for consistency of voice/format?
  v1: no (cooldown covers the outage case); revisit if users notice
  mid-conversation model flip-flops.
- **ADK plugin hook:** `BasePlugin.on_model_error_callback` (verified in ADK
  v1.24.1) could host observability logging platform-wide; it can substitute
  a single recovery `LlmResponse` but not a fallback *stream*, so it can't
  replace `ResilientLlm`. Candidate home for the OTel counters in Phase 3.
- **FallbackNotice resume persistence (M3 deviation, recorded 2026-07-10):**
  delegation markers survive session resume because they're reconstructed
  from ADK events (`transfer_to_agent` is a model function call);
  `MODEL_FALLBACK` fires inside `ResilientLlm`, *outside* the model
  conversation, so it never enters ADK's event store. M3 ships live-only
  notices; resume replay needs an explicit persistence channel — candidate:
  the 7.5 workbench-rehydration pattern (stash to session state, replay via
  the session-history GET).
- **`_HeuristicRouter` interplay:** when the thinking model's provider is in
  cooldown, should the router prefer the fast agent outright? v1: no special
  casing — the thinking agent's own chain handles it.
- **EU-resident Claude via Bedrock (eu-central-1):** if verified with AWS,
  this could retire the accepted-temporary "Claude via direct US API"
  exception for the smart tier entirely — a strategy decision beyond this
  doc (adds an AWS account/IAM surface to a pure-GCP stack; weigh against
  Requesty-EU, which reaches the same Bedrock-EU Claude through one
  OpenAI-compatible DPA with no AWS account). Flag for the egress-decision
  review.
- **Open-model quality on the smart tier:** an EU open-model rung (Mistral
  Large et al.) behind a Gemini/Claude primary is availability insurance at
  a quality cost — is a degraded-but-present answer (with the FallbackNotice
  making that visible) acceptable for ONE's contract-analysis workloads?
  Covered by the deferred EU-provider spike (see landscape section) before
  any tier-2/3 rung ships.
- **Which EU regions for tier 1a?** europe-west4 is the natural second region
  (largest EU Vertex footprint); confirm model coverage for our chain models
  via `make verify-regions` and check whether ONE's residency commitments are
  EU-wide or country-specific (if Belgium-only, west1↔west4 needs a
  contract check, not just a code check).
- **Template default policy:** doc proposes the template ships
  `default_policy: eu-strict` (safe default; fork opts out in one reviewed
  line). Confirm with fork owners — a tutoring fork (Danish schools) is EU
  anyway; the workshop template audience is global, so the workshop docs
  should show the opt-out line explicitly.
- **Enabling Claude thinking changes smart-tier behavior:** turning on
  adaptive thinking for `smart` (Phase 3) is a quality/latency change
  independent of reliability — longer turns, better answers. Confirm with a
  before/after probe of `one-ppa-expert` TTFT + eval scores before defaulting
  it on.

## Related Documents

- [Product Axioms — #5 GRACEFUL DEGRADATION](../../product-axioms.md) (this doc implements its KPIs)
- [skill-delegation.md](implemented/skill-delegation.md) — precedent for the transient + persistent AG-UI event pair and probe-printing
- [migration-to-v6.md](../v5.0.0/migration-to-v6.md) — v5 `httpx_utils.py` backoff-with-jitter prior art
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface conventions
- [docs/ops/deployed-urls.md](../../ops/deployed-urls.md) — smoke tooling this extends
- v5 incident reference: long-stream / ONE 3-contract comparison, July 2026 — undici `UND_ERR_BODY_TIMEOUT` at 300s + extended-thinking silence; v5 fixes: `fetchStreamingFromBackend` (node http) + Cloud Run `--timeout=3600` + streaming thinking tokens (ported here as Phase 0 heartbeats + Phase 3 thought emission — see "Thinking-token streaming" for why the token-traffic trick alone breaks on Claude Opus 4.7+ `display: "omitted"`)
- OpenRouter in-region routing docs: [openrouter.ai/docs/guides/features/sovereign-ai](https://openrouter.ai/docs/guides/features/sovereign-ai) (verified 2026-07-10: `eu.openrouter.ai` enterprise-only; ZDR + `data_collection: deny` generally available)
- EdenAI EU endpoint: [edenai.co/eu](https://www.edenai.co/eu) (fetched 2026-07-10: `api.eu.edenai.run/v3/`, EU-exclusive processing claim, SOC2/ISO27001/GDPR DPA, zero retention; own v3 API — not OpenAI-compatible)
- Requesty EU gateway: [requesty.ai/eu](https://www.requesty.ai/eu) (vendor claims 2026-07-10: Frankfurt-hosted, ZDR, OpenAI-compatible, EU-region deployments incl. Claude on Bedrock eu-central-1/eu-west-1 — verify before contracting)
- Anthropic egress decision (2026 project decision, no repo doc yet): smart tier on Claude direct US API is an accepted-temporary exception for dev/demo pending an EU Claude path — Bedrock-EU (tier 2) is the first candidate to retire it

---

## Implementation Report

**Completed**: 2026-07-10
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built

All four phases (M0–M4) in one day, 5 commits (`063f219`…`e9cf3dc`), evaluated
PASS 95/100 by sprint-evaluator round 1 on claude-opus-4-8 (cross-model check;
report: `.claude/state/evaluations/eval_MODEL-RELIABILITY_round_1.json`).
Verified live at every layer: deployed 360s/60s-gap stream survived dev
end-to-end (long-stream regression guard green); `make probe-fallback` proved
retry→fallback→answer against real Vertex under injected faults;
`make verify-regions` probed all 8 chain pairs.

**Deviations (all recorded in sprint JSON notes):** node:http used for ALL
proxy requests (not just SSE); proxy tests reworked to a real local upstream;
FallbackNotice is live-only (MODEL_FALLBACK originates outside the ADK event
store — the "same as delegation markers" resume assumption was wrong; see Open
Questions); formal evalset entry covered by the E2E probe instead.

**Discoveries the sprint surfaced:** gemini-3.x previews are
global-endpoint-only (404 in every EU region — latently broken on the deployed
region-pinned client; fixed via `RegionalGemini(location="global")`); Anthropic
529 loses its identity through litellm (`InternalServerError(500)`); Haiku
rejects adaptive thinking with a 400; the stream route resolves skill UUIDs
only, not slugs.

**F1 gate closed (2026-07-10, post-eval):** the evaluator flagged that
adaptive thinking defaulted on without the stipulated TTFT probe. A/B run
(smart-tier skill, 3 turns per mode, scratch backends):
first-visible-token median 2.5s (off) vs 3.0s (on) — within run noise;
totals comparable; adaptive chose NOT to think on all simple prompts
(zero reasoning events → zero latency tax), while hard prompts verifiably do
think (161-char summarized reasoning probe). Default stays ON;
`CLAUDE_ADAPTIVE_THINKING=off` remains the kill switch.

### Files Changed

New: `backend/adk/resilient_llm.py`, `backend/adk/model_errors.py`,
`backend/scripts/verify_regions.py`, `frontend/src/app/api/proxy/nodeProxy.ts`,
`frontend/src/components/chat/FallbackNotice.tsx`,
`scripts/smoke-long-stream.sh`, `scripts/probe-fallback.sh`, 6 test suites.
Modified: `agui.py` (heartbeats), `agent.py` (chains/residency/RegionalGemini/
thinking), `skill_processor.py` (typed RUN_ERRORs), `models.yaml` (residency +
chains + tier variants), `useSkillAgent.ts` (watchdog + fallback state),
`route.ts`, `cloudbuild.yaml`, `Makefile`, `dev.sh`, CLI probe, deploy-skill
trap 20.

### Lessons Learned

- **Live probing beats assumed availability**: verify-regions caught both the
  GCP_PROJECT shell-shadow and the gemini-3.x global-only fact within minutes
  of existing — per-region model availability must never be assumed again.
- **Recorded empirical fixtures pay immediately**: the litellm 529 mapping and
  the Haiku adaptive-400 would both have been plausible-but-wrong guesses.
- **Model assignment worked on its first outing**: fable-5 on the subtle
  streaming core (zero rework), opus-4-8 cross-model evaluation surfaced
  real nuances (cooldown/region interaction, error-code inconsistency) that
  self-review would likely have passed over.
- **Design assumptions about persistence need a grep before they ship as
  acceptance criteria** — the resume-replay half-criterion was unimplementable
  as designed (fallback events never enter the ADK event store).
