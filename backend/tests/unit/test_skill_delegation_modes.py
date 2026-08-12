"""Unit tests for delegation floors + observability (SKILL-DELEGATION M2;
per-delegate floors 8.2; unified ADK-native handoff v6.10.0).

v6.10.0: EVERY accessible delegate is wired as a sub_agent, so ADK's native,
enum-constrained `transfer_to_agent` is the model's ONLY handoff verb. The
per-delegate FLOOR is enforced by a `before_tool_callback` (make_handoff_policy_
callback), not by which tool exists:
  - floor "auto"         : real recursive sub_agent -> native in-turn transfer.
  - floor confirm / cwf  : STUB sub_agent (visible to the transfer enum, never
                           runs); the callback short-circuits the transfer and
                           returns the elicitation envelope. A bare `allow` string
                           under mode=suggest inherits the confirm floor.
There is NO `request_handoff` tool anymore. The callback is unit-tested in
test_handoff_policy.py; here we cover the seam wiring + graceful degradation.
"""

from __future__ import annotations

from unittest.mock import patch

from adk.agent import create_agent
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegationConfig, DelegationMode, SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx() -> AccessContext:
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com")


def _skill(
    *,
    skill_id: str,
    name: str = "skill",
    display_name: str = "",
    description: str = "A skill.",
    model: str = "gemini-2.5-flash",
    access: AccessControl | None = None,
    delegation: DelegationConfig | None = None,
) -> SkillConfig:
    return SkillConfig(
        name=name,
        displayName=display_name or name,
        description=description,
        instructions="Do the thing.",
        skillId=skill_id,
        accessControl=access or AccessControl(type="public"),
        skillMetadata=SkillMetadata(model=model, delegation=delegation or DelegationConfig()),
    )


def _deleg(allow, *, mode="auto", enabled=True, max_depth=1) -> DelegationConfig:
    return DelegationConfig(enabled=enabled, mode=DelegationMode(mode), allow=allow, max_depth=max_depth)


def _tool_names(agent) -> list[str]:
    names = []
    for t in agent.tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if name:
            names.append(name)
    return names


# --- auto mode -------------------------------------------------------------


def test_auto_floor_wires_sub_agents_no_handoff_tool():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"], mode="auto"))
    child = _skill(skill_id="child")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    # All delegates auto-floor -> no request_handoff tool (transfer_to_agent only).
    assert "request_handoff" not in _tool_names(agent)


# --- confirm floor (mode=suggest bare strings inherit it) ------------------


def test_confirm_floor_wires_stub_sub_agent_no_handoff_tool():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"], mode="suggest"))
    child = _skill(skill_id="child", display_name="PPA Specialist")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    # confirm floor -> a STUB sub_agent (reachable by the single transfer_to_agent),
    # NOT a request_handoff tool; the before_tool_callback enforces the floor.
    assert len(agent.sub_agents) == 1
    assert "request_handoff" not in _tool_names(agent)
    assert agent.before_tool_callback is not None


def test_explicit_confirm_floor_wires_stub_sub_agent():
    # A structured DelegateRule floor works the same as mode=suggest sugar.
    parent = _skill(
        skill_id="parent",
        delegation=DelegationConfig(enabled=True, allow=[{"skill": "child", "floor": "confirm"}]),
    )
    child = _skill(skill_id="child", display_name="Doc Compare")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    assert "request_handoff" not in _tool_names(agent)


def test_no_sub_agents_when_no_accessible_delegate():
    # allowed delegate is inaccessible -> nothing to hand off -> no sub_agent, no tool
    parent = _skill(skill_id="parent", delegation=_deleg(["child"], mode="suggest"))
    child = _skill(skill_id="child", access=AccessControl(type="private"))
    child.owner_id = "someone-else"
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []
    assert "request_handoff" not in _tool_names(agent)


def test_mixed_floors_all_delegates_are_sub_agents_no_handoff_tool():
    """The one-assistant front-door shape (v6.10.0): auto AND confirm delegates are
    BOTH sub_agents behind the single transfer_to_agent; the floor policy is in the
    before_tool_callback, so there is NO request_handoff tool."""
    parent = _skill(
        skill_id="door",
        delegation=DelegationConfig(
            enabled=True,
            allow=[
                {"skill": "ppa", "floor": "auto"},
                {"skill": "cmp", "floor": "confirm"},
            ],
        ),
    )
    subs = {"ppa": _skill(skill_id="ppa", display_name="PPA"), "cmp": _skill(skill_id="cmp", display_name="Compare")}
    with patch("adk.agent.get_skill", side_effect=lambda sid: subs.get(sid)):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 2  # auto (real) + confirm (stub)
    assert "request_handoff" not in _tool_names(agent)
    assert agent.before_tool_callback is not None


def test_all_auto_front_door_wires_sub_agents_no_handoff_tool():
    """The one-assistant front door shape (all delegates floor=auto, decided
    2026-07-14): transparent transfer_to_agent for every specialist, and NO
    request_handoff tool (confirm levels are reserved for job skills)."""
    parent = _skill(
        skill_id="one-assistant",
        delegation=DelegationConfig(
            enabled=True,
            allow=[
                {"skill": "one-ppa-expert", "floor": "auto"},
                {"skill": "web-researcher", "floor": "auto"},
                {"skill": "one-doc-compare", "floor": "auto"},
            ],
        ),
    )
    subs = {
        "one-ppa-expert": _skill(skill_id="one-ppa-expert", display_name="PPA"),
        "web-researcher": _skill(skill_id="web-researcher", display_name="Web"),
        "one-doc-compare": _skill(skill_id="one-doc-compare", display_name="Compare"),
    }
    with patch("adk.agent.get_skill", side_effect=lambda sid: subs.get(sid)):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 3
    assert "request_handoff" not in _tool_names(agent)


def test_delegate_resolved_by_slug_when_docid_is_uuid():
    """Deployed envs key skills by UUID doc-id, so a `delegation.allow` SLUG
    misses get_skill; the slug fallback (find_by_slug within the parent's owner)
    must still resolve it — else the deployed handoff silently finds zero
    delegates. Regression for the 2026-07-14 deployed-seed miss."""
    parent = _skill(skill_id="door", delegation=_deleg(["one-ppa-expert"], mode="auto"))
    child = _skill(skill_id="2c031269-uuid-not-the-slug", display_name="PPA")
    # get_skill(slug) misses (doc-id is the UUID); find_by_slug resolves it.
    with (
        patch("adk.agent.get_skill", return_value=None),
        patch("adk.agent.find_by_slug", return_value=child) as fbs,
    ):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1  # wired via the slug fallback
    fbs.assert_called()  # the fallback path was exercised


# --- graceful degradation --------------------------------------------------


def test_auto_mode_skips_delegate_that_fails_to_build():
    # A delegate whose build raises a non-cycle error is skipped; parent survives.
    from adk import agent as agent_mod

    parent = _skill(skill_id="parent", model="gemini-2.5-flash", delegation=_deleg(["child"], mode="auto"))
    child = _skill(skill_id="child", model="gemini-2.5-pro")  # distinct model to target
    real_resolve = agent_mod.resolve_model

    def flaky(model_id):
        if model_id == "gemini-2.5-pro":
            raise RuntimeError("boom building child")
        return real_resolve(model_id)

    with patch("adk.agent.get_skill", return_value=child), patch("adk.agent.resolve_model", side_effect=flaky):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []  # child skipped, parent still built


def test_auto_mode_cycle_still_raises():
    # Graceful degradation must NOT swallow cycle detection (a ValueError).
    import pytest

    skill = _skill(skill_id="loop", delegation=_deleg(["loop"], mode="auto"))
    with patch("adk.agent.get_skill", return_value=skill):
        with pytest.raises(ValueError, match="cycle"):
            create_agent(skill, _user(), access_context=_ctx())


# --- observability ---------------------------------------------------------


def test_mark_delegation_enqueues_agent_delegation_event():
    from observability import timing

    if not timing._ENABLED:  # off mode -> no events; skip assertion
        return
    tracker = timing.LatencyTracker(skill_id="parent", session_id="s", user_id="u")
    tracker.mark_delegation(parent="parent", target="ppa", target_display="PPA Specialist", mode="suggest")
    events = tracker.drain_stage_events()
    names = [e.name for e in events]
    assert timing.DELEGATION_EVENT_NAME in names
    ev = next(e for e in events if e.name == timing.DELEGATION_EVENT_NAME)
    assert ev.value["target"] == "ppa"
    assert ev.value["mode"] == "suggest"


class _FakeCtx:
    """Minimal before_agent callback_context: just a .state mapping."""

    def __init__(self):
        self.state = {}


def test_auto_delegate_before_agent_emits_agent_delegation():
    # A delegate agent's before_agent fires on activation (the transfer) and
    # must emit an auto-mode AGENT_DELEGATION marker with its display name.
    from adk.callbacks import make_before_agent
    from observability import timing

    if not timing._ENABLED:
        return
    tracker = timing.LatencyTracker(skill_id="child", session_id="s", user_id="u")
    token = timing.set_current_tracker(tracker)
    try:
        cb = make_before_agent("child", delegation_parent_id="parent", delegation_display="PPA Specialist")
        cb(_FakeCtx())
        events = tracker.drain_stage_events()
    finally:
        timing.reset_current_tracker(token)
    ev = next((e for e in events if e.name == timing.DELEGATION_EVENT_NAME), None)
    assert ev is not None
    assert ev.value["mode"] == "auto"
    assert ev.value["target"] == "child"
    assert ev.value["target_display"] == "PPA Specialist"


def test_permission_enforcer_exempts_transfer_to_agent():
    # transfer_to_agent is ADK-internal control flow, already access-gated at the
    # sub_agent-wiring layer. The tool-permission enforcer must NOT block it, or
    # auto-mode delegation breaks for users without explicit permission.
    from adk.callbacks import make_permission_enforcer

    enforcer = make_permission_enforcer("nobody@nowhere.test", "nowhere.test")

    class _Tool:
        name = "transfer_to_agent"

    class _Ctx:
        agent_name = "parent"

    assert enforcer(_Tool(), {"agent_name": "s_child"}, _Ctx()) is None  # no raise


def test_root_before_agent_emits_no_delegation():
    from adk.callbacks import make_before_agent
    from observability import timing

    tracker = timing.LatencyTracker(skill_id="root", session_id="s", user_id="u")
    token = timing.set_current_tracker(tracker)
    try:
        make_before_agent("root")(_FakeCtx())  # no delegation_parent_id
        events = tracker.drain_stage_events()
    finally:
        timing.reset_current_tracker(token)
    assert timing.DELEGATION_EVENT_NAME not in [e.name for e in events]
