# Agent Factory

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 4.5 days
**Scope**: Backend
**Dependencies**: [Skills Data Model](skills-data-model.md), [Auth & Permissions](../auth-and-permissions.md) (landed 2026-04-17)
**Created**: 2026-04-10
**Last Updated**: 2026-04-21

## Reconciliations (2026-04-21)

Resolving four gaps between this doc and the code that has landed since the first draft:

1. **Config path** — the doc's `skill_config.agent.model` / `.tools` / `.subSkills` / `.toolConfigs` does not exist. The real Pydantic shape is `skill_config.skill_metadata.*` (see [backend/db/models/__init__.py:28-37](../../../backend/db/models/__init__.py#L28-L37)). The instruction lives on `skill_config.instructions`, not `.agent.instruction`. Code samples below use the correct path.
2. **`_before_tool` already landed** in AUTH-PERMISSIONS M3: [backend/adk/callbacks.py:20](../../../backend/adk/callbacks.py#L20) exports `make_permission_enforcer(user_email, user_domain)` that returns a closure-bound `before_tool_callback`. The factory reuses it — this sprint does not re-implement the permission check.
3. **Tools are stubs** — real tool ports (ai_search, file_browser, …) land in the tools-porting sprint (1A.3). For this sprint, `TOOL_REGISTRY` holds placeholder `FunctionTool` instances that return mock data. Registry shape is the same; only the underlying callables swap.
4. **Sessions are in-memory** — Firestore-backed session service lands in session-and-memory (1A.4). For this sprint, `process_skill_request()` uses `InMemorySessionService` so the streaming endpoint works end-to-end without waiting on 1A.4.

## Problem Statement

The agent factory is the heart of v6 — it transforms a `SkillConfig` document into a running ADK agent with the correct model, instruction, tools, and sub-agents. The migration doc shows a 10-line sketch but leaves critical details unresolved:

- How are tools resolved from string names to `FunctionTool` instances?
- How does model routing work across Gemini/Claude/OpenAI?
- How does the `process_skill_request()` lifecycle handle streaming, errors, and session state?
- How are ADK callbacks wired (before/after agent, before/after tool)?
- How does the thinking model selection work?

**Current State:**
- `backend/app.py` has a hardcoded root agent definition
- `backend/adk/agent.py` exists but is empty (TODO)
- `backend/skills/skill_processor.py` exists but is empty (TODO)
- No tool registry, no model routing, no callback wiring

**Impact:**
- This is the critical path — nothing works without agent creation
- Blocks: streaming endpoint, channels, protocols, frontend chat

## Goals

**Primary Goal:** Build a reliable agent factory that creates correctly-configured ADK agents from any valid `SkillConfig`, supporting all three model providers and the full tool catalog.

**Success Metrics:**
- Agent creation from SkillConfig completes in <100ms
- All three model providers (Gemini, Claude, OpenAI) work
- Tool resolution handles all registered tools
- Sub-skill delegation works inline (up to 5 levels)
- `process_skill_request()` streams AG-UI events end-to-end

**Non-Goals:**
- Agent caching/pooling (optimize later if needed)
- Dynamic tool loading from external registries (MCP tools handled separately)
- Model fine-tuning or custom model hosting

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Agent creation <100ms, streams via AG-UI, no blocking steps |
| 2 | EARNED TRUST | 0 | Infrastructure — agent responses carry trust, not the factory |
| 3 | SKILLS, NOT FEATURES | +1 | SkillConfig in, agent out — skills are the only abstraction |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Core purpose: thinking model router, model-aware compaction |
| 5 | GRACEFUL DEGRADATION | 0 | Fallback to hardcoded root agent mentioned but not detailed |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK Agent, Runner, FunctionTool — no custom orchestration |
| 7 | API FIRST | +1 | process_skill_request() is the single entry point for all channels |
| 8 | OBSERVABLE BY DEFAULT | +1 | Callbacks emit traces for every tool call, agent start/end |
| 9 | SECURE BY CONSTRUCTION | +1 | _before_tool enforces permissions architecturally |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All agent logic server-side |
| | **Net Score** | **+8** | Threshold: >= +4 |

## Design

### Overview

The agent factory has three layers: (1) a tool registry that maps tool names to `FunctionTool` instances, (2) a model resolver that creates the correct ADK model wrapper for each provider, and (3) the agent factory itself that assembles these into an ADK `Agent`. The `process_skill_request()` function orchestrates the full lifecycle: load config, create agent, run with streaming, persist messages.

### Tool Registry

A simple dictionary mapping tool names to factory functions. Each tool factory returns an ADK `FunctionTool`.

```python
# backend/adk/tools.py

from collections.abc import Callable
from google.adk.tools import FunctionTool

# Placeholder tool callables — replaced with real implementations in 1A.3.
# Signatures are stable; only bodies change when real tools land.

def _stub_ai_search(query: str) -> str:
    """Search the knowledge base. (stub — returns mock data until 1A.3)"""
    return f"[stub ai_search] matches for '{query}'"

def _stub_google_search(query: str) -> str:
    """Search the public web. (stub — returns mock data until 1A.3)"""
    return f"[stub google_search] results for '{query}'"

# ... etc for file_browser, url_processing, structured_extraction, code_execution

# Registry: tool name → FunctionTool factory. Config dict is per-tool ADK
# FunctionTool kwargs (e.g., description overrides). Empty for stubs.
TOOL_REGISTRY: dict[str, Callable[[dict], FunctionTool]] = {
    "ai_search": lambda config: FunctionTool(_stub_ai_search, **config),
    "google_search": lambda config: FunctionTool(_stub_google_search, **config),
    # ...
}

def resolve_tools(tool_names: list[str], tool_configs: dict) -> list[FunctionTool]:
    """Resolve tool names to FunctionTool instances.

    Unknown tool names log a warning and are skipped (do not raise) — a skill
    should still run if one of its tools is not yet ported.
    """
    tools = []
    for name in tool_names:
        if name not in TOOL_REGISTRY:
            logger.warning("unknown tool '%s' requested by skill; skipping", name)
            continue
        config = tool_configs.get(name, {})
        tools.append(TOOL_REGISTRY[name](config))
    return tools
```

### Model Routing

ADK supports multiple model providers. The model string determines which wrapper to use.

```python
# backend/adk/agent.py

from google.adk.models import Gemini, Claude, LiteLlm

def resolve_model(model_id: str) -> Gemini | Claude | LiteLlm:
    """Create the correct ADK model wrapper for the given model ID.
    
    - Gemini: Gemini(model=...) or pass string ID (ADK resolves via LLMRegistry)
    - Claude: Claude(model=...) — Vertex AI only, requires GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION
    - OpenAI: LiteLlm(model="openai/...") — requires OPENAI_API_KEY
    
    Note: Claude and LiteLlm require google-adk[extensions] (anthropic/litellm packages).
    """
    if model_id.startswith("gemini-"):
        return Gemini(model=model_id)
    elif model_id.startswith("claude-"):
        return Claude(model=model_id)
    elif model_id.startswith("gpt-") or model_id.startswith("o3"):
        return LiteLlm(model=f"openai/{model_id}")
    else:
        raise ValueError(f"Unsupported model: {model_id}")
```

### Agent Factory

Note the field paths: the Pydantic `SkillConfig` keeps agent-runtime fields on `skill_metadata` (author's `SKILL.md` frontmatter) and the system prompt on the top-level `instructions` field. The factory reads from there directly.

```python
# backend/adk/agent.py

from google.adk.agents import Agent, LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from adk.callbacks import make_permission_enforcer
from adk.tools import resolve_tools
from auth import User
from db.models import SkillConfig
from skills.skill_config import get_skill


def create_agent(skill_config: SkillConfig, user: User, _seen: set[str] | None = None) -> Agent:
    """Create an ADK Agent from a SkillConfig for a specific user.

    The permission enforcer closes over the user's email/domain at creation
    time — no thread-local lookup on the tool hot path.

    NOTE: EventsCompactionConfig is set at the App level (see session.py),
    not on individual agents.
    """
    _seen = _seen or set()
    if skill_config.skill_id in _seen:
        raise ValueError(f"Sub-skill cycle detected at {skill_config.skill_id}")
    _seen = _seen | {skill_config.skill_id}

    tools = resolve_tools(
        skill_config.skill_metadata.tools,
        skill_config.skill_metadata.tool_configs,
    )

    sub_agents = []
    for sub_skill_id in skill_config.skill_metadata.sub_skills:
        sub_config = get_skill(sub_skill_id)
        if sub_config is None:
            logger.warning("sub-skill '%s' not found; skipping", sub_skill_id)
            continue
        sub_agents.append(create_agent(sub_config, user, _seen=_seen))

    model = resolve_model(skill_config.skill_metadata.model)
    planner = _planner_for(skill_config)  # see Thinking Model section

    return LlmAgent(
        name=skill_config.skill_id,
        model=model,
        instruction=skill_config.instructions,
        tools=tools,
        sub_agents=sub_agents,
        planner=planner,  # None unless Gemini + dynamic thinking applies
        before_agent_callback=_before_agent,
        after_agent_callback=_after_agent,
        before_tool_callback=make_permission_enforcer(user.email, user.domain),
        after_tool_callback=_handle_large_output,
    )
```

### Thinking Model Routing

**Decision (2026-04-21):** three-tier strategy, picked per-skill at factory time. Investigation showed no Vertex/Gemini auto-router product exists; `model-optimizer-exp` was retired. Google's endorsed patterns are dynamic thinking for single-model and Flash-Lite classification for cross-model. We implement:

| Skill shape | Strategy | Why |
|---|---|---|
| Gemini model, no `thinkingModel` set | **`BuiltInPlanner(thinking_budget=-1)`** on a single `LlmAgent` | Zero routing overhead; the model self-decides thinking depth per query. Gemini 2.5 Flash/Pro both support this. |
| Gemini model, `thinkingModel` also set | Two agents + Python heuristic router | User explicitly wanted two-model routing (e.g., Flash → Pro); respect that. Avoid the extra LLM round-trip on the hot path. |
| Claude or OpenAI model | Two agents + Python heuristic router (when `thinkingModel` set) | `thinking_budget` is Gemini-only. Same heuristic dispatcher. |

```python
# backend/adk/agent.py

def _planner_for(skill_config: SkillConfig) -> BuiltInPlanner | None:
    """Dynamic-thinking planner for Gemini-only skills without a separate thinkingModel."""
    model_id = skill_config.skill_metadata.model
    if not model_id.startswith("gemini-"):
        return None
    if skill_config.skill_metadata.thinking_model:
        return None  # user picked two-model mode; don't double up
    return BuiltInPlanner(
        thinking_config=types.ThinkingConfig(thinking_budget=-1)
    )


def _should_think(message: str) -> bool:
    """Heuristic: should the two-model router dispatch to the thinking agent?

    Keep this dumb and observable. Emit a trace attribute so we can audit.
    Tune after 1 week of prod traffic.
    """
    THINK_KEYWORDS = ("analyze", "compare", "plan", "design", "explain why",
                      "reason", "prove", "strategy", "implement", "refactor",
                      "write code", "debug", "diagnose")
    msg = message.lower()
    if len(message) > 280:
        return True
    if any(k in msg for k in THINK_KEYWORDS):
        return True
    if message.count("?") >= 2:
        return True
    return False


def create_agent_with_thinking(skill_config: SkillConfig, user: User) -> Agent:
    """Create agent(s) honouring the skill's thinking strategy.

    - If no thinkingModel: single agent (with BuiltInPlanner for Gemini).
    - Else: returns a small router agent that dispatches to fast/thinking by heuristic.
    """
    if not skill_config.skill_metadata.thinking_model:
        return create_agent(skill_config, user)

    fast_agent = create_agent(skill_config, user)
    thinking_config = skill_config.model_copy(deep=True)
    thinking_config.skill_metadata.model = skill_config.skill_metadata.thinking_model
    thinking_config.skill_metadata.thinking_model = None  # prevent infinite recursion
    thinking_config.skill_id = f"{skill_config.skill_id}_thinking"
    thinking_agent = create_agent(thinking_config, user)

    # Heuristic router — pure Python, no extra LLM call.
    # The router is invoked by process_skill_request(), not ADK's sub_agent transfer,
    # so we stay provider-agnostic and keep first-token latency low.
    return _HeuristicRouter(fast=fast_agent, thinking=thinking_agent, picker=_should_think)
```

**v6.1 upgrade path**: if heuristic misclassification exceeds 10% (measured via `_before_agent` trace + user retries), swap `_HeuristicRouter` for the **Gemini CLI pattern** — a Flash-Lite classifier with `response_json_schema={"model_choice": ["fast", "thinking"]}`. Same interface, drop-in.

### ADK Callbacks

`_before_tool` already shipped in AUTH-PERMISSIONS M3 as `make_permission_enforcer()` — the factory wires it via closure so user identity is captured at agent-creation time (no thread-local lookup on the hot path). See [backend/adk/callbacks.py:20](../../../backend/adk/callbacks.py#L20). This sprint adds the other three hooks.

```python
# backend/adk/callbacks.py — additions on top of the existing make_permission_enforcer

async def _before_agent(callback_context):
    """Pre-agent hook: seed session state with user profile + routing hint.

    State keys set here are visible to tools via tool_context.state.
    """
    # user is injected into session state by process_skill_request before
    # Runner.run_async is invoked; this hook only logs start + annotates trace.
    span = trace.get_current_span()
    span.set_attribute("skill_id", callback_context.agent_name)
    # Routing observability — only present when the heuristic router fired.
    if "routing_choice" in callback_context.state:
        span.set_attribute("routing_choice", callback_context.state["routing_choice"])

async def _after_agent(callback_context):
    """Post-agent hook: emit terminal trace + usage counter bump.

    Structured extraction / message persistence live in skill_processor, not
    here — this callback stays thin so unit tests don't need Firestore.
    """
    pass  # reserved for v6.1 structured extraction hook

async def _handle_large_output(tool, args, tool_context, tool_response):
    """Post-tool hook: save large outputs as artifacts.

    Threshold 50_000 chars ≈ 12.5K tokens — below this, passthrough; above,
    persist as an ADK artifact and return a summary pointer so the agent
    keeps a compact context.
    """
    content = str(tool_response)
    if len(content) > 50_000:
        artifact_id = f"extraction_{tool_context.invocation_id}"
        await tool_context.save_artifact(
            filename=artifact_id,
            artifact=types.Part.from_text(content),
        )
        return (
            f"[Full content saved as artifact '{artifact_id}' ({len(content)} chars). "
            "Summarize key findings from the beginning of the content.]"
        )
    return tool_response
```

### process_skill_request() Lifecycle

This is the main entry point for all skill invocations — web, Telegram, email, CLI.

```python
# backend/skills/skill_processor.py

from collections.abc import AsyncGenerator

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService  # 1A.4 swaps to Firestore
from google.genai import types

from adk.agent import create_agent_with_thinking
from auth import User
from auth.access_context import AccessContext, can_read_skill
from skills.skill_config import get_skill, increment_usage

# Module-level session service — replaced in 1A.4 via get_session_service().
_session_service = InMemorySessionService()


async def process_skill_request(
    skill_id: str,
    user: User,                       # already-verified (from get_current_user)
    access: AccessContext,            # already computed per-request
    session_id: str | None,
    message: str,
    attachments: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """Process a skill request end-to-end. Yields AG-UI event dicts.

    Steps:
      1. Load skill config (cached in-memory, 60s TTL).
      2. Access check via AccessContext — 404-not-403 if user cannot see.
      3. Build agent (or router) via create_agent_with_thinking().
      4. Resolve/create session.
      5. Run agent with streaming, translating ADK events → AG-UI.
      6. Increment usage counter.
    """
    skill_config = get_skill(skill_id)  # sync — see skill_config.py
    if not skill_config or not can_read_skill(access, skill_config):
        # Collapsed: unknown or unreadable both 404 to avoid existence leaks
        raise SkillNotFoundError(skill_id)

    agent = create_agent_with_thinking(skill_config, user)

    if session_id:
        session = await _session_service.get_session(
            app_name=skill_id, user_id=user.uid, session_id=session_id
        )
    else:
        session = await _session_service.create_session(
            app_name=skill_id, user_id=user.uid
        )

    runner = Runner(
        agent=agent,
        session_service=_session_service,
        app_name=skill_id,
    )

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(message)],
    )

    async for event in runner.run_async(
        new_message=content,
        user_id=user.uid,
        session_id=session.id,
    ):
        yield _to_agui_event(event)

    increment_usage(skill_id)
```

`_to_agui_event()` reuses the translator already shipped in the Phase 0 AG-UI spike (`spikes/agui_harness/`) — no new mapping this sprint. When tools-porting lands (1A.3), real tool events will naturally replace the stub shapes.

### Streaming Endpoint

```python
# In fast_api_app.py

from fastapi.responses import StreamingResponse

@app.post("/api/skill/{skill_id}/stream")
async def stream_skill(
    skill_id: str,
    request: SkillStreamRequest,
    user: User = Depends(get_current_user),
):
    """Stream a skill response as AG-UI events."""
    async def event_stream():
        async for event in process_skill_request(
            skill_id=skill_id,
            user_id=user.uid,
            session_id=request.session_id,
            message=request.message,
            attachments=request.attachments,
        ):
            yield f"data: {json.dumps(event)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )
```

### Architecture Diagram

```
[Request: skill_id + message + user]
         │
         ▼
[process_skill_request()]
         │
    ┌────┴────┐
    ▼         ▼
[get_skill] [get_user]        ← Firestore (cached)
    │         │
    └────┬────┘
         ▼
[create_agent_with_thinking()]
    │
    ├── resolve_model(model_id)        → Gemini | Claude | LiteLlm
    ├── resolve_tools(tool_names)      → [FunctionTool, ...]
    ├── create_agent(sub_skill_ids)    → [Agent, ...] (recursive)
    └── wire callbacks                 → before/after agent/tool
         │
         ▼
[Runner.run_async()]
    │
    ├── ADK agent loop (tool selection → execution → response)
    ├── Callbacks fire (permission check, large output handling)
    └── Events stream back
         │
         ▼
[AG-UI events → SSE → Frontend/Channel]
```

## Implementation Plan

See the companion sprint plan [agent-factory-sprint.md](agent-factory-sprint.md) for milestone-by-milestone breakdown, pause points, and acceptance criteria. Summary:

- **M1** — tool registry + model router + stub tools (~1d)
- **M2** — `create_agent()` + sub-agent recursion + `_planner_for()` + `_HeuristicRouter` + `create_agent_with_thinking()` (~1d)
- **M3** — remaining callbacks (`_before_agent`, `_after_agent`, `_handle_large_output`) wired into factory (~0.5d)
- **M4** — `process_skill_request()` + `/api/skill/{skill_id}/stream` endpoint + integration test (~1.5d)
- **M5** — deployed smoke on `your-project-id` (~0.5d, **pause point**)

## Migration & Rollout

**No database migration required.** The agent factory reads from the `skills/` collection (defined in skills-data-model.md).

**Rollback Plan:** Revert to hardcoded root agent in `app.py`.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Tool registry: resolve known tools, reject unknown tools, pass configs
- [ ] Model routing: Gemini/Claude/OpenAI model IDs resolve correctly
- [ ] Agent creation: valid SkillConfig → Agent with correct model, tools, sub-agents
- [ ] Thinking model: router agent delegates correctly
- [ ] Callbacks: large output → artifact, small output → passthrough
- [ ] process_skill_request: end-to-end with InMemorySessionService
- [ ] Circular sub-skill detection

### Integration Tests
- [ ] Stream endpoint: HTTP request → SSE events with real ADK agent
- [ ] Multi-model: Gemini, Claude, OpenAI agents all respond

## Security Considerations

- Tool permission checks in `_before_tool` callback
- Sub-skill access validated against user's permissions
- `agent.instruction` is user-authored — no code execution from instructions
- Model API keys accessed via Secret Manager, never exposed to agents

## Performance Considerations

- Agent creation target: <100ms (dominated by Firestore read, which is cached)
- Sub-agent creation is recursive but bounded (max 5 sub-skills)
- Tool registry is a dictionary lookup — O(1)
- Model resolution is a string prefix check — O(1)

## Success Criteria

- [ ] `create_agent(skill_config)` returns a working ADK Agent
- [ ] All three model providers produce responses
- [ ] `process_skill_request()` streams events end-to-end
- [ ] Large tool outputs saved as artifacts
- [ ] Thinking model routing works (fast vs. complex queries)
- [ ] Integration test passes with real ADK runner

## Resolved Questions (2026-04-21)

- **Agent caching** → **no caching in v6.0.0**. Creation is dict lookup + closure + a few object allocations, targeted <100ms. User-scoped permission enforcer is captured per-request, so even if we cached agents we'd still need per-user clones. Revisit only if traces show creation on the hot path.
- **Thinking-model routing strategy** → **three-tier** (see "Thinking Model Routing" above): `BuiltInPlanner(thinking_budget=-1)` for single-Gemini, Python heuristic dispatcher for two-model. No Vertex auto-router product exists; Flash-Lite classifier (Gemini CLI pattern) is the documented v6.1 upgrade.
- **Tool execution errors** → bubble as **structured AG-UI `ERROR` events** on the stream, with a short summary string the agent can read back. The agent keeps running; the client renders the error inline. Unhandled exceptions in `_before_tool` (e.g., `ToolPermissionDenied`) already surface as structured tool-result errors — reuse that path.

## Open Questions

- **Heuristic calibration** — the `_should_think` keyword list is a first guess. Keep it under version control and iterate after 1 week of prod traffic.
- **Sub-skill depth cap** — design assumes ≤5, but we only enforce cycle detection. Add a depth counter if a skill ever blows the stack.

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Agent factory sketch (lines 478-493), request flow (lines 460-476)
- [Skills Data Model](skills-data-model.md) — SkillConfig schema
- [Tools Porting Guide](../tools-porting-guide.md) — Tool implementations
- [Streaming & Protocols](../streaming-and-protocols.md) — AG-UI event translation

---

## Implementation Report

**Completed**: 2026-04-21
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
