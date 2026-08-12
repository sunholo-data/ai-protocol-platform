# A2UI Tool Delivery

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 1 day
**Scope**: Fullstack
**Dependencies**: chat-message-rendering (v6.1.0) — must land before or alongside M3 of that sprint; `google-adk>=1.28.0` (installed: 1.29.0 ✅)
**Created**: 2026-04-24
**Last Updated**: 2026-05-18 — extended in v6.2.0 sprint 2.9 [multi-surface-rendering.md](../../v6.2.0/multi-surface-rendering.md). The `send_a2ui_json_to_client` result envelope now optionally carries `surface_id` + `update_mode` so skills can target named A2UI surfaces (workspace, sidebar, modal) instead of always rendering inline-in-chat. See the [skill-author howto](../../../integrations/multi-surface-rendering.md).

## Problem Statement

**Current State:**

A2UI components are delivered via a prompt convention: `A2UI_INSTRUCTION_SUFFIX` appended to every skill instruction tells the LLM to wrap component JSON in ` ```a2ui ``` ` fenced code blocks. On `TEXT_MESSAGE_END`, `extractA2UISegments` regex-splits the completed message text to find these fences.

**Problems with this approach:**

- **False positives**: Triple-backtick fences are common in code examples the agent might include in its response. Any ` ```a2ui ` fence from an agent writing about A2UI is indistinguishable from a real component.
- **No streaming parser**: The split only runs on `TEXT_MESSAGE_END` — components can't begin rendering until the entire message has arrived. A large response with an A2UI table at the end makes the user wait for the full token stream before any structured UI appears.
- **Schema hallucination**: Nothing validates the JSON payload until `A2UIRenderer` tries to parse it. The LLM can hallucinate a schema mismatch; the only guard is the `<pre>{JSON}</pre>` fallback in the renderer.
- **Fragile to prompt drift**: The instruction suffix must be accurate and present in every skill. If a skill's instruction is long, the LLM may ignore or partially follow the suffix. No SDK-managed schema injection.
- **Custom convention**: This is a format we invented. It violates Axiom #6 — `a2ui-agent-sdk` ships a first-party ADK integration that solves exactly this problem.

**Impact:**

- `chat-message-rendering.md` (v6.1.0) is about to build `MessageBubble`'s A2UI wiring on top of `extractA2UISegments`. If we ship that sprint against the fenced-block approach, we'll have two layers to rip out later instead of one.
- The workshop W6 module currently presents fenced blocks as the approach — this is the moment to show the published solution.

## Goals

**Primary Goal:** Replace the fenced-block prompt convention with `SendA2uiToClientToolset` from `a2ui-agent-sdk`. A2UI JSON travels via `TOOL_CALL_*` AG-UI events — the same channel as every other structured tool result — rather than embedded in text.

**Success Metrics:**
- `A2UI_INSTRUCTION_SUFFIX` and `_compose_instruction()` deleted from `backend/adk/agent.py`
- `extractA2UISegments` and `extractMarkers.ts` deleted (or reduced to XML-tag fallback only)
- A skill with `SendA2uiToClientToolset` emits `TOOL_CALL_START` / `TOOL_CALL_END` (tool name: `send_a2ui_json_to_client`) when it decides to render a component — visible in the raw SSE stream
- Frontend `MessageBubble` routes `send_a2ui_json_to_client` tool results to `A2UIRenderer` — no text parsing
- `A2UIRenderer` receives validated JSON (the toolset validates before returning `validated_a2ui_json`)
- All existing A2UI renderer tests still pass; new integration test covers the tool-call delivery path

**Non-Goals:**
- Implementing `A2uiEventConverter` / `A2uiPartConverter` (those are for A2A output, not our AG-UI stack)
- Replacing `A2UIRenderer` or `A2UIViewer` — just changing how A2UI JSON reaches them
- Authoring custom component catalogs (Phase 1B+)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Tool-call streaming means A2UI args arrive via `TOOL_CALL_ARGS` delta events — components can begin rendering before the call completes, not waiting for `TEXT_MESSAGE_END` |
| 2 | EARNED TRUST | 0 | No change to factual claims or citations |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to skill authors and end users |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No change to model selection |
| 5 | GRACEFUL DEGRADATION | +1 | Schema validation at the tool layer (toolset rejects invalid JSON before it reaches the renderer); fallback path to plain text if tool is not called |
| 6 | PROTOCOL OVER CUSTOM | +1 | Deletes our invented fenced-block convention; adopts `a2ui-agent-sdk`'s first-party ADK toolset |
| 7 | API FIRST | 0 | No API surface change — tool calls are already part of the AG-UI contract |
| 8 | OBSERVABLE BY DEFAULT | +1 | `send_a2ui_json_to_client` calls appear as named tool events in traces — debuggable, not invisible in a text diff |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data surface; toolset validates JSON structure, reducing renderer attack surface |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend drops text-parsing logic; schema validation moves to the backend toolset |
| | **Net Score** | **+5** | Threshold: >= +4 ✓ |

## Design

### Overview

`SendA2uiToClientToolset` (from `a2ui-agent-sdk`, `@experimental`) is added to every skill agent via `create_agent()`. It registers a single tool with the LLM: `send_a2ui_json_to_client(a2ui_json: str)`. When the model decides structured UI would help, it calls this tool. The toolset validates the JSON payload and returns `{"validated_a2ui_json": <payload>}`. ADK emits `TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END` AG-UI events — the same events as any other tool. The frontend routes the tool result to `A2UIRenderer` by name.

```
LLM decides to show a form
    │
    └── calls: send_a2ui_json_to_client(a2ui_json='{"component":"form",...}')
                    │
             [SendA2uiToClientToolset validates JSON]
                    │
             ADK emits TOOL_CALL_START
             ADK emits TOOL_CALL_ARGS (streaming)
             ADK emits TOOL_CALL_END {validated_a2ui_json: {...}}
                    │
             AG-UI stream → frontend
                    │
             ToolCallChip sees tool name "send_a2ui_json_to_client"
                    │
             → A2UIRenderer (no text parsing, no regex, no fences)
```

### Backend Changes

**1. Add `a2ui-agent-sdk` dependency**

```toml
# backend/pyproject.toml
"a2ui-agent-sdk>=0.2.1",
# Note: requires google-adk>=1.28.0 (we have 1.29.0)
# Bump lower bound:
"google-adk>=1.28.0,<2.0.0",
```

**2. Build the A2UI toolset (new helper in `backend/adk/a2ui.py`)**

The toolset constructor requires `a2ui_enabled`, `a2ui_catalog`, and `a2ui_examples`. All three accept either a static value or an async callable `(ReadonlyContext) -> <type>`.

> **Catalog loading note:** `A2uiCatalog` is loaded from schema files bundled with the `a2ui` package. The exact path is verified during implementation (spike: `python -c "import a2ui; print(a2ui.__file__)"`). `CatalogConfig.from_path(name, catalog_path)` builds a `FileSystemCatalogProvider`; pass the result as `a2ui_catalog`. If the SDK ships a convenience loader, use it instead.

```python
# backend/adk/a2ui.py
"""A2UI toolset factory — creates SendA2uiToClientToolset for skill agents."""
from __future__ import annotations

from a2ui.adk.send_a2ui_to_client_toolset import SendA2uiToClientToolset
from a2ui.schema.catalog import A2uiCatalog, CatalogConfig

# Loaded once at import time — catalog is immutable schema data.
# Path verified during implementation: locate via importlib.resources or a2ui.__file__.
_CATALOG: A2uiCatalog | None = None


def _get_catalog() -> A2uiCatalog:
    """Return the cached default A2UI catalog (basic/standard component set)."""
    global _CATALOG
    if _CATALOG is None:
        import importlib.resources
        catalog_path = str(importlib.resources.files("a2ui") / "schema" / "catalog.json")
        config = CatalogConfig.from_path(name="basic", catalog_path=catalog_path)
        # Provider loads the actual catalog; verify exact call in implementation spike.
        _CATALOG = config.provider()  # type: ignore[call-arg]
    return _CATALOG


def make_a2ui_toolset() -> SendA2uiToClientToolset:
    """Return a SendA2uiToClientToolset using the basic component catalog."""
    return SendA2uiToClientToolset(
        a2ui_enabled=True,
        a2ui_catalog=_get_catalog(),
        a2ui_examples="",  # Examples optional — expand in Phase 1B for better LLM guidance
    )
```

> **Implementation spike required:** confirm exact `A2uiCatalog` construction from installed package files. The API is marked `@experimental` — verify constructor and provider call pattern against installed `a2ui-agent-sdk==0.2.1` source before shipping.

**3. Wire into `create_agent()` (`backend/adk/agent.py`)**

```python
# In create_agent(), add to the tools list:
from adk.a2ui import make_a2ui_toolset

tools = [retrieve_artifact, *resolve_tools(md.tools, md.tool_configs)]
tools.extend(_resolve_search_tools(...))
tools.extend(resolve_mcp_tools(...))
tools.append(make_a2ui_toolset())  # ← NEW: replaces A2UI_INSTRUCTION_SUFFIX
```

**4. Delete the fenced-block convention**

- Delete `A2UI_INSTRUCTION_SUFFIX` constant (lines ~92–113 in `agent.py`)
- Delete `_compose_instruction()` (line ~110)
- Replace `_compose_instruction(skill_config.instructions)` in `create_agent()` with `skill_config.instructions` directly

### Frontend Changes

**1. Route `send_a2ui_json_to_client` tool results to `A2UIRenderer`**

In `MessageBubble` (being built by `chat-message-rendering.md`), the `ToolCallChip`/tool-result handler checks the tool name:

```typescript
// In MessageBubble or ToolCallChip — wherever TOOL_CALL_END is handled
const A2UI_TOOL_NAME = "send_a2ui_json_to_client";

function renderToolResult(toolName: string, result: unknown) {
  if (toolName === A2UI_TOOL_NAME) {
    const payload = (result as { validated_a2ui_json?: unknown }).validated_a2ui_json;
    return <A2UIRenderer spec={payload} onAction={onAction} />;
  }
  // ... other tools → ToolCallChip
}
```

**2. Delete `extractA2UISegments` and `extractMarkers.ts`**

`MessageBubble` no longer needs to split text on `TEXT_MESSAGE_END`. Remove:
- `frontend/src/lib/a2ui/extractMarkers.ts`
- `frontend/src/lib/a2ui/__tests__/extractMarkers.test.ts`
- Any import of `extractA2UISegments` in `MessageBubble` or `ChatMessageList`

**3. `A2UIRenderer` wiring in `MessageBubble` simplified**

The bubble body becomes pure markdown — no segment array, no `kind: "a2ui"` branching. A2UI renders separately, inline as a tool result, not embedded in text.

### XML Tag Fallback (optional, Phase 1B)

For multi-provider environments where tool calling is unreliable (rare with Gemini/Claude), `a2ui-agent-sdk` provides a text-parsing strategy using `<a2ui-json>` XML tags:

```python
# a2ui-agent-sdk's SchemaManager (text path) uses:
A2UI_OPEN_TAG  = "<a2ui-json>"
A2UI_CLOSE_TAG = "</a2ui-json>"
# parse_response_to_parts() handles partial chunks at SSE boundaries
```

This is **not in scope for this sprint** — tool calling is reliable on Gemini 2.5 and Claude 3.x. If a future model requires it, swapping from the toolset path to the XML path is a backend-only change.

### Sequence relative to `chat-message-rendering`

This doc's work **must precede or be concurrent with** `chat-message-rendering` Phase 3 (A2UI + MCP App wiring). `MessageBubble` should be built assuming tool-call delivery from the start:
- Phase 1 of chat-message-rendering (core bubbles): no A2UI wiring yet — no dependency
- Phase 2 of chat-message-rendering (tool chips): `ToolCallChip` built — can wire `send_a2ui_json_to_client` routing here
- **This doc's work lands here** — before or same PR as chat-message-rendering Phase 3
- Phase 3 of chat-message-rendering (A2UI wiring): just routes the tool name, no regex

### API Changes

No new HTTP endpoints. Tool calls are part of the existing AG-UI SSE stream. `send_a2ui_json_to_client` will appear in traces as a named tool invocation — already observable.

### Architecture Diagram

```
Before (fenced block):                    After (tool call):

Agent text stream:                        Agent tool call:
  "Here is the form:\n```a2ui\n..."         send_a2ui_json_to_client(a2ui_json="...")
         │                                           │
  TEXT_MESSAGE_END                           TOOL_CALL_START/ARGS/END
         │                                           │
  extractA2UISegments (regex)                ToolCallChip sees tool name
         │                                           │
  A2UIRenderer (may get                     A2UIRenderer (gets validated
  hallucinated schema)                      JSON — toolset rejected invalid)
```

## Implementation Plan

### Phase 1 — Backend (~0.5 day)
- [x] `uv add a2ui-agent-sdk>=0.2.1` + pin `a2a-sdk<1.0.0` (1.0.x dropped types needed by a2ui-agent-sdk)
- [x] Bump `google-adk` lower bound to `>=1.28.0` in `pyproject.toml`
- [x] Spike: `BasicCatalog.get_config("0.9")` + `A2uiSchemaManager` is the correct catalog path
- [x] Write `backend/adk/a2ui.py` with `make_a2ui_toolset()`
- [x] Wire into `create_agent()` — add toolset, delete `_compose_instruction` / `A2UI_INSTRUCTION_SUFFIX`
- [ ] Backend test: agent with `SendA2uiToClientToolset` emits `TOOL_CALL_*` events for an A2UI prompt

### Phase 2 — Frontend (~0.25 day)
- [x] Delete `extractMarkers.ts` + tests
- [x] In `MessageBubble`: route `send_a2ui_json_to_client` → `A2UIRenderer`; other tools → `ToolCallChip`
- [ ] Frontend test: `TOOL_CALL_END` with `send_a2ui_json_to_client` result renders `A2UIRenderer`

### Phase 3 — Integration + cleanup (~0.25 day)
- [ ] Smoke test: send a prompt that triggers A2UI through the full stack — confirm `A2UIRenderer` renders
- [x] Confirm `extractA2UISegments` is no longer imported anywhere
- [x] Confirm `A2UI_INSTRUCTION_SUFFIX` is gone from all skill instructions in traces
- [x] Update workshop W6 notes in `docs/talks/workshop.md` to reflect the tool-call delivery path + gotchas

## Migration & Rollout

**No data migration** — prompt convention change only. Sessions in progress may briefly emit fences (if a session was created with the old instruction) — `A2UIRenderer` fallback (`<pre>`) handles gracefully.

**Rollback:** Revert `create_agent()` changes and restore `_compose_instruction`. Zero Firestore changes.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `test_a2ui_tool_delivery.py`: agent with `SendA2uiToClientToolset` + prompt "show me a form" → ADK Runner emits an event with tool name `send_a2ui_json_to_client`
- [ ] `test_a2ui_tool_delivery.py`: `validated_a2ui_json` in tool result is a valid dict (not a string)
- [ ] `test_a2ui_tool_delivery.py`: invalid JSON passed to tool → toolset raises / returns error (not swallowed)
- [ ] Existing agent factory tests still pass (`test_agent_factory.py`)

### Frontend Tests (Vitest)
- [ ] `MessageBubble` renders `A2UIRenderer` when `TOOL_CALL_END` tool name matches `send_a2ui_json_to_client`
- [ ] `MessageBubble` renders `ToolCallChip` (not `A2UIRenderer`) for any other tool name
- [ ] No `extractA2UISegments` calls remain in the component tree

### Integration
- [ ] Raw curl of `/api/skill/<id>/stream` with A2UI-triggering prompt → `TOOL_CALL_START` event visible with `name: "send_a2ui_json_to_client"` before `TEXT_MESSAGE_END`

## Security Considerations

- The toolset validates JSON structure before returning `validated_a2ui_json` — reduces the chance of `A2UIRenderer` receiving an attacker-controlled schema via prompt injection
- A2UI JSON never embeds scripts; `A2UIRenderer` renders declarative component specs only
- No new data egress — all processing backend-side within the GCP project

## Performance Considerations

- One extra tool registration per agent — negligible at construction time (cached `A2uiCatalog` object)
- `render_as_llm_instructions()` adds token overhead to the system prompt (schema description). This replaces `A2UI_INSTRUCTION_SUFFIX` token cost — net change should be similar or better since the schema is precise.
- Components can begin streaming via `TOOL_CALL_ARGS` deltas — lower perceived latency vs. waiting for `TEXT_MESSAGE_END`

## Success Criteria

- [x] `A2UI_INSTRUCTION_SUFFIX` and `_compose_instruction()` deleted
- [x] `extractMarkers.ts` deleted
- [x] All backend tests passing (423 passed, 0 failed)
- [x] All frontend tests passing (lint + typecheck clean)
- [ ] Raw SSE stream shows `TOOL_CALL_START {name: "send_a2ui_json_to_client"}` for an A2UI-triggering prompt
- [ ] `A2UIRenderer` renders a component driven by the tool result (no text parsing)
- [x] `chat-message-rendering` Phase 3 can be written without any fenced-block logic

## Open Questions

- **`A2uiCatalog` construction**: exact path to bundled schema files in installed `a2ui-agent-sdk` package — resolve in implementation spike.
- **`a2ui_examples`**: `SendA2uiToClientToolset` takes `a2ui_examples: str`. Start with `""` and measure whether tool-calling accuracy suffers without examples. If it does, add 2–3 form/table examples in Phase 1B.
- **`a2ui_enabled`**: accepts a static `bool` or an async `(ReadonlyContext) -> bool`. Start with `True` (always enabled). Per-skill opt-out possible later via skill config flag.
- **`@experimental` stability**: `SendA2uiToClientToolset` is marked experimental in 0.2.1. Pin exact version; watch `google/a2ui` releases for breaking changes before the July workshop.

## Related Documents

- [chat-message-rendering.md](chat-message-rendering.md) — `MessageBubble` A2UI wiring; depends on this doc
- [streaming-and-protocols.md](../v6.0.0/implemented/streaming-and-protocols.md) — AG-UI event taxonomy
- [agent-factory.md](../v6.0.0/implemented/agent-factory.md) — `create_agent()` where toolset is added
- [Workshop W6](../../talks/workshop.md#module-w6--a2ui-declarative-ui-15-min) — where this migration is the teaching moment
- [local-dev-cli.md](local-dev-cli.md) — no CLI surface needed for this feature
- [Product Axioms](../../product-axioms.md)

## Sources

- [`a2ui-agent-sdk` on PyPI](https://pypi.org/project/a2ui-agent-sdk/) — v0.2.1, requires `google-adk>=1.28.0` and `a2a-sdk<1.0.0` (see below)
- [`SendA2uiToClientToolset` source](https://github.com/google/a2ui/blob/main/agent_sdks/python/src/a2ui/adk/send_a2ui_to_client_toolset.py) — constructor, `_SendA2uiJsonToClientTool` inner class, `TOOL_NAME = "send_a2ui_json_to_client"`
- [`A2uiCatalog` / `CatalogConfig` source](https://github.com/google/a2ui/blob/main/agent_sdks/python/src/a2ui/schema/catalog.py) — `CatalogConfig.from_path()`, `render_as_llm_instructions()`
- [A2UI v0.9 blog post](https://developers.googleblog.com/a2ui-v0-9-generative-ui/) — "Basic" component set (renamed from Standard), Python agent SDK
- Research finding (2026-04-24): structured output mode (OpenAI/Anthropic/Gemini) forces entire response to JSON — cannot mix prose + structured data in one generation. Tool calls are the correct decomposition.
- Research finding (2026-04-24): `a2ui-agent-sdk` text-path fallback uses `<a2ui-json>` XML tags (not fenced blocks) with a streaming chunk-boundary parser in `streaming.py`
- Implementation finding (2026-04-23): `a2a-sdk 1.0.0` (released 2026-04-20) dropped `DataPart` and `TextPart` — all parts unified into a single `Part` message (protobuf). `a2ui-agent-sdk 0.2.1` imports both — pin `a2a-sdk<1.0.0` in `pyproject.toml`. The broken import is in `a2ui/a2a/parts.py` (used by `A2uiPartConverter` for A2A output, out of scope for our AG-UI path). No fix in flight as of 2026-04-23: no open PR in `google/a2ui`; `google/adk-python` issue #5056 tracks the same ecosystem gap. Watch [PyPI](https://pypi.org/project/a2ui-agent-sdk/#history) for `a2ui-agent-sdk 0.3.x`.
- Implementation finding (2026-04-23): `BasicCatalog.get_config("0.9")` + `A2uiSchemaManager(version="0.9", catalogs=[config])` is the correct catalog construction path; `manager._supported_catalogs[0]` yields the `A2uiCatalog`. The `CatalogConfig.from_path()` approach in the design doc draft was not needed — the bundled catalog provider handles the path resolution.

---

## Implementation Report

**Completed**: 2026-04-24
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
