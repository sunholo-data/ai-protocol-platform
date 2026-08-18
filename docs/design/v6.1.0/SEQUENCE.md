# v6.1.0 Build Sequence

**Gate:** v6.0.0 Phase 0–2 complete (done as of 2026-04-23). Tools-porting (1A.3) complete — landed 2026-04-23.

**Status as of 2026-05-19:** Items 1.0–1.3, **1.6 channels ✅ (all 5 milestones shipped 2026-05-16)**, **1.6a discord-channel ✅**, 1.7, 1.8–1.11, 1.13–1.15, 1.17, 1.18, 1.20–1.25 implemented. Remaining: **1.4 local-dev-cli**, **1.5 skill-friendly-urls**, **1.12 document-data-layer** (subcollection migration), 1.16 message-delete (parked), **1.19 a2ui-workshop-demo** (workshop W6 demo), 1.26 mcp-app-render-ux Phase B+C (snapshot history + pinned panel polish), 1.27 chat-history-tool-call-hydration (post-workshop). Design-doc cleanup 2026-05-19: 13 shipped v6.1.0 docs + sprint plans moved to `implemented/`.

**Demo target:** July 2026 workshop. See **Workshop critical path** section below for what must land before the talk.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 1.0 | [a2ui-tool-delivery.md](implemented/a2ui-tool-delivery.md) ✅ | **P1** | 1d | agent-factory (✅), streaming (✅) | **Must precede or be concurrent with 1.1 Phase 3.** Replaces fenced-block prompt hack with `SendA2uiToClientToolset`. MessageBubble's A2UI wiring should not be built on the approach we're about to delete. |
| 1.1 | [chat-message-rendering.md](implemented/chat-message-rendering.md) ✅ | **P0** | 2d | frontend-architecture (✅), streaming (✅), 1.0 (for Phase 3) | Unblocked for Phases 1–2 now. Foundation for all visible chat output. Must land before 1.3. |
| 1.2 | [file-browser.md](implemented/file-browser.md) ✅ | P1 | 2.5d | document-ui (✅), cloud-infra (✅) | Unblocked. Can run in parallel with 1.1. Folder tree, parse status, tab management, doc→chat context. |
| 1.3 | [rich-media-rendering.md](implemented/rich-media-rendering.md) ✅ | P1 | 3d | chat-message-rendering (1.1) | Wait for MessageBubble before wiring ChatMarkdown. SVG/image/PDF in chat. |
| 1.4 | [local-dev-cli.md](local-dev-cli.md) | P1 | 2–3d | v6.0.0 backend + frontend running | Unblocked now. Ships incrementally — each subsequent feature adds its own CLI command in <0.25d. |
| 1.5 | [skill-friendly-urls.md](skill-friendly-urls.md) | P2 | 1.5d | chat-history (✅), skills-data-model (✅) | Fully unblocked; slug field already in SkillConfig. |
| 1.6 | [channels.md](implemented/channels.md) ✅ | **P1** | **3.5d → 1 calendar day** | agent-factory (✅), auth (✅) | **Shipped 2026-05-16 — all 5 milestones via parallel Task sub-agents.** `BaseChannel` framework + 4 adapters (Discord, Email, Telegram, WhatsApp) + CLI demo + Cloud Run TF module + adapter howto. Net 196 new tests (871 → 1067 backend). All evaluator rounds PASS (M1=95, M2=92, M3=94, M4=90, M5=96 / 100). Framework gaps surfaced by M2/M3 round-1 evals (shared `_dispatch_inbound`, `inbound.metadata` → `OutboundMessage.metadata` forward) closed in commit 6c55b43. Adding a new channel = ~4h documented at `docs/integrations/channels-adapter-howto.md`. Sprint plan at [channels-sprint.md](implemented/channels-sprint.md). |
| 1.6a | [discord-channel.md](implemented/discord-channel.md) ✅ | **P1** | 7h (M2 of 1.6) | channels framework (1.6 M1 ✅) | **Shipped 2026-05-16** (commit fa15281, merged 51ea365). Discord adapter as `BaseChannel` subclass — 656 LOC + 322 Terraform (cloud-run-channel module passes `terraform validate`) + 39 tests. Lifted mechanics from `<local-path>` (472 LOC, Flask-era). Gateway path (mentions/DM) routes through `BaseChannel._dispatch_inbound` after the M2/M3 cleanup. Cloud Run `min-instances=1` required (cold start kills gateway). Sprint evaluator round 1: PASS 92/100. |
| 1.7 | [mcp-app-integrations.md](implemented/mcp-app-integrations.md) | P1 | 3–4d | streaming (✅), tools-porting (✅) | Tools-porting done — McpToolset pattern proven. Can start now. |
| 1.8 | [chat-session-history.md](implemented/chat-session-history.md) ✅ | P1 | 2.5d | chat-history (✅), chat-message-rendering (1.1) | Depends on 1.1 for `ChatMessageList initialMessages` prop. One-line backend fix (shared session service) + `GET /api/sessions/{id}/messages` + skill session list + frontend seeding. |
| 1.9 | [document-to-ai-pipeline.md](implemented/document-to-ai-pipeline.md) ✅ | P0 | 1.5d | file-browser (1.2 ✅) | Backend only. Upload → artifact service write. `load_artifacts_tool` always-on. Unblocks AI seeing uploaded documents. |
| 1.10 | [document-ui.md](implemented/document-ui.md) ✅ (rendering) | P1 | 3d | file-browser (1.2 ✅), document-to-ai-pipeline (1.9 ✅) | Document workspace shipped. `DocumentViewer`, `DocumentPanel`, split-pane render via custom `BlocksRenderer` (not A2UIViewer — see [implemented/document-rendering-decision.md](implemented/document-rendering-decision.md)). Clicking a doc tab shows the parsed document alongside chat. **SkillsBar carved out → 1.11.** |
| 1.11 | [skills-bar-sprint.md](implemented/skills-bar-sprint.md) ✅ | P2 | 2d (actual: 1d) | skills-data-model (✅), chat-history (✅), document-ui rendering (1.10 ✅) | Shipped 2026-04-27. Horizontal skill-tab navigation in the chat header, contextual to active document. `useUserSkills` hook + `SkillsBar` + `SkillTab` + `skillHref` + `/skills/new` stub. Catch-all route `[...path]/page.tsx` needs the same 6-line swap after `git pull`. |
| 1.12 | [document-data-layer.md](document-data-layer.md) | P1 | 2d (Phase 0 ✅; A+B+C+D remaining) | document-ui (1.10 ✅), document-to-ai-pipeline (1.9 ✅) | onSnapshot read path shipped 2026-04-27 (fixes flicker). Remaining: blocks subcollection migration + stable blockIds for editing. Foundational for [agent-driven-document-edits.md](agent-driven-document-edits.md) and [document-edit-loop.md](../v6.2.0/document-edit-loop.md) — must precede both. |
| 1.13 | [chat-history-fixes.md](implemented/chat-history-fixes.md) | **P0** | 1d | chat-history (✅ v6.0.0), chat-session-history (1.8 ✅) | Hardening sprint. Fixes 4 live failure modes (refresh 404, mid-stream flicker, doc-linked history, title rename) with paired regression tests. No new endpoints, no schema changes. **M1+M2 shipped 2026-04-27 (commits d83b1e4, 6411d1f). M3 E2E never ran — three deeper bugs surfaced; folded into 1.14.** |
| 1.14 | [chat-history-deep-fixes.md](implemented/chat-history-deep-fixes.md) | **P0** | 1d (shipped 2026-04-27) | chat-history-fixes (1.13 ✅ M1+M2) | Diagnostic-first follow-up. F1 agent-identity guard shipped (commit ed1e9ba). **Exposed deeper backend layer** — see 1.15. |
| 1.15 | [chat-history-deep-fixes-2.md](implemented/chat-history-deep-fixes-2.md) ✅ | **P0 (Critical)** | shipped 2026-04-27 (status verified 2026-04-30) | chat-history-deep-fixes (1.14 ✅) | **`user_id` triple inconsistency + access-policy gap — shipped.** `build_agui_adk_agent(user_id=user.uid)` aligns Vertex's session with Firestore's `owner_uid` (commit `e3ff7bb`); `useStableThreadId` hook closes the Bug A' post-writeback flicker (commit `31ddbcd`); `get_session_messages` uses `can_access` (sessions_route.py:255) so shared sessions are readable by non-owners while PATCH/DELETE remain owner-only. Bug B' deferred at sprint close (D3' diagnostic test never written, mechanism unclear; not re-reported since). |
| 1.16 | [message-delete.md](message-delete.md) | P2 (parked) | ~1.5d | 1.13–1.15 ✅ | **Per-message delete via Firestore tombstones.** Verified: ADK `BaseSessionService` is append-only (no `delete_event`), AG-UI has no `MESSAGE_DELETE`. Tombstone subcollection at `chat_sessions/{id}/tombstones/{message_id}` + read-path filter + `before_model_callback` filter so the model never sees deleted content next turn. Owner-only delete. Parked: user pivoted to wanting whole-session delete (1.17) first. |
| 1.17 | [session-delete-ui.md](implemented/session-delete-ui.md) ✅ | **P1** | ~0.5h actual | chat-history-fixes (1.13 ✅) | **Frontend wiring for session delete.** Shipped 2026-04-28: trash icon on owner's session rows, confirm dialog, DELETE + refetch, active-session URL clear via `onDeleteActive` → `handleNewSession`. 4 new tests; 281 frontend tests pass; tsc + lint clean. |
| 1.18 | [local-mode-and-workshop-readiness.md](implemented/local-mode-and-workshop-readiness.md) ✅ | **P1** | ~4d (actual: 1d) | session-and-memory (✅) | **Workshop blocker — shipped 2026-05-02.** `LOCAL_MODE=1` boots backend with no GCP creds (InMemoryFirestoreClient + auth stub + safety asserts) and frontend with no Firebase config (yellow banner + AuthContext branch). Plus shared-dev-Firestore tier (anon auth enabled on your-project-id + scoped rules, verify_rules.py 16/16 PASS live), repo scrub for public fork (LICENSE Apache 2.0, CONTRIBUTING.md, sanitize-for-template.sh, check_local_mode_safety.py CI lint), and WORKSHOP.md 3-tier quick-start. 77 new tests (68 backend + 9 frontend). Sprint evaluator round 1: PASS 90/100. |
| 1.19 | [a2ui-workshop-demo.md](a2ui-workshop-demo.md) | P1 | ~1d | a2ui-tool-delivery (1.0 ✅), local-mode-and-workshop-readiness (1.18) | **Workshop W6 demo.** Renderer is shipped (1.0); gap is a polished end-to-end demo skill that emits A2UI form + table reliably from LOCAL_MODE seed. Adds workshop-grade demo skill + `/dev/rich-media` walkthrough. |
| 1.20 | [ttft-instrumentation.md](implemented/ttft-instrumentation.md) ✅ | **P0** | 3d | chat-message-rendering (1.1 ✅), chat-session-history (1.8 ✅), document-to-ai-pipeline (1.9 ✅) | **TTFT measurement + perceived snappiness.** Two tracks sharing one taxonomy: (1) backend OTel + structured log + dev `LatencyHUD` + `aitana skill probe` CLI for measurement; (2) optimistic user-bubble + skeleton assistant-bubble + AG-UI `STAGE_PROGRESS` events ("Reading documents…", "Thinking…", "Calling {tool}…") for perceived TTFT <100ms. No backend optimization in scope. Serves Axiom #1 (INSTANT FEEL) on both real (<1s no-tools, <300ms first event) and perceived axes. Workshop-critical. **Shipped 2026-04-28.** |
| 1.22 | [multi-doc-context-fix.md](implemented/multi-doc-context-fix.md) ✅ | **P0** | ~2h actual | chat-history-deep-fixes-3 (Bug F ✅) | **Multi-doc context regression — fixed 2026-04-29.** Root cause: `_extract_document_ids` priority put `state.document_ids` (AG-UI accumulated state, one turn behind) ahead of `forwardedProps.document_ids` (per-turn fresh). User added doc 2 mid-session; backend kept reading turn 2's stale `[doc1]` from state. Pinned via D1 loader-log elevation + frontend `console.warn`: three ids in `forwardedProps`, one id in state, backend picked state. Fix: reorder candidates so forwardedProps wins. 2 new tests; 613 backend tests pass. |
| 1.21 | [ttft-optimization.md](implemented/ttft-optimization.md) ✅ | P1 | 1.5d (actual: ~2h) | ttft-instrumentation (1.20 ✅) | **Cut laptop TTFT from 9.2s to 2.4s via `AITANA_LOCAL_SESSION=memory` escape hatch.** M1 added `agent_factory_done` + `runner_setup_done` marks; data showed the 5.7s gap was Vertex Agent Engine round-trips from laptop to europe-west1. Production (Cloud Run same-region) already at p50≈2.5s. Strong priors (doc-loader parallelize, factory cache) all invalidated by data. Follow-ups filed: `--min-instances=1` for cold-start, CI deployed-region probe, re-deploy M1 marks. **Shipped 2026-04-28** (commits 8e99bb3, ea36b5d). |
| 1.23 | [stranded-session-prevention.md](implemented/stranded-session-prevention.md) ✅ | P1 | 0.5d (shipped 2026-04-30) | chat-history-fixes (1.13 ✅), multi-doc-context-fix (1.22 ✅) | **Stranded chat session prevention — shipped 2026-04-30.** Option 1: `useSessionMessages` 404 → `SessionNotFoundError` → `sessionGone=true`; `chat/[...path]/page.tsx` `useEffect` calls `handleNewSession()` to drop stale `?session=` from URL before next POST. Option 2: `make_document_loader` ERROR `TURN-1 INVARIANT VIOLATED` when turn 1 requested docs and every load failed (one greppable line per stranded-session creation). 3 backend tests + 3 vitest tests pin the contract. Option 3 deferred per plan. Net axiom score +4 with Option 2's failure-mode test. |
| 1.24 | [mcp-sandbox-separate-origin.md](implemented/mcp-sandbox-separate-origin.md) | P1 | 1d (lands inside sprint 1.7 M3+M4) | mcp-app-integrations (1.7) — sister doc | **MCP sandbox proxy as a separate-origin Node service.** Path Y from the 2026-04-30 architecture decision. Spec mandates the sandbox iframe live on a different origin from the host so `allow-same-origin` on the inner iframe can't read host cookies. New `infrastructure/mcp-sandbox/` service (Express + reference impl from `modelcontextprotocol/ext-apps/examples/basic-host`); local dev on port 3457; deployed as a Cloud Run sidecar `mcp-sandbox-{env}`. Frontend reads `NEXT_PUBLIC_MCP_SANDBOX_URL`. Net axiom score +7 (PROTOCOL OVER CUSTOM + SECURE BY CONSTRUCTION both +2). |
| 1.25 | [mcp-app-update-model-context.md](implemented/mcp-app-update-model-context.md) ✅ | **P0 (workshop W7 demo)** | 0.6d actual (vs 0.75 estimated) — shipped 2026-04-30 | mcp-app-integrations (1.7), mcp-sandbox-separate-origin (1.24) | **Wires the SECOND iframe→host RPC channel** (`ui/update-model-context`) — shipped 2026-04-30. Sprint 1.7 shipped `ui/message` (synthetic chat turns); this closes the spec contract by letting the iframe push structured content (view UUID, current bounds, etc.) into the agent's NEXT-turn context. Three-turn smoke demo verified live: "show me Munich" → "what city is currently centred?" (answered "Munich" without re-rendering) → "now zoom in to its old town" (resolved "its" via context, called geocode + show-map, map re-rendered). Zero `MCP error -32601` lines after the change. New `POST /api/sessions/{id}/iframe-context` endpoint with 7-gate access control (Firebase + session-access + skill-exists + server-in-activated + server-in-`allow_context_writes` opt-in + schema + 4 KB size cap); per-skill `tool_configs.mcp.allow_context_writes` opt-in field (default empty = off, per-server); `wrap_with_iframe_context` InstructionProvider injects `mcp_app_context.*` namespace into agent instructions with explicit framing prose to mitigate prompt-injection. AppRenderer's `onFallbackRequest` was the right hook (no dedicated `onUpdateModelContext` prop exists in `@mcp-ui/client`) — captured for the workshop talk. New `aiplatform sessions inspect <id> --mcp-context` CLI subcommand for debugging. Net axiom score +7. |
| 1.26 | [mcp-app-render-ux.md](mcp-app-render-ux.md) | P1 (workshop W7 polish) | Phase A 0.4d · Phase B 1.5d · Phase C 0.25d (total 2.15d if all land; Phase A alone unblocks the workshop story) — frontend only after 2026-05-01 revision | mcp-app-integrations (1.7), mcp-app-update-model-context (1.25 ✅) | **Snapshot history + pinned widget panel — addresses inline-only iframe rendering UX limits.** Three phases: (A) iframes push view snapshots (image dataUrls) as a vendor-prefixed field on their existing `ui/update-model-context` postMessage; the host extracts + stores client-side, then strips the field BEFORE forwarding the rest to the existing 1.25 backend endpoint — backend never sees snapshot bytes. Older chat bubbles render the snapshot tile instead of remounting heavy WebGL — workshop polish, ~0.4d, primary memory + history-fragmentation win. (B) per-skill `tool_configs.mcp.pinned_panel` opt-in mounts ONE persistent iframe in a side panel; new tool calls update it via `appBridge.sendToolInput` (the spec-supported but until-now-unused primitive) instead of remounting; chat bubbles show compact "📍 Updated → ..." badges. (C) Click-snapshot-to-time-travel ties A + B together for the workshop "compare Paris to Munich" flow. **No backend changes** (revised 2026-05-01 from earlier draft that proposed a backend snapshot endpoint). Net axiom score +7 (PROTOCOL OVER CUSTOM -1 for vendor-prefixed snapshot field while upstream RFC matures — host-internal, small blast radius). |
| 1.27 | [chat-history-tool-call-hydration.md](chat-history-tool-call-hydration.md) | P2 (post-workshop) | 1d | mcp-app-integrations (1.7 ✅), F2a parentMessageId snapshot (✅ 2026-05-01) | **Cross-session chat-history hydration for MCP-App tool calls.** 1.26's sessionStorage-backed snapshots cover in-tab reload; this covers the genuine reload case (open a saved chat from yesterday, see its map). Extends GET /api/sessions/{id}/messages with toolCalls[] per assistant message; frontend threads them into initialMessages instead of hardcoded `[]`. Soft-cap 3 concurrent live iframes; older turns render a placeholder + "Open map" affordance. **Filed as F2b in MCP-APP-RUNTIME-FIXES sprint** — surfaced 2026-05-01 deployed-dev E2E. |
| 1.28 | [action-triggered-agent-turn.md](implemented/action-triggered-agent-turn.md) ✅ | **P1 (workshop W6 Pattern 1)** | ~1d actual (matches estimate) — shipped 2026-06-08 | a2ui-tool-delivery (1.0 ✅), a2ui-workshop-demo (1.19), surface-action endpoint (sprint 2.10 ✅) | **Closes the Pattern 1 loop — non-chat AI UI. Shipped 2026-06-08** (commits `161c9a8` → `56a3292`). New `POST /api/skills/{skill}/sessions/{id}/surface-action-run` bundles the gate-checked state write + synthetic `RunAgentInput(messages=[], state={a2ui_action_trigger:…}, forwardedProps={…})` + AG-UI SSE stream — Button click drives a full agent turn, no chat composer needed. 8 gates (7 reused from `surface-action` via new `_a2ui_surface_shared` module + new `allow_action_triggered_runs` opt-in; 9th implicit gate enforces URL `skill_id` matches session). G41 terminal-event dedup reused as-is. Frontend: `useActionDrivenAgent` hook (~280 LOC, dispatches via `registry.appendMessages` — no AGUIProvider fork) + `<A2UISurfaceMount triggerOnAction skillId>` opt-in prop. CLI: `aiplatform sessions trigger-action` (newline-delimited JSON output, grep-friendly). Demo: seeded `demo-click-counter` skill (LOCAL_MODE; `gemini-2.5-pro`) + `/dev/a2ui` Pattern 1 fixture page + `scripts/smoke-pattern1.sh` 5-iteration smoke. Tests: backend 1427→1450 (+23), frontend 574→594 (+20), CLI 48→54 (+6) — total +49 new. End-to-end smoke pending `make cli-install` refresh + live LOCAL_MODE boot. **Filed upstream**: [a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) (embedded-transport spec gap), [ag-ui#1892](https://github.com/ag-ui-protocol/ag-ui/issues/1892) (terminal dedup bug — workaround reused), [ag-ui#1893](https://github.com/ag-ui-protocol/ag-ui/discussions/1893) (state semantics). Net axiom score +6. |

**1.1 and 1.2 are highest priority and run in parallel.** 1.3 follows 1.1. 1.4 and 1.6 are unblocked alongside the UI work. 1.7 now unblocked (tools-porting done). **1.8 follows 1.1 and can run in parallel with 1.3.** 1.11 (SkillsBar) is independent — can run any time after 1.10 ships. **1.12 (document-data-layer) precedes any document-edit work** — its Phase 0 onSnapshot read path is already shipped; the subcollection migration unblocks both `agent-driven-document-edits.md` and v6.2.0's `document-edit-loop.md`. **1.13 (chat-history-fixes) is P0** — current chat history is broken in user-visible ways; should land before any further v6.1.0 work or the demo path.

---

## What ships in v6.1.0

- **A2UI tool delivery** — `SendA2uiToClientToolset`, tool-call-based A2UI delivery ✅ done
- **Chat message rendering** — MessageBubble, streaming animation, source citations, tool call indicators, context banner ✅ done
- **File browser** — GCS-backed folder tree, parse-status indicators, multi-tab doc management, drag-drop upload, chat context wiring ✅ done
- **Rich media in chat** — SVG (DOMPurify-sanitised), inline images, PDF reference cards ✅ done
- **Chat session history** — durable session service, `GET /sessions/{id}/messages`, skill session list sidebar, history seeding on page load ✅ done
- **Document-to-AI pipeline** — ADK artifact service write at upload time, `load_artifacts_tool` on every agent, AI sees uploaded documents ✅ done
- **Document workspace UI** — `DocumentViewer`, `DocumentPanel`, split-pane layout, `BlocksRenderer` (BlockADT direct render) ✅ done
- `aitana` local dev CLI (dev server, auth, skill/doc/session/docs commands)
- Skill-friendly URLs (`/chat/@{uid}/slug`, owner-scoped, 301 from UUID) ✅ done
- **Channels framework** ✅ — `BaseChannel` ABC + `ChannelRegistry` + shared command parser / attachment pipeline / identity resolver + 4 production adapters (Discord, Email, Telegram, WhatsApp) + CLI demo (worked example for the howto) + Cloud Run TF module. New-channel cost: ~4h documented at [docs/integrations/channels-adapter-howto.md](../../integrations/channels-adapter-howto.md). an internal-tools fork unblocked; event-driven skill output routing has a stable `ChannelRegistry.get(name).send()` API.
- MCP App integrations: geo tool + A2UI `<UIResourceRenderer>` routing
- MCP App `ui/update-model-context` — bidirectional iframe ↔ agent state, closes the spec's second iframe→host RPC channel (1.25, P0 for workshop W7)
- MCP App render UX — snapshot history + pinned widget panel (1.26, P1 workshop polish)
- SkillsBar — horizontal skill-tab navigation in chat header (carved out from 1.10)
- Document data layer — onSnapshot real-time reads (✅ shipped) + blocks subcollection migration for granular edits (planned, foundational for editing features)
- **Chat history hardening** — fix refresh-404 race, mid-stream flicker, doc-linked history sync, title rename refresh (planned, P0; closes regressions found 2026-04-27)
- **TTFT instrumentation + perceived snappiness** ✅ — backend OTel + structured `event="ttft"` log + AG-UI `STAGE_PROGRESS` events with stage labels in TypingIndicator + dev `LatencyHUD` + `aitana skill probe` CLI; `AITANA_TTFT_MODE=full|log|off` kill switch so instrumentation overhead is itself A/B-testable. Shipped 2026-04-28 (commits c0f7923 → a4d9ca8). Empirical baseline pending operator run.
- **Action-triggered agent turn** — closes the Pattern 1 loop (non-chat AI UI). `POST /api/skills/{skill}/sessions/{id}/surface-action-run` bundles A2UI action write + synthetic `RunAgentInput` + AG-UI SSE stream; per-skill `allow_action_triggered_runs` opt-in; `useActionDrivenAgent` hook; `aiplatform sessions trigger-action` CLI. Workshop W6 Pattern 1 demo enabler (1.28).

## Mockup coverage tracker

Reference: [document-workspace.html](/frontend/public/mockups/document-workspace.html)

| Mockup element | Design doc | Ships in |
|---|---|---|
| Skills bar (tabs, logo, avatar) | [implemented/skills-bar-sprint.md](implemented/skills-bar-sprint.md) | v6.1.0 (1.11) ✅ done |
| Split pane layout (no resize handle yet) | [document-ui.md](implemented/document-ui.md) | v6.1.0 (1.10) ✅ done |
| Document rendering (headings, tables, track changes via `BlocksRenderer`, not A2UI) | [implemented/document-rendering-decision.md](implemented/document-rendering-decision.md) | v6.1.0 (1.10) ✅ done |
| Chat message bubbles (bot/user, avatar, timestamp) | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| Streaming animation + typing indicator | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| Inline table in chat bubble | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| Source citation links | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| Tool call chips | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| "Analyzing N documents" context banner | [chat-message-rendering.md](implemented/chat-message-rendering.md) | v6.1.0 ✅ done |
| Doc tabs bar (open files, format badges) | [file-browser.md](implemented/file-browser.md) | v6.1.0 ✅ done |
| Folder tree (accordion, file list, status dots) | [file-browser.md](implemented/file-browser.md) | v6.1.0 ✅ done |
| Parse progress bar | [file-browser.md](implemented/file-browser.md) | v6.1.0 ✅ done |
| Drag-drop upload | [file-browser.md](implemented/file-browser.md) | v6.1.0 ✅ done |
| SVG diagrams in chat | [rich-media-rendering.md](implemented/rich-media-rendering.md) | v6.1.0 ✅ done |
| Images in chat | [rich-media-rendering.md](implemented/rich-media-rendering.md) | v6.1.0 ✅ done |
| PDF reference cards in chat | [rich-media-rendering.md](implemented/rich-media-rendering.md) | v6.1.0 ✅ done |

## Dependency Graph

```
v6.0.0 complete
    │
    ├──► a2ui-tool-delivery (1.0) ✅ ──────────────────────────────────────┐
    │                                                                       │
    ├──► chat-message-rendering (1.1) ✅ ──► rich-media-rendering (1.3) ✅ ┤
    │         └──────────────────────── ► chat-session-history (1.8) ✅ ── ┤
    │                                                                       │
    ├──► file-browser (1.2) ✅ ──► document-to-ai-pipeline (1.9) ✅ ────── ┤
    │                   └──────────────────────────────────────────────────►│
    │                                                                       ▼
    ├──► local-dev-cli (1.4) — unblocked                          (all land in v6.1.0)
    │
    ├──► skill-friendly-urls (1.5) — any gap
    │
    ├──► channels (1.6) — unblocked
    │
    ├──► mcp-app-integrations (1.7) — now unblocked (tools-porting ✅)
    │
    ├──► document-ui (1.10) ✅ — after 1.2 ✅ + 1.9 ✅
    │
    └──► skills-bar (1.11) ✅ — after 1.10 ✅ (independent of workshop critical path)
```

## Timeline estimate

| Sprint | Duration | Can parallel with |
|--------|----------|-------------------|
| a2ui-tool-delivery (1.0) | 1d ✅ | — |
| chat-message-rendering (1.1) | 2d ✅ | file-browser (1.2), local-dev-cli (1.4), channels (1.6) |
| file-browser (1.2) | 2.5d ✅ | chat-message-rendering (1.1) |
| rich-media-rendering (1.3) | 3d ✅ | local-dev-cli (1.4), channels (1.6) |
| chat-session-history (1.8) | 2.5d ✅ | after 1.1; parallel with 1.3 |
| document-to-ai-pipeline (1.9) | 1.5d ✅ | after file-browser (1.2) |
| local-dev-cli (1.4) | 2–3d | everything |
| skill-friendly-urls (1.5) | 1.5d | any gap |
| channels (1.6) — framework + 4 adapters | 3.5d planned, 1 day actual ✅ (M2+M3 parallel, then M4+M5 parallel via Task sub-agents) | shipped 2026-05-16; ran alongside workshop polish track |
| mcp-app-integrations (1.7) | 3–4d | 1.4, 1.5, 1.6 |
| document-ui (1.10) | 3d ✅ | after 1.9 ✅ |
| skills-bar (1.11) | 2d ✅ | after 1.10 ✅ |
| document-data-layer (1.12) | 2d (0.25d ✅) | after 1.10 ✅; precedes any edit feature |
| stranded-session-prevention (1.23) | 0.5d ✅ | shipped 2026-04-30; complements reactive fix already in branch |

**v6.1.0 total:** ~21–25 days of effort, parallelisable to ~10–12 calendar days.

## Workshop critical path (July 2026)

The talk ([ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)) demonstrates all six protocols live. Each workshop module (W2–W7) has a required live demo. Here is what v6.1.0 must deliver before the talk, and what is already done.

| Workshop module | Protocol | Component / feature | Status |
|---|---|---|---|
| W2 — ADK | Framework | `backend/app.py`, agent factory | ✅ v6.0.0 done |
| W3 — MCP | Tools | MCP server, tool invocation | ✅ tools-porting (v6.0.0 1A.3) done |
| W4 — A2A | Discovery | `/.well-known/agent.json` | ✅ v6.0.0 done |
| W5 — AG-UI | Streaming | AG-UI stream, `useSkillAgent`, `HttpAgent` | ✅ v6.0.0 done |
| W6 — A2UI | Declarative UI | `A2UIRenderer.tsx` + wiring into `MessageBubble` (1.1 ✅) | ✅ 1.0 + 1.1 done |
| W8 — Shared State | Block ADT ↔ A2UI | Editable document panel + `EditDelta` protocol | 🔜 v6.2.0 [document-edit-loop.md](../v6.2.0/document-edit-loop.md) |
| W7 — MCP Apps | Sandboxed widgets | `MCPAppFrame.tsx` (built) + wiring (1.1 ✅) + **map-server sidecar** (1.7) | ⚠️ component + wiring done; map-server sidecar needs 1.7 |

**Key insight:** `A2UIRenderer` and `MCPAppFrame` are already implemented (`frontend/src/components/protocols/`). W6 is unblocked (1.0 + 1.1 complete). W7 still needs 1.7 (mcp-app-integrations) for the map-server sidecar — that sprint is now unblocked since tools-porting is done.

**Minimum viable demo path (for July):**
```
1.7 mcp-app-integrations (3–4d) — geo map-server sidecar + tool routing  [NOW UNBLOCKED]
```
All workshop-blocking sprints are either done or unblocked. `file-browser.md` (1.2) and `rich-media-rendering.md` (1.3) are done and make the overall platform more compelling as a demonstration context.

## Next: v6.2.0

See [v6.2.0/](../v6.2.0/) for MCP Toolbox for Databases, v5 data migration, and Agent CLI.
