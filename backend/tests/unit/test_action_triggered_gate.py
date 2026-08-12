"""Gate 8 (action-triggered runs) is delegation-aware (v6.15.0).

Handoffs from a front door are transparent, so a DELEGATE's tool can produce an
elicitation form that renders in the DOOR's chat — and Submit posts against the
door's skill id. The grant sits on the delegate, so the gate must accept that;
but deny-by-default must still hold when nothing is opted in.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from auth.firebase_auth import User
from db.models import SkillConfig, SkillMetadata
from protocols import _a2ui_surface_shared as sh
from protocols import a2ui_surface_action_run_routes as rt


def _user() -> User:
    return User(uid="u1", email="a@x.com", domain="x.com")


def _skill(skill_id: str, *, opted_in: bool, writes: bool = False) -> SkillConfig:
    return SkillConfig(
        skillId=skill_id,
        name=skill_id,
        skill_metadata=SkillMetadata(
            tool_configs={
                "a2ui": {
                    "allow_action_triggered_runs": opted_in,
                    "allow_surface_context_writes": writes,
                }
            }
        ),
    )


def _patch(monkeypatch, *, door: SkillConfig, delegates: list[SkillConfig] | Exception):
    monkeypatch.setattr(rt.skill_config, "get_skill", lambda _sid: door)

    def _rules(_parent, _access):
        if isinstance(delegates, Exception):
            raise delegates
        return [(d, object()) for d in delegates]

    monkeypatch.setattr("adk.agent.accessible_delegate_rules", _rules)


def test_skill_opted_in_passes(monkeypatch):
    _patch(monkeypatch, door=_skill("door", opted_in=True), delegates=[])
    rt._enforce_action_triggered_opt_in("door", _user())  # no raise


def test_delegate_opted_in_allows_the_door(monkeypatch):
    # The door itself is NOT opted in — its opted-in delegate produced the form.
    _patch(
        monkeypatch,
        door=_skill("door", opted_in=False),
        delegates=[_skill("plain", opted_in=False), _skill("specialist", opted_in=True)],
    )
    rt._enforce_action_triggered_opt_in("door", _user())  # no raise


def test_no_grant_anywhere_is_403(monkeypatch):
    _patch(monkeypatch, door=_skill("door", opted_in=False), delegates=[])
    with pytest.raises(HTTPException) as exc:
        rt._enforce_action_triggered_opt_in("door", _user())
    assert exc.value.status_code == 403


def test_delegate_without_grant_does_not_unlock(monkeypatch):
    # Deny-by-default: having delegates is not enough — one must be opted in.
    _patch(monkeypatch, door=_skill("door", opted_in=False), delegates=[_skill("plain", opted_in=False)])
    with pytest.raises(HTTPException) as exc:
        rt._enforce_action_triggered_opt_in("door", _user())
    assert exc.value.status_code == 403


def test_delegate_resolution_error_fails_closed(monkeypatch):
    # A resolution failure must DENY, never open the gate.
    _patch(monkeypatch, door=_skill("door", opted_in=False), delegates=RuntimeError("firestore down"))
    with pytest.raises(HTTPException) as exc:
        rt._enforce_action_triggered_opt_in("door", _user())
    assert exc.value.status_code == 403


def test_missing_skill_is_403(monkeypatch):
    monkeypatch.setattr(rt.skill_config, "get_skill", lambda _sid: None)
    with pytest.raises(HTTPException) as exc:
        rt._enforce_action_triggered_opt_in("gone", _user())
    assert exc.value.status_code == 403


# --- Gate 6 (surface-context writes) is delegation-aware too --------------------
# Gate 6 runs BEFORE gate 8 on surface-action-run, so without this the gate-8 fix
# above is unreachable for a pure front door (the 2026-07-21 audit finding).


def _patch_shared(monkeypatch, *, door: SkillConfig, delegates: list[SkillConfig] | Exception):
    monkeypatch.setattr(sh.skill_config, "get_skill", lambda _sid: door)

    def _rules(_parent, _access):
        if isinstance(delegates, Exception):
            raise delegates
        return [(d, object()) for d in delegates]

    monkeypatch.setattr("adk.agent.accessible_delegate_rules", _rules)


def test_gate6_delegate_with_writes_unlocks_the_door(monkeypatch):
    _patch_shared(
        monkeypatch,
        door=_skill("door", opted_in=False, writes=False),
        delegates=[_skill("specialist", opted_in=True, writes=True)],
    )
    sh._enforce_skill_opt_in("door", _user())  # no raise


def test_gate6_no_grant_anywhere_is_403(monkeypatch):
    _patch_shared(
        monkeypatch,
        door=_skill("door", opted_in=False, writes=False),
        delegates=[_skill("plain", opted_in=False, writes=False)],
    )
    with pytest.raises(HTTPException) as exc:
        sh._enforce_skill_opt_in("door", _user())
    assert exc.value.status_code == 403


def test_gate6_resolution_error_fails_closed(monkeypatch):
    _patch_shared(
        monkeypatch,
        door=_skill("door", opted_in=False, writes=False),
        delegates=RuntimeError("firestore down"),
    )
    with pytest.raises(HTTPException) as exc:
        sh._enforce_skill_opt_in("door", _user())
    assert exc.value.status_code == 403


def test_gate6_and_gate8_both_pass_for_a_pure_front_door(monkeypatch):
    """End-to-end of the real scenario: a door with NO a2ui grants at all,
    fronting a specialist that has both. Both gates must pass."""
    door = SkillConfig(skillId="door", name="door", skill_metadata=SkillMetadata(tool_configs={}))
    specialist = _skill("specialist", opted_in=True, writes=True)
    monkeypatch.setattr(sh.skill_config, "get_skill", lambda _sid: door)
    monkeypatch.setattr(rt.skill_config, "get_skill", lambda _sid: door)
    monkeypatch.setattr("adk.agent.accessible_delegate_rules", lambda _p, _a: [(specialist, object())])
    sh._enforce_skill_opt_in("door", _user())  # gate 6
    rt._enforce_action_triggered_opt_in("door", _user())  # gate 8
