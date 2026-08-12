# Workshop Code Tour — 7 files, ~1,900 LOC

This is the reading map for understanding how the protocol stack actually works in code. The platform is ~13k backend LOC + ~9k frontend LOC — most of which is production scaffolding (auth dispatchers, session persistence, OpenTelemetry, channels framework). **The protocol-interaction code itself is small.** This tour points at the files that matter.

**Recommended order:** start at #1 (smallest, sets the mental model), follow the path down. Each file's commentary calls out the "if you only read N lines, read these."

## The 7 files

| # | File | LOC | What it teaches |
|---|---|---|---|
| 1 | [`backend/app.py`](../../backend/app.py) | 61 | The simplest ADK agent that exists |
| 2 | (Firestore seed) — `backend/db/local_fixture.py` `demo-researcher` block | ~30 | What a skill looks like as data |
| 3 | [`backend/adk/agent.py`](../../backend/adk/agent.py) | 488 | How a skill becomes a runnable LlmAgent |
| 4 | [`backend/skills/skill_processor.py`](../../backend/skills/skill_processor.py) | 331 | Where AG-UI events come from |
| 5 | [`frontend/src/hooks/useSkillAgent.ts`](../../frontend/src/hooks/useSkillAgent.ts) | 463 | How the frontend reads the event stream |
| 6 | [`backend/protocols/a2a.py`](../../backend/protocols/a2a.py) | 135 | Discovery in 135 lines |
| 7 | [`backend/protocols/mcp_proxy.py`](../../backend/protocols/mcp_proxy.py) | 386 | Frontend ↔ MCP server boundary |

**Total: ~1,900 LOC across 7 files.** Readable in a long evening.

---

## 1. `backend/app.py` (61 LOC) — start here

The root agent. The simplest possible ADK agent: name, model, instruction, that's it. No tools, no sub-agents, no callbacks.

**Read it as:** "If I wanted to write the absolute minimum agent that streams text in response to messages, this is what I'd write."

The platform builds on this. Every skill is some variation of this same shape, with more bells attached (tools, multi-surface UI, MCP, etc.). But the core agent is *this*.

---

## 2. Demo skill seed (`backend/db/local_fixture.py` — `demo-researcher` entry, ~30 LOC)

Skills are **data**, not code. The agent factory reads a `SkillConfig` Pydantic model out of Firestore (or the in-memory fixture in LOCAL_MODE) and builds an agent from it.

Look at the `demo-researcher` block (around line 140 in `local_fixture.py`). The fields you care about:

```python
{
  "skillId": "demo-researcher",
  "displayName": "Demo Researcher",
  "description": "...",        # Shown in skills picker, in A2A card
  "instructions": "...",       # System prompt for the agent
  "model": "gemini-2.5-flash", # Or claude-sonnet, gpt-4o, etc.
  "tools": [],                 # FunctionTool names to attach
  "toolConfigs": { ... },      # Per-tool config (A2UI, MCP, budget, etc.)
}
```

**This is what attendees fork in block 5.** Change instructions + add one tool entry = new skill.

---

## 3. `backend/adk/agent.py` (488 LOC) — the agent factory

The biggest file in the tour, but you only need to read **two functions** to understand the protocol layering:

### `create_agent()` (lines ~270–426)

Reads a `SkillConfig`, returns a runnable `LlmAgent`. The body shows what's plugged in by the platform that the skill author gets for free:

- Default tools every skill gets: `load_artifacts_tool`, `retrieve_artifact`, `load_memory_tool`, `preload_memory_tool`
- Tools resolved from `tool_configs.tools` + `tool_configs.mcp.servers`
- A2UI toolset from `make_a2ui_toolset(config=a2ui_cfg)` (the `SendA2uiToClientToolset` that emits UI specs as tool results)
- Sub-skills (skills can delegate to other skills)
- The callback chain: `before_agent`, `before_model`, `after_agent`, `before_tool`, `after_tool`

### `_composed_before_model` (added in sprint 2.12)

Shows how the platform's middleware composes. The document injector runs FIRST, then the budget gate. Sprint 2.13's artefact-review-hook would slot in similarly.

**Mental model:** the agent factory is where "skill config" becomes "runnable thing." The factory orchestrates the protocol layers without the skill author having to.

---

## 4. `backend/skills/skill_processor.py` (331 LOC) — where AG-UI events come from

The per-turn handler. `POST /api/skill/{id}/stream` lands here.

**Read it as a story:**

1. Look up the skill (404 if missing or not visible)
2. Build the agent via `create_agent_with_thinking(skill, user)` (the factory from file #3)
3. Wrap with `ag_ui_adk.ADKAgent` — this is what turns ADK events INTO AG-UI events
4. Construct `RunAgentInput` (the AG-UI wire shape)
5. `async for event in stream_agui_events(...)` — yield each event to the SSE response

**The key insight:** the `ag_ui_adk` library is what bridges ADK (Google's agent framework) to AG-UI (the open streaming protocol). We don't reinvent the protocol — we use the canonical adapter.

Also notice the `BudgetExceededError` and `ClientError` handlers — they translate exceptions into typed `RUN_ERROR` events. That's how the AG-UI protocol surfaces errors without breaking the stream.

---

## 5. `frontend/src/hooks/useSkillAgent.ts` (463 LOC) — how the frontend reads AG-UI

This is the AG-UI subscription. **The headline:** ~30 lines of `agent.subscribe({...})` define how every chat UI feature emerges from the event stream.

### `agent.subscribe(...)` (lines ~170–290)

Each callback maps one AG-UI event type to React state:

- `onRunStartedEvent` → flip `isLoading=true`
- `onTextMessageContentEvent` → append delta to current message
- `onToolCallStartEvent` → add tool-call indicator
- `onToolCallResultEvent` → mark tool complete
- `onRunFinalized` → flip `isLoading=false`
- `onRunFailed` → classify error (network, run_error, budget_exceeded), set banner state

**This is the entire chat UI's reactive plumbing.** 16 event types, ~16 callbacks. No polling, no EventSource fiddling, no custom protocol.

### `classifyRunError()` (sprint 2.12 + later)

Shows how typed error events surface as UI surfaces. Sprint 2.12's `BudgetExceededError` becomes the `BudgetBanner` component via this classifier branch.

---

## 6. `backend/protocols/a2a.py` (135 LOC) — A2A discovery

The smallest file in the tour, and arguably the highest leverage. A2A is the protocol that lets OTHER agents discover this platform's skills.

**Read every line.** It's:
- `_skill_to_a2a()` (12 LOC) — `SkillConfig` → A2A skill dict
- `_build_card()` (24 LOC) — the full agent card
- An `lru_cache` + time-bucket pattern (15 LOC) — 60s caching without a scheduler
- One FastAPI route (10 LOC) — the `/.well-known/agent.json` endpoint

The `_time_bucket()` pattern is worth pointing out at the workshop: **rotating cache key based on `time.time() // TTL`** gives you time-bounded caching with zero infrastructure. No scheduler, no background thread, no Redis. Same trick works in dozens of contexts.

Try it live during block 6:
```bash
curl http://localhost:1956/.well-known/agent.json | jq '.skills | length'
```

---

## 7. `backend/protocols/mcp_proxy.py` (386 LOC) — frontend ↔ MCP boundary

The auth + allowlist boundary for MCP traffic. Frontend's MCP `Client` connects HERE, not directly to upstream MCP servers.

**Two reasons** the proxy exists (cited in the docstring):
1. **Auth boundary** — user's Firebase JWT verifies HERE; upstream MCP server never sees it. Servers carry their own auth via per-server `headers` config.
2. **Per-skill allowlist** — caller can only reach MCP servers referenced by a skill they have access to.

**Key functions:**
- `_forward()` (lines ~127–250) — the shared auth + forward path for POST/GET/DELETE
- `_maybe_review_artefact()` (sprint 2.13, lines ~250–end) — optional content-review hook for `resources/read` responses with `text/html` content

The proxy itself is a dumb forwarder by default — it doesn't parse JSON-RPC. Sprint 2.13's artefact reviewer adds optional inspection, but only when a fork registers one. **Back-compat by construction.**

---

## What you can skip (the other 11k+ LOC)

Categories of code that you don't need to read to understand the protocol stack:

- **Auth dispatchers** (`backend/auth/__init__.py`, `firebase_auth.py`, `local_mode_stub.py`, `group_id_auth.py`) — 4 auth modes; mostly Firebase JWT verification + the token-shape dispatcher. Read if you care about WHO can call the platform.
- **Session services** (`backend/adk/session.py`) — Firestore + Vertex Memory Bank integration. Read if you care about how chats persist.
- **OpenTelemetry** (`backend/observability/`) — tracing, latency markers, tenant attribution. Read if you care about production observability.
- **Channels framework** (`backend/channels/`) — Telegram/Email/Discord/WhatsApp adapters. Read if you care about non-web ingress.
- **Budget enforcement** (`backend/budget/`) — sprint 2.12. Read if you care about per-tenant cost gating.
- **Artefact review hook** (`backend/protocols/artefact_review.py`) — sprint 2.13. Read if you care about content-review patterns above the iframe sandbox.
- **Tests** — `backend/tests/` has 1,293 tests. Read specific ones if you want to see "how do they verify X." Don't read top-to-bottom.

These are all **production scaffolding**, not protocol code. They make the platform shippable; they're not what the workshop is about.

---

## Reading paths by interest

| Interest | Path |
|---|---|
| **Just want to ship a skill** | #1 → #2 → done. The skill config + agent factory hide the rest. |
| **Care about how chat UI actually works** | #5 → #4. AG-UI events → frontend state. |
| **Care about A2A discovery** | #6 — read every line. |
| **Care about MCP integration** | #7 + the MCP server registry at `backend/tools/mcp/registry.py`. |
| **Care about how the platform layers** | #3 + the design docs in `docs/design/v6.0.0/implemented/` (agent-factory.md, streaming-and-protocols.md). |

---

## After the workshop

- The full public template at `sunholo-data/ai-protocol-platform` has the same code, same tests, no Aitana-internal bits.
- Each sprint in `docs/design/v6.0.0/implemented/` + `v6.1.0/implemented/` + `v6.2.0/implemented/` is a small focused design doc with a sprint plan — they're worth reading in their own right as examples of how to design backwards-compatible protocol extensions.
- The four AIPLA template-extensions (sprints 2.11–2.14) are the most polished worked examples: each ships in <1 day, follows the same Protocol + ref impl + tests + howto pattern, and has a sprint-evaluator report.
- **When you deploy your fork:** [`docs/ops/deployment-models.md`](../ops/deployment-models.md) — picks single-service sidecar (default for most forks) vs paired sidecar+standalone (only if you run channel webhooks, external A2A crawlers, or external MCP callers) *before* first push, with a removal recipe for the unused `cloudbuild.yaml`. Workshop ran locally in `LOCAL_MODE=1` so this never came up, but it's the first decision on day one of operating your fork.
