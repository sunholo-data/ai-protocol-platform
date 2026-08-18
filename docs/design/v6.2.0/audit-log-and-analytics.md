# Audit Log + Analytics View

**Status**: Planned — surfaced by an internal-tools fork
**Priority**: P1 — both forks-in-flight will want this; generic enough that every future fork needs it too
**Scope**: Firestore schema + ADK callback + admin React route
**Dependencies**: [agent-factory](../v6.0.0/implemented/agent-factory.md), [auth-and-permissions](../v6.0.0/implemented/auth-and-permissions.md)
**Created**: 2026-05-16

## Problem Statement

Every fork wants to answer "who used the bot today, what skill ran, what tools did it call, how much did it cost." Today this requires reading Cloud Trace + Cloud Logging + Vertex AI billing exports — three places, no joining key, no per-skill view. The template ships OTEL → Cloud Trace by default, which is excellent for engineering debugging but useless for "show me last week's Sheep usage."

Both forks (the internal-tools fork's explicit analytics route, a tutoring fork's teacher dashboard) need an admin-facing audit log with a standard schema. Reinventing it per fork is wasteful and produces inconsistent reports.

## Goals

**Primary:** Standard Firestore-backed audit log + a reusable React admin view, both shipped as part of the template.

**Success Metrics:**
- Every skill firing produces an audit log entry without skill author boilerplate
- An admin can answer "what did Sheep X do this week" in <10s via the admin route
- Per-skill cost-per-firing surfaces accurately within 10% of the actual Vertex AI bill
- Audit log retention configurable per-deployment, with safe defaults (90 days)
- Optional BigQuery export pipe for forks that want deeper analytics

**Non-Goals:**
- Full BI / dashboard tool (Looker, Metabase) — the admin view is operational, not BI
- Real-time alerting on audit events — handled separately by Cloud Monitoring on top of BigQuery export
- Cross-deployment aggregation
- PII redaction (skill content is the skill's responsibility, not the audit log's)

## Design

### Firestore schema

New collection `audit_log/{event_id}` (top-level, not user-nested — easier admin queries):

```python
class AuditEvent(BaseModel):
    event_id: str                          # UUID v7 for sortable IDs
    ts: datetime
    user_id: str                           # Firebase UID or session_id for anonymous
    user_type: Literal["firebase", "anonymous_session", "trigger"]
    skill_slug: str
    skill_version: str
    channel: Literal["web", "discord", "email", "telegram", "cli", "mcp", "scheduled", "pubsub"]
    channel_metadata: dict                 # guild_id, channel_id, thread_id, email_addr, etc.
    event_type: Literal["skill_invoked", "tool_called", "skill_completed", "skill_failed", "guardrail_triggered"]
    tools_called: list[str] | None         # for skill_invoked, tool list
    tool_name: str | None                  # for tool_called event
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: Decimal | None
    duration_ms: int | None
    status: Literal["success", "failure", "partial"]
    error: str | None
    trace_id: str | None                   # Cloud Trace correlation
    parent_event_id: str | None            # tool_called events link to skill_invoked
```

One `skill_invoked` event per turn, plus N `tool_called` events as children, plus one terminal `skill_completed`/`skill_failed`.

### Writing via callbacks

- `before_agent_callback` → write `skill_invoked`
- `before_tool_callback` → write `tool_called` with parent_event_id
- `after_agent_callback` → write `skill_completed` or `skill_failed`
- `before_model_callback` if guardrail trips → write `guardrail_triggered`

Cost calculation lives in `after_agent_callback` using token counts from the model response + a per-provider price table in `backend/observability/pricing.py`. Accurate within ~10% (model price changes lag by 24h; cached tokens not yet itemised).

### Retention

Firestore TTL field on `audit_log/{event_id}` set to `ts + retention_days` (default 90, env-configurable). Documents auto-delete past TTL — no cleanup job needed.

For forks that need longer retention, configure the **BigQuery export** (next section).

### BigQuery export

Optional Pub/Sub-backed export pipe:

1. Audit log writer also publishes the event to `audit-log-export` Pub/Sub topic
2. BigQuery subscription on the topic → `audit_log.events` table (auto-schema)
3. Long-term retention + ad-hoc SQL + Looker connectivity for forks that want it

Off by default; one Terraform variable to enable. the internal-tools fork's stretch "deep analytics" path uses this.

### React admin route

New `/admin/audit` route in the frontend:

- **Auth:** Firebase role check (`role:admin` or `role:owner`) — non-admin Sheep get 403
- **List view:** filter by user, skill, channel, date range, status; default last 7 days
- **Event card:** ts, user (mapped from UID/session_id to human-readable name), skill, channel, status, cost, duration. Click for full event JSON + trace link.
- **Aggregate strip:** count by skill (last 7 days), top users, cost trend (last 30 days as simple sparkline — no chart library)
- **No charts.** No date pickers beyond preset ranges. Operational, not BI.

Component tree small (~200 LOC). Reuses template's Firebase auth context + Firestore client.

### User identity mapping

`user_id` is opaque (Firebase UID or anonymous session ID). The audit view maps it to a human-readable name via a small `user_identities/{user_id}` collection:

```python
class UserIdentity(BaseModel):
    user_id: str
    display_name: str
    email: str | None
    discord_user_id: str | None
    last_seen: datetime
```

Auto-populated on first sign-in for Firebase users. Anonymous sessions show `session:{short_id}`. Forks add custom fields (Sheep handle, etc.) by extending this model.

## Implementation Plan

~8h total.

| Step | Est | Notes |
|------|-----|-------|
| `AuditEvent` Pydantic model + Firestore schema + TTL config | 1h | Standard |
| ADK callbacks wiring (before_agent, before_tool, after_agent, before_model for guardrails) | 2h | Plug into agent factory |
| Pricing table + cost calculator per provider | 1h | Gemini, Claude, OpenAI; doc the staleness |
| Firestore rules (admin-only read) + `make verify-rules` test case | 0.5h | Existing pattern |
| BigQuery export pipe (Terraform + Pub/Sub subscription) — off by default | 1h | Optional opt-in |
| React `/admin/audit` route with list + filter + event card + aggregate strip | 2.5h | Reuses Firebase auth context |

## Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Audit log writes add latency to skill firing | Medium | Writes are fire-and-forget; failure logs to OTEL not blocks user |
| Cost calculator drifts as providers change pricing | High | Documented as ±10% accuracy; quarterly review; encourage BigQuery export + actual-billing reconcile for forks that need precision |
| TTL accidentally set too short, losing investigation data | Medium | Default 90d; document in template ops runbook; can be bumped per-deployment |
| Admin view sprawls into BI demands | High | Hard rule: no charts in the template view. BigQuery export is the BI escape hatch. |
| Per-event Firestore write cost on a chatty fork | Medium | Aggregate batch writes for high-volume forks; document at 10k events/day threshold |
| PII in tool call arguments getting logged | Medium | Audit log stores `tool_name` only by default, not tool arguments. Per-skill opt-in for argument capture. |

## Testing Strategy

- Unit: event serialisation, cost calculator per provider
- Integration: invoke a skill end-to-end, verify all expected events land
- Adversarial: non-admin user tries to read audit log — Firestore rules deny
- Cost: 7-day soak, verify Firestore write cost per skill firing is bounded
- Eval: compare audit-log cost sum against Vertex AI billing export for the same window; assert within 10%

## Security Considerations

- Audit log is admin-read-only. Firestore rules enforce.
- Tool arguments NOT captured by default (PII risk). Per-skill opt-in via `audit_capture_tool_args: true`.
- BigQuery export uses a dedicated SA with append-only IAM
- Cost calculator does not call provider APIs at write time (would explode latency); uses static price table updated quarterly via PR
- User identity mapping is admin-write-only — Sheep can't impersonate

## Open Questions

1. **Cost accuracy expectations.** 10% is fine for "is this skill expensive?" but bad for "bill me precisely." Forks that need exact billing should use BigQuery + actual Vertex AI billing export. Document this.
2. **Per-tenant audit log in multi-tenant forks?** Out of scope for template (multi-tenancy explicit non-goal). Forks that need it sub-collection per tenant.
3. **Streaming dashboard updates?** Admin view polls every 5s for v1. Real-time via AG-UI is doable later if the cost is worth it.
4. **Audit of audits?** A meta-event for "admin viewed the audit log" — yes, write a `audit_view` event when /admin/audit is loaded. Cheap to add, valuable for trust.

## Related Documents

- [Agent factory](../v6.0.0/implemented/agent-factory.md) — callback hooks used to write events
- [Event-driven skills](event-driven-skills.md) — trigger source for `channel="scheduled"` / `"pubsub"`
- [Auth and permissions](../v6.0.0/implemented/auth-and-permissions.md) — admin role check
- an internal-tools fork scope (that fork's own repo) — first consumer
- a tutoring fork scope (that fork's own repo) — uses for teacher dashboard
