# v6.0.0 Build Sequence

**Status as of 2026-04-24:** Phase 0 through 1A fully complete (tools-porting 1A.3 landed 2026-04-23). Phase 1B partial — 1B.2 document-ui UI was never built and is now v6.1.0 item 1.10. Phase 3 (template fork) deferred to mid-to-late May 2026 by design.

---

## Phase 0 — Contracts ✅ Complete

| Order | Doc | What it locks | Est |
|-------|-----|---------------|-----|
| 0.1 | [cloud-infrastructure.md](implemented/cloud-infrastructure.md) | GCP resources, Firestore collections, SA accounts | 0.5d |
| 0.2 | [skills-data-model.md](implemented/skills-data-model.md) | Pydantic models, Firestore schema, REST shape | 1d |
| 0.3 | [document-ui.md](../v6.1.0/document-ui.md) (schema only) | Document model, parse/render contract | 0.5d |
| 0.4 | [streaming-and-protocols.md](implemented/streaming-and-protocols.md) (AG-UI spike) | Event taxonomy, auth/session gotchas | 0.5d |

## Phase 0b — CI/CD + Cloud Run wiring ✅ Complete

| Order | Doc | What it locks | Est |
|-------|-----|---------------|-----|
| 0b.1 | [ci-wire-sprint.md](implemented/ci-wire-sprint.md) | `platform-backend`/`platform-frontend` Cloud Run services + GitHub Actions CI + AG-UI test coverage | 1d |
| 0b.2 | [infra-terraform-sprint.md](implemented/infra-terraform-sprint.md) | Terraform module, state management, Cloud Build triggers | 1d |

## Phase 1A — Backend ✅ Complete

| Order | Doc | Depends on | Est |
|-------|-----|------------|-----|
| 1A.1 | [auth-and-permissions.md](implemented/auth-and-permissions.md) | skills-data-model | 3.5d |
| 1A.1b | [resource-access-control.md](implemented/resource-access-control.md) | 1A.1 | 1.5d |
| 1A.2 | [agent-factory.md](implemented/agent-factory.md) | skills-data-model | 4d |
| 1A.3 | [tools-porting-guide.md](implemented/tools-porting-guide.md) ✅ | agent-factory, resource-access-control | 5d |
| 1A.4 | [session-and-memory.md](implemented/session-and-memory.md) | agent-factory, skills-data-model | 4d |
| 1A.5 | [streaming-and-protocols.md](implemented/streaming-and-protocols.md) (impl) | agent-factory, spike output | 3d |
| 1A.6 | [chat-history.md](implemented/chat-history.md) (backend + idempotency) | resource-access-control, session-and-memory, streaming | 2d |

**Note:** All 1A items complete. 1A.1 and 1A.2 ran in parallel; 1A.3/1A.4/1A.5 started as deps completed.

## Phase 1B — Frontend ⚠️ Partial (1B.1 + 1B.3 done; 1B.2 deferred)

| Order | Doc | Depends on | Est |
|-------|-----|------------|-----|
| 1B.1 | [frontend-architecture.md](implemented/frontend-architecture.md) ✅ | Phase 0 contracts | 5d |
| 1B.2 | [document-ui.md](../v6.1.0/document-ui.md) (UI impl) ❌ deferred → v6.1.0 item 1.10 | frontend-architecture, streaming | 5d |
| 1B.3 | [chat-history.md](implemented/chat-history.md) (frontend + CLI) ✅ | document-ui (UI), 1A.6 backend | 1.5d |

## Phase 2 — Integration ✅ Complete

Backend + frontend wired. Auth flows end-to-end, AG-UI streaming connected, CI gate enforced on every PR push to `dev`.

## Phase 3 — Template fork (target: mid-to-late May 2026)

**Gate:** 1A.5 + 1A.6 + 1B.1 complete — all done. Ready to fork when timing is right.

| Order | Item | Est |
|-------|------|-----|
| 3.1 | Pre-fork config-ification — env vars for admin domain/group, `branding.ts`, `.env.example` | 0.5d |
| 3.2 | License + repo name decision (Apache 2.0 vs MIT; `platform-template` vs `aitana-platform`) | 0.25d |
| 3.3 | Sanitization script (`scripts/sanitize-for-template.sh`) — removes Aitana-only content | 0.5d |
| 3.4 | Tag `template-fork-base-v6.0.0` on private repo | trivial |
| 3.5 | Create public `platform-template` repo, force-push sanitized commit 1 | 0.25d |
| 3.6 | Lift `gotcha_*` memory entries + ops patterns into public `docs/gotchas/` | 1d |
| 3.7 | Dry-run: fresh laptop clones public, follows README, gets a live AG-UI chat in <10 min | 0.5d |
| 3.8 | Post-fork hygiene: pre-push hook, weekly Friday merge, drift-alert CI | 0.5d |

**Does NOT fork:** terraform — stays private in perpetuity; patterns travel as prose in `docs/gotchas/`.

See [template-split-strategy.md](template-split-strategy.md) for full design.

---

## Outstanding v6.0.0 Work

| Item | Doc | Est | Status |
|------|-----|-----|--------|
| Tools porting (1A.3) | [tools-porting-guide.md](implemented/tools-porting-guide.md) | 5d | ✅ Implemented (2026-04-23) |
| Template fork | [template-split-strategy.md](template-split-strategy.md) | ~3d | Deferred to May 2026 |

Everything else is in [implemented/](implemented/).

---

## What ships in v6.0.0

- Skills CRUD + Firestore persistence + access control (5-type model)
- Agent factory (Gemini/Claude/OpenAI, skill-scoped)
- AG-UI streaming (ag-ui-adk + custom protocol boundary layer)
- Firebase auth + role-based permissions
- Sessions + memory (ADK native, VertexAI in prod)
- Chat history (Firestore sessions, read-only resume, team sharing)
- Document workspace (ailang-parse, render, chat)
- v5 tools ported as ADK FunctionTools (ai_search, google_search, url_processing, structured_extraction, code_execution, MCP)
- Frontend: marketplace, chat, skill builder, history panel
- MCP server + A2A discovery + models route
- CI/CD: GitHub Actions PR gate, Cloud Build deploy, smoke probes

## What doesn't ship in v6.0.0 (see v6.1.0, v6.2.0)

- Document workspace UI (DocumentViewer, split-pane Workspace) — [v6.1.0 1.10](../v6.1.0/document-ui.md)
- Channels (Telegram, email, WhatsApp) — [v6.1.0](../v6.1.0/channels.md)
- `aitana` local dev CLI — [v6.1.0](../v6.1.0/local-dev-cli.md)
- Skill-friendly URLs (`/chat/@owner/slug`) — [v6.1.0](../v6.1.0/skill-friendly-urls.md)
- MCP App integrations — [v6.1.0](../v6.1.0/mcp-app-integrations.md)
- MCP Toolbox for Databases — [v6.2.0](../v6.2.0/mcp-toolbox-databases.md)
- v5 data migration — [v6.2.0](../v6.2.0/v5-data-migration.md)
- Agent CLI — [v6.2.0](../v6.2.0/agent-cli.md)

---

## Dependency Graph

```
cloud-infrastructure ──┐
                       ├──► agent-factory ──► tools-porting-guide ✅
skills-data-model ─────┤                 ├──► session-and-memory ──┐
                       ├──► auth-and-permissions                    │
                       │         └──► resource-access-control ──────┤
                       ├──► streaming (spike) ──► streaming (impl) ─┤
                       │                                            ▼
                       └──► frontend-architecture ──► document-ui ──► chat-history ✅
                                                      (schema ✅; UI → v6.1.0 1.10)
```

## Timeline anchors

- **v6.0.0 bring-up phases complete:** 2026-04-23 (all including tools-porting)
- **Template fork:** mid-to-late May 2026
- **Workshop:** July 2026 — clones are of the public `platform-template`

## Next: v6.1.0

See [v6.1.0/SEQUENCE.md](../v6.1.0/SEQUENCE.md) for ordering of channels, CLI, MCP apps, and skill-friendly-urls sprints.
