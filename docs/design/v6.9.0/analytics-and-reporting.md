# Analytics & Reporting (over natively-captured data)

**Status**: Planned (green-field; design-ahead in parts)
**Priority**: P2
**Estimated**: ~4d (foundation) + per-report-template
**Scope**: Fullstack (+ BigQuery)
**Dependencies**: 9.1 administration-overview, 9.3 user-group-administration (identity/audit), cloud-infrastructure ✅ (OTel → Cloud Trace/Logging/BigQuery), 6.x observability instrumentation
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

## Problem Statement

The platform **captures a rich stream of interaction data natively** — that was
a deliberate axiom (#8 OBSERVABLE BY DEFAULT): every skill invocation is traced
(OpenTelemetry → Cloud Trace), full prompt/response content is captured
(`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`, inside the GCP edge),
per-invocation token counts are logged, TTFT/stage timings land in Cloud Logging
→ BigQuery (`event:"ttft"` via `backend/observability/timing.py`), and every chat
session's events are mirrored to Firestore (`chat_sessions`). **But almost none
of this captured value is surfaced back as a product.** There is no analytics
view, no way to browse/search chat-history traces, no usage/cost reporting, and
no path to turn session data into a domain report.

This is a **wide green field**: the data is already there, native and rich. The
AIPLA fork proved the shape of the opportunity with **teacher reports** — a
report generated *per student/cohort from their captured session activity*
(what they asked, how they progressed, where they struggled). The same substrate
generalises: usage analytics, cost dashboards, quality/eval, and per-tenant or
per-domain reports built from the data we already keep.

**Current State:**
- Capture is rich (traces, content, tokens, timings, session mirror) — but
  consumption is engineer-only (Cloud Trace console, BigQuery SQL, log queries).
- No product surface: no analytics UI, no report builder, no "review this
  session" trace viewer, no export.
- `usage_count` on skills is dead (no writer) — even basic usage isn't rolled up.

**Impact:** The captured data — the platform's most differentiated asset — sits
inert. Customers and admins can't see usage, cost, or activity; there's no
teacher-report-style deliverable; and the observability investment pays only
debugging dividends, not product ones.

## Goals

**Primary Goal:** A reporting & analytics layer that turns the natively-captured
interaction data into product surfaces — usage/cost analytics, a searchable
chat-history/trace viewer, and a **report-template engine** (the teacher-report
generalisation) — all inside the GCP trust boundary.

**Success Metrics:**
- Admins/tenants can see usage (by user / tenant / skill), token cost, and activity over time with **zero new instrumentation** (built on existing capture).
- A session's full trace (messages, tool calls, model decisions, tokens) is browsable/searchable in-product for authorized users.
- One report template (e.g. a per-user activity report) ships end-to-end as the reusable pattern; adding a new template is config, not a new pipeline.

**Non-Goals:**
- New capture/instrumentation (we consume what OBSERVABLE-BY-DEFAULT already emits; gaps are noted, not the focus).
- Exporting data outside the GCP edge (any external delivery is a separate, gated decision — confidential-content rule).
- A general BI tool (we ship targeted platform-native views + a report engine, not a Looker replacement).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Reporting surface, off the chat path (async report generation acceptable). |
| 2 | EARNED TRUST | +1 | Trace viewer makes model decisions/sources inspectable after the fact; reports cite the underlying sessions. |
| 3 | SKILLS, NOT FEATURES | 0 | Cross-cutting product surface, not a skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Report *generation* itself routes to the right tier (summarize with a fast model; deep synthesis where it earns it). |
| 5 | GRACEFUL DEGRADATION | +1 | Reports degrade to partial when a data source is thin; never fabricate. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Consumes existing OTel/BigQuery; report API is standard REST. |
| 7 | API FIRST | +1 | Analytics/report endpoints serve UI + CLI + channels alike. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The direct product payoff of the capture axiom — this is what the data was captured *for*. |
| 9 | SECURE BY CONSTRUCTION | +1 | All data stays inside the GCP edge; access-scoped (a tenant sees only its own; a user only theirs); reports never leak cross-tenant. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Aggregation/report generation server-side; the UI renders pre-computed results. |
| | **Net Score** | **+7** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### The substrate (what we already capture — consume, don't re-capture)
- **Traces & content:** OpenTelemetry → Cloud Trace; full prompt/response via GenAI capture (internal). Model/tool decisions per invocation.
- **Metrics/timings:** `event:"ttft"` + stage marks (`backend/observability/timing.py`) → Cloud Logging → BigQuery; per-invocation token counts.
- **Session mirror:** `chat_sessions` (Firestore) + the ADK session store — the canonical message/tool-call history per thread.

### Three surfaces (build order: viewer → analytics → report engine)

**1. Chat-history / trace viewer (foundational).** An access-scoped view to
browse and search past sessions and their full trace (messages, tool calls,
delegations, model, tokens). Reuses the session-history reconstruction already
built (`backend/protocols/sessions_route.py`). Authorization: a user sees their
own; a tenant-admin their tenant's; `aitana-admin` all — via the 9.1 unified
admin identity. This is the "review what happened" primitive everything else
builds on.

**2. Usage & cost analytics.** Aggregations over the BigQuery/token data: active
users, invocations & tokens by user / tenant / skill / model, cost, TTFT trends.
A materialized rollup (scheduled query or a durable Firestore cache tier — the
`map_ppa_obligations` durable-cache pattern) so dashboards don't hit raw traces
live. Wire the dead `usage_count` into this rollup or retire it.

**3. Report-template engine (the teacher-report generalisation).** A report =
(a data query over the captured substrate for a subject: a user, a cohort, a
tenant, a date range) + (a generation step that turns that data into a narrative
+ figures) + (an access-scoped delivery: in-product, PDF, or a channel). The
teacher report is one template (per-student activity/progress); a per-tenant
usage report, a per-skill quality report, a per-user "what you worked on" report
are others. **Adding a template is declaring its query + prompt + layout**, not
building a new pipeline. Generation routes by tier (axiom #4) and stays inside
the edge.

### Backend
- **New:** `backend/analytics/` — query layer over BigQuery + the session store; a report-template registry; `/api/analytics/*` (usage rollups) and `/api/reports/*` (list/generate/fetch templates), all access-scoped via `AccessContext`.
- Scheduled rollup (Cloud Scheduler → a rollup job or BigQuery scheduled query) feeding a durable summary store.

### Frontend
- A gated `/analytics` area (or a tab in the 9.1 `/admin` surface): the trace viewer, the usage dashboards, and the report gallery/generator. Thin — renders API results.

### CLI
- `aiplatform analytics usage [--tenant|--user|--skill]` and `aiplatform report run <template> --subject <id>` for headless generation.

## Implementation Plan

### Phase 1: Trace viewer (~1.5d)
- [ ] Access-scoped session list + full-trace read API (reuse sessions_route reconstruction); search by user/tenant/skill/date.
- [ ] `/analytics` (or admin-tab) trace viewer.

### Phase 2: Usage & cost analytics (~1.5d)
- [ ] BigQuery rollup (invocations/tokens/cost by dimension) + durable summary cache; `/api/analytics/usage`.
- [ ] Dashboard tiles; wire or retire `usage_count`.

### Phase 3: Report-template engine + first template (~1.5d + per-template)
- [ ] Report registry (query + prompt + layout); `/api/reports/*`; tier-routed generation inside the edge.
- [ ] Ship one template end-to-end (per-user activity report) as the reusable exemplar. Port the AIPLA teacher-report as a second template where a teaching tenant exists.

## Migration & Rollout

- **No new capture** — additive read/rollup layer; feature-flagged `/analytics` (like Skill Studio's env flag).
- **BigQuery**: rollup tables/scheduled queries are additive; raw capture untouched.
- **Rollback**: disable the flag; the rollup + endpoints are inert if unused.

## Security Considerations

- **Access-scoped by construction:** every analytics/report query is filtered by the caller's `AccessContext` — a tenant sees only its own sessions/usage, a user only theirs; `aitana-admin` all. No cross-tenant aggregation leaks.
- **Stays inside the GCP edge** — captured content is confidential (CLAUDE.md); reports render in-product / to internal sinks. Any external delivery (email a PDF, share a link) is a **separate gated decision** with the confidential-content rule applied (cf. 7.10's external-export gate).
- **Audit**: report generation + trace views are themselves audited (who viewed whose data), per 9.1.

## Success Criteria

- [ ] An authorized user can browse + search past sessions and open a full trace in-product.
- [ ] Usage/cost analytics by user/tenant/skill/model render from existing capture with no new instrumentation.
- [ ] One report template generates end-to-end, access-scoped, inside the edge; adding a second is config only.

## Open Questions

- OQ1: BigQuery live-query vs materialized rollup for dashboards — cost/latency trade-off (lean: rollup + durable cache).
- OQ2: Report delivery beyond in-product (PDF via the pptx/docx skills? channel push?) — and the external-delivery gate if it leaves the edge.
- OQ3: Retention/PII policy for the trace viewer — how far back, and per-tenant retention/region controls (ties to 9.4 tenant policy).
- OQ4: Is the report engine a set of platform **job skills** (8.3) generating reports (dogfooding the delegation/jobs model) rather than a bespoke subsystem? (Lean: yes — a report is a job.)

## Related Documents

- [administration-overview.md](administration-overview.md) (identity/audit), [user-group-administration.md](user-group-administration.md), [domain-tenant-administration.md](domain-tenant-administration.md)
- [jobs-and-subagents.md](../v6.8.0/jobs-and-subagents.md) — reports-as-jobs (OQ4)
- product-axioms #8 OBSERVABLE BY DEFAULT (the capture this consumes), #9 SECURE BY CONSTRUCTION (the edge boundary)
