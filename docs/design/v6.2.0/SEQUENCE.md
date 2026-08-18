# v6.2.0 Build Sequence

**Gate:** v6.1.0 substantially complete. The fork-surfaced extensions (2.6–2.8 below) are now **unblocked** — v6.1.0 sprint 1.6 channels framework + 4 adapters shipped 2026-05-16 (the only soft dependency 2.6 and 2.7 had).

**Status as of 2026-05-19:** Fourteen docs planned (one exploratory). 2.9 + 2.10 shipped 2026-05-18. **2.11 + 2.12 + 2.13 + 2.14 all shipped 2026-05-19 — the four AIPLA template-extensions complete in a single day** (2.11 8 days ahead of the AIPLA 27 May deadline; 2.12 ~4 weeks ahead of the AIPLA mid-June pre-pilot; 2.13 ~6 weeks ahead of AIPLA's early-July need-by; 2.14 ~6-10 weeks ahead of the week-9-13 need-by). All four added 2026-05-19 from one morning of feature-request triage — **AIPLA fork feature requests** surfacing template-level gaps (2.11 anonymous-group-id-auth ✅, 2.12 budget-enforcement ✅, 2.13 artefact-render-hook ✅, 2.14 tenant-id-span-attribute ✅). Three docs added 2026-05-16 — **template extensions surfaced by an internal-tools fork and a tutoring fork fork (that fork's own repo)** (sprints 2.6, 2.7, 2.8). Two added 2026-05-18 — **2.9 multi-surface-rendering** (surfaced by AIPLA fork's ADR-015; shipped same day) and **2.10 a2ui-surface-context** (closes the workspace → agent direction; sibling of the MCP Apps version shipped in 1.25).

**External dependency note:** `document-edit-loop.md` requires ailang_parse ≥ 0.10.0. File the feature request before scheduling that sprint.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 2.1 | [document-edit-loop.md](document-edit-loop.md) | P1 | 4d | chat-message-rendering (v6.1.0), file-browser (v6.1.0), **ailang_parse 0.10.0** | Do not start until `apply_edit_delta` is available. Workshop W8. |
| 2.2 | [mcp-toolbox-databases.md](mcp-toolbox-databases.md) | P2 | 3d | agent-factory (✅), tools-porting (v6.0.0) | Unblocked once tools-porting lands. Firestore + BigQuery access via MCP. |
| 2.3 | [v5-data-migration.md](v5-data-migration.md) | P2 | TBD | auth (✅), skills-data-model (✅) | Depends on v5 usage audit. Not on critical path for July workshop. |
| 2.4 | [agent-cli.md](agent-cli.md) | P2 | TBD | local-dev-cli (v6.1.0) | AILANG Cloud / Cloud Run Job pattern. Not on July critical path. |
| 2.5 | [webmcp-interop.md](webmcp-interop.md) | P2 | TBD | Chrome WebMCP GA OR W3C draft with 2+ browser impls | **Exploratory — tracking only.** Net axiom score +1 (below threshold); do not start until protocol stabilizes and security design is written. Quarterly reassess. |
| 2.6 | [event-driven-skills.md](event-driven-skills.md) | **P1** | 8h | skills-data-model (✅), agent-factory (✅), **channels framework (1.6 ✅)** | **New doc 2026-05-16 — surfaced by both forks. Unblocked.** `SkillTrigger` abstraction (`request_response` / `scheduled` / `pubsub`). Lifts Pub/Sub + Scheduler + worker-job patterns from `sunholo/ailang-multivac/terraform/`. Output routes via `ChannelRegistry.get(name).send()` — the channels framework (shipped 2026-05-16) provides this contract. the internal-tools fork's `contract-watch` and a tutoring fork's `stuck-detection` are the first consumers. |
| 2.7 | [audit-log-and-analytics.md](audit-log-and-analytics.md) | **P1** | 8h | agent-factory (✅), auth (✅), **channels framework (1.6 ✅)** | **New doc 2026-05-16 — surfaced by an internal-tools fork. Unblocked.** Standard Firestore audit log + ADK callback wiring + admin React route. Reads `channel` event metadata from `BaseChannel.handle_webhook` + `inbound.metadata` now forwarded into outbound events. Optional BigQuery export pipe. 90d default retention via TTL. Cost ±10% accuracy via per-provider price table. Every future fork wants this. |
| 2.8 | [google-workspace-mcp-integration.md](google-workspace-mcp-integration.md) | P2 | 2h | agent-factory (✅) — template already supports `McpToolset` | **New doc 2026-05-16, mostly documentation.** Auth-pattern decision tree (OAuth-per-user vs SA+DWD vs OAuth-per-write), folder-scoping pattern, quota awareness, reference `drive-search` skill. the internal-tools fork will be the proof case. Fallback to 4h bespoke `drive-contracts` server build if catalogue doesn't fit. |
| 2.9 | [multi-surface-rendering.md](implemented/multi-surface-rendering.md) | **P1** | 3d | a2ui-tool-delivery (v6.1.0 1.0 ✅), chat-message-rendering (1.1 ✅), document-ui layout (1.10 ✅) | **Shipped 2026-05-18.** Adopts A2UI's first-class `surfaceId` semantic. Adds optional `surface_id` + `update_mode` to A2UI tool-call schema; adds frontend `SurfaceRegistry` + `A2UISurfaceMount` + registry-driven dispatch. Backwards compatible by design (skills without `surface_id` keep rendering inline-in-chat). Bonus follow-up 2026-05-18: rewrote renderer onto `@a2ui/react/v0_9` native API (`MessageProcessor` + `<A2uiSurface>`) when the v0.8 `A2UIViewer` wrapper drifted from the SDK validator — see talk-doc verification log. |
| 2.10 | [a2ui-surface-context.md](implemented/a2ui-surface-context.md) | **P1** | 0.5–0.75d | multi-surface-rendering (2.9 ✅), mcp-app-update-model-context (v6.1.0 1.25 ✅) | **Shipped 2026-05-18.** Sibling of MCP Apps' `ui/update-model-context`. Closes the workspace → agent direction: surface `dataModel` flows back via AG-UI `forwardedProps.a2ui_surface_state`; user actions via `A2uiClientAction` → POST `/api/sessions/{id}/surface-action`. InstructionProvider injects under `a2ui_surface_context.{surfaceId}` namespace, mirror of `mcp_app_context.{server}.{tool}`. Workshop W6 second-turn "what's the current revenue?" answers from context without re-invoking the tool. Per-skill opt-in via `tool_configs.a2ui.allow_surface_context_writes` (default false). Four-turn live smoke verified: 0 tool calls on the context-read turns. Closes the audit row that was flagged in the talk-doc Discipline section earlier the same day. |
| 2.11 | [anonymous-group-id-auth.md](implemented/anonymous-group-id-auth.md) | **P1** | 2d | None (sits alongside existing auth modes) | **Shipped 2026-05-19** (8 days ahead of the AIPLA 27 May deadline). Fourth auth mode alongside Firebase / Identity Platform / LOCAL_MODE stub: short-code session join, no persistent accounts, no PII, signed HS256 JWT, server-side rate-limit + per-group session cap + revocation. Synthesizes a `User` with `uid="anon-<group>-<random>"` + `group_id` + `auth_mode="anonymous_group_id"`. M1 backend module (30 unit tests, all 7 gates explicit) + M2 routes + dispatcher (21 API tests) + M3 frontend provider + `/group` page (20 tests) + M4 fork-adoption howto + dev-local secret wiring. AIPLA fork's ADR-001 unblocked. Generic for classroom / event / kiosk / customer-demo deployments. Net axiom +4 (SECURE_BY_CONSTRUCTION -2, mitigations enumerated in design). |
| 2.12 | [budget-enforcement.md](implemented/budget-enforcement.md) | **P1** | 1.5d | observability/llm_metrics.py (✅) | **Shipped 2026-05-19** (~4 weeks ahead of the AIPLA mid-June pre-pilot). `BudgetEnforcer` runtime-checkable Protocol + ADK before/after_model callbacks + `InMemoryBudgetEnforcer` reference impl + per-skill `cost_multiplier` config. Soft warn at 80% (state-key plumbing in place; frontend banner deferred as follow-up); hard block at 100% (typed AG-UI `RUN_ERROR{code:"BUDGET_EXCEEDED", retry_after_seconds}` → `BudgetBanner` with countdown). Identity opaque to platform — AIPLA's cohort/class schema is a fork-side impl. M1 8-gate matrix at function layer (24 unit tests) + M2 ADK callback integration + agent.py composition refactor introducing `_composed_before_model`/`_composed_after_model` (23 integration tests) + M3 frontend `BudgetBanner` + `useSkillAgent` classifier (14 frontend tests + 1 SSE-level backend test) + M4 fork-adoption howto. Pairs cleanly with 2.11 (group_id identity) and 2.14 (tenant attribution on spans). Net axiom +7. |
| 2.13 | [artefact-render-hook.md](implemented/artefact-render-hook.md) | P2 | 0.5d | mcp-app-integrations (v6.1.0 1.7 ✅), sandbox proxy (v6.1.0 1.7 M3 ✅) | **Shipped 2026-05-19** (~6 weeks ahead of AIPLA's early-July need-by). `ArtefactReviewer` runtime-checkable Protocol with TS + Python mirrors — plug points in both `MCPAppToolCallRouter` (frontend) and `mcp_proxy._forward` (backend, optional). `PermissiveArtefactReviewer` shipped default — existing demos (Cesium map) unaffected. Forks register stricter reviewers in either layer (AIPLA's static-analysis ruleset stays fork-side). M1 TS Protocol + permissive default (10 tests) + M2 router consult + ArtefactRefused / ArtefactWarningStripe + 4-path tests (17 tests) + M3 Python Protocol + mcp_proxy interception with scope guards (19 tests) + M4 fork-adoption howto. Defence-in-depth above the existing sandbox + CSP layer — reviewer crash + slow reviewer + malformed body all fail open. Net axiom +5. |
| 2.14 | [tenant-id-span-attribute.md](implemented/tenant-id-span-attribute.md) | P2 | 0.5d | observability/telemetry.py (✅) | **Shipped 2026-05-19** (~6-10 weeks ahead of AIPLA's week-9-13 need-by). Contextvar `_tenant_context` + `TenantAttributeSpanProcessor` (standard OTel SpanProcessor) + single-insertion wire-up in `auth.get_current_user` dispatcher covers all 13 endpoints. Four non-PII platform defaults: `tenant.uid`, `tenant.auth_mode`, `tenant.group_id` (when present), `tenant.uid_hash` (SHA256 of email, when present). Raw email + display_name NEVER land on a span — `set_tenant_context` reads User fields explicitly (no reflection), 6 PII tests across M1+M3 lock the rule. M1 contextvar module + 15 unit tests (incl. concurrent-task isolation via asyncio.gather + barriers) + M2 dispatcher refactor + integration tests for all 3 auth paths + M3 PII hardening + golden SHA256 verification + M4 fork-adoption howto. **Sequence-close: completes the four AIPLA template-extensions (2.11 ✅ + 2.12 ✅ + 2.13 ✅ + 2.14 ✅) — the same tenant identity threads through all four mechanisms. Cloud Trace query `tenant.group_id = "PHYS-7K2N"` filters every LLM call from that cohort.** Net axiom +6 (OBSERVABLE_BY_DEFAULT +2). |
| 2.15 | [workshop-helper-agent.md](workshop-helper-agent.md) | **P1** | 2d | 2.9 ✅ + 2.10 ✅ + 2.11 ✅ + 2.12 ✅ + 2.13 ✅ + 2.14 ✅ + Path B (separate ticket) | **New doc 2026-05-20 — the meta-demo that turns four shipped AIPLA template-extensions into a live workshop narrative.** Workshop helper agent extends Path B's RAG-over-docs skill with: `submit_for_showandtell` + `list_showandtell_submissions` tools, per-cohort showcase A2UI surface (multi-surface 2.9), live updates via surface-context loop (2.10), anonymous-group join flow (2.11), per-cohort budget reference enforcer (2.12 pattern), Cloud Trace finale filtered by `tenant.group_id` (2.14). New `aiplatform workshop new <NAME>` CLI provisions a workshop cohort with one command (join code + budget + helper-as-default). Public `/cohort/<code>/showcase` page survives the workshop 30d. Net axiom +10. **WebSummerCamp Croatia 2026 is the first session this targets.** |

**2.2 can run in parallel with 2.1.** 2.3, 2.4, and 2.5 are independent of each other and of 2.1/2.2. **2.5 is parked**, not on any critical path.

**2.6 + 2.7 + 2.8 are template extensions surfaced by forks.** Channels framework (1.6) shipped 2026-05-16 so the soft dependencies are now satisfied. 2.6 uses `ChannelRegistry.get(name).send()` for output routing. 2.7 reads `channel` event metadata written by `BaseChannel.handle_webhook` and benefits from the `inbound.metadata` → `OutboundMessage.metadata` forward (commit 6c55b43). 2.8 is pure documentation + one reference skill, no code dependencies.

**2.9 is also a fork-surfaced template extension.** AIPLA's ADR-015 needs four named A2UI surfaces (chat / workspace / sidebar / modal); current renderer mounts inline-in-chat only. Backwards-compat means existing skills keep working; new skills opt in via `surface_id`. Strengthens W6 workshop story and benefits every multi-surface fork (a tutoring fork's teacher dashboard could adopt for the live group-status view).

---

## What ships in v6.2.0

- **Document Edit Loop** — editable A2UI in document panel, structured `EditDelta` protocol, chat-driven edits via `apply_structured_edit_tool`, export DOCX/PPTX/XLSX with style preservation, `MergeBar` for AI suggestions
- MCP Toolbox for Databases — Firestore + BigQuery access as MCP tools
- v5 data migration — user/skill data from v5 Firestore to v6 schema
- Agent CLI — AILANG-coordinated agent driving `aitana` via Cloud Run Job
- **Event-driven skills** — `SkillTrigger` abstraction (`scheduled` / `pubsub`), Pub/Sub + Cloud Scheduler + worker-job pattern, output routes to any registered channel. Drives the internal-tools fork's `contract-watch` and a tutoring fork's `stuck-detection`
- **Audit log + analytics view** — standardised Firestore schema, ADK callback wiring, admin React route with filter + count-by-day, optional BigQuery export pipe. 90d default retention, cost-per-firing within ±10%
- **Google Workspace MCP integration guide** — auth decision tree (OAuth vs SA+DWD), folder-scoping pattern, quota awareness, reference `drive-search` skill
- **Multi-surface A2UI rendering** — `surface_id` schema addition + frontend `SurfaceRegistry`; agent targets named surfaces (chat / workspace / sidebar / modal) with per-surface persistence + patch semantics. Strengthens W6 workshop demo
- _(Exploratory)_ WebMCP interop — tracked, not scheduled. Adopt when Chrome's WebMCP API stabilizes beyond EPP or W3C draft has multiple browser implementations.

## Key insight for workshop (W8)

`document-edit-loop.md` is the capstone of the workshop protocol stack. It demonstrates **shared state** — the Block ADT lives in Python session state and is projected to A2UI JSON on the React side. Edits flow back as typed `EditDelta` structs, processed deterministically by `apply_edit_delta`. Zero LLM tokens for direct edits. The same delta type is used whether the editor is the human user (direct edit) or the AI agent (tool call). See [document-edit-loop.md](document-edit-loop.md) §Workshop Integration for the W8 comment specifications.

## Dependency Graph

```
v6.1.0 complete
    │
    ├──► document-edit-loop (2.1) ──► ailang_parse 0.10.0 (external)
    │
    ├──► mcp-toolbox-databases (2.2) ──► tools-porting (v6.0.0 1A.3)
    │
    ├──► v5-data-migration (2.3) — independent
    │
    ├──► agent-cli (2.4) ──► local-dev-cli (v6.1.0)
    │
    └──► webmcp-interop (2.5, exploratory) ──► Chrome WebMCP GA / W3C 2+ impls (external, no ETA)

Template extensions from forks (can run alongside v6.1.0 sprint 1.6 channels):

    ┌──► event-driven-skills (2.6) ──► soft-dep on channels framework (1.6 Phase 0)
    │          │
    │          └──► consumers: forks/internal-tools-fork, forks/tutoring-fork
    │
    ├──► audit-log-and-analytics (2.7) ──► reads channel event metadata from 1.6 Phase 0
    │          │
    │          └──► consumers: both forks; admin route reusable across all forks
    │
    ├──► google-workspace-mcp-integration (2.8) — pure docs + reference skill, no code deps
    │          │
    │          └──► consumer: forks/internal-tools-fork (Drive Q&A skill)
    │
    └──► multi-surface-rendering (2.9) ──► a2ui-tool-delivery (1.0 ✅), chat-rendering (1.1 ✅), document-ui layout (1.10 ✅)
                │
                └──► consumers: AIPLA fork (ADR-015), a tutoring fork (teacher dashboard, optional)
```

## Next: v6.3.0

Not yet planned. Candidates: real-time collaborative editing, voice (Gemini Live), skill marketplace.
