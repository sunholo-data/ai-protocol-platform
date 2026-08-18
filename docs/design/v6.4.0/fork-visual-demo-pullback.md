# Fork Visual Demo Pull-Back — Stage-Grade Walkthrough for Aitana

**Status**: Planned
**Priority**: P1
**Estimated**: ~7.5 days (1 sprint, single dev)
**Scope**: Frontend (heavy) + Backend (light — 3 demo skills)
**Dependencies**:
- multi-surface rendering (v6.2.0 2.9 ✅)
- a2ui-surface-context (v6.2.0 2.10 ✅)
- ACTION-TRIGGER M1–M3 (shipped 2026-06-08 ✅)
- StaticArtefactFrame (v6.2.0 2.9 / G35 ✅)
- artefact render hook (v6.2.0 2.13 ✅)
- branding.ts (frontend, already in place ✅)
**Created**: 2026-06-08
**Last Updated**: 2026-06-08

## Problem Statement

Five downstream forks have stress-tested the platform over the last quarter — **CPH AIPLA** (Copenhagen University physics tutor), **a tutoring fork** (Danish schools), **the internal-tools fork / internal-tools Tools** (collective ops), **gde-ap-agent** (Sunholo's Gemini Enterprise demo), and the early **Aitana** product surfaces. Each pushed protocol fixes upstream (the AIPLA template-extensions in v6.2.0 2.11–2.14; the gde-ap-agent G37–G46 hardening; the ACTION-TRIGGER write-and-run sprint). Those flowed back cleanly.

What didn't flow back: the **visible polish** that makes those forks compelling on stage. The audit pane (gde-ap-agent), the editorial landing + tech narrative (gde-ap-agent), the workspace artefact pattern (CPH AIPLA's KineBot / LedPlanck / Boldkast), and the workspace primitives (ProgressChecklist, ProblemStatementCard, WorkspaceShell, ChatRevealTab) all live downstream-only. Aitana itself currently boots into a chat surface with no narrative on top, no audit-style protocol visibility, and no rich workbench artefacts. The protocol stack is there; nothing in the demo shows it off.

**Current State:**
- Landing page is a single `<main>` with a sign-in button and a marketplace skill list — no hero, no narrative, no protocol visualisation
- No `/tech` or `/how-it-works` route — the architecture story isn't tellable in-product
- No audit/inspector pane — users see a chat bubble but not the tool calls, sub-agent handoffs, or intermediate emissions that make the system reasoning-transparent
- The only workspace component upstream is `StaticArtefactFrame.tsx`; the workbench composition primitives (split-pane shell, progress checklist, problem-statement card, reveal tab) are fork-only
- Zero "wow" demo skills — nothing in the marketplace shows what A2UI surface + ACTION-TRIGGER + MCP App can do when wired together with intent

**Impact:**
- **Aitana commercial deliverable this week** — sales conversations need a stage-grade walkthrough, not a chat box
- **Croatia WebSummerCamp July 2026** — the workshop helper agent (v6.2.0 2.15) lands into an unstaged landing page; visitors see "another chat" rather than "the protocol stack in action"
- **Template publication** — the next `aitana-template-publish` run will sync v6.4.0 features to `sunholo-data/ai-protocol-platform`. Without the visual layer, the public template demo bar stays where it is. Forks keep having to reinvent the narrative.
- **Affected**: Aitana (immediate), every future fork (downstream), workshop attendees (July)

## Goals

**Primary Goal:** Ship the fork-tested demo-impressive subset back to Aitana in one week, generalised so downstream forks adopt the patterns by overriding `BRANDING` rather than copy-pasting components.

**Success Metrics:**
- A visitor lands on `/`, sees an editorial hero + `ProtocolStripe` + `DemoSteps`, hits "Try the demo", and reaches a workbench artefact within 2 clicks
- Audit/Inspector pane reveals the agent's tool calls + sub-agent handoffs in real time during the demo flow; one click closes/opens the pane
- 3 Aitana-themed workbench artefacts ship as marketplace skills (ROI calculator, agent-stack visualiser, vendor-comparison validator) — each demonstrates one protocol concept (A2UI surface, ACTION-TRIGGER, MCP App)
- `/tech` route exists and renders an animated `ArchitectureDiagram` that scales cleanly between light/dark mode and stays accurate as the stack evolves (data-driven, no hardcoded coordinates)
- All ported components consume `BRANDING.*` for strings/links/logos — zero hardcoded "Aitana" or "Sunholo" outside `branding.ts`
- New fork forking the public template gets the demo polish for free: `git clone` → edit `branding.ts` → working stage-grade demo

**Non-Goals:**
- **Teacher dashboard / operator-dashboard pattern** — deferred to v6.5+. CPH's `teacher/` directory (insights + voice settings) is a separate, larger sprint
- **AP-pipeline-specific skills** (`ap-orchestrator`, `ap-pipeline`, `ap-poster`, `ap-validator`, `invoice-extractor`) — those stay in gde-ap-agent. We port the *pane that displays them*, not the skills themselves
- **CPH skill content** — physics tutor (KineBot kinematics, LedPlanck experiment, Boldkast simulator) stays fork-side. We port the *workbench composition pattern*, instantiated with Aitana-consultancy themes
- **Template publication of these features** — separate concern, follows AFTER Aitana proves them via the `aitana-template-publish` skill
- **Video production for DemoVideo component** — ship the component with a placeholder; real video is a marketing task, not engineering
- **Wide redesign of existing chat surface** — drop-in next to it, don't refactor it

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Workspace artefacts mount to named surfaces (v6.2.0 2.9), survive turn-over without remount; landing/tech pages are static / RSC-rendered → instant TTFB. Audit pane streams from existing AG-UI events — no new latency path. |
| 2 | EARNED TRUST | +2 | The Audit / Inspector pane is **literally** earned trust — surfaces every tool call, sub-agent emission, and intermediate result so the user sees the agent's reasoning rather than only its conclusion. Strongest axiom hit in the doc. |
| 3 | SKILLS, NOT FEATURES | +1 | The 3 new demos ship as marketplace skills (`roi-calculator`, `agent-stack-visualiser`, `vendor-comparison-validator`), discoverable via the existing skill builder. Workspace primitives are infrastructure that future skill-builders compose without code changes. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing changes; demo skills use sensible defaults (Gemini Flash for chat, Claude Sonnet for the validator). |
| 5 | GRACEFUL DEGRADATION | +1 | Audit pane is opt-in (toggle in header), hidden when no specialist data is available; landing/tech pages are static fallback; workbench artefacts degrade to chat-only when the workspace surface isn't mounted (consistent with v6.2.0 2.9 backwards-compat). |
| 6 | PROTOCOL OVER CUSTOM | +2 | Zero new protocols. The audit pane consumes AG-UI events the platform already emits; workbench artefacts mount via the A2UI multi-surface API (v6.2.0 2.9); workbench buttons drive the agent via the ACTION-TRIGGER hook (just shipped); state flows back via A2UI surface-context (v6.2.0 2.10). Every new visible thing renders an existing protocol event. |
| 7 | API FIRST | 0 | Backend changes are 3 demo skill configs + their skill metadata — no new API endpoints, no business logic outside skill prompts. Channels (Telegram, email, CLI) keep working unchanged. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Audit pane surfaces the same data that already lands in Cloud Trace / GenAI logging — closes the gap between dev-grade tracing (Cloud Trace UI) and demo-grade tracing (the InspectorPanel) without adding any new instrumentation. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data flows, no new trust boundaries. The audit pane is gated by the same skill-access check as the chat surface; demo skills use existing tool-permission machinery. No data leaves the GCP project. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Audit pane is presentation over AG-UI events — no business logic moves to the client. Landing/tech pages are RSC-rendered (React Server Components, no hydration of marketing copy). Bundle-size budget tracked — see Performance Considerations. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Standards Compliance

This doc adopts established protocols at every protocol boundary:

- **A2UI v0.9** — workspace artefact mount + state-back via `A2uiSurface` and `surface-action` (already in platform, v6.2.0 2.9 + 2.10)
- **AG-UI** — audit pane consumes `TEXT_DELTA`, `TOOL_CALL_START`, `TOOL_CALL_END`, `STAGE_PROGRESS`, `RUN_FINISHED`, `RUN_ERROR` events; no new event types
- **MCP Apps** — workbench artefacts render via `StaticArtefactFrame` (G35-spec-compliant, shipped in v6.2.0)
- **ACTION-TRIGGER** — workbench buttons drive the agent via `useActionDrivenAgent` (shipped 2026-06-08)
- **Agent Skills spec** — the 3 demo skills are SKILL.md format with frontmatter, loaded by `SkillToolset`

No custom formats, no proprietary interfaces. Per Axiom 6, this is a +2 score on the Standards Compliance axis: every visible surface composes an existing standard rather than inventing.

## CLI Surface

Per design-doc-creator skill rule 5b-bis, this feature has developer-facing surface, so the CLI commands ship in the same sprint:

- `aiplatform demo open [--browser chrome|firefox]` — launches the local Aitana frontend on `/` and opens the browser to the landing page. Zero-friction "show me the demo" entry point for sales, support, or onboarding flows.
- `aiplatform demo verify` — runs the smoke harness against the local demo: hits `/`, `/tech`, `/chat/...`, checks each new component renders without console errors, confirms the audit pane toggles and the 3 demo skills appear in the marketplace. Output is a green/red 12-line checklist. Pairs with the existing `make smoke-auth` / `scripts/smoke-deployed.sh` patterns.

Each command is ~0.15d (Click subcommand + httpx call + Vitest contract test). Both land in M5.

## Design

### Overview

Lift the visible polish from gde-ap-agent (audit + landing + tech) and cphu-aipla-app (workspace primitives + workbench pattern) to upstream Aitana, **generalising** the AP-pipeline-specific and physics-tutor-specific assumptions as we go. Then instantiate three new Aitana-themed demo skills that exercise the now-generic primitives. Net result: Aitana ships a stage-grade walkthrough on top of the protocol plumbing it already has; downstream forks adopt the polish by overriding `BRANDING`.

### Generalisation Rules

The forks were not built with upstream-portability in mind. When porting, three things must be rewritten:

1. **`SpecialistKey` union → string** — gde-ap-agent's `audit/InspectorPanel` types specialists as `"docparse" | "validator" | "poster"`. Upstream takes opaque `specialistKey: string` keyed by skill id (with display metadata resolved from skill frontmatter).
2. **`pipelineEmissions: {invoice, verdict, posting}` → `Map<string, RawEmission>`** — same panel currently encodes the three-stage AP pipeline as named fields. Upstream uses a generic ordered map; sequence + naming come from skill config.
3. **Hardcoded copy / Danish text / physics topics → `BRANDING.demo.*` + skill frontmatter** — every user-visible string already routes through `BRANDING` in the upstream branding module. Every fork-specific copy gets either a `BRANDING` field (chrome) or a skill frontmatter field (skill content).

### Frontend Changes

**New Components — workspace primitives** (M1, ~1d):
- `src/components/workspace/WorkspaceShell.tsx` — split-pane layout container (left: chat, right: workspace). Port from CPH; remove the Danish copy fallback. ~175 LOC.
- `src/components/workspace/WorkspaceDivider.tsx` — resizable column divider with drag handle. Port verbatim. ~195 LOC.
- `src/components/workspace/ProgressChecklist.tsx` — sessionStorage-backed checklist that pushes state to A2UI surface-context on every toggle. Already generic-shaped in CPH (skill-id keyed); port + delete the v0.1-specific items default. ~218 LOC.
- `src/components/workspace/ProblemStatementCard.tsx` — markdown + KaTeX card for the "what we're working on" context. Already generic. ~44 LOC.
- `src/components/workspace/ChatRevealTab.tsx` — toggle that collapses chat to a tab on the side when the workspace artefact takes focus. ~38 LOC.

**New Components — landing + tech** (M2, ~2d):
- `src/components/landing/Hero.tsx` — generalised from `APHero.tsx`. Asymmetric editorial layout: eyebrow + two-line display headline + body + CTA pair on the left, a `HeroVisual` slot on the right (passed as `children`, so each fork can render its own diagram without forking the Hero shell). ~220 LOC.
- `src/components/landing/DemoSteps.tsx` — 3-step "how the demo works" strip. Steps come from `BRANDING.demo.steps[]`. ~70 LOC.
- `src/components/landing/DemoVideo.tsx` — `<video>` element with poster, playing on click. Source path from `BRANDING.demo.videoSrc`. Ships with a placeholder until marketing produces the real video. ~55 LOC.
- `src/components/landing/ProtocolStripe.tsx` — horizontal strip of protocol logos (A2UI, AG-UI, MCP, A2A, ADK). Data-driven from `PROTOCOLS` constant. ~80 LOC.
- `src/components/tech/ArchitectureDiagram.tsx` — animated SVG of the request-to-render path. Refactored to consume a `nodes: NodeSpec[]` and `edges: EdgeSpec[]` from `branding.tech.architecture` rather than hardcoding the gde-ap-agent shape. ~500 LOC including SVG markup.
- `src/components/tech/ProtocolDiagram.tsx` — per-protocol drill-down diagram. Same data-driven shape. ~140 LOC.
- `src/components/tech/ProtocolIcon.tsx` — small icon component for protocol logos. Generic. ~80 LOC.

**New Components — audit / inspector pane** (M3, ~2d):
- `src/components/audit/InspectorPanel.tsx` — slide-in side panel showing the latest invocation of the selected specialist. **Generalised:** `specialistKey: string`, `state: SpecialistState | null`, `emissions: Map<string, RawEmission>` instead of the named-fields version. ~450 LOC.
- `src/components/audit/AuditViewBar.tsx` — top-of-chat strip showing one chip per specialist (with status badge). Port verbatim. ~40 LOC.
- `src/components/audit/SpecialistChip.tsx` — single chip; click to open InspectorPanel. ~100 LOC.
- `src/components/audit/sharedView.tsx` — shared `InputOutputCard` + `SectionLabel` primitives. ~190 LOC.
- `src/components/audit/StandaloneResultView.tsx`, `StandaloneToolCallCard.tsx`, `RunStandaloneSection.tsx` — the "run this specialist standalone" form path. Generalised: the form fields come from skill frontmatter, not hardcoded per-specialist. ~440 LOC.
- `src/components/audit/finalResponseRender.ts` — small util for rendering the agent's final structured output card. ~90 LOC.
- `src/hooks/useSpecialistInvocations.ts` — collects per-specialist `TOOL_CALL_*` and structured-output events from the AG-UI stream into a `Map<string, SpecialistState>`. ~150 LOC.
- **Deliberately NOT ported:** `VendorKgPanel.tsx` — vendor-specific knowledge-graph display, AP-pipeline-only. Stays in gde-ap-agent.

**New Components — Aitana consultancy workbenches** (M4, ~2d):
- `src/components/workspace/RoiCalculatorWorkbench.tsx` — inputs (current cost, team size, hours/week) → live ROI estimate + breakdown card. Demonstrates A2UI surface + surface-context (user edits a field → next agent turn sees the values). ~280 LOC + ~100 LOC StaticArtefactFrame content.
- `src/components/workspace/AgentStackVisualiser.tsx` — interactive stack diagram of which protocols are active in the current skill (lit up vs greyed out). Demonstrates ACTION-TRIGGER (click a protocol layer → agent explains that layer in chat). ~250 LOC.
- `src/components/workspace/VendorComparisonValidator.tsx` — table of vendor offerings with a per-row "Validate" button. Demonstrates MCP App pattern (validator runs in the sandboxed iframe, surface-context back to agent). ~300 LOC + ~150 LOC iframe artefact.

**New Pages:**
- `src/app/tech/page.tsx` — new `/tech` route, RSC-rendered, composes `Hero` + `ArchitectureDiagram` + `ProtocolDiagram` per-protocol.

**Modified Components:**
- `src/app/page.tsx` — current `HomePage` swaps its centered sign-in card for the `Hero` + `ProtocolStripe` + `DemoSteps` + marketplace strip composition. Keeps the marketplace fetch.
- `src/components/chat/ChatLayout.tsx` (or equivalent) — adds an `AuditViewBar` slot at the top and a slide-out `InspectorPanel` on the right, toggle in the chat header.
- `src/lib/branding.ts` — adds a `demo.steps[]`, `demo.videoSrc`, `tech.architecture: {nodes, edges}` block. Aitana fork sets these; the public template ships a sensible default.

**State Management:**
- `useSpecialistInvocations` hook subscribes to the AG-UI stream + ADK session state, maintains a per-specialist `SpecialistState` (latest invocation + history). sessionStorage-persisted via `loadPersistedInspectorKey` / `persistInspectorKey` so judges who refresh keep their open panel.

**UI/UX:**
- Landing: hero → "Try the demo" → chat. Audit toggle is always available in chat header; off by default for first-time visitors, on by default when `?audit=1` query param present.
- Workbench: each demo skill mounts to the `workspace` named surface (v6.2.0 2.9). Chat continues on the left; the workbench artefact occupies the right pane. `ChatRevealTab` lets the user collapse one side or the other.

### Backend Changes

Light. Three new demo skills as SKILL.md files; one optional `audit_specialist` field in `SkillConfig` to hint the frontend which sub-agents to surface in the Audit pane.

**New Skills (Firestore + skill templates):**
- `backend/skills/templates/roi-calculator/SKILL.md` — ROI calc orchestrator; tools = `compute_roi` FunctionTool + A2UI surface emit. ~80 LOC + frontmatter.
- `backend/skills/templates/agent-stack-visualiser/SKILL.md` — explainer agent; tools = `explain_protocol_layer` + A2UI surface emit. ~80 LOC.
- `backend/skills/templates/vendor-comparison-validator/SKILL.md` — multi-vendor comparator; sub-agent = `validate_vendor` (visible in Audit pane as a specialist). ~100 LOC + the validator MCP App iframe artefact at `infrastructure/mcp-sandbox/artefacts/vendor-validator/`.

**Modified `SkillConfig`:**
- Add optional `audit_specialists: list[str] | None` field — list of sub-agent ids the frontend should chip into the AuditViewBar. Backwards compatible: skills without this field render no audit chips (the existing behaviour). ~5 LOC + migration not needed (additive nullable).

**No new endpoints.** The audit pane reads from the AG-UI stream already emitted by the existing `/api/chat/agui-stream` endpoint; the workbench surfaces use the v6.2.0 2.9 / 2.10 `/api/sessions/{id}/surface-action` endpoint.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| —      | —        | No new endpoints | — |
| GET    | /api/skills/{id} | Response gains optional `audit_specialists: string[] \| null` | No (additive, nullable) |

### Architecture Diagram

```
[Visitor] → /  (RSC) →  Hero + ProtocolStripe + DemoSteps + marketplace strip
                            │
                            └── "Try the demo" CTA ──► /chat/<demo-skill-slug>
                                                              │
                                                              ├── ChatLayout
                                                              │     ├── AuditViewBar (chips per specialist)
                                                              │     └── (toggle) InspectorPanel ─── consumes AG-UI events
                                                              │                                       (TOOL_CALL_*, STAGE_PROGRESS, RUN_*)
                                                              │
                                                              └── WorkspaceShell (when skill mounts workspace surface)
                                                                    ├── ProblemStatementCard (from skill frontmatter)
                                                                    ├── ProgressChecklist    ─── pushes to surface-context
                                                                    └── <demo artefact>      ─── A2UI surface + ACTION-TRIGGER buttons
                                                                          │
                                                                          └── surface-action ──► backend ──► next agent turn
```

## Implementation Plan

### M1 — Workspace primitives (~1d)
- [ ] Port `WorkspaceShell.tsx` from `<local-path>` — generalise: drop Danish fallback copy, parameterise the "expand chat" tooltip via `BRANDING.workspace.expandChatTooltip` (~175 LOC + tests)
- [ ] Port `WorkspaceDivider.tsx` — verbatim, plus contract tests for drag behaviour (~195 LOC)
- [ ] Port `ProgressChecklist.tsx` — keep skill-id-keyed sessionStorage; delete the v0.1-specific items default; document `items` prop as required (~218 LOC + tests)
- [ ] Port `ProblemStatementCard.tsx` — verbatim, render null path covered by test (~44 LOC + tests)
- [ ] Port `ChatRevealTab.tsx` — verbatim (~38 LOC + tests)
- [ ] Vitest coverage: 4 new components × 2-4 tests each (~10 tests total)

### M2 — Landing + tech pages (~2d)
- [ ] Port `APHero.tsx` → `Hero.tsx`, hoist visual to `children` slot so Aitana renders `PipelineDiagram`, AP fork renders the AP one, public template renders a neutral data-flow loop (~220 LOC + 4 tests)
- [ ] Port `DemoSteps.tsx`, drive steps from `BRANDING.demo.steps[]` (~70 LOC + 2 tests)
- [ ] Port `DemoVideo.tsx`, placeholder until marketing video lands (~55 LOC + 1 test)
- [ ] Port `ProtocolStripe.tsx` — data-driven `PROTOCOLS` const (~80 LOC + 2 tests)
- [ ] Port `ArchitectureDiagram.tsx` — **refactor** to take `nodes` + `edges` from `branding.tech.architecture`; ship a sensible default for the public template (~500 LOC + 5 tests covering data-driven render + animation contract)
- [ ] Port `ProtocolDiagram.tsx`, `ProtocolIcon.tsx` (~220 LOC + 3 tests)
- [ ] New `/tech/page.tsx` route (RSC, no client JS) (~50 LOC + 1 contract test)
- [ ] Modify `app/page.tsx` — Hero + ProtocolStripe + DemoSteps + existing marketplace strip (~80 LOC change + 2 tests)
- [ ] Extend `branding.ts` with `demo.steps`, `demo.videoSrc`, `tech.architecture` (~50 LOC, no tests — types only)

### M3 — Audit / Inspector pane (~2d)
- [ ] Port `InspectorPanel.tsx` — generalise `SpecialistKey` to `string`, replace `pipelineEmissions: {invoice, verdict, posting}` with `emissions: Map<string, RawEmission>`. Pipe in skill display metadata via `getMetaByKey` (already exists upstream) (~450 LOC + 8 tests)
- [ ] Port `AuditViewBar.tsx`, `SpecialistChip.tsx`, `sharedView.tsx` (~330 LOC + 5 tests)
- [ ] Port `StandaloneResultView.tsx`, `StandaloneToolCallCard.tsx`, `RunStandaloneSection.tsx` — generalise form fields to come from skill frontmatter `audit_form_schema` (optional, falls back to "raw JSON" textarea) (~440 LOC + 6 tests)
- [ ] Port `finalResponseRender.ts` (~90 LOC + 2 tests)
- [ ] New `useSpecialistInvocations` hook — subscribes to AG-UI stream, builds the specialist map (~150 LOC + 6 tests covering accumulation + reset on session change)
- [ ] Wire `AuditViewBar` + `InspectorPanel` into `ChatLayout`; add toggle in chat header; `?audit=1` query-param overrides default-off (~80 LOC change + 3 tests)
- [ ] Backend: add `audit_specialists: list[str] | None` to `SkillConfig`; include in `/api/skills/{id}` response (~10 LOC backend + 2 pytest)

### M4 — Three Aitana consultancy workbench artefacts (~2d)
- [ ] **ROI Calculator** — `RoiCalculatorWorkbench.tsx` + `backend/skills/templates/roi-calculator/SKILL.md` + `compute_roi` FunctionTool. Demonstrates A2UI surface + surface-context (~380 LOC frontend + 80 LOC backend + 6 tests)
- [ ] **Agent Stack Visualiser** — `AgentStackVisualiser.tsx` + skill template + `explain_protocol_layer` FunctionTool. Demonstrates ACTION-TRIGGER (click protocol layer → agent explains in chat) (~250 LOC frontend + 80 LOC backend + 5 tests)
- [ ] **Vendor Comparison Validator** — `VendorComparisonValidator.tsx` + skill template + `validate_vendor` sub-agent (surfaces in Audit pane as specialist) + `infrastructure/mcp-sandbox/artefacts/vendor-validator/` static artefact. Demonstrates MCP App + audit pane integration (~450 LOC frontend + 100 LOC backend + 100 LOC iframe + 6 tests)

### M5 — Demo wiring, CLI, smoke (~0.5d)
- [ ] `aiplatform demo open` CLI command — Click subcommand + browser launch via `webbrowser.open` (~30 LOC + 1 test)
- [ ] `aiplatform demo verify` CLI command — smoke harness that hits `/`, `/tech`, `/chat/<each demo skill>`, asserts no console errors, asserts audit toggle works (~80 LOC + Vitest contract harness called from CLI + 1 e2e test)
- [ ] Update `cli/services.yaml` if a new local process is needed (probably not — uses existing `make dev`)
- [ ] Smoke entry in `scripts/smoke-deployed.sh` — assert `/` returns Hero markup, `/tech` returns 200 (~10 LOC + run once green)
- [ ] Update `docs/talks/ai-ui-protocol-stack.md` verification log entry — "v6.4.0 / 2026-XX-XX: Aitana lands the fork visual layer" with confirmed protocol mappings
- [ ] `make demo-verify` Makefile target wrapping `aiplatform demo verify`

## Migration & Rollout

**Database Migrations:** None. The optional `audit_specialists` field on `SkillConfig` is additive + nullable.

**Feature Flags:**
- `NEXT_PUBLIC_DEMO_LANDING=1` (default `1` upstream, opt-out for forks that prefer to ship their own landing) — when `0`, `/` renders the legacy minimal landing
- Audit pane toggle is per-user UI state (no flag needed); off by default

**Rollback Plan:**
- Each milestone (M1–M4) is independently revert-safe: workspace primitives are unused until M4 instantiates them; landing/tech pages are new routes; audit pane is gated by a toggle (default off)
- If the audit pane misbehaves in prod, the toggle defaults to off so the chat experience continues working
- Demo skills are ordinary marketplace skills — delete them from Firestore and they vanish

**Environment Variables:**
- `NEXT_PUBLIC_DEMO_LANDING` — frontend, all envs (default `1`)
- No new backend env vars

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] M1: 4 workspace primitives × 2–4 tests each (mount, prop variations, sessionStorage round-trip for ProgressChecklist)
- [ ] M2: Hero / DemoSteps / DemoVideo / ProtocolStripe / ArchitectureDiagram / ProtocolDiagram render contract tests (~15 tests)
- [ ] M3: InspectorPanel state machine across `loadPersistedInspectorKey` + AG-UI event accumulation; AuditViewBar toggle; useSpecialistInvocations hook (~20 tests)
- [ ] M4: Each workbench's surface mount + surface-action round-trip + ACTION-TRIGGER button click (~17 tests)
- [ ] M5: `aiplatform demo verify` contract harness (~6 assertions)

### Backend Tests (pytest)
- [ ] `audit_specialists` field round-trips through `SkillConfig` serialization + Firestore (~3 tests)
- [ ] Each new demo skill loads via `load_skill_from_dir`, validates against `SkillConfig` schema, runs one happy-path message through the runner (~3 × 3 = 9 tests)
- [ ] `compute_roi`, `explain_protocol_layer`, `validate_vendor` FunctionTool unit tests (~6 tests)

### Manual Testing (verified via `aiplatform demo verify` + chrome-devtools MCP)
- [ ] Land on `/`, see Hero + ProtocolStripe + DemoSteps + marketplace strip; no console errors
- [ ] Click "Try the demo" CTA → ROI calculator chat loads; surface mounts; edit a field → next agent turn references the value
- [ ] Toggle audit pane → AuditViewBar appears, chip-click opens InspectorPanel showing the live specialist invocation
- [ ] Visit `/tech` → ArchitectureDiagram animates; dark-mode flip works without coordinate drift
- [ ] Vendor Comparison Validator: click "Validate" on a row → sub-agent specialist chip lights up in AuditViewBar; sub-agent emission visible in InspectorPanel
- [ ] Run `aiplatform demo verify` against a freshly-deployed dev env → 12-line green checklist

## Security Considerations

- **No new data flows** — audit pane reads AG-UI events the platform already streams to the same user; same auth boundary
- **No new trust relationships** — workbench artefacts mount via the existing sandboxed-iframe MCP App proxy (separate-origin, v6.1.0 1.24 ✅)
- **Per-user skill access** — audit pane respects the same `can_access` check as chat; users can't see specialist emissions for skills they don't own
- **No third-party egress** — all telemetry consumed by the audit pane stays inside the GCP project (consistent with Axiom #8 and #9)

## Performance Considerations

- **Bundle-size impact**: estimated ~80KB gzipped across all new components (landing ~25KB, tech ~30KB, audit ~25KB). Workbench artefacts are lazy-loaded per skill — not in the initial bundle. Keeps the <200KB initial JS budget intact (Axiom #10 KPI)
- **RSC for marketing pages**: `/` and `/tech` are React Server Components — no client-side hydration cost for the marketing copy. Only interactive widgets (`DemoVideo`, the Hero CTA) ship as client components
- **Audit pane**: subscribes to the AG-UI stream the chat is already consuming — no additional network connection. State held in memory + sessionStorage; no Firestore writes per event
- **Workbench surfaces**: `A2uiSurface` is already optimised to mount-once-per-skill (v6.2.0 2.9); doesn't remount on `surface-action`
- **TTFT not affected** — landing is static; chat path is unchanged

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] All backend tests passing (`cd backend && make lint && make test-fast`)
- [ ] `make demo-verify` (wraps `aiplatform demo verify`) returns green
- [ ] Bundle size: initial JS budget stays under 200KB (CI assertion)
- [ ] Landing page Lighthouse score >= 90 (verified via chrome-devtools MCP)
- [ ] `/` renders Hero + ProtocolStripe + DemoSteps without hydration errors
- [ ] `/tech` renders ArchitectureDiagram with animated edges; dark mode toggle works
- [ ] Chat layout exposes an audit toggle; opening it reveals AuditViewBar
- [ ] Each of the 3 demo skills appears in the marketplace and reaches a workbench artefact in <2 clicks from `/`
- [ ] Vendor Comparison Validator's sub-agent invocation surfaces as a SpecialistChip in the AuditViewBar with InspectorPanel content
- [ ] `aiplatform demo open` opens the browser to `/`; `aiplatform demo verify` runs the 12-line checklist
- [ ] Zero hardcoded "Aitana" / "Sunholo" / "AIPLA" strings outside `branding.ts` (CI grep assertion)
- [ ] Documentation updated: `docs/talks/ai-ui-protocol-stack.md` verification log + `docs/ops/deployed-urls.md` if `/tech` adds a probe target

## Open Questions

- **Q1** — Does the Croatia workshop's helper agent (v6.2.0 2.15) want to *use* the new Audit pane to teach the protocol stack, or does the workshop have its own narrative? If the former, M3 should also wire the workshop helper agent's specialists into the AuditViewBar — adds ~0.25d.
- **Q2** — Should the public template ship the 3 Aitana-themed demo skills, or replace them with neutral examples ("translate this", "summarise this", "extract this")? Recommendation: ship the 3 with `aitana-template-publish`'s sanitize pipeline rebranding them — saves writing four new skills. Open for sign-off.
- **Q3** — Marketing video for `DemoVideo.tsx` — who/when? Component ships with a placeholder; real video is a separate deliverable. Not on this sprint's critical path.
- **Q4** — Should the Audit pane's "Run standalone" path require the user to be the skill owner, or can any user-with-access trigger a standalone run? Default to **owner-only** in this sprint; relax later if demand surfaces.

## Related Documents

- [docs/design/v6.2.0/implemented/multi-surface-rendering.md](../v6.2.0/implemented/multi-surface-rendering.md) — workspace surface foundation
- [docs/design/v6.2.0/implemented/a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — workspace → agent state loop
- [docs/design/v6.2.0/implemented/artefact-render-hook.md](../v6.2.0/implemented/artefact-render-hook.md) — defence-in-depth for workbench artefacts
- [docs/design/template/](../template/) — upstream-feedback tracker (this doc is the next iteration after G37–G46)
- docs/design/forks/tutoring-fork/v0.1.0/scope.md (that fork's own repo) — sister fork with similar workspace needs
- [docs/design/v6.1.0/local-dev-cli.md](../v6.1.0/implemented/local-dev-cli.md) — `aiplatform` CLI parent doc for `aiplatform demo`
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — Croatia July 2026 narrative tracker
- gde-ap-agent source: `<local-path>`
- CPH AIPLA source: `<local-path>`
