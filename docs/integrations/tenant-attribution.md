# Tenant attribution — fork adoption howto

**Sprint reference:** [v6.2.0 sprint 2.14 design doc](../design/v6.2.0/implemented/tenant-id-span-attribute.md)
**For:** Forks running the platform in multi-tenant contexts where Cloud Trace needs to be queryable by tenant identity (cohort, organization, customer, team).

This is the simplest of the four AIPLA template-extensions to adopt — the platform's defaults give you `tenant.uid`, `tenant.auth_mode`, optional `tenant.group_id`, and optional `tenant.uid_hash` on every span out of the box. Forks plug `register_tenant_enricher` to add their own attributes (e.g. AIPLA's `tenant.class_id`).

---

## TL;DR

Default behaviour (no fork code needed):

```python
# At runtime, every OTel span emitted during a request carries:
{
    "tenant.uid": "firebase-abc",       # or "anon-PHYS-xyz" for anon-group
    "tenant.auth_mode": "firebase",     # or "anonymous_group_id" | "local_mode_stub"
    "tenant.group_id": "PHYS-7K2N",     # only when present (sprint 2.11 identity)
    "tenant.uid_hash": "<SHA256 hex>",  # only when email present; one-way irreversible
}
```

To add fork-specific attributes:

```python
# In your fork's startup module (after env loaded):
from observability.tenant_context import register_tenant_enricher

def class_id_enricher(user):
    """AIPLA-style: resolve class_id from the group_id via Firestore."""
    if not user.group_id:
        return {}
    class_id = lookup_class_for_group(user.group_id)  # your fork's logic
    return {"tenant.class_id": class_id} if class_id else {}

register_tenant_enricher(class_id_enricher)
```

That's the full surface. The platform's `auth.get_current_user` dispatcher already calls `set_tenant_context(user)` before returning — every span emitted during the request inherits the contextvar via Python's per-task contextvar semantics.

---

## How it works

```
POST /api/skill/{id}/stream
  │
  ▼  FastAPI dependency resolution
  ▼  Depends(get_current_user) — single insertion point
  │
  ├─ _resolve_user(request) — token-shape dispatch (Firebase / group / LOCAL_MODE)
  │
  ▼  set_tenant_context(user) — binds contextvar with the four non-PII defaults
  │   + invokes every registered enricher
  │
  ▼  Endpoint handler + ADK runner + tools + LLM calls run in same async task
  │
  ▼  Every started span ── TenantAttributeSpanProcessor.on_start ──┐
  │                          │                                     │
  │                          ├─ reads contextvar                   │
  │                          ├─ stamps attrs onto span             │
  │                          ▼                                     │
  │                       span exported with tenant.* attrs ───────┘
```

Python contextvars are per-task — concurrent requests under different tenants get isolated attrs automatically. Tested explicitly via `asyncio.gather` + barriers.

---

## Attribute naming convention

The platform uses the `tenant.*` namespace per OTel semantic-convention precedent (`service.*`, `user.*`, `enduser.*` are documented; `tenant.*` is not yet but follows the same pattern):

| Attribute | Type | Source | When present |
|---|---|---|---|
| `tenant.uid` | string | `User.uid` | Always |
| `tenant.auth_mode` | string | `User.auth_mode` | Always |
| `tenant.group_id` | string | `User.group_id` | Only when non-empty (sprint 2.11 identity) |
| `tenant.uid_hash` | string | SHA256 of `User.email` | Only when email is non-empty |

Forks adding their own attributes via enrichers should:
- Use the `tenant.*` namespace for consistency
- Use lowercase + dotted-namespace per OTel conventions
- Document which attrs they add (the SpanProcessor doesn't introspect the enricher list at runtime)

---

## PII rule (non-negotiable)

Span attributes leak to Cloud Trace and may be subject to GDPR / CCPA right-of-access queries. The platform's defaults are non-PII by construction.

**Hard rules for fork enrichers:**

1. **NEVER return raw email, display name, phone number, or other reversible PII.** Hash before returning if you need an identity-stable correlation key.
2. **NEVER return free-form user input** (chat messages, query text, etc.) — high cardinality + likely PII.
3. **Hashing recommendation: SHA256.** The platform's `_hash_email` uses SHA256; mirror it.
4. **The platform does NOT gate enricher outputs.** A fork that returns `{"tenant.email": user.email}` will land raw email on every span. This is YOUR responsibility.

The implementation reads `User` fields **explicitly** (no reflection over `__dict__`) so a future `User` subclass with `display_name` or similar PII fields cannot accidentally leak through the platform's default code path. Forks subclassing `User` should review their enrichers for the same property.

---

## AIPLA reference enricher

AIPLA's specific shape is fork-side; here's the sketch:

```python
# AIPLA fork — backend/aipla/tenant_enrichers.py
import logging
from observability.tenant_context import register_tenant_enricher
from auth.firebase_auth import User

logger = logging.getLogger(__name__)

def class_id_enricher(user: User) -> dict[str, str]:
    """Resolve the parent class_id for an anonymous-group student.

    The fork maintains a Firestore mapping group_id → class_id
    (set when the teacher creates the group). At request time we
    look it up + stamp `tenant.class_id` on every span so PhD
    research queries can filter by institution-level cohort.

    Returns {} if group_id is missing or the lookup fails — never
    raise (caught at the platform layer + logged as WARN, but
    explicit graceful-failure here keeps the enricher tight).
    """
    if not user.group_id:
        return {}
    try:
        class_id = lookup_class_for_group(user.group_id)  # Firestore
    except Exception as exc:
        logger.warning("class_id_enricher: lookup failed for group=%s: %s", user.group_id, exc)
        return {}
    return {"tenant.class_id": class_id} if class_id else {}

# Register at app bootstrap
register_tenant_enricher(class_id_enricher)
```

---

## Cloud Trace query examples

With the four shipping defaults + AIPLA's `tenant.class_id` enricher, the following Cloud Trace queries become trivial:

```
# All LLM calls for one cohort over the past 24h
tenant.group_id = "PHYS-7K2N" AND name = "llm_call"

# Per-cohort p99 latency for the stream endpoint
tenant.group_id != "" AND name = "POST /api/skill/{id}/stream"
# Then group by tenant.group_id and aggregate p99(duration)

# Anonymous-group sessions only (exclude Firebase + LOCAL_MODE)
tenant.auth_mode = "anonymous_group_id"

# Cross-mechanism correlation:
# Find every block + every budget warn + every refused artefact
# for one cohort:
tenant.group_id = "PHYS-7K2N" AND (
    name = "ARTEFACT_BLOCKED" OR
    name = "BUDGET_EXCEEDED" OR
    name = "GROUP_REVOKED"
)
```

---

## Integration with sprints 2.11 / 2.12 / 2.13

The four AIPLA template-extensions share the same tenant identity:

| Mechanism | Identity key | Where it surfaces |
|---|---|---|
| 2.11 anonymous-group auth | `User.group_id` (mints the short code) | Every request after a student joins a group |
| 2.12 budget enforcement | `identity_value` (typically `User.group_id`) | `consult()` + `record()` calls; budget logs |
| 2.13 artefact review | `tool_name + server_id + invocation_id` | Audit POST on every block decision |
| 2.14 tenant attribution | `tenant.group_id` | Every OTel span for the request |

A single Cloud Trace query like `tenant.group_id = "PHYS-7K2N" AND span.name = "llm_call"` filters every LLM call from that cohort, including the ones blocked by the budget enforcer (the budget_exceeded RUN_ERROR span carries the same `tenant.group_id`). The four sprints are deliberately complementary.

---

## Cost of cardinality

Each unique `tenant.group_id` adds a label dimension. The cost story differs by exporter:

| Exporter | Cardinality behaviour |
|---|---|
| **Cloud Trace (recommended)** | Handles arbitrary cardinality fine. Trace IDs are already unique; tenant attrs are extra attributes on existing spans, not extra time-series. |
| **Prometheus-style exporters** | Would explode. Each unique label combination is a separate time-series; thousands of group_ids × dozens of metrics = millions of series. NOT recommended for tenant attribution. |
| **Pre-aggregated counters** | Use a small bucket of tenant tiers (`tenant.tier = "free" | "pro" | "enterprise"`) rather than per-cohort group_ids if you must export to Prometheus. |

For AIPLA's classroom shape — hundreds of cohorts × dozens of spans per request — Cloud Trace is the right home.

---

## Configuration knobs

The platform doesn't ship knobs — every detail lives in the registered enrichers. If your enricher needs config (e.g. a Firestore project for `class_id` lookups), keep it in the enricher's closure: read env vars at app bootstrap, capture in the function.

---

## Migration from hand-rolled per-request log enrichers

If your fork currently does this:

```python
# Old pattern: per-call structured log enrichment
logger.info("agent turn", extra={"group_id": user.group_id, "class_id": class_id})
```

…migration is mechanical: write the equivalent as a tenant enricher, register once at bootstrap, and the same attribution lands on every OTel span (not just the log lines you remembered to enrich). The old `extra={...}` calls can stay — both sources of attribution are useful.

---

## Smoke testing locally

```bash
# 1. Register an in-memory enricher in backend/app.py or your fork's bootstrap:
#    def test_enricher(user):
#        return {"tenant.test_flag": "yes"}
#    register_tenant_enricher(test_enricher)

# 2. Set up the OTel collector or use Cloud Trace dev project.
# 3. Fire a request to /api/whoami:
make dev
curl -H "Authorization: Bearer <token>" http://localhost:1956/api/whoami

# 4. Inspect the exported span:
#    - tenant.uid + tenant.auth_mode always present
#    - tenant.test_flag = "yes" (proves the enricher ran)
```

The platform's own test suite is the canonical smoke. See [.dev-logs/tenant-span-attribute-smoke.txt](../../.dev-logs/tenant-span-attribute-smoke.txt) for the chain of evidence.

---

## Open questions / follow-ups

- **Log-line enrichment** (in addition to spans): today `extra={...}` on each log carries some context. A `logging.LogRecord` filter mirroring the SpanProcessor would extend attribution to log lines. Out of scope for v1; small follow-up if a fork needs it.
- **AG-UI `forwardedProps.tenant_id` shortcut**: when a frontend wants to bypass auth-resolution (e.g. embedding the platform in a different host), should forwardedProps override the tenant context? Risky — defaults to a forgery vector. Recommendation: NEVER trust forwardedProps for tenant; always derive from authenticated User.
- **OpenTelemetry semantic-conventions PR for `tenant.*` namespace**: no published spec yet. The platform follows the precedent of `service.*` and `user.*`. Worth upstreaming if the OTel SIG accepts it.

---

## AIPLA template-extension sequence — sprint 2.14 closes the loop

This is the fourth and final AIPLA-derived sprint. After shipping, the four template-extensions are complete:

- 2.11 ✅ anonymous-group-id-auth (mints `group_id`)
- 2.12 ✅ budget-enforcement (keys consultations on `identity_value`)
- 2.13 ✅ artefact-render-hook (audit-correlates blocks by `group_id`)
- 2.14 ✅ tenant-id-span-attribute (stamps `tenant.group_id` on every span)

The same tenant identity threads through all four — a single Cloud Trace query like `tenant.group_id = "PHYS-7K2N"` filters every interaction from that cohort across all four mechanisms.

---

## Related docs

- [Sprint 2.14 design doc](../design/v6.2.0/implemented/tenant-id-span-attribute.md) — full Protocol contract, axiom alignment, security considerations.
- [Sprint 2.11 anonymous-group-id-auth howto](anonymous-group-id-auth.md) — provides the `group_id` identity that lands as `tenant.group_id`.
- [Sprint 2.12 budget-enforcement howto](budget-enforcement.md) — consumes the same identity for budget consultations; correlates with span attribution via shared `group_id`.
- [Sprint 2.13 artefact-review-hooks howto](artefact-review-hooks.md) — third sibling sprint; audit logs share the `tenant.group_id` attribution.
- [AIPLA ADR-005](https://www.sunholo.com/aipla/architecture.html#adr-005-chat-log-storage) — the request that surfaced this.
