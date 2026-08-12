# Tenant-ID span attribute — universal OTel attribution hook

**Status**: Proposed
**Priority**: P2 (small, broadly useful; AIPLA need by week 9-13 not blocking)
**Estimated**: ~0.5 day (contextvar + OTel span processor + skill_processor wire + tests + docs)
**Scope**: Backend — one new `tenant_context` contextvar, one OTel `SpanProcessor` that stamps every span, one extension point for forks to add MORE attributes.
**Dependencies**: Existing OTel setup (`backend/observability/telemetry.py`).
**Surfaced by**: AIPLA fork [ADR-005 — chat-log storage / per-cohort attribution](https://www.sunholo.com/aipla/architecture.html#adr-005-chat-log-storage). Broader uses: any multi-tenant fork wants per-tenant filtering on traces / logs.
**Created**: 2026-05-19

---

## Problem Statement

The platform emits OTel spans from every layer (FastAPI request span, ADK agent span, tool spans, LLM-call spans, MCP-proxy spans). Spans carry technical attributes (model, latency, tokens) but **no business attribution** — the operator can't filter "show me all spans for cohort X last week" without grepping logs.

Every multi-tenant fork hits this:

| Fork shape | What they want to attribute by |
|---|---|
| AIPLA classroom | `group_id` (cohort), `class_id` (institution) |
| Multi-org SaaS | `org_id`, `project_id` |
| Customer support tool | `customer_id`, `ticket_id` |
| Internal multi-team | `team_id` |

The KEY varies; the SHAPE doesn't:

1. Authoritative identity gets established at the FastAPI ingress (auth resolves it).
2. The identity needs to thread through async code — agent runs, tool calls, MCP requests — without explicit plumbing.
3. Every OTel span emitted during that turn needs the identity stamped on it.
4. Forks add MORE attributes than the platform knows about (`class_id`, `lesson_id`, etc.) — needs an extension point.

The platform already has the right OTel setup (`telemetry.py` configures the exporters). The gap is the context plumbing + the span enricher.

### Current state

- `backend/observability/telemetry.py`: configures OTel SDK, sets up `OTLPSpanExporter` to Cloud Trace, batches spans. No custom enrichment.
- `backend/observability/timing.py`: `LatencyTracker` per request, bound to a contextvar (`_current_tracker`). Pattern is established.
- `backend/fast_api_app.py`: each `/api/skill/{id}/stream` request creates a tracker context. Auth has already resolved.
- `backend/auth/`: produces `User` objects with `uid` + possibly `group_id` (post-sprint-2.11) + `domain`.
- ADK + downstream: no contextvar for tenant identity; spans emit with technical attributes only.

### Impact

- **AIPLA research path blocked**: PhD analysis wants "what did cohort X's students do over the term"; without `group_id` on spans, the query path is impossible at trace level.
- **Every commercial fork hits the same wall**: they each invent a side-channel for attribution (a parallel structured log line) rather than fixing the spans.
- **Cost attribution per tenant** (sprint 2.12): the budget enforcer needs to log "this tenant spent $X today" — same attribution problem.

### Why it's small

The platform pattern is already there: `LatencyTracker` is a contextvar bound at request start; OTel exporters are configured centrally. We add one MORE contextvar + one SpanProcessor that reads it. The total change is ~150 LOC including tests.

---

## Goals

**Primary Goal:** Add a `tenant_context` Python contextvar set at FastAPI request start (sourced from `User`); a `TenantAttributeSpanProcessor` that stamps every span with the current context's attributes; an extension point so forks can add their own attributes without forking the processor.

**Success Metrics:**
- Every span emitted during a `/api/skill/{id}/stream` request carries `tenant.uid`, `tenant.auth_mode`, and (if present) `tenant.group_id` attributes.
- Forks can register additional enrichers (`register_tenant_enricher(fn)`) that contribute their own attributes — AIPLA registers one that adds `tenant.class_id` from a Firestore lookup.
- Trace query in Cloud Trace: filter spans by `tenant.group_id = "PHYS-7K2N"` → returns every span (request → agent → tool → LLM) from that cohort's sessions in the time window.
- Performance: contextvar set is O(1) per request; SpanProcessor adds <50µs per span (per OTel reference).
- LOCAL_MODE: contextvar still works; spans land in the local OTel collector (or no-op if exporter is disabled).

**Non-Goals:**
- PII on spans. The processor lowercase-only strings; the platform never enriches with `email` or `display_name`. Forks responsible for not registering PII enrichers.
- Log-line enrichment (vs spans). Log line attribution is a separate concern; the structured-log path already includes most of this via `extra={...}`. Spans are the gap.
- Custom exporters. Cloud Trace / OTLP target is already configured; the processor stamps attributes regardless of exporter.
- Cross-process propagation. Already works via OTel W3C TraceContext baggage; we add tenant attributes as span attributes, not baggage (baggage is opt-in per consumer).

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral (~50µs per span; sub-perceptual). |
| 2 | EARNED TRUST | +1 | Operators / researchers can show "we know exactly what cohort X did" — straightforward attribution. |
| 3 | SKILLS, NOT FEATURES | 0 | Orthogonal. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Neutral. |
| 5 | GRACEFUL DEGRADATION | +1 | Contextvar missing → SpanProcessor emits without tenant attrs (no crash). Cloud Trace upload failure is already best-effort. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses OTel's standard `SpanProcessor` extension point. No new wire format. |
| 7 | API FIRST | +1 | Single `register_tenant_enricher(fn)` for forks. |
| 8 | OBSERVABLE BY DEFAULT | +2 | This IS the OBSERVABLE BY DEFAULT axiom. Without it, attribution is impossible. |
| 9 | SECURE BY CONSTRUCTION | 0 | Neutral if forks don't register PII enrichers. Document the rule clearly. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend-only feature. |
| | **Net Score** | **+6** | Threshold: >= +4 ✓ |

---

## Standards Compliance Check

- **OTel `SpanProcessor`**: documented extension point ([opentelemetry-python docs](https://opentelemetry.io/docs/languages/python/instrumentation/#span-processors)). Standard interface — `on_start(span, parent_context)`, `on_end(span)`.
- **Python contextvars**: standard library. Tracker pattern already established in `timing.py`.
- **Attribute naming**: `tenant.uid`, `tenant.group_id`, `tenant.class_id`, etc. — dotted-namespace per OTel semantic conventions (no published `tenant.*` namespace yet, but `service.*`, `user.*`, `enduser.*` exist as precedent).

---

## Design

### Overview

```
┌────────────────────────────────────────────────────────────────┐
│ REQUEST LIFETIME                                               │
│                                                                │
│  POST /api/skill/{id}/stream                                   │
│     │                                                          │
│     ▼  auth resolves User                                      │
│     ▼  set_tenant_context(user)  ─── contextvar set here       │
│     │                                                          │
│     ▼  ADK Runner starts                                       │
│        │                                                       │
│        ▼  every span emitted DURING this request               │
│           └── TenantAttributeSpanProcessor.on_start()          │
│                  │ reads contextvar                            │
│                  │ adds tenant.uid, tenant.auth_mode,          │
│                  │      tenant.group_id (if set)               │
│                  │ + invokes registered enrichers              │
│                  ▼                                             │
│              span has tenant.* attributes                      │
│                                                                │
│  on response END: contextvar resets automatically              │
└────────────────────────────────────────────────────────────────┘
```

### Backend Changes

**1. New module** `backend/observability/tenant_context.py` (~80 LOC).

```python
from contextvars import ContextVar
from typing import Callable, Protocol
from auth import User

_tenant_context: ContextVar[dict | None] = ContextVar("tenant_context", default=None)

# Public API
def set_tenant_context(user: User, extra: dict | None = None) -> None:
    """Bind the per-request tenant attributes. Called at FastAPI ingress
    after auth resolves. The same User flows into ADK; spans below this
    point get tagged."""
    attrs = {
        "tenant.uid": user.uid,
        "tenant.auth_mode": getattr(user, "auth_mode", "firebase"),
    }
    if group_id := getattr(user, "group_id", None):
        attrs["tenant.group_id"] = group_id
    if user.email:
        # Hashed, never raw — privacy.
        attrs["tenant.uid_hash"] = _hash(user.email)
    for fn in _registered_enrichers:
        try:
            attrs.update(fn(user))
        except Exception as exc:
            log.warning("tenant enricher failed: %s", exc)
    if extra:
        attrs.update(extra)
    _tenant_context.set(attrs)

def get_tenant_context() -> dict | None:
    return _tenant_context.get()

# Fork extension point
TenantEnricher = Callable[[User], dict[str, str]]
_registered_enrichers: list[TenantEnricher] = []

def register_tenant_enricher(fn: TenantEnricher) -> None:
    """Forks register at startup to add their own attributes
    (e.g. tenant.class_id resolved from Firestore lookup of group_id)."""
    _registered_enrichers.append(fn)
```

**2. SpanProcessor** `backend/observability/tenant_span_processor.py` (~50 LOC).

```python
from opentelemetry.sdk.trace import SpanProcessor, Span
from .tenant_context import get_tenant_context

class TenantAttributeSpanProcessor(SpanProcessor):
    """OTel SpanProcessor that stamps every started span with the
    current request's tenant attributes."""

    def on_start(self, span: Span, parent_context=None) -> None:
        attrs = get_tenant_context()
        if attrs:
            for k, v in attrs.items():
                span.set_attribute(k, v)

    def on_end(self, span: Span) -> None:
        pass  # no-op
```

**3. Wire-up in `backend/observability/telemetry.py`** (~10 LOC patch):

```python
from .tenant_span_processor import TenantAttributeSpanProcessor

def setup_telemetry(...):
    # ... existing setup ...
    provider.add_span_processor(TenantAttributeSpanProcessor())  # NEW
    # ... existing OTLP processor stays — order doesn't matter, both run.
```

**4. Wire-up in `backend/fast_api_app.py`** (~10 LOC patch):

```python
# In stream_skill (and any other endpoint where User is resolved):
from observability.tenant_context import set_tenant_context

@app.post("/api/skill/{skill_id}/stream")
async def stream_skill(..., user: User = Depends(get_current_user)):
    set_tenant_context(user)
    # ... existing logic ...
```

Apply to all endpoints that receive a `User` (sessions, skills, group-auth join, etc.). Single line each.

**5. AIPLA-style reference enricher** (in fork, NOT platform — documented as an example in the howto):

```python
# AIPLA fork — backend/aipla/tenant_enrichers.py
from observability.tenant_context import register_tenant_enricher

def class_id_enricher(user):
    group_id = getattr(user, "group_id", None)
    if not group_id:
        return {}
    class_id = lookup_class_for_group(group_id)  # Firestore
    return {"tenant.class_id": class_id} if class_id else {}

register_tenant_enricher(class_id_enricher)
```

---

## Implementation Plan

### Phase 1 — Module + processor + tests (~0.2d)

- `tenant_context.py`: contextvar + setters + enricher registry.
- `tenant_span_processor.py`: SpanProcessor impl.
- Pytest: contextvar set/get; processor reads attrs and stamps span (use InMemorySpanExporter to assert); enricher exception swallowed + logged; multiple enrichers compose.

### Phase 2 — Wire-up + integration test (~0.2d)

- `telemetry.py` patch.
- `fast_api_app.py`: set_tenant_context calls on each User-bearing endpoint.
- Integration test: TestClient request → check that the span exported during that request carries `tenant.uid`.

### Phase 3 — Docs + howto (~0.1d)

- Howto `docs/integrations/tenant-attribution.md` — registering enrichers + the AIPLA-style example + naming conventions + the PII rule.
- Talk-doc audit row.

---

## Migration & Rollout

- **Backward compatible**: default platform attributes are non-PII (`uid`, `auth_mode`, `group_id` if present, `uid_hash` of email if present). No existing span loses attributes.
- **Forks add via registration**: no upstream changes needed for fork-specific attributes.
- **LOCAL_MODE**: works the same way; contextvar logic doesn't depend on the exporter.

---

## Testing Strategy

### Backend (pytest)

- Contextvar isolation: two concurrent async tasks set different contexts; spans emitted in each carry their respective attrs (no leakage).
- SpanProcessor with no context: emits span unchanged (no crash).
- Enricher exception: logged at warning, doesn't break span emission, other enrichers still run.
- Integration: TestClient + `InMemorySpanExporter` → POST → assert `tenant.uid` on the FastAPI request span.

### Manual

- Local OTel collector → fire a request → see the tenant attrs in the exported span JSON.

---

## Security Considerations

- **PII rule**: docstring + howto explicitly forbid registering PII as span attributes. Email becomes `uid_hash` (one-way hash) automatically. Display names never on spans.
- **Cross-tenant leakage**: contextvar is per-async-task; OTel's SpanProcessor reads the CURRENT context at `on_start`. Concurrent requests under different tenants cannot share attrs.
- **Cost of attribute cardinality**: each unique `tenant.group_id` value adds a label dimension. Cloud Trace handles arbitrary cardinality; cost-aware exporters (Prometheus) would need a different strategy. Document the trade-off.

---

## Open Questions

1. **Should we also enrich structured log lines (not just spans)?** Today `extra={...}` on each log emits some context. Tenant attribution on log lines would also be useful. Probably yes; same hook can emit a `logging.LogRecord` filter. Out of scope for v1 but small follow-up.
2. **AG-UI `forwardedProps.tenant_id` shortcut?** When a frontend wants to bypass the auth-resolution path (e.g. embedding the platform in a different host), allow forwardedProps to override the tenant context? Risky — defaults to a forgery vector. Recommend: never trust forwardedProps for tenant; always derive from authenticated User.

---

## Related Documents

- [AIPLA ADR-005](https://www.sunholo.com/aipla/architecture.html#adr-005-chat-log-storage) — the request.
- [anonymous-group-id-auth.md](anonymous-group-id-auth.md) — sprint 2.11 (shipped 2026-05-19); the `group_id` this depends on for AIPLA's case.
- [budget-enforcement.md](budget-enforcement.md) — sprint 2.12; consumes the same tenant identity as gate keys.
- [backend/observability/telemetry.py](../../../../backend/observability/telemetry.py) — existing OTel setup; this extends it via the documented SpanProcessor interface.
