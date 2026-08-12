# Budget enforcement — fork adoption howto

**Sprint reference:** [v6.2.0 sprint 2.12 design doc](../design/v6.2.0/implemented/budget-enforcement.md)
**For:** Forks running the platform with cost-per-tenant guardrails — classroom cohorts on a fixed monthly LLM budget, commercial tenants billed against a quota, internal teams with chargeback policies.

This howto walks a fork operator from "I want to enforce a budget per cohort" to a running deployment with soft warnings, hard blocks, and a working banner. The platform ships a pluggable Protocol + reference in-memory implementation; the actual policy (Firestore, BigQuery, Redis, billing-system integration) is fork-side.

---

## TL;DR

```python
# In your fork's startup module (after env is loaded, before agents fire):
from budget import register_budget_enforcer, InMemoryBudgetEnforcer

register_budget_enforcer(InMemoryBudgetEnforcer.from_env())
```

```yaml
# In your skill's metadata (firestore document for skills/<id>):
tool_configs:
  budget:
    identity_key: group_id      # which User field to key the budget on
    cost_multiplier: 1.0        # >1 for expensive skills
    exempt: false               # true bypasses the gate (system tools)
```

```bash
# Backend env (set on Cloud Run):
BUDGET_DEFAULT_CAP_USD=50.00
BUDGET_SOFT_THRESHOLD=0.8
BUDGET_PERIOD=monthly
```

That's the full surface. Frontend renders the `BudgetBanner` automatically when the backend blocks a turn.

---

## How it works

```
agent turn
  └─ before_model_callback ─→ enforcer.consult(BudgetConsultation)
                                    ↓
              allow / warn / block ←┘
                ↓        ↓       ↓
              proceed   set     raise
                       state    BudgetExceededError
                                    ↓
                              skill_processor catches
                                    ↓
                        AG-UI RUN_ERROR{code:"BUDGET_EXCEEDED"}
                                    ↓
                              BudgetBanner with countdown
```

The Protocol is consulted **before** every LLM call. The decision determines whether the call proceeds:

- `allow` — call proceeds normally
- `warn` — call proceeds, but the warn message is written to session state (frontend banner surface deferred to a follow-up; the state key is `budget:warn_message`)
- `block` — `BudgetExceededError` is raised before the model is invoked; the user sees a typed banner with a retry-after countdown

After the call lands, `enforcer.record()` is called with the realised cost so the held projection can be reconciled.

---

## Plugging a custom enforcer

Forks implement the `BudgetEnforcer` Protocol. It's [`@runtime_checkable`](https://docs.python.org/3/library/typing.html#typing.runtime_checkable) — duck typing is enough; no inheritance required.

```python
# fork-side: backend/fork_budget.py
from budget.enforcer import BudgetConsultation, BudgetDecision

class FirestoreBudgetEnforcer:
    def __init__(self, fs_client):
        self._fs = fs_client

    async def consult(self, request: BudgetConsultation) -> BudgetDecision:
        cohort_doc = await self._fs.collection("cohorts").document(request.identity_value).get()
        if not cohort_doc.exists:
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None,
                message=None, retry_after_seconds=None,
            )
        cap = cohort_doc.get("monthly_cap_usd") or 0.0
        spend = await self._sum_spend_this_period(request.identity_value)
        projected_total = spend + request.projected_cost_usd
        if projected_total >= cap:
            return BudgetDecision(
                action="block",
                remaining_usd=0.0,
                period_end=self._period_end(),
                message=f"{request.identity_value} is at budget. Reach out to your instructor to top up.",
                retry_after_seconds=self._seconds_until_next_period(),
            )
        if projected_total >= cap * 0.8:
            return BudgetDecision(
                action="warn",
                remaining_usd=cap - projected_total,
                period_end=self._period_end(),
                message=f"{request.identity_value} has used {int(projected_total / cap * 100)}% of this month's budget.",
                retry_after_seconds=None,
            )
        return BudgetDecision(
            action="allow", remaining_usd=cap - projected_total,
            period_end=self._period_end(), message=None, retry_after_seconds=None,
        )

    async def record(self, request: BudgetConsultation, actual_cost_usd: float) -> None:
        # Append a spend row keyed by (identity, period, invocation_id) for
        # idempotency. Released projection (projected - actual) is implicit
        # if you sum spend over the period instead of tracking held amounts.
        await self._fs.collection("spend").add({
            "identity_value": request.identity_value,
            "skill_id": request.skill_id,
            "model_id": request.model_id,
            "actual_cost_usd": actual_cost_usd,
            "invocation_id": request.invocation_id,
            "period_key": self._period_key(),
            "ts": firestore.SERVER_TIMESTAMP,
        })
```

Then register at startup:

```python
# fork-side: backend/main.py or wherever fork init lives
from budget import register_budget_enforcer
from fork_budget import FirestoreBudgetEnforcer

register_budget_enforcer(FirestoreBudgetEnforcer(fs_client=get_firestore_client()))
```

`register_budget_enforcer` validates the shape via the runtime-checkable Protocol — if your class is missing one of the two async methods or has them as sync, registration raises `TypeError`.

---

## Configuration knobs

| Env var | Default | Purpose |
|---|---|---|
| `BUDGET_DEFAULT_CAP_USD` | `0.0` | Per-identity monthly cap in USD. `0.0` means "unconfigured" — the in-memory enforcer falls back to allow with a WARN log (default-deny is opt-in; surprise denial breaks forks that forget to configure). |
| `BUDGET_SOFT_THRESHOLD` | `0.8` | Fraction of cap at which the decision flips from allow to warn. |
| `BUDGET_PERIOD` | `monthly` | One of `daily` / `weekly` / `monthly`. Drives the period key — spend resets at the boundary. |

These knobs are for the reference `InMemoryBudgetEnforcer`. Your own enforcer can read whatever config it wants.

---

## Per-skill config

Each skill opts into budget gating via its `tool_configs.budget` block:

```yaml
tool_configs:
  budget:
    identity_key: group_id
    cost_multiplier: 1.0
    exempt: false
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `identity_key` | `str` | (required) | Which `User` field the enforcer keys on. Common: `group_id` (anonymous-group cohorts), `uid` (per-user), `domain` (per-org). |
| `cost_multiplier` | `float` | `1.0` | Scales the projected cost before consult. Use `>1` for expensive skills (`code-grader` at `3.0` etc.). Zero is rejected — that's what `exempt` is for. |
| `exempt` | `bool` | `false` | Bypasses the gate entirely. No consult, no log line. Use for system tools that must never be gated (e.g. auth or login skills). |

Skills without a `budget` block are exempt by absence — the platform's existing skills don't need to change.

---

## Identity scheme guidance

`identity_value` is **opaque** to the platform. The fork picks the shape. Common choices:

| Fork shape | `identity_key` | `identity_value` example |
|---|---|---|
| Anonymous cohort (AIPLA pattern) | `group_id` | `"group:PHYS-7K2N"` |
| Per-user | `uid` | `"firebase-abc123"` |
| Per-organization | `domain` | `"acme-co.example.com"` |
| Per-customer (commercial) | (custom User field) | `"cust:enterprise-tier-2025"` |
| Per-cohort + per-model | (computed) | `"group:PHYS-7K2N|model:gpt-5"` — concatenated to budget GPT-5 separately from Gemini |

The platform reads `User.<identity_key>` via `getattr`. Any string field on the `User` model works.

---

## No-PII contract

The platform writes structured log lines on every consult:

```
{identity_value, skill_id, projected_cost_usd, decision, remaining}
```

These land in Cloud Logging and (with sprint 2.14) as OTel span attributes. If your fork uses PII-bearing identities (email, name), **hash them before passing to `consult`**:

```python
import hashlib
def hash_identity(raw: str) -> str:
    return "user:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
```

The anonymous-group fork (sprint 2.11) already gets this right — `group_id` is a synthetic short code, no PII.

---

## Ops concerns

### Multi-instance scale-out

The reference `InMemoryBudgetEnforcer` tracks state in a process-local dict. Single-instance Cloud Run (`min=max=1`) is fine. **Multi-instance is NOT fine** — each instance has its own ledger, so the cap gets multiplied by the instance count.

For multi-instance, write your own enforcer backed by a shared store: Firestore, Memorystore Redis, BigQuery streaming inserts (for audit + chargeback). The `FirestoreBudgetEnforcer` sketch above is a starting point.

### Secret rotation

The reference impl has no secrets to rotate. Fork impls that authenticate to Firestore / BigQuery / Redis use ADC; rotate via your usual GCP IAM / SA flows.

### Default-deny vs default-allow

The reference impl defaults to **allow** when no cap is configured (`BUDGET_DEFAULT_CAP_USD=0`). This is intentional — surprise denial breaks forks that forget to configure. A WARN log on every unconfigured consult makes the misconfiguration visible.

Forks that want default-deny implement their own enforcer that returns `action="block"` when no cap row exists for the identity.

### Performance budget

The platform consults the enforcer **synchronously** before every LLM call. Budget the impl to **≤50ms** per consult. The reference impl is O(1) dict lookup (microseconds). A Firestore round-trip is typically 20–80ms — fine. A cross-region BigQuery query is too slow.

If your enforcer is slow, the platform's existing telemetry will surface it. Sprint 2.14 lands tenant attribution on every span so per-cohort latency is filterable.

### Replay / retry storms

The reference impl dedups consults by `(invocation_id, identity_value)` within a 60-second window — a retried turn doesn't double-charge. Forks impls should match this behaviour or use their backend's idempotency primitives.

---

## What's gated, what's not

- ✅ **Every LLM call** that goes through the platform's ADK pipeline is gated.
- ✅ **Sub-agent calls** are gated because they re-enter `before_model_callback`.
- ❌ **Tool execution** is NOT gated by budget. If your skill calls an expensive external API, that's still on the skill author. Budget covers LLM token cost, not arbitrary side-effects.
- ❌ **Streaming mid-cancellation** is NOT supported. The gate is per-call (pre-call), not per-token. If a 4096-token response is half-way through and the user crosses the cap, the response finishes. The next call blocks.

---

## What the user sees

**Allow** — nothing. The turn runs normally.

**Warn** (state key is set; frontend banner is a follow-up surface) — the message lands in `state["budget:warn_message"]`. AG-UI emits it on STATE_SNAPSHOT. A follow-up sprint will wire the frontend `BudgetBanner` to render the warn variant; for now, downstream operators can read the state key from their own surface.

**Block** — `BudgetBanner` renders with:
- The backend's message verbatim (e.g. "Cohort PHYS-7K2N is over its monthly budget.")
- A live retry-after countdown (e.g. "Resets in 12 days.")
- A "Got it" dismiss button
- `role="alert"` + `aria-live="assertive"` so screen readers announce it

The model call doesn't happen. The user can dismiss the banner; the next attempt re-consults. If the cap resets (period rollover or admin top-up), the next attempt allows.

---

## Smoke testing locally

```bash
# 1. Set a tiny cap in backend/.env:
echo 'BUDGET_DEFAULT_CAP_USD=0.0001' >> backend/.env
echo 'BUDGET_PERIOD=daily' >> backend/.env

# 2. Configure a skill with budget gating. In Firestore emulator or
#    via the API, set:
#    skills/<your-skill>.tool_configs.budget = {
#      "identity_key": "uid",  # or "group_id" if anon-group
#      "cost_multiplier": 1.0,
#      "exempt": false
#    }

# 3. Register the in-memory enforcer at startup. The simplest path:
#    add to backend/app.py:
#       from budget import register_budget_enforcer, InMemoryBudgetEnforcer
#       register_budget_enforcer(InMemoryBudgetEnforcer.from_env())

# 4. Fire a turn. The first turn should warn (low input tokens still
#    cross 80% of $0.0001). The second turn should hard-block.
make dev
# In the frontend, send a message. Watch:
#   - Backend log: `budget.cap_unconfigured` if cap=0 (sanity check)
#   - Network panel: RUN_ERROR with code=BUDGET_EXCEEDED on block
#   - DOM: BudgetBanner with countdown
```

---

## Migration from hand-rolled cost limiters

If your fork already has a cost limiter (a custom decorator, a middleware, a session-state counter), migration is mechanical:

1. **Move your existing accounting into a class** implementing `BudgetEnforcer` (`async consult` + `async record`).
2. **Delete your custom decorator/middleware** that intercepted LLM calls. The platform's `before_model_callback` handles it.
3. **Add `tool_configs.budget`** to skills that opt in.
4. **Register your enforcer** at startup.
5. **Test the gate paths** against the reference implementation's 8-gate matrix as a template.

The old hand-rolled UX (custom error message in chat? generic 500?) is replaced by the typed `BudgetBanner` automatically.

---

## Audit / chargeback

`record()` is the natural integration point for billing and audit. Append a row per call:

```python
async def record(self, request, actual_cost_usd):
    await self._bq.insert_rows_json("ops.llm_spend", [{
        "ts": datetime.now(UTC).isoformat(),
        "identity_value": request.identity_value,
        "skill_id": request.skill_id,
        "model_id": request.model_id,
        "actual_cost_usd": actual_cost_usd,
        "invocation_id": request.invocation_id,
    }])
```

Combined with sprint 2.14 (`tenant.uid`/`tenant.group_id` on every OTel span), you get per-cohort spend AND per-cohort latency from one filter on one table.

---

## Open questions / follow-ups

- **Per-model caps** (`GPT-5 budget vs Gemini budget`): support via the `identity_value` convention (`"group:X|model:gpt-5"`); no platform change needed.
- **Forecast-based gating** ("you'll hit your limit by Friday"): out of scope for v1.
- **Warn-prefix injection into the assistant text bubble** (vs banner): the design considered text mutation, the implementation prefers banners — non-blocking warn shows up as a STATE_SNAPSHOT key; surfacing as a yellow banner is the next sprint's frontend slice.
- **`record()` on partial failures**: if the model errors mid-stream, the after-model callback may not fire — held projection stays for the period. The 60s replay dedup mitigates the retry case. Forks needing tighter reconciliation should add a periodic "release stale holds" job.

---

## Related docs

- [Sprint 2.12 design doc](../design/v6.2.0/implemented/budget-enforcement.md) — full Protocol contract, axiom alignment, security/performance considerations.
- [Sprint 2.11 anonymous-group-id-auth howto](anonymous-group-id-auth.md) — provides the `group_id` identity AIPLA's enforcer will key on.
- [Sprint 2.14 tenant-id-span-attribute](../design/v6.2.0/tenant-id-span-attribute.md) — lands `tenant.uid` / `tenant.group_id` on every OTel span. Pairs cleanly: every `consult` log line gets the same identity attribution.
- [backend/observability/llm_metrics.py](../../backend/observability/llm_metrics.py) — the per-model pricing table the in-memory enforcer reuses for cost projection.
- [AIPLA ADR-014](https://www.sunholo.com/aipla/architecture.html#adr-014-per-group-per-class-budget-enforcement) — the request that surfaced this.
