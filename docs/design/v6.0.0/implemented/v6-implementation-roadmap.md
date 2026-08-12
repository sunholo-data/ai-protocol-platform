# v6 Implementation Roadmap

**Status**: Planned
**Priority**: P0 (High)
**Estimated**: ~2 weeks (Phase 0: 2 days, Phase 1: 1 week, Phase 2: 2-3 days). Phase 1B shrunk by ~3 days after off-the-shelf scan on 2026-04-10 — slack reallocated to brand polish, spike depth, and integration buffer.
**Scope**: Fullstack (orchestrating doc)
**Dependencies**: All v6.0.0 design docs in [docs/design/v6.0.0/](.)
**Created**: 2026-04-10
**Last Updated**: 2026-04-10

## Problem Statement

We have eleven independent design docs covering the v6 platform — skills, agents, documents, channels, auth, infra, streaming, tools, sessions, migration. Each one is internally complete, but **none of them say what to build first** or how the work parallelises across the backend and frontend tracks.

If we start implementing in arbitrary order we hit two failure modes:
1. **Frontend blocked on backend** — building the React UI against a backend that hasn't decided its event shapes yet, then rewriting both halves when the contract changes.
2. **Integration cliff** — both halves built in isolation against assumed contracts, then a multi-day mess when they meet.

**Current State:**
- 11 sub-design docs exist; no master sequencing doc.
- Mark is the sole implementer in the Phase 1 timeframe — needs a plan that minimises wasted work and context-switching.
- Critical protocol assumption (ADK → AG-UI translation) is unverified — this is the single biggest unknown that could blow up the schedule.

**Impact:**
- Affects: solo dev velocity over the next ~2 weeks of v6 build-out.
- Significance: blocker for starting implementation. Without a roadmap, every morning starts with "what should I work on today?"

## Goals

**Primary Goal:** Define a phased implementation order that lets backend and frontend tracks proceed in parallel against frozen protocol contracts, with a verified de-risk spike before any sustained work begins.

**Success Metrics:**
- Phase 0 contracts frozen and committed before any Phase 1 work starts.
- AG-UI translation spike either confirms native ADK support OR delivers a working `backend/protocols/agui.py` translator before Phase 1 day 1.
- Zero integration-day rework caused by changed contracts (measured by Phase 2 PRs touching only wiring, not types).
- Both tracks usable end-to-end by end of Phase 2 (a user can pick a skill, upload a document, and chat about it with streaming).

**Non-Goals:**
- Doesn't decide *what* the v6 product is — that's settled in the sub-design docs.
- Doesn't replace the per-feature design docs — references them.
- Doesn't include channel ports (Telegram, email, WhatsApp) — those are post-Phase-2 work.
- Doesn't include marketplace, billing, or skill discovery features — out of scope for the bring-up.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Phase 0 freezes the AG-UI contract first — streaming UX is the primary integration target, not an afterthought |
| 2 | EARNED TRUST | 0 | Roadmap is process; per-feature trust work happens in sub-docs |
| 3 | SKILLS, NOT FEATURES | +1 | Skills CRUD is the very first backend deliverable in Phase 1 (Day 3-4), confirming skills are the spine |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Routing logic lives in agent-factory.md, not here |
| 5 | GRACEFUL DEGRADATION | +1 | Phase 0 explicitly defines fallback contracts (plain text when A2UI fails, fixture mode for FE without BE) |
| 6 | PROTOCOL OVER CUSTOM | +2 | The whole roadmap is built around freezing **protocol contracts** (AG-UI, A2UI, REST) before code — this is the axiom in action |
| 7 | API FIRST | +1 | Backend contracts are locked in Phase 0 *before* any channel-specific code; the FE consumes the same API a future CLI/Telegram channel will |
| 8 | OBSERVABLE BY DEFAULT | 0 | OpenTelemetry wiring lives in cloud-infrastructure.md; roadmap doesn't change it |
| 9 | SECURE BY CONSTRUCTION | 0 | Auth boundaries set in auth-and-permissions.md; roadmap doesn't relax them |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Parallel-track strategy is only possible *because* the client is thin — locks in the architectural commitment |
| | **Net Score** | **+7** | Strong alignment — proceed |

**Conflict Justifications:** None — no axioms scored -1.

## Design

### Overview

Three sequenced phases. Phase 0 freezes contracts (no implementation, just type/event/schema definitions on both sides of every boundary). Phase 1 runs backend and frontend tracks in parallel against the frozen contracts. Phase 2 wires them together end-to-end.

The whole strategy hinges on **Axiom 6 (Protocol Over Custom)**: because every cross-boundary surface is a published protocol (AG-UI, A2UI, MCP, REST/JSON), each track can consume a contract instead of waiting on the other side's implementation.

### The Three Phases

```
┌─────────────────────────────────────────────────────────────────┐
│                  PHASE 0 — LOCK CONTRACTS                       │
│                       (~2 days, sequential)                     │
│  Everything below depends on these. No code, just types/schemas │
│  ───────────────────────────────────────────────────────────    │
│  • AG-UI event taxonomy frozen + spike verified                 │
│  • Skills Firestore schema + REST shape frozen                  │
│  • Document API contract + parsed_documents schema frozen       │
│  • Cloud infra (Firestore collections, GCS buckets) provisioned │
└─────────────────────────────────────────────────────────────────┘
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
┌───────────────────────────┐ ┌───────────────────────────┐
│   PHASE 1A — BACKEND      │ │   PHASE 1B — FRONTEND     │
│        (~1 week)          │ │        (~1 week)          │
│  ───────────────────────  │ │  ───────────────────────  │
│  Skills CRUD              │ │  Skills nav bar           │
│  ADK agent factory        │ │  Workspace shell          │
│  ailang-parse FunctionTool│ │  A2UIViewer (fixtures)    │
│  AG-UI stream endpoint    │ │  Document upload UI       │
│  Auth + permissions       │ │  AG-UI hookup (mock SSE)  │
└───────────────────────────┘ └───────────────────────────┘
                │                         │
                └────────────┬────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  PHASE 2 — INTEGRATION                          │
│                       (~2-3 days)                               │
│  ───────────────────────────────────────────────────────────    │
│  • Point FE at real backend                                     │
│  • End-to-end smoke: pick skill → upload doc → chat → stream    │
│  • Iterate edge cases (fallbacks, errors, slow paths)           │
└─────────────────────────────────────────────────────────────────┘
```

### Phase 0 — Lock Contracts (~2 days, sequential)

Phase 0 produces **no runtime code**. It produces type definitions, JSON schemas, OpenAPI specs, Firestore structure docs, and event tables. These are the contracts both tracks consume in Phase 1.

#### 0.1 Cloud infra provisioning (~0.5 day)
**Owner doc:** [cloud-infrastructure.md](implemented/cloud-infrastructure.md)

- Confirm `your-project-id` Firestore collections exist (`skills/`, `parsed_documents/`, `sessions/`, `users/`)
- GCS buckets: `platform-dev-documents/`, `platform-dev-uploads/`
- Secret Manager entries for Gemini/Claude/OpenAI keys
- Verify Cloud Run service accounts have correct IAM bindings
- This is mostly verifying what cloud-infrastructure.md already designed; no new infra design needed.

#### 0.2 Skills data model finalised (~0.25 day)
**Owner doc:** [skills-data-model.md](implemented/skills-data-model.md)

- Pydantic `Skill` model frozen (matching Agent Skills spec + ADK extensions)
- Firestore document shape committed as JSON schema
- TypeScript interface generated from Pydantic (so FE doesn't drift)
- REST shape: `GET/POST/PATCH/DELETE /api/skills`, `GET /api/skills/{skillId}`

#### 0.3 Document API contract (~0.25 day)
**Owner doc:** [document-ui.md](../v6.1.0/document-ui.md)

- Pydantic `ParsedDocument`, `Block`, `A2UIComponent`, `EditedBlock` models frozen
- TypeScript interfaces generated
- REST endpoints frozen:
  - `POST /api/documents/upload` → returns `docId`
  - `GET /api/documents/{docId}` → returns parsed document + a2ui components
  - `PATCH /api/documents/{docId}/blocks/{blockIndex}` → applies edit overlay
  - `POST /api/documents/generate` → produces output doc from blocks

#### 0.4 AG-UI event taxonomy + spike (~0.5 day) — **DE-RISKED 2026-04-10**
**Owner doc:** [streaming-and-protocols.md](streaming-and-protocols.md)

**Original concern:** the single biggest unknown was whether we'd need to write `backend/protocols/agui.py` to translate ADK Event objects into AG-UI events.

**Resolution (verified 2026-04-10):** CopilotKit publishes [`ag-ui-adk` on PyPI](https://pypi.org/project/ag-ui-adk/), an official ADK ↔ AG-UI bridge. v0.6.0 released 2026-04-06, Python 3.10–3.14, MIT, 271 tests.

The integration pattern is:

```python
from fastapi import FastAPI
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.agents import Agent

my_agent = Agent(name="assistant", instruction="...", tools=[...])
adk_agent = ADKAgent(adk_agent=my_agent, app_name="aitana", user_id="...")

app = FastAPI()
add_adk_fastapi_endpoint(
    app, adk_agent, path="/chat",
    extract_headers=["x-user-id", "x-tenant-id"],
)
```

It also supplies `ag-ui-protocol` (v0.1.15) as the Python type/encoder layer, with all 16 AG-UI event types as Pydantic models and an `EventEncoder` for SSE framing. The 16 canonical AG-UI events we'll consume are:

**Lifecycle:** `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR`, `STEP_STARTED`, `STEP_FINISHED`
**Text:** `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `TEXT_MESSAGE_CHUNK`
**Tools:** `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT`, `TOOL_CALL_CHUNK`
**State:** `STATE_SNAPSHOT`, `STATE_DELTA`, `MESSAGES_SNAPSHOT`
**Reasoning:** `REASONING_*` (for Claude/Gemini thinking modes)
**Special:** `RAW`, `CUSTOM`, `META_EVENT`

**Reduced spike scope (~0.5 day instead of 1 day):**
1. `uv add ag-ui-adk ag-ui-protocol` in `backend/`
2. Write `backend/protocols/agui_smoke.py` — minimal ADK Agent → `add_adk_fastapi_endpoint(app, ...)` mounted at `/chat`
3. Curl the endpoint with a simple text prompt, verify `RUN_STARTED → TEXT_MESSAGE_* → RUN_FINISHED` event sequence
4. Hit it from a minimal CopilotKit React client to confirm end-to-end browser compatibility
5. Note any deviations from the documented 16-event taxonomy

**What stays in our codebase:**
- `backend/protocols/agui.py` becomes a thin module that constructs `ADKAgent` instances per skill, applies our auth context (Firebase user ID via `extract_headers`), and registers the endpoint. **Not** a translator — a configurator.

**What we still need to verify in the spike:**
- Session-service injection (does `ADKAgent.from_app()` accept our Vertex AI Agent Engine session service, or does it force in-memory?)
- Auth middleware composition (Firebase ID token verification has to run *before* `ag-ui-adk` consumes the request — verify FastAPI middleware ordering)
- AG-UI events for our `parse_document` and `generate_document` FunctionTools surface as `TOOL_CALL_*` events (should be automatic, but verify)
- Whether `ResumabilityConfig` plays well with our per-document chat sessions

**Why this is still critical path even though de-risked:** the verified gotchas (session service injection, auth ordering) are exactly the kind of thing that derails Phase 1 if discovered late. A 4-hour spike now saves a 2-day rewrite later.

### Phase 1 — Parallel Tracks (~1 week)

Both tracks start the morning Phase 0 ends. They share the contracts from Phase 0 and never call each other directly until Phase 2.

#### Phase 1A — Backend track

**Owner docs:** [agent-factory.md](implemented/agent-factory.md), [tools-porting-guide.md](tools-porting-guide.md), [auth-and-permissions.md](implemented/auth-and-permissions.md), [session-and-memory.md](session-and-memory.md)

| # | Task | Owner doc | Estimate |
|---|------|-----------|----------|
| 1A.1 | Skills CRUD (`backend/skills/`) — Pydantic model + Firestore repo + 4 REST handlers | skills-data-model.md | 0.5 day |
| 1A.2 | Firebase auth middleware on FastAPI routes | auth-and-permissions.md | 0.5 day |
| 1A.3 | ADK agent factory (`backend/adk/agent_factory.py`) — load skill → build LlmAgent | agent-factory.md | 1 day |
| 1A.4 | `parse_document` ADK FunctionTool wrapping ailang-parse | tools-porting-guide.md, document-ui.md | 0.5 day |
| 1A.5 | `generate_document` ADK FunctionTool | document-ui.md | 0.5 day |
| 1A.6 | Document REST endpoints (upload/get/patch/generate) | document-ui.md | 1 day |
| 1A.7 | AG-UI streaming endpoint (uses Phase 0 spike output) | streaming-and-protocols.md | 0.5 day |
| 1A.8 | Session service wiring (Vertex AI Agent Engine in cloud, InMemory in dev) | session-and-memory.md | 0.5 day |
| 1A.9 | Backend integration test: skill → agent → tool → AG-UI events | — | 0.5 day |

**Phase 1A total:** ~5 days. Can absorb a 1-day ADK gotcha and still finish.

#### Phase 1B — Frontend track

**Owner docs:** [frontend-architecture.md](implemented/frontend-architecture.md), [document-ui.md](../v6.1.0/document-ui.md)

**Off-the-shelf adoption (decided 2026-04-10):** scaffold inspection of `npx copilotkit@latest create -f adk` confirmed `<CopilotSidebar>`, `useCoAgent`, `useFrontendTool`, `useRenderToolCall`, and `@copilotkit/a2ui-renderer@1.55.1` ship most of what 1B.4 and 1B.7 used to build from scratch. Phase 1B shrinks from ~6 days to ~3 days of new code; freed days reallocated to brand polish (1B.10), buffer (1B.11), and skills builder hardening. Full ecosystem analysis lives in [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md).

| # | Task | Owner doc | Estimate |
|---|------|-----------|----------|
| 1B.0 | Scaffold cherry-pick: `npx copilotkit@latest create -f adk`, port `route.ts` + `layout.tsx`, add CopilotKit deps | streaming-and-protocols.md | 0.5 day |
| 1B.1 | Convert `document-workspace.html` mockup to React components | document-ui.md | 1 day |
| 1B.2 | Skills nav bar component, fed by `/api/skills` (mocked initially) | document-ui.md | 0.5 day |
| 1B.3 | Workspace shell — split panes, collapsible, multi-doc tabs | document-ui.md | 0.5 day |
| 1B.4 | A2UIViewer thin wrapper around `@copilotkit/a2ui-renderer` with PATCH callback | document-ui.md | 0.25 day (was 1 day) |
| 1B.5 | Document upload UI + progress tracking | document-ui.md | 0.5 day |
| 1B.6 | Static A2UI fixture set in `frontend/src/fixtures/` for parallel work | — | 0.25 day |
| 1B.7 | Chat panel via `<CopilotSidebar>` + `useFrontendTool` + `useCoAgent` (document-aware glue) | streaming-and-protocols.md | 0.5 day (was 1 day) |
| 1B.8 | Skills builder modal (create/edit) | skills-data-model.md | 0.75 day |
| 1B.9 | Frontend smoke test against fixtures | — | 0.5 day |
| 1B.10 | CopilotKit brand theming (override `--copilot-kit-primary-color` etc.) | document-ui.md | 0.5 day |
| 1B.11 | Buffer / spike depth / skills builder polish | — | 0.5 day |

**Phase 1B total:** ~5.75 days (6 days of new code minus the deletions in 1B.4 and 1B.7, plus the new scaffold and theming tasks). Roughly the same elapsed time, but ~3 days of from-scratch code replaced with prebuilt components and reallocated to brand fidelity.

### Phase 2 — Integration (~2-3 days)

Both tracks meet. Frontend stops reading fixtures, points at real backend.

| # | Task | Estimate |
|---|------|----------|
| 2.1 | Wire FE to real `/api/skills`, fix any contract drift | 0.25 day |
| 2.2 | Wire FE to real `/api/documents/*`, real upload flow | 0.5 day |
| 2.3 | Wire FE to real AG-UI stream, debug event mismatches | 1 day |
| 2.4 | End-to-end smoke: pick skill → upload doc → chat → see streamed response → edit doc inline | 0.5 day |
| 2.5 | Edge cases: A2UI render fallback to plain text, model API failure, large doc upload | 0.5 day |

**Phase 2 total:** ~2.5 days.

### Day-by-Day Schedule

| Day | Backend track | Frontend track |
|-----|---------------|----------------|
| **Phase 0** | | |
| 1 | Cloud infra check, contracts (skills + docs) | (waiting on Phase 0) |
| 2 | **AG-UI spike** (critical path) | (waiting on Phase 0) |
| **Phase 1** | | |
| 3 | Skills CRUD (1A.1) + auth (1A.2) | Scaffold cherry-pick (1B.0) + Mockup → React start (1B.1) |
| 4 | Agent factory start (1A.3) | Mockup → React finish (1B.1) + Skills nav bar (1B.2) + fixtures (1B.6) |
| 5 | Agent factory finish + parse_document (1A.3, 1A.4) | Workspace shell (1B.3) + A2UIViewer wrapper (1B.4) + CopilotSidebar chat (1B.7) |
| 6 | generate_document + doc endpoints (1A.5, 1A.6) | Upload UI (1B.5) |
| 7 | AG-UI endpoint (1A.7) + sessions (1A.8) | Skills builder (1B.8) + smoke (1B.9) |
| 8 | Backend integration test (1A.9) | CopilotKit brand theming (1B.10) + buffer (1B.11) |
| **Phase 2** | | |
| 9 | Wire skills + docs APIs (2.1, 2.2) | Wire skills + docs APIs (2.1, 2.2) |
| 10 | Wire AG-UI (2.3) | Wire AG-UI (2.3) |
| 11 | Smoke + edge cases (2.4, 2.5) | Smoke + edge cases (2.4, 2.5) |

**Total: ~11 working days.** Realistic with one ADK gotcha or one AG-UI translation surprise absorbed.

### Architecture

```
            ┌─────────────────── PHASE 0 CONTRACTS ─────────────────┐
            │                                                       │
            │   Skills schema    Doc API schema    AG-UI events     │
            │                                                       │
            └─────────────┬─────────────────────────┬───────────────┘
                          │                         │
              ┌───────────▼──────────┐  ┌──────────▼────────────┐
              │   BACKEND TRACK      │  │   FRONTEND TRACK      │
              │                      │  │                       │
              │   FastAPI            │  │   Next.js 14          │
              │   ADK agent factory  │  │   React 18            │
              │   ailang-parse tool  │  │   @a2ui/react         │
              │   AG-UI emitter      │  │   CopilotKit (AG-UI)  │
              │   Firestore + GCS    │  │   Fixtures (offline)  │
              │                      │  │                       │
              │   localhost:1956     │  │   localhost:3000      │
              └───────────┬──────────┘  └──────────┬────────────┘
                          │                         │
                          └────────── PHASE 2 ──────┘
                                  Integration
                                  end-to-end smoke
```

## Sub-Design Docs Referenced

This roadmap orchestrates the following docs. Each Phase 1 task points to its owner doc for the actual design:

| Doc | Phase 1 tasks | Status |
|-----|---------------|--------|
| [cloud-infrastructure.md](implemented/cloud-infrastructure.md) | 0.1 | Planned |
| [skills-data-model.md](implemented/skills-data-model.md) | 0.2, 1A.1, 1B.8 | Planned |
| [document-ui.md](../v6.1.0/document-ui.md) | 0.3, 1A.4-6, 1B.1-5 | Planned |
| [streaming-and-protocols.md](streaming-and-protocols.md) | 0.4, 1A.7, 1B.7 | Planned |
| [agent-factory.md](implemented/agent-factory.md) | 1A.3 | Planned |
| [tools-porting-guide.md](tools-porting-guide.md) | 1A.4-5 | Planned |
| [auth-and-permissions.md](implemented/auth-and-permissions.md) | 1A.2 | Planned |
| [session-and-memory.md](session-and-memory.md) | 1A.8 | Planned |
| [frontend-architecture.md](implemented/frontend-architecture.md) | 1B.* | Planned |
| [channels.md](channels.md) | (post-Phase 2) | Planned |
| [v5-data-migration.md](v5-data-migration.md) | (post-Phase 2) | Planned |
| [mcp-app-integrations.md](mcp-app-integrations.md) | (post-Phase 2 follow-up — geo as first integration) | Planned |
| [local-dev-cli.md](local-dev-cli.md) | (incremental — bring-up surface lands alongside Phase 1, subsequent commands ship with their respective features) | Planned |
| [agent-cli.md](agent-cli.md) | (decision-only — no code lands until a real use case appears; ~0.5d spike when triggered, picks Option C: reuse AILANG Cloud) | Planned |

## Migration & Rollout

**Database Migrations:**
- Phase 0.1 provisions Firestore collections in `your-project-id` only.
- Test/prod Firestore stays untouched until Phase 2 completes and the dev environment is stable.

**Feature Flags:**
- v6 lives at separate Cloud Run services (`platform-backend`, `platform-frontend`) initially, stood up by the CI-WIRE sprint. v5 traffic stays on the existing `backend-api` / `frontend` services.
- No flag inside v6 — the entire v6 product is the flag.

**Rollback Plan:**
- v5 stays running throughout Phase 1 and 2. If v6 bring-up fails, v5 traffic is unaffected.
- Phase 2 produces a working dev environment, not a production rollout — production cutover is a separate decision after Phase 2.

**Environment Variables:**
- All v6 env vars defined per cloud-infrastructure.md.
- Roadmap adds none.

## Testing Strategy

### Phase 0 verification
- [ ] AG-UI spike emits valid SSE consumed by curl + a minimal CopilotKit client
- [ ] Pydantic models pass `mypy` and `pydantic.TypeAdapter` round-trip
- [ ] TypeScript interfaces generated from Pydantic compile in the FE project

### Phase 1 backend tests (pytest)
- [ ] Skills CRUD: create, read, update, delete, list
- [ ] Document upload + parse roundtrip with a real `.docx` fixture
- [ ] AG-UI stream emits correct event sequence for a fixed prompt
- [ ] Auth middleware rejects unauthenticated requests, accepts valid Firebase ID tokens

### Phase 1 frontend tests (Vitest + React Testing Library)
- [ ] Skills nav bar renders + tab switching
- [ ] Workspace shell collapse/resize behaviour
- [ ] A2UIViewer renders fixture documents
- [ ] AG-UI hook consumes mock SSE and renders streamed text

### Phase 2 manual smoke
- [ ] Open frontend, sign in with Google
- [ ] Pick "Doc Analyst" skill from nav bar
- [ ] Upload `Q1-2026-financials.docx`
- [ ] See parsed document render in left panel
- [ ] Ask "what was Q1 EMEA revenue?" in chat
- [ ] See streamed answer with citation pointing back to a table cell
- [ ] Edit a heading in the document, see the patch applied
- [ ] Click "generate" → receive a formatted output doc

## Security Considerations

- All backend routes from Phase 1A.2 onward require Firebase ID token verification.
- Document uploads scoped per-user; cross-user access denied at Firestore rules level.
- AG-UI stream endpoint authenticated; SSE connection inherits the request's Firebase token.
- ailang-parse runs in-process (no shell-out); upload size capped per cloud-infrastructure.md.
- Phase 0 spike intentionally **does not** call real model APIs — uses a stub agent — so no production keys are touched until Phase 1A.3.

## Performance Considerations

- Phase 0 spike must measure first-event latency (target: <300ms per Axiom 1).
- Phase 1A.7 must verify SSE backpressure handling — an idle client should not block the agent runner.
- Phase 1B.4 (A2UIViewer) must lazy-load `@a2ui/react` to keep the initial JS bundle <200KB (Axiom 10).
- Phase 2 smoke test must record end-to-end latency on a 50-block document (target: first token <3s).

## Success Criteria

- [ ] Phase 0: All four contract artifacts checked into the repo
- [ ] Phase 0: AG-UI spike verified working (translator path OR native path)
- [ ] Phase 1A: Backend integration test green
- [ ] Phase 1B: Frontend smoke test green against fixtures
- [ ] Phase 2: End-to-end smoke checklist all green
- [ ] No Phase 2 PR rewrites a Phase 1 type (contract drift = roadmap failure)
- [ ] Mark can demo the dev environment to anyone in <2 minutes

## Open Questions

- ~~**AG-UI native vs translator**~~ — **RESOLVED 2026-04-10**: use `ag-ui-adk` v0.6.0 from CopilotKit. No translator needed, but Phase 0.4 spike still required to verify session-service injection and auth-middleware ordering.
- ~~**Build chat UI from scratch vs. adopt CopilotKit components**~~ — **RESOLVED 2026-04-10** after scaffolding `npx copilotkit@latest create -f adk` and inspecting the output. Adopt `<CopilotSidebar>`, `useCoAgent`, `useFrontendTool`, `useRenderToolCall`, and `@copilotkit/a2ui-renderer@1.55.1`. Phase 1B shrinks ~3 days; new tasks 1B.0 (scaffold cherry-pick) and 1B.10 (brand theming) added. Decision recorded in [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log.
- **Vertex AI Agent Engine session service** — does it exist yet for v6 or do we stay on InMemory through Phase 2? (Tracked in session-and-memory.md.) **New sub-question raised by spike research:** does `ADKAgent.from_app()` accept a custom session service or force InMemory?
- **`ag-ui-adk` ResumabilityConfig** — does it support our per-document chat session model (one session per `(user, document)` pair), or does it assume one session per user? Verify in Phase 0.4 spike.
- **Skills marketplace seed data** — do we need any seeded skills in dev for Phase 2 smoke, or does the user create one in the smoke? (Suggestion: seed "Doc Analyst" to keep the smoke deterministic.)

## Related Documents

- All v6.0.0 sub-design docs (table above)
- [docs/product-axioms.md](../../../../product-axioms.md)
- [v5 → v6 migration](v5-data-migration.md)
- Sprint plan: (to be created next via `/sprint-planner`)
