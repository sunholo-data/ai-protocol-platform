"""Unit tests for access-scoped job discovery (v6.8.0 8.3 JOBS).

A "job" is a skill tagged `metadata.job: true` with a self-declared confirmation
`job_floor`. A door that opts in (`delegation.discover_jobs: true`) offers every
accessible job as a delegate WITHOUT a hand-maintained `allow` entry — but the
access filter stays the hard gate (discovery only narrows, never widens).

Contract under test:
  - SkillMetadata.job / job_floor and DelegationConfig.discover_jobs round-trip
  - find_jobs returns only job-tagged skills in the owner namespace
  - discover_jobs door offers an accessible auto-floor job as a sub_agent
  - a confirm-floor job is reachable via request_handoff, NOT a silent sub_agent
  - an inaccessible job is dropped (deny-by-default holds)
  - a job already in explicit `allow` is not double-wired
  - discover_jobs off (default) offers nothing extra
"""

from __future__ import annotations

from unittest.mock import patch

from adk.agent import _safe_agent_name, accessible_delegate_rules, create_agent
from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models import DelegationConfig, DelegationMode, SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _ctx(tags=()) -> AccessContext:
    return AccessContext(uid="u1", email="alice@example.com", domain="example.com", group_tags=frozenset(tags))


def _skill(
    *,
    skill_id: str,
    access: AccessControl | None = None,
    owner_id: str = "",
    delegation: DelegationConfig | None = None,
    job: bool = False,
    job_floor: str = "confirm",
) -> SkillConfig:
    return SkillConfig(
        name=skill_id,
        description=f"{skill_id} for job-discovery tests.",
        instructions="Do the thing.",
        skillId=skill_id,
        ownerId=owner_id,
        accessControl=access or AccessControl(type="public"),
        skillMetadata=SkillMetadata(
            model="gemini-2.5-flash",
            job=job,
            jobFloor=job_floor,
            delegation=delegation or DelegationConfig(),
        ),
    )


def _tool_names(agent) -> set[str]:
    out: set[str] = set()
    for t in agent.tools:
        # A tool may be a FunctionTool (.name / .func.__name__) or a raw callable
        # (.__name__ — request_handoff is appended as a bare function).
        name = (
            getattr(t, "name", None)
            or getattr(t, "__name__", None)
            or getattr(getattr(t, "func", None), "__name__", None)
        )
        if name:
            out.add(name)
    return out


# --- model round-trip ------------------------------------------------------


def test_job_flags_round_trip():
    md = SkillMetadata.model_validate({"job": True, "jobFloor": "confirm_with_fields"})
    assert md.job is True
    assert md.job_floor == "confirm_with_fields"
    deleg = DelegationConfig.model_validate({"enabled": True, "discoverJobs": True})
    assert deleg.discover_jobs is True
    # defaults
    assert SkillMetadata().job is False
    assert SkillMetadata().job_floor == "confirm"
    assert DelegationConfig().discover_jobs is False


# --- find_jobs -------------------------------------------------------------


def test_find_jobs_filters_by_flag():
    from skills.skill_config import find_jobs

    job_doc = {
        "name": "job-a",
        "description": "d",
        "instructions": "i",
        "skillId": "job-a",
        "ownerId": "aitana-platform",
        "accessControl": {"type": "public"},
        "skillMetadata": {"model": "gemini-2.5-flash", "job": True},
    }
    plain_doc = {**job_doc, "skillId": "plain", "name": "plain", "skillMetadata": {"model": "gemini-2.5-flash"}}
    with patch("skills.skill_config.fs.query_documents", return_value=[job_doc, plain_doc]):
        jobs = find_jobs("aitana-platform")
    assert [j.skill_id for j in jobs] == ["job-a"]


# --- discovery in create_agent --------------------------------------------


def test_discovery_offers_accessible_auto_job():
    door = _skill(skill_id="door", delegation=DelegationConfig(enabled=True, discover_jobs=True))
    job = _skill(skill_id="job-a", job=True, job_floor="auto", access=AccessControl(type="public"))
    with (
        patch("adk.agent.find_jobs", return_value=[job]),
        patch("adk.agent.get_skill", side_effect=lambda sid: {"job-a": job}.get(sid)),
    ):
        agent = create_agent(door, _user(), access_context=_ctx())
    assert [a.name for a in agent.sub_agents] == [_safe_agent_name("job-a")]


def test_discovery_confirm_job_wired_as_stub_sub_agent():
    """v6.10.0: a discovered confirm-floor job is a STUB sub_agent (reachable by
    the single transfer_to_agent); the floor policy lives in the
    before_tool_callback, not a separate request_handoff tool."""
    door = _skill(skill_id="door", delegation=DelegationConfig(enabled=True, discover_jobs=True))
    job = _skill(skill_id="job-a", job=True, job_floor="confirm", access=AccessControl(type="public"))
    with (
        patch("adk.agent.find_jobs", return_value=[job]),
        patch("adk.agent.get_skill", side_effect=lambda sid: {"job-a": job}.get(sid)),
    ):
        agent = create_agent(door, _user(), access_context=_ctx())
    assert len(agent.sub_agents) == 1
    assert "request_handoff" not in _tool_names(agent)
    assert agent.before_tool_callback is not None


def test_discovery_drops_inaccessible_job():
    door = _skill(skill_id="door", delegation=DelegationConfig(enabled=True, discover_jobs=True))
    job = _skill(
        skill_id="job-a",
        job=True,
        job_floor="auto",
        access=AccessControl(type="private"),
        owner_id="someone-else",
    )
    with (
        patch("adk.agent.find_jobs", return_value=[job]),
        patch("adk.agent.get_skill", side_effect=lambda sid: {"job-a": job}.get(sid)),
    ):
        agent = create_agent(door, _user(), access_context=_ctx())
    assert agent.sub_agents == []
    assert "request_handoff" not in _tool_names(agent)


def test_discovery_dedupes_against_explicit_allow():
    door = _skill(
        skill_id="door",
        delegation=DelegationConfig(enabled=True, discover_jobs=True, mode=DelegationMode.AUTO, allow=["job-a"]),
    )
    job = _skill(skill_id="job-a", job=True, job_floor="auto", access=AccessControl(type="public"))
    with (
        patch("adk.agent.find_jobs", return_value=[job]),
        patch("adk.agent.get_skill", side_effect=lambda sid: {"job-a": job}.get(sid)),
    ):
        agent = create_agent(door, _user(), access_context=_ctx())
    # job-a is BOTH pinned in allow and discovered — must wire exactly once.
    assert [a.name for a in agent.sub_agents] == [_safe_agent_name("job-a")]


def test_accessible_delegate_rules_is_the_validation_source():
    """accessible_delegate_rules returns the door's full access-filtered delegate
    set (allow + discovered jobs) — the set the confirm→switch loop validates a
    target against. An inaccessible job must NOT appear."""
    door = _skill(
        skill_id="door",
        delegation=DelegationConfig(enabled=True, discover_jobs=True, allow=["pinned"]),
    )
    pinned = _skill(skill_id="pinned", access=AccessControl(type="public"))
    ok_job = _skill(skill_id="job-ok", job=True, access=AccessControl(type="public"))
    bad_job = _skill(skill_id="job-bad", job=True, access=AccessControl(type="private"), owner_id="someone-else")
    lookup = {"pinned": pinned, "job-ok": ok_job, "job-bad": bad_job}
    with (
        patch("adk.agent.find_jobs", return_value=[ok_job, bad_job]),
        patch("adk.agent.get_skill", side_effect=lambda sid: lookup.get(sid)),
    ):
        pairs = accessible_delegate_rules(door, _ctx())
    ids = {sub.skill_id for sub, _rule in pairs}
    assert ids == {"pinned", "job-ok"}  # inaccessible job-bad dropped


def test_discovery_off_by_default_offers_nothing():
    door = _skill(skill_id="door", delegation=DelegationConfig(enabled=True))  # discover_jobs defaults False
    job = _skill(skill_id="job-a", job=True, job_floor="auto", access=AccessControl(type="public"))
    with (
        patch("adk.agent.find_jobs", return_value=[job]) as find_mock,
        patch("adk.agent.get_skill", side_effect=lambda sid: {"job-a": job}.get(sid)),
    ):
        agent = create_agent(door, _user(), access_context=_ctx())
    assert agent.sub_agents == []
    find_mock.assert_not_called()
