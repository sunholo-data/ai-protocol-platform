"""FANOUT M1 — `DelegateRule.invoke` selects HOW the parent reaches a delegate.

`floor` is how much user consent a handoff needs; `invoke` is the mechanism:

  - ``transfer`` (default): the delegate is an ADK ``sub_agent`` and the parent
    hands the turn over via ``transfer_to_agent``. One-way — the parent's turn
    is over, so it can never merge two delegates' answers.
  - ``tool``: the delegate is wrapped in ``AgentTool`` and CALLED, returning its
    final text to the parent, which can call it several times in one response
    and merge. This is what parallel fan-out needs.

See docs/design/v6.31.0/parallel-skill-fanout.md.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from google.adk.tools import AgentTool

from adk.agent import create_agent
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegateRule, DelegationConfig, SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx() -> AccessContext:
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com")


def _skill(*, skill_id: str, slug: str = "", delegation: DelegationConfig | None = None) -> SkillConfig:
    cfg = SkillConfig(
        name=skill_id,
        displayName=skill_id,
        description="A skill.",
        instructions="Do the thing.",
        skillId=skill_id,
        accessControl=AccessControl(type="public"),
        skillMetadata=SkillMetadata(model="gemini-2.5-flash", delegation=delegation or DelegationConfig()),
    )
    if slug:
        cfg.slug = slug
    return cfg


def _agent_tools(agent) -> list[AgentTool]:
    return [t for t in agent.tools if isinstance(t, AgentTool)]


def _sub_agent_names(agent) -> list[str]:
    return [a.name for a in agent.sub_agents]


# --- the wiring ------------------------------------------------------------


def test_invoke_tool_wires_an_agent_tool_not_a_sub_agent():
    """The whole point: call-and-return, so the parent can merge."""
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=[DelegateRule(skill="child", invoke="tool")]),
    )
    child = _skill(skill_id="child", slug="research_worker")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())

    tools = _agent_tools(agent)
    assert len(tools) == 1, "invoke='tool' must put the delegate in tools"
    assert tools[0].agent.name == "research_worker"
    # NOT also a sub_agent: two routes to one delegate would let the model
    # transfer away mid-fan-out instead of calling and returning.
    assert "research_worker" not in _sub_agent_names(agent)


def test_invoke_transfer_is_unchanged_default():
    """Every existing skill must keep today's behaviour with no config change."""
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=["child"]),
    )
    child = _skill(skill_id="child", slug="specialist")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())

    assert _sub_agent_names(agent) == ["specialist"]
    assert not _agent_tools(agent), "the default must not wrap delegates as tools"


def test_invoke_defaults_to_transfer_on_a_bare_rule():
    assert DelegateRule(skill="x").invoke == "transfer"


def test_mixed_invoke_modes_on_one_parent():
    """A door may transfer to a specialist AND call a worker in the same skill."""
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(
            enabled=True,
            allow=[
                DelegateRule(skill="worker", invoke="tool"),
                DelegateRule(skill="specialist", invoke="transfer"),
            ],
        ),
    )
    subs = {
        "worker": _skill(skill_id="worker", slug="worker"),
        "specialist": _skill(skill_id="specialist", slug="specialist"),
    }
    with patch("adk.agent.get_skill", side_effect=lambda sid: subs.get(sid)):
        agent = create_agent(parent, _user(), access_context=_ctx())

    assert [t.agent.name for t in _agent_tools(agent)] == ["worker"]
    assert _sub_agent_names(agent) == ["specialist"]


# --- the combination that must fail loudly ---------------------------------


@pytest.mark.parametrize("floor", ["confirm", "confirm_with_fields"])
def test_invoke_tool_with_a_confirm_floor_is_rejected_at_build_time(floor):
    """A confirm floor promises the user approves BEFORE the delegate runs.

    An AgentTool call is issued by the model mid-turn: there is no seam at
    which to render the confirm card. Silently honouring `invoke` and dropping
    `floor` would remove a consent gate without telling anyone, so the build
    fails and names both fields.
    """
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=[DelegateRule(skill="child", floor=floor, invoke="tool")]),
    )
    child = _skill(skill_id="child", slug="child")
    with patch("adk.agent.get_skill", return_value=child):
        with pytest.raises(ValueError, match="invoke='tool'"):
            create_agent(parent, _user(), access_context=_ctx())


def test_the_rejection_names_the_floor_so_the_fix_is_obvious():
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=[DelegateRule(skill="child", floor="confirm", invoke="tool")]),
    )
    child = _skill(skill_id="child", slug="child")
    with patch("adk.agent.get_skill", return_value=child):
        with pytest.raises(ValueError) as exc:
            create_agent(parent, _user(), access_context=_ctx())
    msg = str(exc.value)
    assert "confirm" in msg and "child" in msg and "parent" in msg


# --- safety properties inherited from the existing delegate machinery ------


def test_a_tool_delegate_is_still_access_filtered():
    """`invoke` is a mechanism, never a grant — the access filter still gates."""
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=[DelegateRule(skill="child", invoke="tool")]),
    )
    child = _skill(skill_id="child", slug="child")
    child.access_control = AccessControl(type="private")
    child.owner_id = "someone-else"
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert not _agent_tools(agent), "an inaccessible delegate must not become a callable tool"


def test_a_tool_delegate_cannot_fan_out_to_itself():
    """Cycle detection is shared with the transfer path, and it RAISES rather
    than degrading — a self-call would recurse until the process died, and N
    concurrent branches multiply that. `create_agent` re-raises ValueError out
    of the per-delegate graceful-degradation handler for exactly this reason."""
    parent = _skill(
        skill_id="parent",
        slug="parent",
        delegation=DelegationConfig(enabled=True, allow=[DelegateRule(skill="parent", invoke="tool")]),
    )
    with patch("adk.agent.get_skill", return_value=parent):
        with pytest.raises(ValueError, match="cycle detected"):
            create_agent(parent, _user(), access_context=_ctx())
