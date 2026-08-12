# Workshop Agenda — Build AI UIs Beyond Chat

> The new agent UI protocols let AI generate real interfaces, not just text. Build with all three in one session: **MCP Apps**, **A2UI**, **AG-UI**.

**Audience:** Developers with some Python + React familiarity.
**Format:** Half-day in-person, ~3 hours.
**Outcome:** Every attendee leaves with their own working multi-protocol skill running locally + a public-template clone they own + concrete next steps.

**First session:** WebSummerCamp Croatia 2026 — [websummercamp.com](https://websummercamp.com/2026/news/super-early-sold-out-get-your-early-bird-now)
**Future sessions:** Tue 18 Aug · Tue 17 Nov · Tue 16 Feb · + more TBA

This doc is the **instructor's running order**. Companion docs:
- [`pre-work.md`](pre-work.md) — what attendees do 24–48h before arriving
- [`code-tour.md`](code-tour.md) — the 7-file ~1,600-LOC reading map
- [`protocol-gotchas.md`](protocol-gotchas.md) — the bear traps we hit at v6 bring-up, mapped to blocks (so attendees can route around them in block 5)
- [`skeleton-skill.md`](skeleton-skill.md) — the block 5 template
- [`helper-agent-design.md`](helper-agent-design.md) — the meta-demo agent (TBD)

---

## Pre-work assumptions

Attendees arrive having already run `make dev-local`. The room buzzes from minute 0 because everyone can already see their own chat UI working. If pre-work didn't work, block 1 catches it; if more than ~3 people failed pre-work, instructors triage in parallel during block 0.

**Required pre-installed:**
- Node 20+
- Python 3.11+
- GitHub account (for the show-and-tell submission in block 6)

**NOT required:**
- Docker — `make dev-local` is pure Node + Python (LOCAL_MODE stubs Firestore + auth, optional MCP sandbox is plain Node Express)
- GCP credentials
- Firebase account

---

## Running order

### Block 0 — The Chat Wall Problem (10 min)

- **The chat wall** — why pure text-streaming hits a ceiling. Three concrete failure modes from real deployments (complex forms, persistent widgets, structured workflows).
- The layered diagram from [`../talks/ai-ui-protocol-stack.md`](../talks/ai-ui-protocol-stack.md):
  - Layer 4 UI: **A2UI** (declarative JSON UI) + **MCP Apps** (sandboxed iframes)
  - Layer 3 Transport: **AG-UI** (event streaming)
  - Layer 2 Coordination: A2A (discovery) + MCP (tools)
  - Layer 1 Framework: Google ADK
- The 10 product axioms (INSTANT FEEL, PROTOCOL OVER CUSTOM, etc.) — one slide.
- **"The platform you're about to see is built on this stack. You'll watch the stack work, then plug into it."**

### Block 1 — Quick-start verification (10 min)

- Everyone confirms `make dev-local` running.
- Open `http://localhost:3456` (frontend) — see the LOCAL_MODE yellow banner.
- Send a message in the chat. If it streams, you're good.
- If anyone failed pre-work: instructors triage in parallel; the rest move to block 2 reading material (the [`code-tour.md`](code-tour.md) opening files).

### Block 2 — ADK + AG-UI live walkthrough (30 min)

**Demo skill:** `demo-researcher`

- Open `backend/app.py` (~50 LOC) — the root ADK agent. **Show the simplest possible agent**: just `Agent(name=..., model=..., instruction=...)`.
- **Multi-provider routing** (1-line aside): the `model=` arg accepts `gemini-2.5-flash`, `claude-sonnet` (via `google.adk.models.Claude`), or `gpt-4o` (via `LiteLlm`). Three providers, zero provider-specific code per skill.
- Open `frontend/src/hooks/useSkillAgent.ts` — find `agent.subscribe({...})`. **This is the AG-UI subscription** — four callbacks mapping lifecycle events to React state.
- Open browser DevTools → Network tab → `stream` request. Look at the SSE events as you send a message. **The protocol IS this byte stream.** No magic.
- Point out: `RUN_STARTED`, `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT` deltas, `RUN_FINISHED`. 16 event types total in 6 categories.
- Cite [`code-tour.md`](code-tour.md) for the full set; encourage reading on commute home.

### Block 3 — A2UI + the multi-surface loop (35 min)

**Demo skills:** `demo-form-builder` → `demo-workspace` → `demo-workspace-interactive`

- **Part A (10 min) — declarative UI generation.** Open `demo-form-builder`. Say "make me a contact form". Watch the agent emit A2UI JSON, watch the renderer turn it into a real React form. Show the A2UI spec is just JSON — no React code per skill.
- **Part B (10 min) — multi-surface.** Open `demo-workspace`. "Show me the dashboard." A2UI renders to the workspace pane, NOT the chat bubble. Skill author declared `surface_id="workspace"` in `tool_configs.a2ui`.
- **Part C (15 min) — surface-context loop.** Open `demo-workspace-interactive`. Ask "what's the current revenue?" — agent answers from context, **zero tool calls**. Show the surface state riding back on `forwardedProps.a2ui_surface_state`. **This is the breakthrough**: the UI is bidirectional, not one-shot.
- Reference the sprint 2.9 + 2.10 design docs for the curious.

### Block 4 — MCP Apps + sandbox (30 min)

**Demo skill:** `demo-map-explorer`

- Show the Cesium 3D globe demo (cloud mode) OR static walkthrough (LOCAL_MODE shows "MCP not available" fallback — explain why: cloud-only demo).
- The **two RPC channels** the MCP Apps spec defines:
  1. **`ui/message`** — synthetic chat turns from the iframe (e.g. "I clicked Munich")
  2. **`ui/update-model-context`** — iframe pushes structured state (current bounds, selected city) into the agent's NEXT-turn context
- The three-turn live demo:
  1. "Show me Munich" — globe renders + iframe context POST 204
  2. "What city is currently centred?" — agent answers "Munich" WITHOUT re-rendering (read from context)
  3. "Now zoom in to its old town" — agent resolves "its" via context, calls geocode + show-map, map re-renders
- **The separate-origin sandbox** at port 3457: explain why iframes need to be on a different origin (`allow-same-origin` on the inner iframe can't read host cookies). **This is production security in action** — the sandbox is the safety net even if a fork's content-review reviewer crashes or misses something (sprint 2.13 defence-in-depth posture).
- 7-gate access control on the `/api/sessions/{id}/iframe-context` endpoint.

### Block 5 — Build your own skill (55 min) ⭐ make-or-break moment

**Three paths** — attendees pick ONE based on what excited them most:

| Path | What they build | What it exercises |
|---|---|---|
| **A. Custom ADK agent** | Fork `demo-researcher`. Change instructions. Add one FunctionTool (e.g. `summarise_url(url: str) -> str`). | ADK + AG-UI (by inheritance) |
| **B. A2UI surface** | Fork `demo-form-builder`. Modify the form schema to match their use case. Add a workspace-pane render. | A2UI + multi-surface |
| **C. MCP integration** | Pick an MCP server from `mcp-servers/...`. Add it to `tool_configs.mcp.servers` on a skill. Smoke-test in chat. | MCP + MCP Apps |

**Time budget (within the 55min):**
```
5min  — Instructor intros the skeleton skill template (single file)
5min  — Choose your path; ask the helper agent any questions
30min — Build (instructors circulate, helper agent answers FAQs)
10min — Submit via helper agent (submit_skill_for_show_and_tell tool)
5min  — 2-3 attendee demos
```

**Skeleton skill** lives at [`skeleton-skill.md`](skeleton-skill.md). One file. ~30 LOC. Attendees copy + modify.

### Block 6 — A2A discovery + close (10 min)

**Two checkpoints** — the honest version of "your skill is in the marketplace":

1. **A2A wire format** (machine-readable):
   ```bash
   curl http://localhost:1956/.well-known/agent.json | jq '.skills'
   ```
   Show the JSON card: the 5 demo skills + the workshop's new skills. **Other AI agents can discover this card and invoke your skill.** Not just decorative — actual protocol contract.

2. **Skills picker** (human-readable):
   - Open the chat UI. Click the skill dropdown. See their own skill listed.
   - Same data source (`list_marketplace()`), different surface.

- **Production patterns** (60-second aside before Q&A) — security + multi-provider + enterprise lessons from a live deployment:
  - Auth dispatcher with 4 modes (Firebase, anonymous-group-id, LOCAL_MODE stub, Identity Platform)
  - Per-cohort budget enforcement (sprint 2.12) — typed `RUN_ERROR{code:"BUDGET_EXCEEDED"}` with retry-after
  - Artefact review hook (sprint 2.13) — defence-in-depth above the iframe sandbox
  - Tenant attribution on every span (sprint 2.14) — PII rule, `tenant.uid_hash` not raw email
  - **"All four ship as sprints — see SEQUENCE.md if you want the worked examples."**
- "Where to take this next" closing slide:
  - Public template at [sunholo-data/ai-protocol-platform](https://github.com/sunholo-data/ai-protocol-platform) — clone with full history
  - Workshop materials at [sunholo-data/build-ai-uis-beyond-chat](https://github.com/sunholo-data/build-ai-uis-beyond-chat) — agenda, code tour, helper-agent design
  - The 4 AIPLA template-extensions (sprints 2.11–2.14) as advanced patterns
  - **Deploying your fork:** the template ships two `cloudbuild.yaml` files because the platform itself runs both Cloud Run topologies. Most forks only need one. [`docs/ops/deployment-models.md`](../ops/deployment-models.md) is the 5-minute read that picks single-service sidecar vs paired sidecar+standalone *before* `git push`, with a removal recipe for the file you don't need. Saved one downstream fork ~6 months of failing CI builds (G44).
  - GitHub Discussions for questions
- Q&A.

---

## Buffer + timing reality

Total: 10 + 10 + 30 + 35 + 30 + 55 + 10 = **180 minutes** (3h flat, no slack).

Realistic schedule expects ~10 min slack across blocks (someone's question, a demo hiccup, a misread instruction). Either:
- Build a 5–10 min break between blocks 3 and 4
- Or accept a 3h 10min finish — fine for "half-day" framing

**Hard floor:** if running late, the cuts in order are:
1. Block 4 to 20 min (skip the third turn of the map demo)
2. Block 3 Part C to 5 min (just show the "zero tool calls" moment, skip the deep explanation)
3. Block 2 to 20 min (skip the DevTools network walkthrough)
4. **NEVER cut block 5** — it's the workshop's value proposition

---

## What makes it amazing (five amplifiers)

1. **Pre-work works.** Attendees arrive with a working platform. Dry-run pre-work on a fresh laptop NOT in the dev environment to catch invisible-to-you issues.
2. **The workshop helper agent IS the meta-demo.** It runs on the platform, uses every protocol, and demonstrates all four AIPLA template-extensions. **Critically: it's preloaded with the workshop's own docs corpus** — this agenda, the [`code-tour`](code-tour.md), the [`protocol-gotchas`](protocol-gotchas.md), every shipped sprint doc in `docs/design/v6.X.Y/implemented/`, every howto in `docs/integrations/`. Attendees can ask it "how does the surface-context loop work?" or "what's the seven-gate access control on iframe-context?" and get the actual design-doc answer, not a hallucinated one. Detailed design in [`helper-agent-design.md`](helper-agent-design.md). **The docs corpus is the demo** — RAG over real design docs proves the "document-centric UI" axiom in real time.
3. **"Watch the wire" moments.** Each protocol block has a DevTools-network demo showing actual bytes. Developers want to see the protocol, not abstractions over it.
4. **Live `/.well-known/agent.json` + skills dropdown finale.** Two checkpoints. Attendees see their public artifact.
5. **Show-and-tell submission via the helper agent.** Workspace pane updates live with submissions. Social momentum.

---

## Materials checklist (instructor)

| Item | Where | Status |
|---|---|---|
| Working `make dev-local` on instructor laptop | local | ✅ verified daily |
| Pre-work email + script | [`pre-work.md`](pre-work.md) | TBD |
| Slides (axioms, stack diagram, "where next") | local | TBD |
| Skeleton skill template | [`skeleton-skill.md`](skeleton-skill.md) | TBD |
| Helper agent live + per-attendee budget configured | platform | TBD (own sprint) |
| 7-file code-tour map | [`code-tour.md`](code-tour.md) | ✅ |
| Fresh-laptop pre-work dry-run | external machine | TBD (one week before) |
| MCP map sandbox running in cloud (block 4 hardware backup) | Cloud Run | ✅ |
| Anonymous-group code printed on slide for helper agent join | slide | TBD |

---

## Related design docs

- [`../talks/ai-ui-protocol-stack.md`](../talks/ai-ui-protocol-stack.md) — the protocol overview deck
- [`code-tour.md`](code-tour.md) — what to read after the workshop
- v6.2.0 SEQUENCE.md — the four AIPLA template-extensions referenced as "advanced patterns" in block 6
