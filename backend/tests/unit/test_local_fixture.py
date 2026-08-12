"""Tests for db.local_fixture — workshop fixture seeding.

The seeder must be idempotent (re-runs don't duplicate) and a no-op when
LOCAL_MODE is off.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_firestore_singleton():
    from db import firestore

    firestore._reset_client_for_testing()
    yield
    firestore._reset_client_for_testing()


def test_seed_no_op_when_local_mode_off(monkeypatch):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    from db.local_fixture import seed_local_fixture

    # Should silently no-op; should not even instantiate a client.
    seed_local_fixture()


def test_seed_populates_collections_in_local_mode(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    client = get_client()
    assert len(list(client.collection("users").stream())) == 1
    # Workshop/demo (owned by workshop-user): researcher (W2),
    # form-builder (W6), map-explorer (W7),
    # workspace (sprint 2.9 — read-only dashboard demo),
    # workspace-interactive (sprint 2.10 follow-up — discrete-action loop),
    # click-counter (sprint 1.21 — Pattern 1 click-driven AI UI),
    # workshop-helper (Path B — RAG over the docs corpus),
    # skill-authoring-assistant (v6.6.0 M3 — Skill Studio copilot),
    # plus web-researcher (owned by PLATFORM_OWNER_UID) = 9.
    # ONE production (owned by PLATFORM_OWNER_UID): one-assistant (8.2 front
    # door), one-ppa-expert, one-obligation-analysis (8.3 job), one-doc-compare
    # — seeded only when their SKILL.md template ships (the customer templates
    # are stripped from the public template fork, where this must still pass).
    from db.local_fixture import _TEMPLATES_DIR

    one_skills = ("one-assistant", "one-ppa-expert", "one-obligation-analysis", "one-doc-compare")
    expected = 9 + sum(1 for s in one_skills if (_TEMPLATES_DIR / s / "SKILL.md").exists())
    assert len(list(client.collection("skills").stream())) == expected
    assert len(list(client.collection("documents").stream())) == 1


def test_seed_is_idempotent(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    seed_local_fixture()
    seed_local_fixture()
    client = get_client()
    # Counts unchanged after multiple seeds.
    from db.local_fixture import _demo_skills

    assert len(list(client.collection("users").stream())) == 1
    assert len(list(client.collection("skills").stream())) == len(_demo_skills(0.0))


def test_seeded_skills_have_required_fields(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture
    from skills.platform import PLATFORM_OWNER_UID

    seed_local_fixture()
    client = get_client()
    skills = [s.to_dict() for s in client.collection("skills").stream()]
    for s in skills:
        assert s["skillId"]
        assert s["displayName"]
        assert s["description"]
        assert s["instructions"]
        assert s["accessControl"]["type"] == "public"
        # Workshop/demo skills are owned by the workshop-user; the ONE
        # production skills (ONE-FORK-CONVERGENCE) are owned by the
        # platform owner. Both are valid seed owners.
        assert s["ownerId"] in ("workshop-user", PLATFORM_OWNER_UID)


def test_tool_permissions_wildcard_seeded(monkeypatch):
    """The LOCAL_MODE workshop sandbox needs a wildcard tool_permissions
    rule so the workshop-user can invoke any tool — including the agent's
    own A2UI emit (send_a2ui_json_to_client). Without this, the demo
    skill agent gets ToolPermissionDenied on its first tool call. Spotted
    2026-05-18 against the workspace-demo skill end-to-end.
    """
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    client = get_client()
    wildcard = client.collection("tool_permissions").document("*").get().to_dict()
    assert wildcard is not None, "wildcard tool_permissions doc must be seeded"
    assert wildcard["tools"] == ["*"]
    assert wildcard["denied"] == []

    # End-to-end: can_use_tool() honours the wildcard.
    from auth.permissions import can_use_tool, clear_cache

    clear_cache()
    assert can_use_tool("workshop@local", "local", "send_a2ui_json_to_client") is True
    assert can_use_tool("workshop@local", "local", "any_other_tool") is True


def test_workspace_demo_skill_has_surface_config(monkeypatch):
    """MULTI-SURFACE-A2UI sprint 2.9 — the workspace demo skill is the
    deterministic end-to-end demo for the surface routing infrastructure.
    Its `default_surface: workspace` is the critical wiring; without it
    the skill emits A2UI inline-in-chat and the workspace pane stays
    empty. Regression-guards that the fixture stays in sync with the
    surface-rendering howto's "try the demo skill" instructions.
    """
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    client = get_client()
    skill = client.collection("skills").document("demo-workspace").get().to_dict()
    assert skill is not None, "demo-workspace skill not seeded"
    a2ui_cfg = skill["skillMetadata"]["toolConfigs"]["a2ui"]
    assert a2ui_cfg["default_surface"] == "workspace"
    assert a2ui_cfg["default_update_mode"] == "replace"
    # Instructions must reference the trigger phrases the agent listens
    # for — if these drift, the user won't get a deterministic demo.
    assert "show me the dashboard" in skill["instructions"].lower()
    assert "refresh" in skill["instructions"].lower()


def test_workspace_demo_interactive_skill_opts_into_surface_writes(monkeypatch):
    """Sprint 2.10 follow-up — the interactive demo skill MUST set
    `allow_surface_context_writes: true` because the whole point of
    the demo is the action POST → lastAction loop. Without that flag
    the POST returns 403 default-deny and the agent never sees the
    user's submission. Regression-guards the wiring."""
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    client = get_client()
    skill = client.collection("skills").document("demo-workspace-interactive").get().to_dict()
    assert skill is not None, "demo-workspace-interactive skill not seeded"
    a2ui_cfg = skill["skillMetadata"]["toolConfigs"]["a2ui"]
    assert a2ui_cfg["default_surface"] == "workspace"
    assert a2ui_cfg["allow_surface_context_writes"] is True
    # Trigger phrases the agent listens for — both halves of the loop
    # must be present.
    instructions = skill["instructions"].lower()
    assert "show me the form" in instructions
    assert "lastaction" in instructions  # the read-back instruction


def test_click_counter_skill_opts_into_action_triggered_runs(monkeypatch):
    """ACTION-TRIGGER M3.1 (sprint 1.21) — the click-counter demo MUST set
    BOTH ``allow_surface_context_writes`` AND ``allow_action_triggered_runs``
    on its a2ui tool config, because the entire point of Pattern 1 is that
    a click both writes lastAction AND runs an agent turn against the
    bundled write+run endpoint. Without either flag the surface click is
    a 403 default-deny. Regression-guards the contract pinned by the M3
    parallel sprints (CLI + smoke script + frontend hook all depend on
    these two flags being set on this skill id)."""
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()
    client = get_client()
    skill = client.collection("skills").document("demo-click-counter").get().to_dict()
    assert skill is not None, "demo-click-counter skill not seeded"
    assert skill["displayName"] == "Demo Click Counter"
    a2ui_cfg = skill["skillMetadata"]["toolConfigs"]["a2ui"]
    assert a2ui_cfg["allow_surface_context_writes"] is True
    assert a2ui_cfg["allow_action_triggered_runs"] is True
    # The agent prompt must teach the model to honour the action-trigger
    # branch — without that the click leads to a chat-style text reply
    # rather than a re-emitted surface (Pattern 1 contract violation).
    instructions = skill["instructions"].lower()
    assert "_action_trigger" in instructions
    assert "increment" in instructions
    # The shared contract pins surfaceId=counter-main, action=increment.
    # Smoke script and CLI subcommand both encode these strings.
    assert "counter-main" in skill["instructions"]


def test_seeded_user_matches_stub_identity(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")

    from db.firestore import get_client
    from db.local_fixture import (
        WORKSHOP_USER_EMAIL,
        WORKSHOP_USER_UID,
        seed_local_fixture,
    )

    seed_local_fixture()
    client = get_client()
    user = client.collection("users").document(WORKSHOP_USER_UID).get().to_dict()
    assert user["userId"] == WORKSHOP_USER_UID
    assert user["email"] == WORKSHOP_USER_EMAIL
