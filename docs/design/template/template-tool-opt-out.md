# Template Tool Opt-Out (A2UI + Default Tools)

**Status**: Implemented in platform — **template sync pending** (next periodic publish)  
**Priority**: P1  
**Estimated**: 0.5d (sync only; code and tests already shipped)  
**Scope**: Backend  
**Dependencies**: None  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-05  
**Source items**: #22 #25 (CPH Uni AIPLA upstream feedback)

> **Implementation status (2026-06-05):** Both items are shipped in the platform repo:
> - `A2uiToolConfig.enabled: bool = True` field at [backend/adk/a2ui.py:158-167](../../../backend/adk/a2ui.py#L158-L167)
> - Agent factory gate at [backend/adk/agent.py:323-325](../../../backend/adk/agent.py#L323-L325)
> - `toolConfigs.defaults.{artifacts, memory}` parsing + gates at [backend/adk/agent.py:304-308](../../../backend/adk/agent.py#L304-L308)
> - Tests: `test_skill_config_a2ui_surface.py:131-156` (5 cases) + `test_create_agent.py:303,318,330,343` (4 cases). 49/49 green on 2026-06-05.
>
> Pending: next periodic `aitana-template-publish` run to sync the diff to
> `sunholo-data/ai-protocol-platform`.

## Problem Statement

The agent factory in `backend/adk/agent.py` unconditionally appends several tool sets to
every skill it builds — regardless of what that skill declares in `SKILL.md`:

- **Item #22:** `make_a2ui_toolset(...)` is appended to every skill even when the skill
  declares `tools: []`. A chat-only skill that explicitly opts out of tools still gets
  `send_a2ui_json_to_client` wired up. Models with tool-to-suggestion bias call it
  unprompted: the first deploy of AIPLA's `problem-set-hints` produced a
  "projectile_motion_dashboard" A2UI surface on a "hi" greeting, followed by a
  `Surface already exists` error on the second turn.

- **Item #25:** Four more tools are hard-wired before the dynamic list:
  `load_artifacts_tool`, `retrieve_artifact`, `load_memory_tool`, `preload_memory_tool`.
  A skill with `tools: []` still sees all four. Gemini occasionally decides to invoke
  `load_artifacts` ("let me check...") and returns nothing useful — visibly confusing
  in a teacher demo.

**Impact:**

- Chat-only skills receive 200+ tokens/turn of tool schema they can never meaningfully use.
- Model bias toward available tools produces hallucinated tool calls that confuse users.
- The only escape is prompt-level hard rules ("NEVER call X") — a fragile workaround that
  doesn't actually remove the tool from the model's context.

## Goals

**Primary Goal:** Skills that don't need A2UI or artifact/memory tools should be able to
opt out cleanly — the model literally can't see those tools.

**Success Metrics:**
- A skill with `toolConfigs.a2ui.enabled: false` produces an agent with no `send_a2ui_json_to_client` in its tool list.
- A skill with `toolConfigs.defaults.artifacts: false` produces an agent with no `load_artifacts_tool` or `retrieve_artifact`.
- A skill with `toolConfigs.defaults.memory: false` produces an agent with no `load_memory_tool` or `preload_memory_tool`.
- Default behavior (no `toolConfigs` key) is unchanged — all tools included, existing workshop demos unaffected.

**Non-Goals:**
- Finer-grained opt-out per individual tool within the defaults group (artifacts vs retrieve_artifact separately).
- Opt-out for MCP tools or search tools (those are already config-driven).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Fewer tools = shorter tool-schema injection = marginally lower TTFT |
| 2 | EARNED TRUST | 0 | |
| 3 | SKILLS, NOT FEATURES | +1 | Skills can now be precisely scoped |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Right tools for the right skill |
| 5 | GRACEFUL DEGRADATION | 0 | |
| 6 | PROTOCOL OVER CUSTOM | 0 | |
| 7 | API FIRST | 0 | |
| 8 | OBSERVABLE BY DEFAULT | +1 | Cleaner tool list makes agent behavior more predictable |
| 9 | SECURE BY CONSTRUCTION | 0 | |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+4** | Meets threshold |

## Design

### SKILL.md frontmatter additions

```yaml
# Example: chat-only skill that wants no UI or memory tools
toolConfigs:
  a2ui:
    enabled: false       # default: true
  defaults:
    artifacts: false     # default: true — controls load_artifacts_tool + retrieve_artifact
    memory: false        # default: true — controls load_memory_tool + preload_memory_tool
```

Both `a2ui.enabled` and `defaults.*` default to `true` so the existing workshop demos
(3 of 5 inherited skills are A2UI showcases) remain unaffected by the change.

### `backend/adk/a2ui.py` — `A2uiToolConfig.enabled`

```python
@dataclass
class A2uiToolConfig:
    enabled: bool = True          # new field — False = skip make_a2ui_toolset entirely
    # ... existing fields unchanged
```

### `backend/adk/agent.py` — factory gate for A2UI

```python
# Before (unconditional)
tools.append(make_a2ui_toolset(config=a2ui_cfg))

# After
if a2ui_cfg.enabled:
    tools.append(make_a2ui_toolset(config=a2ui_cfg))
```

### `backend/adk/agent.py` — factory gates for default tools

```python
# Existing (unconditional, runs before the dynamic list)
tools = [
    load_artifacts_tool,
    retrieve_artifact,
    load_memory_tool,
    preload_memory_tool,
]

# After — read defaults config
defaults_cfg = md.tool_configs.get("defaults", {})
if defaults_cfg.get("artifacts", True):
    tools += [load_artifacts_tool, retrieve_artifact]
if defaults_cfg.get("memory", True):
    tools += [load_memory_tool, preload_memory_tool]
```

### `backend/db/models.py` — SkillConfig / ToolConfigs model

```python
class DefaultToolsConfig(BaseModel):
    artifacts: bool = True
    memory: bool = True

class ToolConfigs(BaseModel):
    a2ui: A2uiToolConfig = Field(default_factory=A2uiToolConfig)
    defaults: DefaultToolsConfig = Field(default_factory=DefaultToolsConfig)
    # mcp, search already present
```

### AIPLA source commits

- `A2uiToolConfig.enabled` field: AIPLA `backend/adk/a2ui.py` (commit TBD-after-push)
- Factory gate for A2UI: AIPLA `backend/adk/agent.py` (same commit)
- Tests: AIPLA `backend/tests/unit/test_skill_config_a2ui_surface.py` (5 new cases)
  + `backend/tests/unit/test_create_agent.py` (2 new cases on factory gate)
- `defaults` flags: AIPLA follow-up commit (same session)
- Tests: AIPLA `backend/tests/unit/test_create_agent.py` (2 new cases on defaults flags)

## Implementation Plan

This is a sync, not new work. Steps:

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Cherry-pick / port A2UI `enabled` field + factory gate from AIPLA | 1h |
| 2 | Cherry-pick / port `defaults` flags + factory gates from AIPLA | 1h |
| 3 | Port AIPLA tests (7 total) + verify against template's test suite | 1h |
| 4 | Update `docs/design/template/` SEQUENCE.md status | 0.25h |

**Total: ~3h ≈ 0.5d**

## Testing Strategy

All tests are ported from AIPLA — see commit references above. Key cases:

- `test_create_agent.py`:
  - `toolConfigs.a2ui.enabled: false` → `send_a2ui_json_to_client` NOT in tool names.
  - `toolConfigs.a2ui.enabled: true` (default) → `send_a2ui_json_to_client` IS in tool names.
  - `toolConfigs.defaults.artifacts: false` → `load_artifacts_tool` NOT present.
  - `toolConfigs.defaults.memory: false` → `load_memory_tool` NOT present.
  - No `toolConfigs` key → all tools present (backwards compat).

- `test_skill_config_a2ui_surface.py`:
  - `A2uiToolConfig(enabled=False)` serializes correctly.
  - SKILL.md with `a2ui.enabled: false` parses into `A2uiToolConfig(enabled=False)`.

## Success Criteria

- [ ] Skill with `toolConfigs.a2ui.enabled: false` → agent has no `send_a2ui_json_to_client`.
- [ ] Skill with no `toolConfigs` key → all tools present (no regression).
- [ ] Skill with `toolConfigs.defaults.artifacts: false` → `load_artifacts_tool` and `retrieve_artifact` absent.
- [ ] Skill with `toolConfigs.defaults.memory: false` → `load_memory_tool` and `preload_memory_tool` absent.
- [ ] All 7 ported tests pass.
- [ ] Existing workshop skill demos unchanged in end-to-end smoke.

## Related Documents

- [a2ui-surface-context.md](../../v6.2.0/implemented/a2ui-surface-context.md) — A2UI background
- [SEQUENCE.md](SEQUENCE.md)
