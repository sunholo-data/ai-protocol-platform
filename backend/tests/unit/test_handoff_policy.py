"""Unit tests for the unified ADK-native handoff (v6.10.0 unified-adk-handoff).

The door's ONLY handoff verb is ADK's native `transfer_to_agent`; a single
`before_tool_callback` (make_handoff_policy_callback) enforces each delegate's
FLOOR — auto passes through to the native transfer, confirm/cwf short-circuit
into the 8.1 elicitation envelope. Covers:
  * _delegate_agent_name — slug-derived, uuid fallback, collision suffix.
  * _build_handoff_envelope — floor decides kind; cwf-without-fields degrades.
  * make_handoff_policy_callback — the tool-name / unknown-target / floor matrix.
  * the render-registry trap (transfer_to_agent envelope actually renders).
"""

from __future__ import annotations

from types import SimpleNamespace

from adk.agent import (
    CONFIRM_DELEGATION_ACTION,
    _build_handoff_envelope,
    _delegate_agent_name,
    _safe_agent_name,
    make_handoff_policy_callback,
)
from db.models import DelegateRule, DelegationConfig, DelegationMode

# --- DelegationConfig.rules() (preserved from the pre-refactor suite) ---------


def test_bare_string_inherits_auto_floor():
    rules = DelegationConfig(allow=["ppa"]).rules()
    assert len(rules) == 1 and rules[0].skill == "ppa" and rules[0].floor == "auto"


def test_bare_string_under_suggest_mode_gets_confirm_floor():
    rules = DelegationConfig(mode=DelegationMode.SUGGEST, allow=["ppa"]).rules()
    assert rules[0].floor == "confirm"


def test_explicit_rule_floor_wins():
    rules = DelegationConfig(allow=[{"skill": "cmp", "floor": "confirm_with_fields"}]).rules()
    assert rules[0].skill == "cmp" and rules[0].floor == "confirm_with_fields"


def test_mixed_allow_entries():
    cfg = DelegationConfig(allow=["ppa", {"skill": "cmp", "floor": "confirm"}])
    assert {r.skill: r.floor for r in cfg.rules()} == {"ppa": "auto", "cmp": "confirm"}


def test_extra_skills_fold_in_and_dedup():
    rules = DelegationConfig(allow=["ppa"]).rules(extra_skills=["ppa", "legacy"])
    assert [r.skill for r in rules] == ["ppa", "legacy"] and all(r.floor == "auto" for r in rules)


def _sub(skill_id: str, display: str, desc: str = "a specialist", slug: str | None = None):
    return SimpleNamespace(skill_id=skill_id, display_name=display, name=display, description=desc, slug=slug)


def _tool(name: str):
    return SimpleNamespace(name=name)


# --- _delegate_agent_name ----------------------------------------------------


def test_agent_name_is_slug_derived():
    taken: set[str] = set()
    name = _delegate_agent_name(
        _sub("26124699-f558-4096", "PPA Obligation Analysis", slug="demo-obligation-analysis"), taken
    )
    assert name == "demo_obligation_analysis"
    assert name in taken


def test_agent_name_falls_back_to_uuid_form_when_no_slug():
    uid = "26124699-f558-4096-a4a8-a9f73f27eb26"
    name = _delegate_agent_name(_sub(uid, "X", slug=None), set())
    assert name == _safe_agent_name(uid)


def test_agent_name_disambiguates_collisions():
    taken: set[str] = set()
    a = _delegate_agent_name(_sub("id1", "A", slug="ppa"), taken)
    b = _delegate_agent_name(_sub("id2", "B", slug="ppa"), taken)
    assert a == "ppa" and b == "ppa_2"


# --- _build_handoff_envelope -------------------------------------------------


def test_confirm_floor_builds_confirm_envelope():
    env = _build_handoff_envelope(
        "door", _sub("cmp", "Doc Compare", "compares two contracts"), DelegateRule(skill="cmp", floor="confirm")
    )
    assert env.kind == "confirm"
    assert env.action == CONFIRM_DELEGATION_ACTION
    assert env.context == {"target_skill_id": "cmp", "parent_skill_id": "door"}
    assert "Doc Compare" in env.message and "compares two contracts" in env.message
    assert env.fields == []


def test_cwf_floor_with_fields_builds_form():
    rule = DelegateRule(
        skill="job",
        floor="confirm_with_fields",
        fields=[{"name": "market", "type": "select", "label": "Market", "options": ["ES", "AR"]}],
    )
    env = _build_handoff_envelope("door", _sub("job", "Some Job"), rule)
    assert env.kind == "confirm_with_fields"
    assert [f.name for f in env.fields] == ["market"]


def test_cwf_floor_without_fields_degrades_to_confirm():
    env = _build_handoff_envelope(
        "door", _sub("job", "Some Job"), DelegateRule(skill="job", floor="confirm_with_fields")
    )
    assert env.kind == "confirm"  # never an invalid empty form


# --- make_handoff_policy_callback --------------------------------------------


def _map(*entries):
    return {name: (sub, rule) for name, sub, rule in entries}


def test_non_transfer_tool_passes_through():
    cb = make_handoff_policy_callback(
        "door", _map(("ppa", _sub("ppa", "PPA"), DelegateRule(skill="ppa", floor="confirm")))
    )
    assert cb(tool=_tool("map_ppa_obligations"), args={}, tool_context=None) is None


def test_unknown_agent_name_passes_through_to_adk_validation():
    cb = make_handoff_policy_callback(
        "door", _map(("ppa", _sub("ppa", "PPA"), DelegateRule(skill="ppa", floor="confirm")))
    )
    assert cb(tool=_tool("transfer_to_agent"), args={"agent_name": "nope"}, tool_context=None) is None


def test_auto_floor_passes_through_to_native_transfer():
    cb = make_handoff_policy_callback(
        "door", _map(("web", _sub("web", "Web"), DelegateRule(skill="web", floor="auto")))
    )
    assert cb(tool=_tool("transfer_to_agent"), args={"agent_name": "web"}, tool_context=None) is None


def test_confirm_floor_short_circuits_with_elicitation():
    cb = make_handoff_policy_callback(
        "door",
        _map(
            (
                "one_obl",
                _sub("26124699-f558", "PPA Obligation Analysis"),
                DelegateRule(skill="26124699-f558", floor="confirm"),
            )
        ),
    )
    out = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "one_obl"}, tool_context=None)
    assert out is not None
    assert out["needs_input"] is True and out["placement"] == "chat"
    env = out["elicitation"]
    assert env["kind"] == "confirm"
    assert env["action"] == CONFIRM_DELEGATION_ACTION
    # Canonical target id in the context — the frontend switch navigates by it.
    assert env["context"]["target_skill_id"] == "26124699-f558"


def test_cwf_floor_short_circuits_with_form():
    rule = DelegateRule(
        skill="job", floor="confirm_with_fields", fields=[{"name": "capacity", "type": "number", "label": "Capacity"}]
    )
    cb = make_handoff_policy_callback("door", _map(("job", _sub("job", "Some Job"), rule)))
    out = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "job"}, tool_context=None)
    assert out["elicitation"]["kind"] == "confirm_with_fields"
    assert [f["name"] for f in out["elicitation"]["fields"]] == ["capacity"]


def test_confirm_short_circuit_pauses_the_turn():
    """The reported 2026-07-22 spam loop: a confirm-floor short-circuit that does
    NOT set skip_summarization leaves the turn non-final, so ADK re-invokes the
    lite front-door, which re-issues transfer_to_agent → another card → repeat.
    The policy must pause the turn on the card (mirrors ADK get_user_choice)."""

    class _Actions:
        skip_summarization = False

    ctx = SimpleNamespace(actions=_Actions(), state={})
    cb = make_handoff_policy_callback(
        "door",
        _map(
            (
                "one_obl",
                _sub("26124699-f558", "PPA Obligation Analysis"),
                DelegateRule(skill="26124699-f558", floor="confirm"),
            )
        ),
    )
    out = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "one_obl"}, tool_context=ctx)
    assert out is not None and out["needs_input"] is True
    assert ctx.actions.skip_summarization is True, "confirm card must end the turn, not loop the model"


# --- render-registry trap ----------------------------------------------------


def test_transfer_to_agent_envelope_renders_via_registry():
    """The policy returns the envelope AS transfer_to_agent's result — the render
    registry keys on the TOOL NAME, so transfer_to_agent must be registered
    (the recurring A2UI-won't-render trap)."""
    import adk.a2ui_elicitation_render  # noqa: F401 — registers the transform
    from adk.a2ui_result_render import is_render_payload_tool, render_for_emit

    cb = make_handoff_policy_callback(
        "door", _map(("cmp", _sub("cmp", "Doc Compare"), DelegateRule(skill="cmp", floor="confirm")))
    )
    out = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "cmp"}, tool_context=None)
    rendered = render_for_emit("transfer_to_agent", out)
    assert rendered is not None, "transfer_to_agent card must render (tool_names gate)"
    assert rendered.artifact and rendered.artifact.get("placement") == "chat"
    assert is_render_payload_tool("transfer_to_agent")


# --- channels with no clickable UI: confirm in TEXT ---------------------------


def _ctx(invocation: str, state: dict):
    return SimpleNamespace(invocation_id=invocation, state=state)


def _writer_cb():
    return make_handoff_policy_callback(
        "door", _map(("writer", _sub("dw-1", "Document Writer"), DelegateRule(skill="dw-1", floor="confirm")))
    )


def test_channel_confirm_asks_in_text_then_transfers_on_the_next_turn():
    """Discord can't press an elicitation button — a form there re-asked forever."""
    from adk.elicitation import NON_INTERACTIVE

    cb, state = _writer_cb(), {}
    token = NON_INTERACTIVE.set(True)
    try:
        first = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-1", state))
        assert first["status"] == "needs_confirmation" and "elicitation" not in first
        assert "plain text" in first["message"]
        # The model retrying in the SAME turn is not the person agreeing.
        again = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-1", state))
        assert again is not None
        # The person replied "yes" -> a LATER turn calls the handoff again -> transfer.
        assert (
            cb(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-2", state))
            is None
        )
        assert "dw-1" not in state["channel_handoff_asked"]  # consumed: the next handoff asks again
    finally:
        NON_INTERACTIVE.reset(token)


def test_stale_text_confirmation_expires(monkeypatch):
    import time as _time

    from adk.elicitation import NON_INTERACTIVE

    cb, state = _writer_cb(), {}
    token = NON_INTERACTIVE.set(True)
    try:
        cb(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-1", state))
        state["channel_handoff_asked"]["dw-1"]["at"] = _time.time() - 31 * 60
        out = cb(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-2", state))
        assert out is not None and out["status"] == "needs_confirmation"  # asks again
    finally:
        NON_INTERACTIVE.reset(token)


def test_web_still_gets_the_clickable_form():
    out = _writer_cb()(tool=_tool("transfer_to_agent"), args={"agent_name": "writer"}, tool_context=_ctx("inv-1", {}))
    assert out["needs_input"] is True and out["elicitation"]["kind"] == "confirm"


def test_self_transfer_is_carried_on_not_crashed():
    """Live 2026-10-27: after a confirmed handoff the delegate called the handoff
    to ITSELF and ADK failed the whole run ("cannot transfer to itself")."""
    from adk.agent import refuse_self_transfer

    ctx = SimpleNamespace(agent_name="document_writer", state={})
    out = refuse_self_transfer(_tool("transfer_to_agent"), {"agent_name": "document_writer"}, ctx)
    assert out["status"] == "already_active"
    # A real handoff to someone else, and other tools, are untouched.
    assert refuse_self_transfer(_tool("transfer_to_agent"), {"agent_name": "memo-writer"}, ctx) is None
    assert refuse_self_transfer(_tool("generate_document"), {"agent_name": "document_writer"}, ctx) is None
