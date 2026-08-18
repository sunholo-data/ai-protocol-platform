# Event-Driven Skills (Pub/Sub + Scheduler Pattern)

**Status**: Planned — surfaced by an internal-tools fork (contract-watch) and re-confirmed by a tutoring fork (that fork's own repo) (stuck-detection)
**Priority**: P1 — both forks-in-flight need it; generic template-level abstraction
**Scope**: Backend skill abstraction + Terraform module + Cloud Run worker pattern
**Dependencies**: [skills-data-model](../v6.0.0/implemented/skills-data-model.md), [agent-factory](../v6.0.0/implemented/agent-factory.md)
**Created**: 2026-05-16

## Problem Statement

Template skills are currently **request/response only** — a user message arrives, an agent runs, a reply streams back. Both early forks need something different:

- **the internal-tools fork** wants `contract-watch` to scan Drive every morning and ping Discord when a renewal is approaching
- **a tutoring fork** wants `stuck-detection` to run periodically over active sessions and update the teacher dashboard

This is the same shape: **a skill fires from a non-user trigger, produces a result, and routes the result to a configured destination**. The patterns to implement this already exist in `sunholo/ailang-multivac/terraform/` (Pub/Sub topics, Cloud Scheduler, eventarc, Cloud Run jobs) but they're customer-specific and not abstracted into the template. The work is to lift them into a reusable module + a new skill trigger type.

Without this, every fork reinvents the wiring — and inevitably gets parts wrong (DLQ handling, idempotency, scheduler quotas).

## Goals

**Primary:** Add a `SkillTrigger` abstraction so a skill author writes a normal prompt + tools + a YAML config specifying when it fires, and the template handles the rest.

**Success Metrics:**
- A skill author can add a daily-cron skill with zero infrastructure code (just a YAML block)
- Pub/Sub-triggered skills get exactly-once semantics within the deduplication window
- A failed firing lands in a DLQ with retry-able context; doesn't silently drop
- Audit log captures every firing including trigger source
- Local dev mode (`LOCAL_MODE=1`) can fire a triggered skill manually for testing

**Non-Goals:**
- Real-time event ingestion from external SaaS (Slack events, GitHub webhooks) — separate channel-side concern
- Complex workflow orchestration (skill A triggers skill B based on skill A's output) — defer to a separate workflow doc if/when a fork needs it
- Cross-deployment event fanout

## Design

### `SkillTrigger` type

Extension on `SkillConfig`:

```python
class SkillTrigger(BaseModel):
    type: Literal["request_response", "scheduled", "pubsub"]

    # for scheduled
    cron: str | None = None                # e.g., "0 8 * * *" — daily 08:00 UTC
    timezone: str = "Europe/Copenhagen"

    # for pubsub
    topic: str | None = None               # template-namespaced: "the internal-tools fork.contract-events"

    # output routing
    output_channel: str | None = None      # "discord:#the internal-tools forks-and-sheep" or "email:team@the internal-tools fork.org"

    # idempotency
    dedupe_window_minutes: int = 60        # for Pub/Sub message replay
```

A skill with `type="request_response"` works as today. The other two activate the Pub/Sub/Scheduler path.

### Infrastructure flow

```
Cloud Scheduler ──► Pub/Sub topic ──► Cloud Run worker job ──► ADK Runner ──► output channel
                                              │
                                              └──► DLQ on failure
```

For `type="scheduled"`, the worker job container reads the skill ID from the Pub/Sub message, instantiates the skill's agent via the existing agent factory, runs it with a system-generated trigger payload (`{trigger: "scheduled", ts: ..., skill_id: ...}`), and routes the result.

For `type="pubsub"`, an external publisher (another service, a webhook handler, an eventarc trigger from GCS or Firestore) puts a message on the topic; the worker picks it up and runs the skill with the message body as input.

### Terraform module

New `infrastructure/modules/event-driven-skill/`:

- Pub/Sub topic + subscription + DLQ topic
- Cloud Run worker job (single shared job, reads skill ID from message)
- Cloud Scheduler entries (one per scheduled skill, generated from skill registry)
- IAM bindings (worker job SA can read skills config, write audit log, publish to channel topics)

Lifted patterns from `sunholo/ailang-multivac/terraform/`:
- `pubsub.tf` + `pubsub_cascade.tf` — topology + DLQ
- `eventarc.tf` — Scheduler → Pub/Sub binding
- `cloud_run_jobs.tf` — worker job shape

Skill authors don't touch Terraform. They drop a `trigger:` block in their skill YAML; a `terraform plan` regen picks it up via the skill registry data source.

### Idempotency

Pub/Sub redelivers. The worker:
1. Reads message ID
2. Checks Firestore `triggered_skill_runs/{message_id}` — if present + recent, ack and exit
3. Otherwise runs the skill, writes the run record, acks
4. Window: `dedupe_window_minutes` (default 60) — after that, replays are treated as fresh

### Output routing

Result lands on a configured `output_channel`:
- `discord:#channel-name` — uses the Discord channel adapter's outbound API
- `email:addr@domain` — uses the email channel adapter
- `webhook:url` — POSTs JSON
- `dashboard` — emits an AG-UI server-sent event to all subscribers of that skill (a tutoring fork's dashboard pattern)

Channel adapters expose a simple `send(channel_id, content)` interface; trigger output uses the same path as user-initiated replies.

### Local dev

In `LOCAL_MODE=1`:
- Scheduled skills can be fired manually via `aiplatform skill fire <slug> --trigger=scheduled`
- Pub/Sub-triggered skills via `aiplatform skill fire <slug> --message='{...}'`
- No real Pub/Sub or Scheduler required for local iteration

## Implementation Plan

~8h total.

| Step | Est | Notes |
|------|-----|-------|
| `SkillTrigger` Pydantic model + Firestore schema update | 1h | Extends `SkillConfig` |
| Cloud Run worker job container — reads message, runs skill, routes output | 2h | New compute shape |
| Terraform module `event-driven-skill` lifted from `ailang-multivac` | 2h | Rename, parameterise |
| Output routing — extend Discord + email adapters with `send()` interface | 1h | Small adapter additions |
| Idempotency via Firestore run records | 1h | Standard pattern |
| Local-mode `aiplatform skill fire` command | 1h | CLI extension |

## Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Cloud Scheduler quota (per-project limit ~500) becomes a ceiling | Low | Document the limit; consolidate skills to fewer schedules at scale |
| DLQ silently fills | Medium | Cloud Monitoring alert on DLQ depth > 0; documented in template ops runbook |
| Idempotency window mis-set for slow skills | Medium | Default 60min; expose as per-skill config; document the trade-off |
| Worker job concurrency creates duplicate audit log entries | Low | Run record write is the dedupe gate; audit-log only writes after run record is claimed |
| Skill author writes a 5-min scheduled task and bankrupts the project | High | Add a `min_interval_seconds` guard; warn on schedules < 5 min |

## Testing Strategy

- Unit: `SkillTrigger` validation, payload routing
- Integration: deploy a `daily-greeting` reference skill on `scheduled`, verify it fires once at the right time and lands in the audit log
- Idempotency: replay the same Pub/Sub message twice within window, confirm only one skill run
- DLQ: simulate skill failure, confirm message lands in DLQ with traceback
- Cost: 7-day soak with 3 scheduled skills, verify Cloud Run + Pub/Sub cost stays under documented bound

## Security Considerations

- Worker job SA scoped to: read skill config (Firestore), write audit log + run records (Firestore), publish to channel topics (Pub/Sub)
- No external network beyond Vertex AI + Pub/Sub publish unless skill explicitly uses an MCP server
- Pub/Sub topic publish requires SA role; document that external publishers (eventarc, GCS triggers, manual webhooks) need to be granted explicitly
- Message bodies may contain PII if external publishers don't sanitize — audit-log retention policy applies

## Open Questions

1. **Skill author UX for output routing.** Do we want a higher-level `notify(channel, content)` skill primitive, or just hard-wire output via `output_channel`? Recommendation: start with `output_channel`, add `notify()` if a skill needs to route conditionally.
2. **Cross-skill triggers.** Should skill A be allowed to publish to a topic that skill B subscribes to? Recommended: yes, but document the pattern as a "workflow" pattern with explicit dedupe responsibility.
3. **Real-time vs polling for external SaaS.** Webhook handlers (Stripe, GitHub, Severa) go on top of this layer; they're a channel-adapter concern, not a trigger-system concern.

## Related Documents

- [Skills data model](../v6.0.0/implemented/skills-data-model.md) — the abstraction this extends
- [Agent factory](../v6.0.0/implemented/agent-factory.md) — runner used inside the worker job
- [Audit log + analytics](audit-log-and-analytics.md) — companion extension
- an internal-tools fork scope (that fork's own repo) — first consumer (contract-watch)
- a tutoring fork scope (that fork's own repo) — second consumer (stuck-detection)
- Source patterns: `sunholo/ailang-multivac/terraform/pubsub.tf`, `eventarc.tf`, `cloud_run_jobs.tf`
