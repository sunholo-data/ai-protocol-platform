# Sprint Plan: TENANT-SPAN-ATTRIBUTE — v6.2.0 Sprint 2.14

## Summary

Universal per-tenant attribution on every OTel span via a contextvar
+ SpanProcessor pair, as designed in
[tenant-id-span-attribute.md](tenant-id-span-attribute.md). Forks
plug additional enrichers via `register_tenant_enricher(fn)`. The
platform's defaults are non-PII (`tenant.uid`, `tenant.auth_mode`,
optional `tenant.group_id`, optional hashed `tenant.uid_hash`).

This is the **fourth and final AIPLA template-extension** sprint —
closes the per-cohort filterability gap that surfaced as fork-side
workarounds in 2.11 (anon-group auth), 2.12 (budget enforcement),
and 2.13 (artefact review). The same tenant identity threads through
all three.

**Duration:** 0.5 focused day + 0.2d buffer
**Scope:** Backend only. One contextvar + one SpanProcessor + single-insertion-point wire-up in `get_current_user` + integration test + howto.
**Dependencies:**
- [backend/observability/telemetry.py](../../../../backend/observability/telemetry.py) — existing OTel setup; we extend via the documented `SpanProcessor` interface (no breaking change)
- [backend/observability/timing.py](../../../../backend/observability/timing.py) — establishes the contextvar pattern (`_current_tracker`); we mirror it
- [backend/auth/__init__.py:89](../../../../backend/auth/__init__.py) `get_current_user` dispatcher — the canonical single insertion point for `set_tenant_context(user)` (13 endpoints use `Depends(get_current_user)`; touching the dispatcher covers all of them)

**Risk Level:** Low — small surface, well-bounded, established contextvar pattern. Main risk is forgetting to wire up an endpoint that doesn't go through the dispatcher (none expected, but audit).

**Design Doc:** [tenant-id-span-attribute.md](tenant-id-span-attribute.md) — full Protocol contract, axiom alignment (+6 net), security considerations, performance budget (≤50µs per span).

**Deadline:** AIPLA need-by week 9-13 (~5-9 weeks out). 0.5-day estimate. Massive slack.

## Velocity Context (recent sprint days)

- Sprint 2.11 anon-group-auth: 2d est / ~0.5d actual + 2340 LOC vs 1490 (+57%)
- Sprint 2.12 budget-enforcement: 1.5d est / ~0.4d actual + 2605 LOC vs 1845 (+41%)
- Sprint 2.13 artefact-render-hook: 0.5d est / 0.5d actual + 2433 LOC vs 970 (+151%)
- **Implication for 2.14:** ~680 LOC raw — expect ~900-1200 actual. Backend-only, no 8-gate matrix, no 4-path matrix, no PII-leak audit (PII is one rule: hash email, never display_name). Time should land at ~0.2-0.3 days.

## Milestone Breakdown

### M1 — Context module + SpanProcessor + unit tests (`backend`, ~0.2d)

**Files**
- `backend/observability/tenant_context.py` (~120 LOC, new) — `_tenant_context: ContextVar[dict[str, str] | None]`; `set_tenant_context(user, extra=None)` reads `user.uid` + `user.auth_mode` (default "firebase" via getattr) + conditionally `user.group_id` + conditionally hashed email as `tenant.uid_hash` via SHA256; `get_tenant_context() -> dict | None`; `TenantEnricher = Callable[[User], dict[str, str]]`; `register_tenant_enricher(fn)` validates callable (raises TypeError); `clear_tenant_enrichers()` for tests; internal `_hash_email(email: str) -> str` helper
- `backend/observability/tenant_span_processor.py` (~60 LOC, new) — `TenantAttributeSpanProcessor(SpanProcessor)`; `on_start(span, parent_context)` reads contextvar + sets each attr; `on_end` no-op; `shutdown` no-op; `force_flush` returns True (OTel SpanProcessor contract)
- `backend/tests/unit/test_tenant_context.py` (~150 LOC) — contextvar set/get; contextvar isolation across concurrent asyncio.gather tasks; SpanProcessor stamps attrs via `InMemorySpanExporter`; SpanProcessor with no context emits span unchanged; enricher exception swallowed + WARN log; multiple enrichers compose; `_hash_email` is one-way (same input → same output, output ≠ input); register_tenant_enricher rejects non-callables

**Acceptance criteria**
1. `set_tenant_context(user)` stamps `tenant.uid`, `tenant.auth_mode` always; `tenant.group_id` only when `user.group_id` is a non-empty string; `tenant.uid_hash` (SHA256 of email) only when `user.email` is non-empty
2. `tenant.uid_hash` is one-way — same email produces same hash, hash is NOT the email
3. `TenantAttributeSpanProcessor.on_start` stamps every attr from the contextvar onto the span; no context → no attrs (no crash)
4. **Contextvar isolation across concurrent tasks**: two `asyncio.gather` tasks setting different contexts produce spans with their own attrs (zero cross-tenant leakage) — the headline correctness test
5. Enricher exception is caught + logged at WARN; other enrichers still run; the span still emits
6. `register_tenant_enricher(non_callable)` raises TypeError (fail-loud at fork bootstrap — mirrors sprint 2.12 + 2.13 patterns)
7. Multiple enrichers compose — later registrations add to earlier ones; collisions on the same attr key resolve to the LAST enricher's value

**Risk:** None — pure stdlib + OTel SDK. Established pattern from `timing.py`'s `_current_tracker`.

### M2 — Single-insertion wire-up + integration test (`backend`, ~0.2d)

**Files**
- `backend/observability/telemetry.py` patch (~15 LOC) — instantiate `TenantAttributeSpanProcessor()` + register via `provider.add_span_processor(...)`. Order vs the existing OTLP batch processor doesn't matter (both run per-span); add the tenant processor FIRST so attrs are visible to any other processor that inspects them
- `backend/auth/__init__.py` patch (~10 LOC) — at the bottom of `get_current_user` (the dispatcher), call `set_tenant_context(user)` exactly once before returning. Covers all 13 `Depends(get_current_user)` callers without touching them individually
- `backend/auth/local_mode_stub.py` — verify `get_current_user_local_mode` also threads through the dispatcher's `set_tenant_context` call (it does — the dispatcher wraps it)
- `backend/tests/integration/test_tenant_attribution.py` (~140 LOC, new) — TestClient + `InMemorySpanExporter` registered alongside `TenantAttributeSpanProcessor`; POST `/api/whoami` (simplest auth-bearing endpoint); assert exported span carries `tenant.uid` + `tenant.auth_mode`; second test with a User who has `group_id` set asserts `tenant.group_id` is on the span; third test with two concurrent async requests under different Users → spans isolated correctly

**Acceptance criteria**
1. `telemetry.py:setup_telemetry()` registers the `TenantAttributeSpanProcessor` on the OTel provider; no regression on existing OTLP export path
2. `get_current_user` dispatcher calls `set_tenant_context(user)` once per request, BEFORE returning the User to the endpoint handler
3. All three auth paths (Firebase, group-auth, LOCAL_MODE stub) flow through the same insertion — verified by per-path integration tests (one per auth mode)
4. **Integration smoke**: TestClient request to `/api/whoami` produces at least one exported span carrying `tenant.uid` matching the authenticated User
5. **Group-auth integration**: when User has `group_id="PHYS-7K2N"`, the exported span carries `tenant.group_id="PHYS-7K2N"`
6. **Concurrent-tenant isolation**: two simultaneous requests via `asyncio.gather` under different Users produce spans with correctly-isolated tenant attrs
7. Existing tests (auth, sessions, skills, mcp_proxy) all still pass — no behavioural regression from the dispatcher patch

**Risk:** The `get_current_user` dispatcher has 3 branches (group / local / firebase). The patch must wrap ALL three. Easiest pattern: extract a `_resolve_user` helper that returns the User, then `set_tenant_context(user); return user` at the dispatcher exit. Or add `set_tenant_context(user)` to each branch's return path — verbose but explicit. Pick the cleanest.

### M3 — Privacy hardening + PII rule documentation (`backend`, ~0.05d)

**Files**
- `backend/observability/tenant_context.py` — module docstring expanded with the PII rule:
  > **PII rule (non-negotiable):** Span attributes leak to Cloud Trace and may be subject to GDPR / CCPA queries. The platform's defaults are non-PII: `tenant.uid` (synthetic id), `tenant.auth_mode` (enum), `tenant.group_id` (synthetic short code), `tenant.uid_hash` (SHA256 of email, one-way irreversible). Raw email, display names, and other PII MUST NEVER land on a span. Forks registering enrichers are documented to follow the same rule; the platform does not gate enricher outputs.
- `backend/tests/unit/test_tenant_context.py` extension (~50 LOC) — explicit tests for the PII rule:
  - `tenant.uid_hash` is deterministic across calls (same email → same hash)
  - `tenant.uid_hash` is irreversible (hash != email; cannot be inverted by visual inspection)
  - Empty email → NO `tenant.uid_hash` attr at all (not "hash of empty string")
  - User with display_name does NOT have a `tenant.display_name` attr (platform never enriches with display_name)

**Acceptance criteria**
1. Module docstring explicitly states the PII rule with the four non-PII defaults enumerated
2. `_hash_email` is SHA256 (cryptographically irreversible — not a custom or weak hash)
3. Empty email field on User → no `tenant.uid_hash` key at all in the attrs dict
4. No code path in `set_tenant_context` writes raw email or display_name to any attr

**Risk:** None — documentation + targeted test additions. Done as a separate milestone for review-trail clarity (the PII rule is the most security-sensitive aspect of this sprint).

### M4 — Howto + audit-row flip + implemented/ move (`fullstack-docs`, ~0.05d)

**Files**
- `docs/integrations/tenant-attribution.md` (~180 LOC, new) — fork adoption howto:
  - TL;DR: registering an enricher; what lands on every span by default
  - Attribute naming conventions (`tenant.*` namespace per OTel semantic-convention precedent)
  - PII rule with the explicit non-PII default list
  - Cloud Trace query examples (`tenant.group_id = "PHYS-7K2N"` filter; per-cohort latency p99; budget-attribution cross-reference)
  - AIPLA-style reference enricher: `class_id_enricher` that looks up `tenant.class_id` from Firestore based on `user.group_id`
  - Integration with sprint 2.12 budget-enforcement (the BudgetEnforcer's `identity_value` and the span's `tenant.uid`/`tenant.group_id` are the same identity — cross-reference for log-correlation queries)
  - Cost-of-cardinality note: Cloud Trace handles arbitrary cardinality; Prometheus-style exporters would explode on high-cardinality attrs. Recommend Cloud Trace for tenant attribution; Prometheus only for pre-aggregated counters
  - Migration from hand-rolled per-request log-enrichers (the platform's pattern subsumes the common `log.bind(group_id=...)` workaround)
- `docs/talks/ai-ui-protocol-stack.md` — flip the sprint 2.14 audit-row from 📋 → ✅ with detailed verification-log entry mirroring sprint 2.13's row; update the status banner to "all four AIPLA-extension sprints shipped 2026-05-19"
- `docs/design/v6.2.0/SEQUENCE.md` — row 2.14 marked Shipped; doc link → `implemented/`; status banner updated
- `docs/design/v6.2.0/{ => implemented}/tenant-id-span-attribute.md` + `-sprint.md` → moved; relative-path depth fixed (`../../../` → `../../../../` for backend refs)
- **Housekeeping**: in the moved design doc, fix line 301's stale link `[budget-enforcement.md](budget-enforcement.md)` (broken since sprint 2.12 M4 moved budget-enforcement to `implemented/`). Since both docs are NOW siblings in `implemented/`, the relative path stays `budget-enforcement.md` — but verify it resolves
- `.dev-logs/tenant-span-attribute-smoke.txt` — chain-of-evidence summary documenting the integration test IS the smoke

**Acceptance criteria**
1. Howto covers: TL;DR, attribute naming, PII rule, Cloud Trace query examples, AIPLA-style enricher sketch, integration with sprints 2.11 + 2.12, cardinality trade-off, migration guidance
2. Talk-doc audit row 2.14 flipped 📋 → ✅ with verification log entry matching sprint 2.13's pattern
3. SEQUENCE.md row 2.14 marked shipped; status banner updated to reflect all four AIPLA-extension sprints shipped
4. Both design docs moved to `implemented/`; relative-path depth fixed for backend refs; stale `budget-enforcement.md` link resolves correctly post-move
5. `.dev-logs/tenant-span-attribute-smoke.txt` enumerates the test-as-smoke chain of evidence

**Risk:** None — pure docs + moves. Standard close-out pattern from sprints 2.11 + 2.12 + 2.13.

## Day-by-Day (0.5-day plan)

| Half-day | Slot | Work |
|---|---|---|
| Day 1 AM | 1 | M1: contextvar module + SpanProcessor + 7 unit tests; close M1 |
| Day 1 AM | 2 | M2: telemetry.py + get_current_user patch + integration test; close M2 |
| Day 1 PM | 1 | M3: PII docstring + privacy hardening tests; close M3 |
| Day 1 PM | 2 | M4: howto + audit-row flip + implemented/ move + housekeeping; close sprint |
| (buffer) | | 0.2d for the unknown — most likely the multi-branch dispatcher patch in M2 |

## Quality Gates (per milestone close)

```bash
# Backend (M1, M2, M3, M4)
cd backend && make lint && make test-fast

# `make lint` was fixed in commit ec9da26 to match CI exactly. Local
# failures now surface BEFORE push.
```

No frontend changes in this sprint.

## Push Policy

- Commit at each milestone close (4 commits expected).
- DO NOT push until user confirms — per project pre-push review convention.
- Smoke = the integration test in M2 (chrome-devtools not load-bearing for a backend-only sprint).

## Open Questions (carried from design doc)

1. **Should we also enrich structured log lines (not just spans)?** Today `extra={...}` on each log carries some context. Tenant attribution on log lines would also be useful. Probably yes; same hook can emit a `logging.LogRecord` filter. Out of scope for v1 but small follow-up.
2. **AG-UI `forwardedProps.tenant_id` shortcut?** When a frontend wants to bypass auth-resolution (e.g. embedding the platform in a different host), allow forwardedProps to override the tenant context? Risky — defaults to a forgery vector. Recommend: never trust forwardedProps for tenant; always derive from authenticated User.

Resolution: neither blocks the AIPLA week-9-13 need-by.

## Out of Scope (explicitly)

- **PII on spans** — non-negotiable. Email becomes `tenant.uid_hash`; display_name never lands.
- **Log-line enrichment** — separate concern; deferred to follow-up.
- **Custom exporters** — Cloud Trace / OTLP target is already configured.
- **Cross-process propagation** — already works via OTel W3C TraceContext baggage; this sprint adds tenant attrs as span attributes, not baggage (baggage is opt-in per consumer).

## AIPLA Sequence Close-out

This is the **fourth and final** AIPLA-derived sprint. After shipping:

- 2.11 ✅ anonymous-group-id-auth (shipped 2026-05-19)
- 2.12 ✅ budget-enforcement (shipped 2026-05-19)
- 2.13 ✅ artefact-render-hook (shipped 2026-05-19)
- 2.14 ✅ tenant-id-span-attribute (this sprint)

All four AIPLA template-extensions shipped from one day of feature-request triage on 2026-05-19. Pattern continues from sprints 2.6–2.10: fork requests surface template gaps; ship the seams upstream so the next fork doesn't reinvent.

The four sprints share the same tenant identity:
- 2.11 mints `group_id` (the synthetic short code)
- 2.12 keys budget consultations on `identity_value` (typically `group_id`)
- 2.13's reviewer can correlate audit blocks by `group_id`
- 2.14 stamps `tenant.group_id` on every span

A future Cloud Trace query like `tenant.group_id = "PHYS-7K2N" AND span.name = "llm_call"` filters every LLM call from that cohort across all four mechanisms.
