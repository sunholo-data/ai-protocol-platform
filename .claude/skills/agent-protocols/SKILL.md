---
name: agent-protocols
description: Disambiguates the four-protocol stack (AG-UI / A2UI / MCP / MCP Apps / Agent Skills) with vendored offline specs. Load when writing design docs, implementing a new protocol surface, or verifying spec compliance.
triggers:
  - "which protocol"
  - "A2UI or MCP"
  - "what's the difference"
  - "ag-ui spec"
  - "a2ui spec"
  - "mcp apps spec"
  - "agent skills spec"
  - "protocol stack"
  - "MCP Apps vs"
  - "what protocol should"
  - "ag-ui architecture"
---

# Agent Protocols Reference

Vendored offline specs for the platform's four-protocol stack. Use these instead of fetching from external sources so design docs are written from current spec versions, not stale training data.

## Protocol Decision Table

| Need | Use |
|------|-----|
| Stream text + tool calls from agent to frontend | **AG-UI** — `RunAgentInput` → SSE events |
| Render structured UI (tables, cards, forms) inside the chat | **A2UI** — `send_a2ui_json_to_client` tool, declarative JSON |
| Embed an interactive app in a sandboxed iframe | **MCP Apps** — `load_mcp_app` tool, postMessage protocol |
| Discover agents on other services | **A2A** — agent card, `/.well-known/agent.json` |
| Connect external tools to the agent | **MCP** — `McpToolset`, MCP server over stdio or SSE |
| Define a reusable skill that other agents can load | **Agent Skills spec** — `SKILL.md` frontmatter |

## Quick Disambiguation

**AG-UI vs A2UI**: AG-UI is the transport (streaming protocol between agent and browser). A2UI is the rendering layer (what the agent returns inside an AG-UI turn to get structured UI). You need both: AG-UI carries the A2UI payload.

**A2UI vs MCP Apps**: A2UI is stateless declarative rendering (good for data tables, readonly forms, charts). MCP Apps are stateful sandboxed iframes (good for interactive editors, authenticated apps, maps). A2UI renders inside the message bubble; MCP Apps render in the workspace panel.

**MCP (tools) vs MCP Apps (iframes)**: MCP (Model Context Protocol) is the tool-invocation protocol — the agent calls a tool via MCP. MCP Apps is a completely separate concept: a sandboxed iframe that an agent can open as a UI surface. They share "MCP" in the name for historical reasons.

**AG-UI vs A2A**: AG-UI is user↔agent communication (frontend → agent). A2A (Agent-to-Agent) is agent↔agent discovery and delegation. A2A uses a static "agent card" served at `/.well-known/agent.json`.

## Spec Files (in `references/`)

Run `scripts/refresh-specs.sh` to populate these from authoritative sources.

| File | Content | Source |
|------|---------|--------|
| `ag-ui-architecture.md` | AG-UI protocol overview + event types | ag-ui.com/introduction |
| `ag-ui-events.md` | Full event reference (RUN_STARTED, TEXT_MESSAGE_*, etc.) | ag-ui.com/concepts/events |
| `ag-ui-tools.md` | Tool call protocol | ag-ui.com/concepts/tools |
| `a2ui-v0.10-protocol.md` | A2UI spec (component schema, tools, hooks) | a2ui.org |
| `a2ui-v0.9-basic-catalog.md` | **A2UI Basic-catalog component vocabulary** (Tabs/Column/Card/List/ChoicePicker/… + props). Read BEFORE designing an A2UI surface. | a2ui==0.9.x |
| `mcp-architecture.md` | MCP architecture overview | modelcontextprotocol.io |
| `mcp-apps-spec-2026-01-26.md` | MCP Apps SEP-1865 (sandbox, postMessage envelope, CSP) | internal |
| `agent-skills-spec.md` | Agent Skills spec (SKILL.md frontmatter schema) | agentskills.io/specification |

## Common Mistakes

1. **Mixing `app_name` with skill IDs**: The ADK app_name is always `APP_NAME = "aitana_platform"`. Never use a skill ID or agent name as `app_name` in session service calls.

2. **Rendering A2UI in an MCP App and vice versa**: A2UI JSON goes through `send_a2ui_json_to_client` → AG-UI → `useAGUIAgent`. MCP Apps are opened via `load_mcp_app` tool → workspace panel. They are separate rendering paths.

3. **postMessage envelope in MCP Apps**: The iframe and parent window communicate via a specific `{ type, payload }` envelope over `window.postMessage`. The sandbox's `allow-same-origin` flag is required for `ui/update-model-context`. See `mcp-apps-spec-2026-01-26.md` for the exact shape.

4. **CSP for MCP App iframes**: The sandbox URL must serve its own CSP headers. The `sandbox` attribute on the iframe must include `allow-scripts allow-forms allow-same-origin`. Missing `allow-same-origin` breaks postMessage cross-frame reads.

5. **AG-UI `forwardedProps` vs `state`**: `body.state` mirrors `STATE_SNAPSHOT` — it's one event behind per turn. Per-turn signals (current doc context, surface state snapshot) go on `forwardedProps`, not `state`.

## Refreshing Specs

```bash
.claude/skills/agent-protocols/scripts/refresh-specs.sh
```

Run quarterly or whenever a spec version bumps. The script fetches from authoritative URLs and writes into `references/`. Commit the result.
