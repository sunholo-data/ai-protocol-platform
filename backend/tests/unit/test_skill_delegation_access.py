"""Unit tests for access-aware skill delegation (SKILL-DELEGATION M1).

Delegation lets a skill hand a turn to an allow-listed specialist skill. The
security invariant: a delegate is wired as a `sub_agent` ONLY when the
requesting user can access it — the `delegation.allow` list is a ceiling, not
a grant. This closes the pre-v6.7.0 gap where `create_agent` recursed
`sub_skills` through `get_skill` with no access check and an inert
`access_context`.

Contract under test (adk.agent.create_agent):
  - delegation off (default) -> no sub_agents
  - delegation on + user CAN access delegate -> delegate wired
  - delegation on + user CANNOT access delegate -> delegate dropped (deny-by-default)
  - transitive: an inaccessible delegate is never built even via an accessible parent
  - unknown delegate id -> skipped + warned
  - max_depth bounds the delegation chain
  - access_context is None -> no delegates built (fail-safe; the request path always supplies it)
  - legacy `subSkills` list is access-filtered too (gap closed for the deprecated field)
"""

from __future__ import annotations

from unittest.mock import patch

from google.adk.agents import LlmAgent

from adk.agent import _safe_agent_name, create_agent, create_agent_with_thinking
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegationConfig, DelegationMode, SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx(uid: str = "u1", email: str = "alice@example.com", domain: str = "example.com", tags=()) -> AccessContext:
    return AccessContext(uid=uid, email=email, domain=domain, group_tags=frozenset(tags))


def _skill(
    *,
    skill_id: str,
    name: str = "skill",
    access: AccessControl | None = None,
    owner_id: str = "",
    delegation: DelegationConfig | None = None,
    sub_skills: list[str] | None = None,
) -> SkillConfig:
    return SkillConfig(
        name=name,
        description=f"{name} for delegation tests.",
        instructions="Do the thing.",
        skillId=skill_id,
        ownerId=owner_id,
        accessControl=access or AccessControl(type="public"),
        skillMetadata=SkillMetadata(
            model="gemini-2.5-flash",
            subSkills=sub_skills or [],
            delegation=delegation or DelegationConfig(),
        ),
    )


def _deleg(allow: list[str], *, enabled: bool = True, mode: str = "auto", max_depth: int = 1) -> DelegationConfig:
    return DelegationConfig(enabled=enabled, mode=DelegationMode(mode), allow=allow, max_depth=max_depth)


# --- default off -----------------------------------------------------------


def test_delegation_disabled_builds_no_sub_agents():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"], enabled=False))
    child = _skill(skill_id="child")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []


# --- access gating ---------------------------------------------------------


def test_delegation_wires_accessible_delegate():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="public"))
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    assert agent.sub_agents[0].name == _safe_agent_name("child")


def test_delegation_drops_inaccessible_delegate():
    # Child is private, owned by someone else -> alice cannot access it.
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="private"), owner_id="someone-else")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []


def test_delegation_tagged_delegate_requires_matching_tag():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="tagged", tags=["ONE"]))
    # user without the ONE tag -> dropped
    with patch("adk.agent.get_skill", return_value=child):
        assert create_agent(parent, _user(), access_context=_ctx()).sub_agents == []
    # user WITH the ONE tag -> wired
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx(tags=["ONE"]))
    assert len(agent.sub_agents) == 1


def test_delegation_transitive_denial():
    # A (accessible) delegates to B (inaccessible). B must never be built.
    a = _skill(skill_id="A", delegation=_deleg(["B"]))
    b = _skill(skill_id="B", access=AccessControl(type="private"), owner_id="someone-else")
    lookup = {"A": a, "B": b}
    with patch("adk.agent.get_skill", side_effect=lambda sid: lookup.get(sid)):
        agent = create_agent(a, _user(), access_context=_ctx())
    assert agent.sub_agents == []


def test_delegation_unknown_delegate_skipped(caplog):
    parent = _skill(skill_id="parent", delegation=_deleg(["missing"]))
    with patch("adk.agent.get_skill", return_value=None):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []


# --- depth -----------------------------------------------------------------


def test_delegation_respects_max_depth():
    # A -> B -> C with max_depth=1: A delegates B, but B may not delegate C.
    a = _skill(skill_id="A", delegation=_deleg(["B"], max_depth=1))
    b = _skill(skill_id="B", delegation=_deleg(["C"], max_depth=1))
    c = _skill(skill_id="C")
    lookup = {"A": a, "B": b, "C": c}
    with patch("adk.agent.get_skill", side_effect=lambda sid: lookup.get(sid)):
        agent = create_agent(a, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1  # B
    b_agent = agent.sub_agents[0]
    assert b_agent.sub_agents == []  # C not built (depth cap)


# --- fail-safe on missing context -----------------------------------------


def test_delegation_without_access_context_builds_nothing():
    # A caller that enables delegation but supplies no access_context must NOT
    # get delegates — the request path always supplies one; a None here is a bug,
    # and failing safe closes the escalation gap.
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="public"))
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=None)
    assert agent.sub_agents == []


# --- legacy subSkills alias (gap closed for deprecated field) ---------------


def test_legacy_sub_skills_are_access_filtered():
    # Deprecated `subSkills` still delegates, but now honours access control.
    parent = _skill(skill_id="parent", sub_skills=["child"])
    child = _skill(skill_id="child", access=AccessControl(type="private"), owner_id="someone-else")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert agent.sub_agents == []  # inaccessible -> dropped (was a leak pre-v6.7.0)


def test_legacy_sub_skills_accessible_still_wire():
    parent = _skill(skill_id="parent", sub_skills=["child"])
    child = _skill(skill_id="child", access=AccessControl(type="public"))
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1


# --- threading through the thinking factory --------------------------------


def test_access_context_threaded_through_thinking_factory():
    # create_agent_with_thinking must forward access_context so the filter is
    # not inert. A denying context -> no delegate on the resulting agent.
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="private"), owner_id="someone-else")
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent_with_thinking(parent, _user(), access_context=_ctx())
    assert isinstance(agent, LlmAgent)
    assert agent.sub_agents == []


def test_access_context_threaded_allows_accessible_delegate():
    parent = _skill(skill_id="parent", delegation=_deleg(["child"]))
    child = _skill(skill_id="child", access=AccessControl(type="public"))
    with patch("adk.agent.get_skill", return_value=child):
        agent = create_agent_with_thinking(parent, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
