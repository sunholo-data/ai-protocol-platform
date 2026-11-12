"""A delegate with a `thinkingModel` must still stream its thoughts.

A handed-off specialist is built by the plain `create_agent` recursion, not by
`create_agent_with_thinking`, so it runs as ONE agent on its `model`. For a
skill with `thinkingModel` set, `_planner_for` returns None (the two-agent path
passes explicit planners instead) — so the delegate got NO planner, thought at
the API default with include_thoughts unset, and sent nothing until it had
finished thinking. After a large tool result (a two-contract PPA comparison)
that silence outlasted the first-token deadline on every chain member, and the
turn died as MODEL_SILENT_STALL ("Model busy: retrying…" then an error). The
same skill opened directly worked, because its fast agent has the planner.
Reported on a deployed fork's prod, 2026-10-30.
"""

from __future__ import annotations

from unittest.mock import patch

from google.adk.planners import BuiltInPlanner

from adk.agent import create_agent
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegationConfig, DelegationMode, SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx() -> AccessContext:
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com")


def _skill(skill_id: str, *, model: str, thinking_model: str | None = None, allow=None) -> SkillConfig:
    delegation = (
        DelegationConfig(enabled=True, mode=DelegationMode("auto"), allow=allow, max_depth=1)
        if allow
        else DelegationConfig()
    )
    return SkillConfig(
        name=skill_id,
        displayName=skill_id,
        description="A skill.",
        instructions="Do the thing.",
        skillId=skill_id,
        accessControl=AccessControl(type="public"),
        skillMetadata=SkillMetadata(model=model, thinkingModel=thinking_model, delegation=delegation),
    )


def _delegate(child: SkillConfig):
    door = _skill("door", model="gemini-2.5-flash", allow=[child.skill_id])
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(door, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    return agent.sub_agents[0]


def test_delegate_with_thinking_model_streams_thoughts():
    sub = _delegate(_skill("specialist", model="gemini-2.5-flash", thinking_model="smart"))
    assert isinstance(sub.planner, BuiltInPlanner)
    assert sub.planner.thinking_config.include_thoughts is True


def test_delegate_without_thinking_model_keeps_its_planner():
    sub = _delegate(_skill("specialist", model="gemini-2.5-flash"))
    assert isinstance(sub.planner, BuiltInPlanner)
    assert sub.planner.thinking_config.include_thoughts is True


def test_non_gemini_delegate_gets_no_planner():
    # BuiltInPlanner is Gemini-only; Claude/OpenAI carry their own reasoning wiring.
    sub = _delegate(_skill("specialist", model="claude-opus-4-7", thinking_model="smart"))
    assert sub.planner is None
