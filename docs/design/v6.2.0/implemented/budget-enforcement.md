# Budget enforcement — pluggable interface + soft/hard gating

**Status**: Proposed
**Priority**: P1 (AIPLA needs by mid-June for pre-pilot; broadly useful to every commercial fork)
**Estimated**: ~1.5 days (interface + ADK callback + reference impl + tests + docs)
**Scope**: Backend — new `BudgetEnforcer` Protocol + ADK before/after_model callbacks + in-memory reference impl + Pydantic config on `SkillMetadata` for per-skill multipliers.
**Dependencies**: `backend/observability/llm_metrics.py` (existing — provides the per-call cost numbers).
**Surfaced by**: AIPLA fork [ADR-014 — per-group, per-class budget enforcement](https://www.sunholo.com/aipla/architecture.html#adr-014-per-group-per-class-budget-enforcement). Generalises across every commercial fork.
**Created**: 2026-05-19

---

## Problem Statement

The platform already **meters** token + cost per LLM call via `backend/observability/llm_metrics.py` (OTel counters, per-model pricing table, structured logging). The metering is good — admins can see "how much did the demo-workspace skill cost in cohort X last month".

The platform does NOT **gate** on that meter. There is no mechanism to:

- Cut off a runaway agent loop that's burning a tenant's quota.
- Warn a user that they're approaching their cohort's monthly limit.
- Charge different skills different rates against the same pool.
- Refuse the next turn when a budget is exceeded.

Every commercial fork will hit this. AIPLA is the first concrete consumer asking, with a specific shape ([ADR-014](https://www.sunholo.com/aipla/architecture.html#adr-014-per-group-per-class-budget-enforcement)):

| Tier | Identity | Limit | Skill multiplier? |
|---|---|---|---|
| Group (cohort) | `group_id` from anonymous-auth (sprint 2.11) | Monthly soft + hard cap | Yes (physics-tutor = 1×, code-grader = 3×) |
| Class (institution) | `class_id` parent of multiple groups | Monthly soft + hard cap | (inherited from group tier) |
| Soft warning | 80% of cap | Banner / chat prefix | — |
| Hard block | 100% of cap | Reject the turn, return budget-exceeded error | — |

AIPLA's specific tiering (`group`, `class`) is **policy** — other forks will have different keys (`org_id`, `customer_id`, `project_id`). What's universal is:

- The **interface** between "agent about to make an LLM call" and "budget-keeper".
- The **soft + hard threshold** semantics.
- The **per-skill multiplier** on top of the model's native cost.
- The **failure path**: how the agent + UI degrade when budget is exceeded.

### Current state

- `backend/observability/llm_metrics.py`: per-call cost calculation against a static pricing table; emits OTel counter + structured log line per call.
- `backend/adk/callbacks.py`: ADK before/after callbacks for tools, agents, models. The before_model_callback is the natural gate point.
- `backend/db/models/__init__.py`: `SkillMetadata` is loosely-typed dict for `toolConfigs`. Per-skill budget multiplier would live under `tool_configs.budget` (paralleling `tool_configs.a2ui`, `tool_configs.mcp`).

### Impact

- **AIPLA cannot pilot** without this — running a teacher pilot with no spend bounds is a non-starter.
- **Every commercial fork rebuilds**: each writes its own gate, each gets the threshold semantics + the failure UX subtly wrong.
- **Soft warnings vs hard blocks are easy to get wrong**: failing fast on 100% with no soft signal is brittle; failing slow at >100% bleeds money. The platform should ship the correct shape.

---

## Goals

**Primary Goal:** Ship a `BudgetEnforcer` Protocol the platform calls before every LLM model invocation, with a reference in-memory implementation, soft/hard threshold semantics, per-skill multipliers, and clean degradation paths. Forks plug their own implementation (Firestore-backed, BigQuery-streamed, etc.) without touching the platform.

**Success Metrics:**
- A skill with `tool_configs.budget.identity_key = "group_id"` and a registered `BudgetEnforcer` impl is gated: the impl receives `(identity_value, model_id, projected_cost_usd, skill_id)` before each model call; can `allow`, `warn`, or `block`.
- Reference impl tracks usage in-memory; configurable caps via env var; usable for LOCAL_MODE smoke + workshop demos.
- Soft warning at 80%: the agent's response gets a structured chat-prefix block (configurable, default warning prose) but the call proceeds.
- Hard block at 100%: the LLM call is REFUSED before the model is invoked; client gets a `RUN_ERROR` AG-UI event with `kind: "budget_exceeded"` and a human-readable message + retry-after hint.
- Per-skill multiplier: skill declares `cost_multiplier: 3.0` in `tool_configs.budget`; the projected cost is scaled before the enforcer is consulted.
- AIPLA's pilot runs on a fork-side `FirestoreBudgetEnforcer` impl that satisfies the same Protocol; no platform changes needed for AIPLA's specific cohort schema.

**Non-Goals:**
- Specific per-tenant identity scheme (group / class / org / customer). The Protocol takes an arbitrary `identity_value: str`; forks pick the key.
- Billing integration / payment gateway. The platform tracks and gates; charge-the-customer is downstream.
- Time-window logic beyond a single "current period". The Protocol's impl decides what "period" means (daily, monthly, rolling 30d).
- Token-stream-level gating (cut off mid-response). The gate is per-call, not per-token. Mid-stream cancellation has UX nuance we leave to forks.
- Forecast-based gating ("you'll hit your limit by Friday"). Out of scope for v1.

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral — the gate consults the enforcer synchronously (<1ms for in-memory; budget the impl ≤50ms for any reasonable backend). |
| 2 | EARNED TRUST | +1 | Users see explicit "you've used 80% of your monthly allotment" rather than mysterious silent throttling. |
| 3 | SKILLS, NOT FEATURES | +1 | Per-skill multipliers preserve the SKILLS, not features axiom — the skill author declares cost shape, the platform enforces. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Cost-sensitive skills can declare a multiplier so the platform's heuristic-router (thinking vs fast) interacts with budget — cohorts close to cap default to fast. |
| 5 | GRACEFUL DEGRADATION | +1 | Soft-warn-then-block is the textbook degradation curve. Hard-block returns a typed AG-UI error the frontend can render cleanly. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Neutral — Protocol/Interface is conventional. No published external spec. |
| 7 | API FIRST | +1 | One Python Protocol + one config field on `SkillMetadata`. Forks reuse both. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every consult logs `{identity_value, model_id, projected_cost, decision, remaining}` — observability + audit + research data all converge. |
| 9 | SECURE BY CONSTRUCTION | +1 | Removes a known attack: a malicious caller can no longer drain a tenant's quota by spamming requests. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend never sees budget logic; it only renders the structured warn/error from AG-UI events. |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ |

---

## Standards Compliance Check

- **Python `typing.Protocol`** — duck-typed interface, doesn't require forks to inherit. Standard library, no new dep.
- **ADK `before_model_callback`** — documented extension point ([ADK callbacks docs](https://google.github.io/adk-docs/callbacks/)). We append a budget-gating callback to the existing `compose_before_model_callbacks` chain in `adk/callbacks.py`.
- **AG-UI `RUN_ERROR`** — typed error event with `kind` + `message` + `retryable`. Reuses the existing `frontend/src/hooks/useSkillAgent.ts` error path (the `runFailedRef.current` branch). No new wire format.

---

## Design

### Overview

```
┌──────────────────────────────────────────────────────────────────┐
│ AGENT TURN                                                       │
│                                                                  │
│  user message ──► skill_processor ──► ADK Runner                 │
│                                            │                     │
│                                            ▼ before_model_callback│
│                                     ┌────────────────┐           │
│                                     │ BudgetGate     │           │
│                                     │  identity_key  │           │
│                                     │  cost_multiplier│           │
│                                     │  ┌──────────┐  │           │
│                                     │  │ Enforcer │  │ ◄─── Fork plugs│
│                                     │  │ .consult()│ │       impl here│
│                                     │  └──────────┘  │           │
│                                     └─────┬──────────┘           │
│                                           │                      │
│                       ┌───────────────────┴────────────┐         │
│                       ▼                                ▼         │
│                  allow / warn                       block        │
│                       │                                │         │
│                       ▼                                ▼         │
│              LLM call proceeds              RUN_ERROR event      │
│              (warning prefix              (kind="budget_exceeded")│
│               on assistant turn                                  │
│               if warn)                                           │
└──────────────────────────────────────────────────────────────────┘
```

### The Protocol

```python
from typing import Protocol, Literal
from dataclasses import dataclass

@dataclass(frozen=True)
class BudgetConsultation:
    identity_value: str       # "group:PHYS-7K2N" or "org:acme-co" — opaque to platform
    skill_id: str
    model_id: str
    projected_cost_usd: float # after skill multiplier applied
    invocation_id: str        # for idempotency / replay protection

@dataclass(frozen=True)
class BudgetDecision:
    action: Literal["allow", "warn", "block"]
    remaining_usd: float | None         # None when not knowable
    period_end: str | None              # ISO timestamp, advisory
    message: str | None                 # human-readable; rendered for warn/block
    retry_after_seconds: int | None     # advisory hint for block

class BudgetEnforcer(Protocol):
    """Consulted before every LLM call to decide allow / warn / block."""

    async def consult(self, request: BudgetConsultation) -> BudgetDecision:
        ...

    async def record(self, request: BudgetConsultation, actual_cost_usd: float) -> None:
        """Called AFTER the model call completes with the real cost."""
        ...
```

### Backend Changes

**1. New module** `backend/budget/enforcer.py` (~100 LOC).

Defines the Protocol + `BudgetConsultation` + `BudgetDecision` types. No I/O. Pure type definitions + a registry function `register_budget_enforcer(impl)`.

**2. Reference impl** `backend/budget/in_memory_enforcer.py` (~150 LOC).

- Tracks `{identity_value: float}` cost-to-date in a dict (keyed by current period — default monthly).
- Reads caps from env: `BUDGET_DEFAULT_CAP_USD`, `BUDGET_SOFT_THRESHOLD` (default 0.8), `BUDGET_PERIOD` (`monthly` / `weekly` / `daily`).
- No persistence — resets on backend restart. Suitable for LOCAL_MODE and dev smoke.

**3. ADK callback** `backend/budget/callback.py` (~80 LOC).

```python
def make_budget_callback(enforcer: BudgetEnforcer) -> BeforeModelCallback:
    async def _gate(callback_context, llm_request):
        identity_value = _extract_identity(callback_context)  # reads from User + skill config
        if identity_value is None:
            return None  # opt-out: skill didn't declare budget identity
        skill_id = _extract_skill_id(callback_context)
        multiplier = _read_skill_multiplier(skill_id)  # default 1.0
        projected = _estimate_cost(llm_request) * multiplier
        decision = await enforcer.consult(BudgetConsultation(
            identity_value=identity_value, skill_id=skill_id,
            model_id=llm_request.model, projected_cost_usd=projected,
            invocation_id=callback_context.invocation_id,
        ))
        if decision.action == "block":
            raise BudgetExceededError(decision)
        if decision.action == "warn":
            # Inject a warning system-line into the next response — see below.
            callback_context.state["budget:warn_message"] = decision.message
        return None  # allow → proceed
    return _gate
```

**4. After-model callback** (records actual cost):

```python
def make_budget_record_callback(enforcer: BudgetEnforcer) -> AfterModelCallback:
    async def _record(callback_context, llm_response):
        # Pull actual usage from llm_response.usage_metadata
        # ... compute actual cost via existing llm_metrics pricing table
        await enforcer.record(consultation, actual_cost_usd)
    return _record
```

**5. Wiring in `adk/agent.py`**:

```python
budget_callback = make_budget_callback(get_registered_enforcer())
_composed_before_model = compose_before_model_callbacks(
    _document_injector,
    budget_callback,           # NEW — sprint 2.12
)
```

**6. Skill config extension** `backend/adk/budget_config.py` (~40 LOC).

`SkillMetadata.tool_configs.budget`:
```python
{
  "budget": {
    "identity_key": "group_id",      # which User field to use as identity
    "cost_multiplier": 1.0,          # per-skill scaler
    "exempt": false,                 # bypass the gate entirely (system tools)
  }
}
```

Mirrors `A2uiToolConfig` shape. Skills without this section are exempt (back-compat).

**7. Warning chat-prefix injection** (`backend/adk/callbacks.py`):

If `state["budget:warn_message"]` is set when after_agent_callback fires, prepend a structured warning to the assistant's final text. Cleared after read.

**8. AG-UI error event for block**:

`BudgetExceededError` propagates through ADK; existing AG-UI translator catches it and emits `RUN_ERROR{kind: "budget_exceeded", message: <decision.message>, retry_after: <seconds>}`.

### Frontend Changes

Existing `useSkillAgent.onRunFailed` already classifies errors. Add a `budget_exceeded` case:
- Renders a banner with the message + retry-after countdown.
- Suppresses the standard "Something went wrong" fallback.

~30 LOC patch to `useSkillAgent.ts` + ~50 LOC for a `BudgetBanner` component.

---

## Implementation Plan

### Phase 1 — Protocol + reference impl + unit tests (~0.4d)

- `backend/budget/enforcer.py`: types + registry.
- `backend/budget/in_memory_enforcer.py`: ref impl.
- Tests: consult under cap → allow; consult above soft → warn; consult above hard → block; record updates state; period rollover resets state.

### Phase 2 — ADK callback + skill config + integration (~0.5d)

- `backend/budget/callback.py`: before + after callbacks.
- `BudgetConfig` Pydantic model alongside `A2uiToolConfig`.
- Wire into `adk/agent.py` `_composed_before_model`.
- Tests: end-to-end via in-memory enforcer + a stub skill — allow → call happens; warn → call happens + state has warn_message; block → BudgetExceededError raised + no model invocation.

### Phase 3 — AG-UI error translation + frontend banner (~0.3d)

- AG-UI translator: catch `BudgetExceededError`, emit typed RUN_ERROR.
- Frontend `BudgetBanner` + `useSkillAgent` classifier branch.
- Frontend tests: error → banner; banner shows retry-after; banner dismisses on retry success.

### Phase 4 — Docs + smoke (~0.3d)

- Howto doc `docs/integrations/budget-enforcement.md` — how forks plug their own enforcer + the config knobs.
- Audit-row in talk doc.
- Smoke via chrome-devtools: set tiny cap → fire a skill → see warn → fire again → see block.
- AIPLA reference impl sketch as an appendix in the howto doc — gives them a concrete starting point.

---

## Migration & Rollout

- **Backward compatible**: skills without `tool_configs.budget` are exempt. No existing skill needs to change.
- **Default disabled**: if no `BudgetEnforcer` is registered, the callback is a no-op. Forks opt in by calling `register_budget_enforcer(MyImpl())` at startup.
- **LOCAL_MODE behaviour**: in-memory enforcer with high default cap; suitable for workshop demos.
- **AIPLA fork path**: write `FirestoreBudgetEnforcer` + `BigQueryBackedEnforcer` in the fork; register at startup. No platform changes needed.

---

## Testing Strategy

### Backend (pytest)

- Protocol conformance: in-memory impl satisfies the Protocol (static check via `isinstance` against `runtime_checkable`).
- Consult + record interaction: 100 calls under cap → all allow; 1 call pushes past soft → warn; subsequent → block.
- Period rollover: change current period, state resets.
- Multi-identity isolation: usage on identity A doesn't affect B.
- Skill multiplier: declared 3.0 multiplier scales projected cost.
- Exempt skill: `exempt: true` bypasses the gate.
- ADK callback integration: end-to-end with a fake LLM.

### Frontend (Vitest)

- `useSkillAgent` classifies budget_exceeded → banner state.
- `BudgetBanner` renders message + countdown; dismisses on subsequent successful run.

### Manual

- Set `BUDGET_DEFAULT_CAP_USD=0.001` in `.env`; fire a skill; watch banner appear after one turn (the first turn likely tips over hard).

---

## Security Considerations

- **Identity extraction**: the callback reads identity from `User.{group_id, uid, ...}`. If the wrong field is configured, gating fails open (no identity → exempt). Mitigation: log a structured warning at skill-load time if `tool_configs.budget.identity_key` doesn't resolve on the User shape currently in use.
- **Race condition between consult and record**: two concurrent turns both pass the consult check then both record, blowing past cap by one call. Acceptable for v1; ref impl uses an asyncio lock for the in-memory case. Forks with high concurrency need optimistic locking on their backend.
- **Replay attacks via `invocation_id`**: ref impl deduplicates consult calls by `invocation_id` for 60s. Stops a retry storm from double-counting.
- **Forced bypass**: a malicious skill author could set `exempt: true`. Mitigation: skill review (already gated by `can_access_skill`) is the enforcement axis; admins should treat `exempt` as a privileged claim.
- **No PII in budget logs**: `identity_value` is opaque to the platform. Forks that use PII-bearing identities (email, name) are responsible for their own log hygiene — recommend hashing before passing to `consult`.

---

## Performance Considerations

- **Sync consult on every model call**: hot path. Ref impl is O(1) dict lookup. Budget the Protocol contract to ≤50ms for any backed impl; >50ms → emit a warning log; >500ms → degrade to allow + emit error log.
- **Cost estimation pre-call**: `_estimate_cost(llm_request)` uses prompt tokens × input-rate + max_response_tokens × output-rate. Over-estimates by design (allows worst-case planning).

---

## Open Questions

1. **Should the warn message be model-rendered or platform-rendered?** Model-rendered (let the agent phrase it) gives nicer UX but burns the budget the warning is about. Recommend platform-rendered (deterministic prefix) for v1.
2. **Per-model budget caps?** "GPT-5 budget is X, Claude budget is Y." Likely yes for cost-conscious forks; trivially supportable by `identity_value = "group:X|model:gpt-5"`. Document the convention.
3. **Daily soft-reset of "warned today" flag?** So users don't see the warning banner forever once they've seen it once. Punt to ref impl decision.

---

## Related Documents

- [AIPLA ADR-014](https://www.sunholo.com/aipla/architecture.html#adr-014-per-group-per-class-budget-enforcement) — the request.
- [backend/observability/llm_metrics.py](../../../../backend/observability/llm_metrics.py) — existing meter; budget enforcer consults the same pricing table.
- [anonymous-group-id-auth.md](anonymous-group-id-auth.md) — sprint 2.11 (shipped 2026-05-19); provides the `group_id` identity that AIPLA's enforcer impl will key on.
- [tenant-id-span-attribute.md](../tenant-id-span-attribute.md) — sprint 2.14; budget consultations land in OTel with full attribution.
