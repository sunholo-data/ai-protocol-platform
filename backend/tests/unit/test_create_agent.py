"""Unit tests for create_agent() (AGENT-FACTORY M2).

The factory assembles an ADK LlmAgent from a SkillConfig + authenticated
User. It reads from `skill_metadata.*`, wires a per-user
`before_tool_callback` via `make_permission_enforcer`, and recurses into
sub-skills (resolved by ID through `skills.skill_config.get_skill`) with
cycle detection.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from a2ui.adk.send_a2ui_to_client_toolset import SendA2uiToClientToolset
from google.adk.agents import LlmAgent

from adk.agent import _safe_agent_name, create_agent
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegationConfig, SkillConfig, SkillMetadata
from db.models.access import AccessControl


@pytest.fixture(autouse=True)
def _disable_platform_preamble():
    """Keep these factory tests focused on the per-skill / state wrappers.

    ``create_agent`` now PREPENDS the admin-configured platform preamble (which
    defaults to a non-empty value), so a resolved instruction no longer starts
    with the raw skill body. Disable the preamble here so the wrapper-ordering
    assertions below stay about iframe / a2ui / date. The preamble's own prepend
    behaviour is covered in test_platform_preamble_context.py.
    """
    from db.models import PlatformConfig

    with patch(
        "adk.platform_preamble_context.get_platform_config",
        return_value=PlatformConfig(preamble="", enabled=False),
    ):
        yield


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx() -> AccessContext:
    # Permissive context matching _user(); delegation is access-filtered
    # (v6.7.0) so sub-skill tests must supply one and use accessible children.
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com")


def _skill(
    name: str = "test-skill",
    skill_id: str = "11111111-1111-1111-1111-111111111111",
    tools: list[str] | None = None,
    sub_skills: list[str] | None = None,
    model: str = "gemini-2.5-flash",
    thinking_model: str | None = None,
    instructions: str = "Do the thing.",
    tool_configs: dict | None = None,
    access: AccessControl | None = None,
    delegation: DelegationConfig | None = None,
) -> SkillConfig:
    return SkillConfig(
        name=name,
        description="Test skill for unit tests.",
        instructions=instructions,
        skillId=skill_id,
        accessControl=access or AccessControl(type="public"),
        skillMetadata=SkillMetadata(
            model=model,
            thinkingModel=thinking_model,
            tools=tools or [],
            subSkills=sub_skills or [],
            toolConfigs=tool_configs or {},
            delegation=delegation or DelegationConfig(),
        ),
    )


# --- _safe_agent_name ---


def test_safe_agent_name_converts_hyphens_to_underscores():
    # Skill IDs default to UUIDs with hyphens, ADK rejects hyphens.
    assert _safe_agent_name("abc-123-def") == "abc_123_def"


def test_safe_agent_name_prepends_underscore_when_starts_with_digit():
    # UUIDs can start with a digit; ADK names must start with letter/underscore.
    safe = _safe_agent_name("1abc-def")
    assert safe.startswith(("s_", "_"))
    assert "-" not in safe


def test_safe_agent_name_leaves_valid_identifiers_unchanged():
    assert _safe_agent_name("already_valid") == "already_valid"


# --- happy path ---


def test_create_agent_returns_llmagent_with_expected_name_and_instruction():
    agent = create_agent(_skill(), _user())
    assert isinstance(agent, LlmAgent)
    # name is a sanitized skill_id (hyphens -> underscores)
    assert agent.name == _safe_agent_name("11111111-1111-1111-1111-111111111111")
    # Instruction is now an InstructionProvider (sprint 1.25 wraps the
    # static string with `wrap_with_iframe_context` so the runtime can
    # inject `mcp_app_context.*` state at agent-instruction-build time).
    # When session state has no `mcp_app_context.*` keys (the common
    # case), the wrapper returns the base string unchanged. Resolve the
    # callable with a stub ReadonlyContext to assert that contract.
    import asyncio
    from unittest.mock import MagicMock

    assert callable(agent.instruction)
    fake_ctx = MagicMock()
    fake_ctx.state = {}
    # asyncio.run rather than get_event_loop().run_until_complete — the
    # latter relies on the deprecated default-loop-on-main-thread that
    # gets closed by pytest-asyncio fixtures in other tests, leading to
    # "no current event loop" failures that depend on suite ordering.
    resolved = asyncio.run(agent.instruction(fake_ctx))
    # The skill's own instruction passes through untouched when no iframe /
    # a2ui / saved-form context is present. Every agent is ALSO grounded in
    # today's date (v6.12.0) — without it, relative dates ("start of year")
    # were resolved against the model's training-era guess and silently
    # answered about the wrong year — so that block is expected here.
    assert resolved.startswith("Do the thing.")
    assert "Today's date is" in resolved


def test_create_agent_instruction_appends_iframe_context_when_state_has_it():
    """End-to-end: when session state carries `mcp_app_context.*` keys
    (because an MCP App iframe pushed `ui/update-model-context`), the
    agent's runtime instruction MUST include the iframe-context block
    so the model can reference what's currently on screen.

    Sprint 1.25 — pairs with the iframe-context endpoint and the
    frontend `onFallbackRequest` handler."""
    import asyncio
    from unittest.mock import MagicMock

    agent = create_agent(_skill(), _user())
    fake_ctx = MagicMock()
    fake_ctx.state = {
        "mcp_app_context.ext-apps-map.show-map": {
            "structuredContent": {"label": "Munich", "viewUUID": "abc-123"},
        },
    }
    resolved = asyncio.run(agent.instruction(fake_ctx))
    assert resolved.startswith("Do the thing.")
    assert "Current iframe-app context" in resolved
    assert "Munich" in resolved
    assert "ext-apps-map.show-map" in resolved


def test_create_agent_instruction_appends_a2ui_surface_context_when_state_has_it():
    """Sprint 2.10 sibling of the iframe-context test above. When session
    state carries A2UI surface data (either per-turn snapshot under
    ``a2ui_surface_state`` OR persisted action writes under
    ``a2ui_surface_context.{surface}.*``), the agent's runtime
    instruction MUST include the surface-context block so the model
    knows what surfaces the user is viewing."""
    import asyncio
    from unittest.mock import MagicMock

    agent = create_agent(_skill(), _user())
    fake_ctx = MagicMock()
    fake_ctx.state = {
        "a2ui_surface_state": {
            "workspace": {"dataModel": {"activeUsers": "42 users online"}},
        },
        "a2ui_surface_context.workspace.lastAction": {
            "name": "approve",
            "sourceComponentId": "row-47",
        },
    }
    resolved = asyncio.run(agent.instruction(fake_ctx))
    assert resolved.startswith("Do the thing.")
    # A2UI block prose
    assert "A2UI surface state" in resolved
    # Per-turn dataModel content
    assert "42 users online" in resolved
    # Persisted action content
    assert "approve" in resolved
    assert "row-47" in resolved
    # Single workspace heading — both sources merged, not stacked
    assert resolved.count("## workspace") == 1


def test_create_agent_instruction_appends_both_iframe_and_a2ui_blocks():
    """Chain coexistence: when state has BOTH mcp_app_context.* AND
    a2ui_surface_state, the agent prompt carries BOTH blocks. The two
    InstructionProvider wrappers are independent — each appends its
    own block in order (iframe first, A2UI second)."""
    import asyncio
    from unittest.mock import MagicMock

    agent = create_agent(_skill(), _user())
    fake_ctx = MagicMock()
    fake_ctx.state = {
        "mcp_app_context.ext-apps-map.show-map": {
            "structuredContent": {"label": "Munich"},
        },
        "a2ui_surface_state": {
            "workspace": {"dataModel": {"revenue": "$1,234"}},
        },
    }
    resolved = asyncio.run(agent.instruction(fake_ctx))
    assert resolved.startswith("Do the thing.")
    assert "Current iframe-app context" in resolved
    assert "Munich" in resolved
    assert "A2UI surface state" in resolved
    assert "$1,234" in resolved
    # iframe block appears first (inner wrapper appends earlier)
    assert resolved.index("iframe-app context") < resolved.index("A2UI surface state")


def test_create_agent_includes_a2ui_toolset():
    """A2UI is delivered via SendA2uiToClientToolset tool calls, not fenced blocks."""
    agent = create_agent(_skill(), _user())
    a2ui_toolsets = [t for t in agent.tools if isinstance(t, SendA2uiToClientToolset)]
    assert len(a2ui_toolsets) == 1


def test_create_agent_wires_tools_from_skill_metadata():
    # list_documents + get_document_content are in the registry (model-agnostic).
    # ai_search/google_search are model-aware and handled by agent.py directly, so not here.
    agent = create_agent(_skill(tools=["list_documents", "get_document_content"]), _user())
    # Defaults from agent.py: load_artifacts_tool + retrieve_artifact +
    # load_memory_tool + preload_memory_tool. Plus 2 registry tools,
    # SendA2uiToClientToolset, and request_confirmation (default-on) = 8 total.
    assert len(agent.tools) == 8
    tool_names = _tool_ids(agent)
    assert "load_artifacts" in tool_names
    assert "load_memory" in tool_names
    assert "preload_memory" in tool_names
    assert "request_confirmation" in tool_names


def test_create_agent_includes_request_confirmation_by_default():
    """v6.12.0 — every agent gets the agent-authored elicitation tool unless the
    skill opts out (a TTFT-critical front door)."""
    agent = create_agent(_skill(), _user())
    assert "request_confirmation" in _tool_ids(agent)


def test_create_agent_omits_request_confirmation_when_disabled():
    from db.models import SkillMetadata

    skill = _skill()
    # Rebuild metadata with the opt-out flag (front-door / lean-router case).
    skill.skill_metadata = SkillMetadata(model="gemini-2.5-flash", enableConfirmation=False)
    agent = create_agent(skill, _user())
    assert "request_confirmation" not in _tool_ids(agent)


def _tool_ids(agent) -> list[str]:
    """Return a list of identifiers for each tool in agent.tools.

    ADK tools may be raw functions (use __name__) or FunctionTool/built-in
    objects (use .name). This helper normalises both so tests aren't brittle
    against ADK's internal wrapping choices.
    """
    ids = []
    for t in agent.tools:
        ids.append(getattr(t, "name", None) or getattr(t, "__name__", type(t).__name__))
    return ids


def test_create_agent_search_uses_sub_agent_pattern():
    """Search skills use AgentTool(search_agent) for all models — keeps root agent
    FunctionTool-compatible so retrieve_artifact and a2ui coexist with search."""
    from google.adk.tools import AgentTool

    agent = create_agent(_skill(tools=["google_search"], model="gemini-2.5-flash"), _user())
    tool_ids = _tool_ids(agent)
    # FunctionTools are NOT excluded — search goes via sub-agent
    assert "retrieve_artifact" in tool_ids
    a2ui_toolsets = [t for t in agent.tools if isinstance(t, SendA2uiToClientToolset)]
    assert len(a2ui_toolsets) == 1
    # Search is delivered as an AgentTool wrapping the search_agent sub-agent
    agent_tools = [t for t in agent.tools if isinstance(t, AgentTool)]
    assert any(getattr(t.agent, "name", None) == "web_search_agent" for t in agent_tools)


def test_create_agent_non_gemini_search_retains_retrieve_artifact():
    """Non-search Gemini skills should still get retrieve_artifact."""
    agent = create_agent(_skill(tools=["list_documents"], model="gemini-2.5-flash"), _user())
    assert "retrieve_artifact" in _tool_ids(agent)


def test_create_agent_unknown_tool_raises():
    """Unknown tool names raise ValueError to prevent silent misconfiguration."""
    with pytest.raises(ValueError, match="not_a_real_tool"):
        create_agent(_skill(tools=["list_documents", "not_a_real_tool"]), _user())


def test_create_agent_attaches_permission_enforcer_callback():
    agent = create_agent(_skill(), _user())
    # ADK may coerce a single callback into a list; handle both.
    cb = agent.before_tool_callback
    assert cb is not None
    if isinstance(cb, list):
        assert len(cb) >= 1
    else:
        assert callable(cb)


# --- sub-agent recursion ---


def test_create_agent_recurses_into_sub_skills():
    # Delegation is access-filtered (v6.7.0): supply an access_context and an
    # accessible (public, via _skill default) child.
    child = _skill(name="child-skill", skill_id="child-id", instructions="child work")
    parent = _skill(name="parent-skill", skill_id="parent-id", sub_skills=["child-id"])
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    assert agent.sub_agents[0].name == _safe_agent_name("child-id")


def test_create_agent_missing_sub_skill_warns_and_skips(caplog):
    parent = _skill(name="parent-skill", skill_id="parent-id", sub_skills=["missing-id"])
    with patch("adk.agent.get_skill", return_value=None):
        with caplog.at_level(logging.WARNING):
            agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []
    assert any("missing-id" in rec.message for rec in caplog.records)


def test_create_agent_detects_direct_self_cycle():
    # Skill references itself as a sub-skill — must raise (detected at the first
    # delegate build attempt, depth 0, so default max_depth=1 still reaches it).
    skill = _skill(name="loop-skill", skill_id="loop-id", sub_skills=["loop-id"])
    with patch("adk.agent.get_skill", return_value=skill):
        with pytest.raises(ValueError, match="Sub-skill cycle detected"):
            create_agent(skill, _user(), access_context=_ctx())


def test_create_agent_detects_indirect_cycle():
    # A->B->A needs 2 hops to reach the cycle, so max_depth must allow it.
    deep = DelegationConfig(max_depth=2)
    a = _skill(name="a-skill", skill_id="a-id", sub_skills=["b-id"], delegation=deep)
    b = _skill(name="b-skill", skill_id="b-id", sub_skills=["a-id"], delegation=deep)
    lookup = {"a-id": a, "b-id": b}
    with patch("adk.agent.get_skill", side_effect=lambda sid: lookup.get(sid)):
        with pytest.raises(ValueError, match="Sub-skill cycle detected"):
            create_agent(a, _user(), access_context=_ctx())


# --- tool opt-out (template-hardening M1) ---


def test_create_agent_a2ui_disabled_removes_toolset():
    """toolConfigs.a2ui.enabled: false → no SendA2uiToClientToolset attached."""
    skill = _skill(tool_configs={"a2ui": {"enabled": False}})
    agent = create_agent(skill, _user())
    a2ui_toolsets = [t for t in agent.tools if isinstance(t, SendA2uiToClientToolset)]
    assert len(a2ui_toolsets) == 0
    # send_a2ui_json_to_client must not appear in any tool name
    assert "send_a2ui_json_to_client" not in _tool_ids(agent)


def test_create_agent_a2ui_enabled_by_default():
    """No toolConfigs key → A2UI toolset still attached (backwards compat)."""
    agent = create_agent(_skill(), _user())
    a2ui_toolsets = [t for t in agent.tools if isinstance(t, SendA2uiToClientToolset)]
    assert len(a2ui_toolsets) == 1


def test_create_agent_defaults_artifacts_false_removes_artifact_tools():
    """toolConfigs.defaults.artifacts: false → load_artifacts + retrieve_artifact absent."""
    skill = _skill(tool_configs={"defaults": {"artifacts": False}})
    agent = create_agent(skill, _user())
    ids = _tool_ids(agent)
    assert "load_artifacts" not in ids
    assert "retrieve_artifact" not in ids
    # Memory tools still present (only artifacts opted out)
    assert "load_memory" in ids
    assert "preload_memory" in ids


def test_create_agent_defaults_memory_false_removes_memory_tools():
    """toolConfigs.defaults.memory: false → load_memory + preload_memory absent."""
    skill = _skill(tool_configs={"defaults": {"memory": False}})
    agent = create_agent(skill, _user())
    ids = _tool_ids(agent)
    assert "load_memory" not in ids
    assert "preload_memory" not in ids
    # Artifact tools still present
    assert "load_artifacts" in ids
    assert "retrieve_artifact" in ids


def test_create_agent_defaults_all_false_removes_all_default_tools():
    """toolConfigs.defaults.artifacts: false + memory: false → only skill tools + A2UI."""
    skill = _skill(
        tools=["list_documents"],
        tool_configs={"defaults": {"artifacts": False, "memory": False}},
    )
    agent = create_agent(skill, _user())
    ids = _tool_ids(agent)
    assert "load_artifacts" not in ids
    assert "retrieve_artifact" not in ids
    assert "load_memory" not in ids
    assert "preload_memory" not in ids
    # list_documents still present; A2UI still present
    assert "list_documents" in ids


def test_create_agent_no_tool_configs_includes_all_defaults():
    """No toolConfigs → all four default tools present (backwards compat)."""
    agent = create_agent(_skill(), _user())
    ids = _tool_ids(agent)
    assert "load_artifacts" in ids
    assert "retrieve_artifact" in ids
    assert "load_memory" in ids
    assert "preload_memory" in ids
