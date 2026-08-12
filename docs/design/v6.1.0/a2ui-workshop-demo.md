# A2UI Workshop Demo

**Status**: Planned
**Priority**: P1 (workshop W6 demo)
**Estimated**: ~1 day
**Scope**: Fullstack (small backend skill seed + frontend dev fixture page)
**Dependencies**:
  - [A2UI Tool Delivery](implemented/a2ui-tool-delivery.md) ✅ — `SendA2uiToClientToolset` + `A2UIRenderer.tsx` already shipped
  - [Chat Message Rendering](implemented/chat-message-rendering.md) ✅ — A2UI is mounted inside `MessageBubble`
  - [LOCAL_MODE & Workshop Readiness](local-mode-and-workshop-readiness.md) — supplies the demo skill seed mechanism in LOCAL_MODE
**Created**: 2026-04-28
**Last Updated**: 2026-04-28

## Problem Statement

The July 2026 workshop talk ([ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)) module **W6 — A2UI** demonstrates declarative agent-driven UI live. The renderer is built and the protocol plumbing is shipped, but there is no polished, end-to-end "ask a skill, get an A2UI form, submit, agent reacts" demo path that an attendee can run from a clean checkout.

**Current State:**

What's done:
- `frontend/src/components/protocols/A2UIRenderer.tsx` — renders A2UI form + table + fallback (shipped in [protocols-1a5-sprint](../v6.0.0/implemented/protocols-1a5-sprint.md))
- `SendA2uiToClientToolset` — agents emit A2UI via tool call rather than fenced markers (shipped in [a2ui-tool-delivery.md](implemented/a2ui-tool-delivery.md))
- Renderer is wired into `MessageBubble` in chat (shipped in [chat-message-rendering.md](implemented/chat-message-rendering.md))
- Vitest covers `A2UIRenderer` for `form`, `table`, and unknown-type fallback ([A2UIRenderer.test.tsx](../../../frontend/src/components/protocols/__tests__/A2UIRenderer.test.tsx))

What's missing for the workshop:
- **No demo skill** seeded in any environment that reliably emits a workshop-ready A2UI form. To trigger A2UI today, an attendee has to write their own skill prompt and figure out the toolset wiring. The W6 module needs a "you click here, it just works" path.
- **No standalone dev fixture page.** [rich-media/page.tsx](../../../frontend/src/app/dev/rich-media/page.tsx) is the model — a hard-coded set of `SkillMessage` fixtures in `ChatMessageList` showing every render path. A2UI has no equivalent. The placeholder at [file-browser/page.tsx:321](../../../frontend/src/app/dev/file-browser/page.tsx#L321) literally reads `(A2UI content would render here)`.
- **No A2UI-as-a-document path** documented. The talk distinguishes A2UI-in-chat from A2UI-as-a-document (the workspace pane); only the in-chat path is demoable today.
- **No interaction round-trip demo.** The renderer renders forms, but the demo for "submit a form → agent sees the values in the next turn" is not seeded as a tested, repeatable flow.

**Impact:**
- W6 demo risk: without a seeded skill, the live demo is fragile. A model that occasionally forgets to call the A2UI toolset will produce a broken slot in front of an audience.
- Workshop attendee path: attendees who want to add their own A2UI skill have nothing to copy from. A polished demo skill is also the canonical reference implementation.
- The dev experience for engineers iterating on the A2UI renderer (component coverage, styling) lacks a fast feedback page — every change requires booting the full stack and prompting a model.

## Goals

**Primary Goal:** Ship a workshop-grade A2UI demo path: a seeded `Demo A2UI Forms` skill (LOCAL_MODE fixture) + a `/dev/a2ui` standalone fixture page with hand-crafted A2UI payloads, such that attendees can both *interact with* and *copy from* a working example in <2 minutes from a fresh `make dev`.

**Success Metrics:**
- W6 live demo runs successfully from a `LOCAL_MODE=1 make dev` boot, no flakiness across 5 consecutive runs (form renders, submit, agent acks the values)
- `/dev/a2ui` fixture page renders 4+ component variants (form, table, card, button group) with no backend dependency — workshop attendees can land on it directly
- A2UI demo skill prompt is reproducible: the model emits the toolset call on >95% of "show me a form" prompts (measured via a 20-run smoke harness)

**Non-Goals:**
- **Not** expanding A2UI component coverage beyond what's already in the renderer. New component types (`chart`, `card`, `alert`) are deferred to v6.2 / per-component sprints; the demo uses what exists.
- **Not** the document-pane A2UI ("A2UI-as-document") rendering path. That's covered by [document-rendering-decision.md](../v6.0.0/implemented/document-rendering-decision.md) — this doc is in-chat A2UI only.
- **Not** evaluating the A2UI protocol design itself. The talk's protocol critique lives in [ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Polished demo + dev fixture mean A2UI feels solid in the live demo; flakiness undermines INSTANT FEEL. |
| 2 | EARNED TRUST | 0 | No factual-claim surface. |
| 3 | SKILLS, NOT FEATURES | +1 | The demo *is* a skill — reinforces the abstraction; attendees see A2UI delivered as a normal skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing change. |
| 5 | GRACEFUL DEGRADATION | +1 | Fixture page works without the backend; if the live demo skill misfires the dev fixture is the fallback. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Reinforces A2UI adoption; no custom JSON shape introduced. |
| 7 | API FIRST | 0 | Same toolset and AG-UI surface as before. |
| 8 | OBSERVABLE BY DEFAULT | 0 | No telemetry change. |
| 9 | SECURE BY CONSTRUCTION | 0 | A2UI submission round-trip is via existing AG-UI tool-result event; no new surface. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The demo skill ships the A2UI payload from the agent; client only renders. |
| | **Net Score** | **+5** | Acceptable. |

## Design

### Overview

Two artifacts: (a) a small demo skill seeded into the LOCAL_MODE fixture (and into the shared dev Firestore tier) that reliably emits A2UI via the existing toolset; (b) a `/dev/a2ui` page that renders hand-crafted A2UI payloads through `<A2UIRenderer>` directly, with no backend dependency, mirroring the [rich-media/page.tsx](../../../frontend/src/app/dev/rich-media/page.tsx) pattern.

### Frontend Changes

**New Components:**
- `src/app/dev/a2ui/page.tsx` — fixture page with 4 sections:
  1. **Form** — name + email + select; "submit" simulates a tool-result echo
  2. **Table** — quarterly sales-by-region (matches the document-workspace mockup table)
  3. **Card** + **Button group** — falls back gracefully if the renderer doesn't yet support them, demonstrating the fallback path
  4. **Round-trip** — form submission rendered alongside the simulated agent response

**Modified Components:**
- `src/app/dev/file-browser/page.tsx:321` — replace the `(A2UI content would render here)` placeholder with a rendered fixture (or remove the section and link to `/dev/a2ui`)

**State Management:**
- None. Page is purely presentational; submit handlers are local `useState`.

**UI/UX:**
- Each section has a "View payload" expandable showing the JSON the agent would emit — workshop attendees can copy it as a starter for their own skills.
- Each section also has a "Try it in chat" link routing to `/chat/demo-a2ui-forms` (the seeded skill).

### Backend Changes

**New Services/Modules:**
- `backend/db/local_fixture.py` — extend the LOCAL_MODE fixture (introduced in [local-mode-and-workshop-readiness.md](local-mode-and-workshop-readiness.md) M3) with the `Demo A2UI Forms` skill:
  - System prompt: explicit instruction to call `send_a2ui_to_client` for any UI-shaped request, with worked examples in the prompt
  - Toolset: `SendA2uiToClientToolset` (already shipped)
  - Slug: `demo-a2ui-forms`
  - Description: "Demonstrates the A2UI declarative-UI protocol — ask for a form, table, or card."

**Modified Endpoints:**
- None. The skill uses the existing skill-streaming endpoint.

### A2UI Demo Payloads

Four canonical fixtures, hand-crafted to be deterministic. These are the same payloads the seeded skill's prompt nudges the model toward, so the chat path and the dev fixture path render identical output.

```jsonc
// Fixture 1 — Form (workshop W6 hero demo)
{
  "type": "form",
  "title": "Schedule a Document Review",
  "fields": [
    { "name": "reviewer", "label": "Reviewer", "type": "text", "required": true },
    { "name": "deadline", "label": "Deadline", "type": "date" },
    { "name": "priority", "label": "Priority", "type": "select",
      "options": ["Low", "Medium", "High"] }
  ],
  "submit": { "label": "Schedule" }
}

// Fixture 2 — Table (matches the workspace mockup row)
{
  "type": "table",
  "columns": ["Region", "Q1 Revenue", "Δ vs LY"],
  "rows": [
    ["EMEA", "$3.2M", "+12%"],
    ["APAC", "$2.7M", "+8%"],
    ["Americas", "$5.1M", "+15%"]
  ]
}

// Fixtures 3 & 4 — card + button_group (showing fallback gracefully)
```

### Demo Skill System Prompt (extract)

```
You are a demo of the A2UI protocol. When the user asks for any
UI-shaped output (form, table, card, list of buttons), respond by
calling the `send_a2ui_to_client` tool with a JSON payload of the
right shape. Do not describe the UI in text — emit it as A2UI.

Example: if the user asks "show me a form to schedule a review",
call:
  send_a2ui_to_client({"type": "form", "title": "...", ...})

After the user submits a form, the submitted values arrive as a
tool result. Acknowledge them in plain text and offer the next step.
```

### Architecture Diagram

```
W6 demo path (live):

[User] → "show me a form to schedule a review"
    │
    ▼
[Demo A2UI Forms skill]  ──► tool call: send_a2ui_to_client(form payload)
    │                                            │
    │                                            ▼
    │                                    [AG-UI tool-call event]
    │                                            │
    │                                            ▼
    │                                    [<A2UIRenderer> in MessageBubble]
    │                                            │
    │                                            ▼
    │                                    [User submits form]
    ▼                                            │
[Demo skill receives values as tool result] ◄────┘
    │
    ▼
"Scheduled review for Alice on 2026-05-12 (priority: High)"

Dev fixture path (no backend):

[User] → /dev/a2ui
    │
    ▼
[Hand-crafted A2UI JSON] → <A2UIRenderer> → rendered UI
```

## Implementation Plan

### Phase 1: Demo skill seed (~0.25 day)
- [ ] Add `Demo A2UI Forms` skill config to `backend/db/local_fixture.py` with the worked-example prompt above
- [ ] Confirm `SendA2uiToClientToolset` is in the skill's toolset list
- [ ] Add the same skill to the shared dev Firestore tier seed (or document the manual create-once step)
- [ ] Add a 20-run smoke test in `backend/tests/integration/` that prompts the seeded skill with "show me a form" and asserts the toolset was called (via ADK eval)

### Phase 2: `/dev/a2ui` fixture page (~0.5 day)
- [ ] `src/app/dev/a2ui/page.tsx` with the 4 fixture sections (form, table, card, button group)
- [ ] "View payload" expandable per section
- [ ] "Try it in chat" link per section deep-linking to `/chat/demo-a2ui-forms`
- [ ] Vitest: page renders all 4 sections; payload-expandables open
- [ ] Replace the `(A2UI content would render here)` placeholder in [file-browser/page.tsx:321](../../../frontend/src/app/dev/file-browser/page.tsx#L321)

### Phase 3: Workshop docs (~0.25 day)
- [ ] Add a `## A2UI` section to `WORKSHOP.md` (introduced in [local-mode-and-workshop-readiness.md](local-mode-and-workshop-readiness.md) M3) walking attendees through the live demo + the `/dev/a2ui` fixture page
- [ ] Cross-link from `WORKSHOP.md` to the `/dev/a2ui` route
- [ ] Document how to copy the demo skill into your own GCP project (export skill JSON, import via aitana CLI once that's wired)

## Migration & Rollout

- Pure additive — no existing skill or component changes behaviourally.
- Demo skill is `public` access-control so anyone can run it; LOCAL_MODE attendees are auto-signed-in to the workshop user.
- Rollback: delete the fixture entry, delete `/dev/a2ui` page.

## Testing Strategy

### Frontend Tests (Vitest)
- [ ] `/dev/a2ui` page renders all 4 sections without errors
- [ ] Each "View payload" expandable opens and shows valid JSON
- [ ] Form submit handler captures values and shows the simulated agent ack
- [ ] Existing `A2UIRenderer.test.tsx` continues to pass

### Backend Tests (pytest)
- [ ] Smoke harness: 20 runs of the demo skill with "show me a form" prompt; toolset called >95% of runs (the prompt is robust enough)
- [ ] Smoke: form-submit round-trip — skill receives the submitted values in the next turn's tool result and acknowledges them in plain text

### Manual Testing (chrome-devtools MCP)
- [ ] `LOCAL_MODE=1 make dev`, navigate to `/chat/demo-a2ui-forms`, ask "show me a form to schedule a review", form renders, fill it out, submit, agent acks
- [ ] Navigate to `/dev/a2ui`, all 4 fixtures render
- [ ] Inspect AG-UI network panel — see the `send_a2ui_to_client` tool-call event with the form payload

## Success Criteria

- [ ] `LOCAL_MODE=1 make dev` → `/chat/demo-a2ui-forms` → "show me a form" → A2UI form renders → submit → agent ack, all visible end-to-end
- [ ] `/dev/a2ui` page renders 4 fixture sections without backend
- [ ] 20-run smoke harness >95% pass rate on toolset emission
- [ ] All frontend tests passing (`npm run test:run`)
- [ ] Demo runs cleanly from a fresh laptop checkout (paired with LOCAL_MODE M3 dry-run)

## Open Questions

- **Demo skill prompt robustness:** if the model occasionally drops the toolset call, do we (a) iterate the prompt, (b) add a `before_tool_callback` that nudges retry, or (c) accept some flakiness and lean on the dev-fixture page as the deterministic fallback? Recommend (a) first; (b) if (a) is insufficient.
- **Component coverage drift:** if the A2UI renderer expands to support `chart` / `alert` mid-workshop, the fixture page may want a 5th section. Trivial to add; not blocking.
- **Demo content theme:** the form is "schedule a document review" and the table is "Q1 sales by region" — chosen to match the workspace mockup. Confirm this resonates for the workshop audience or pick a less B2B-flavoured example.

## Related Documents

- [LOCAL_MODE & Workshop Readiness](local-mode-and-workshop-readiness.md) — supplies the seed mechanism + WORKSHOP.md
- [A2UI Tool Delivery](implemented/a2ui-tool-delivery.md) — the toolset this skill uses
- [Chat Message Rendering](implemented/chat-message-rendering.md) — how A2UI mounts inside MessageBubble
- [Protocols 1A.5 Sprint](../v6.0.0/implemented/protocols-1a5-sprint.md) — original A2UI renderer sprint
- [Talks: AI UI Protocol Stack](../../talks/ai-ui-protocol-stack.md) — workshop module W6 owner
- [MCP App Integrations](mcp-app-integrations.md) — sibling demo for workshop W7 (Cesium globe)
