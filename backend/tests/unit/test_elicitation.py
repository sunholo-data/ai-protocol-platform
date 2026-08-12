"""Unit tests for the reusable elicitation-in-chat primitive (v6.8.0 8.1).

Guards the extraction that WS2 (elicited handoff) builds on:
  * the envelope/field schema (confirm vs confirm_with_fields; select needs options),
  * ``request_confirmation`` result shape,
  * the generic ``elicitation_form_to_a2ui`` transform — schema-validated against
    the REAL A2UI v0.9 Basic catalog (the guard against the silent render trap),
    covering all five field types + the confirm kind,
  * ``read_submitted_values`` surface read-back,
  * registry wiring (importing the module registers the mapping).
"""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pytest

# Importing this module registers the mapping as a side effect.
from adk.a2ui_elicitation_render import elicitation_form_to_a2ui, is_elicitation
from adk.a2ui_result_render import render_for_emit
from adk.elicitation import (
    ElicitationEnvelope,
    ElicitationField,
    make_elicitation_result,
    read_submitted_values,
    request_confirmation,
)


@functools.lru_cache(maxsize=1)
def _validator():
    from a2ui.basic_catalog import BasicCatalog
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.schema.validator import A2uiValidator

    config = BasicCatalog.get_config("0.9")
    catalog = A2uiSchemaManager(version="0.9", catalogs=[config])._supported_catalogs[0]
    return A2uiValidator(catalog)


def _assert_valid_v09(messages: list[dict]) -> None:
    """Schema-validate against the real Basic catalog (tolerates a trailing
    updateDataModel seed message beyond createSurface + updateComponents)."""
    assert isinstance(messages, list) and len(messages) >= 2
    assert "createSurface" in messages[0]
    assert "updateComponents" in messages[1]
    _validator().validate(messages)


def _components(messages: list[dict]) -> list[dict]:
    return messages[1]["updateComponents"]["components"]


def _of_type(messages: list[dict], component: str) -> list[dict]:
    return [c for c in _components(messages) if c.get("component") == component]


def _seed(messages: list[dict]) -> dict:
    for msg in messages:
        if "updateDataModel" in msg:
            return msg["updateDataModel"].get("value") or {}
    return {}


# --- schema validation -------------------------------------------------------


def test_select_without_options_degrades_to_text_not_crash():
    # A select with no options can't render a picker — degrade to text rather
    # than raise, so an AI-authored form never RUN_ERRORs the turn.
    f = ElicitationField(name="x", type="select", label="X")
    assert f.type == "text"
    # options present → stays a select
    assert ElicitationField(name="x", type="select", label="X", options=["a", "b"]).type == "select"


def test_field_type_normalizes_ai_vocabulary():
    """The model authors natural type names; the engine normalizes them (live
    2026-07-16: Gemini authored type='dropdown' and RUN_ERROR'd the turn)."""
    cases = {
        "dropdown": "select",
        "checkbox": "bool",
        "boolean": "bool",
        "string": "text",
        "integer": "number",
        "float": "number",
        "datetime": "date",
        "SELECT": "select",
        " Text ": "text",
    }
    for raw, want in cases.items():
        opts = ["a", "b"] if want == "select" else None
        assert ElicitationField(name="n", type=raw, label="L", options=opts).type == want
    # an entirely unknown type degrades to text, never raises
    assert ElicitationField(name="n", type="hologram", label="L").type == "text"


def test_request_confirmation_skips_malformed_field_not_crash():
    from adk.elicitation import request_confirmation

    # One good field + one malformed (missing name/label) → the good one survives,
    # the bad one is skipped, and the call does NOT raise.
    out = request_confirmation(
        message="Set prefs",
        kind="confirm_with_fields",
        fields=[
            {"name": "market", "type": "dropdown", "label": "Market", "options": ["ES", "DK"]},
            {"type": "text"},  # no name/label — malformed
        ],
    )
    fields = out["elicitation"]["fields"]
    assert [f["name"] for f in fields] == ["market"]
    assert fields[0]["type"] == "select"  # 'dropdown' normalized


def test_envelope_kind_invariants():
    with pytest.raises(ValueError):
        ElicitationEnvelope(kind="confirm_with_fields")  # no fields
    with pytest.raises(ValueError):
        ElicitationEnvelope(kind="confirm")  # no message/reason
    # valid confirm
    ElicitationEnvelope(kind="confirm", message="Proceed?")
    # valid confirm_with_fields
    ElicitationEnvelope(kind="confirm_with_fields", fields=[ElicitationField(name="a", label="A")])


# --- request_confirmation ----------------------------------------------------


def test_request_confirmation_result_shape():
    result = request_confirmation("Hand off to the PPA specialist?")
    assert result["needs_input"] is True
    assert result["placement"] == "chat"
    env = result["elicitation"]
    assert env["kind"] == "confirm"
    assert env["message"] == "Hand off to the PPA specialist?"
    assert is_elicitation(result)


def test_request_confirmation_with_fields_carries_context():
    result = request_confirmation(
        "Compare which two contracts?",
        kind="confirm_with_fields",
        fields=[{"name": "jurisdiction", "type": "select", "label": "Jurisdiction", "options": ["ES", "AR"]}],
        action="confirm_delegation",
        context={"target_skill_id": "one-doc-compare"},
    )
    env = result["elicitation"]
    assert env["action"] == "confirm_delegation"
    assert env["context"] == {"target_skill_id": "one-doc-compare"}


# --- turn-pause (skip_summarization) — the 2026-07-22 confirm-spam fix --------
# An elicitation is a wait-for-the-user boundary: ADK must NOT re-invoke the
# model after the card, or a lite front-door re-issues transfer_to_agent every
# round → the runaway of "Confirm" cards + delegation markers reported live.
# make_elicitation_result mirrors ADK's own get_user_choice (skip_summarization).


class _Actions:  # minimal EventActions stand-in
    skip_summarization = False


def _ctx_with_actions() -> SimpleNamespace:
    return SimpleNamespace(actions=_Actions(), state={})


def test_make_elicitation_result_pauses_turn():
    ctx = _ctx_with_actions()
    make_elicitation_result(
        ElicitationEnvelope(kind="confirm", action="confirm_delegation", message="Hand off?"),
        tool_context=ctx,
    )
    assert ctx.actions.skip_summarization is True


def test_request_confirmation_pauses_turn():
    ctx = _ctx_with_actions()
    request_confirmation("Proceed with the handoff?", tool_context=ctx)
    assert ctx.actions.skip_summarization is True


def test_elicitation_without_tool_context_is_noop_not_crash():
    # unit/preview paths pass no tool context — must not raise, still returns the card.
    out = make_elicitation_result(ElicitationEnvelope(kind="confirm", action="confirm_delegation", message="Hand off?"))
    assert out["needs_input"] is True


def test_elicitation_tolerates_actions_it_cannot_write():
    # a read-only / exotic actions object must never kill the turn.
    ctx = SimpleNamespace(actions=object(), state={})
    out = make_elicitation_result(
        ElicitationEnvelope(kind="confirm", action="confirm_delegation", message="Hand off?"),
        tool_context=ctx,
    )
    assert out["needs_input"] is True


# --- transform: confirm ------------------------------------------------------


def test_confirm_renders_valid_button_no_inputs():
    result = request_confirmation("Proceed with the handoff?")
    messages = elicitation_form_to_a2ui(result)
    _assert_valid_v09(messages)
    assert _of_type(messages, "Button"), "confirm must render a submit button"
    # No field inputs for a bare confirm.
    assert not _of_type(messages, "TextField")
    assert not _of_type(messages, "DateTimeInput")
    # The button fires the envelope action.
    btn = _of_type(messages, "Button")[0]
    assert btn["action"]["event"]["name"] == "elicit_submit"


def test_confirm_leads_with_message_no_oversized_heading():
    """A plain confirm's message IS the ask — it must lead the card, not sit
    beneath a redundant 'Please confirm' h3 that dwarfs it (2026-07-15 report)."""
    result = request_confirmation("Hand this conversation to the PPA specialist?")
    messages = elicitation_form_to_a2ui(result)
    texts = _of_type(messages, "Text")
    # No big heading, and no literal "Please confirm" when a message is present.
    assert not any(t.get("variant") == "h3" for t in texts)
    assert not any(t.get("text") == "Please confirm" for t in texts)
    # The message leads.
    assert texts[0]["text"] == "Hand this conversation to the PPA specialist?"


# --- transform: confirm_with_fields (all field types) ------------------------


def _all_types_result():
    envelope = ElicitationEnvelope(
        kind="confirm_with_fields",
        action="confirm_delegation",
        message="Supply the run parameters.",
        context={"target_skill_id": "some-job"},
        fields=[
            ElicitationField(name="start", type="date", label="Start", required=True),
            ElicitationField(name="notes", type="text", label="Notes"),
            ElicitationField(name="capacity", type="number", label="Capacity", unit="MW", required=True),
            ElicitationField(name="market", type="select", label="Market", options=["ES", "AR"]),
            ElicitationField(name="include_fm", type="bool", label="Include Force Majeure", default=True),
        ],
    )
    return make_elicitation_result(envelope)


def test_all_field_types_render_valid():
    messages = elicitation_form_to_a2ui(_all_types_result())
    _assert_valid_v09(messages)
    assert len(_of_type(messages, "DateTimeInput")) == 1
    assert len(_of_type(messages, "ChoicePicker")) == 1
    assert len(_of_type(messages, "CheckBox")) == 1
    # text + number both map to TextField (number carries a numeric regexp).
    text_fields = _of_type(messages, "TextField")
    assert len(text_fields) == 2
    assert any(tf.get("validationRegexp") for tf in text_fields), "number field must carry a client regexp"


def test_seed_covers_every_bound_path():
    messages = elicitation_form_to_a2ui(_all_types_result())
    seed = _seed(messages)
    assert set(seed) == {"start", "notes", "capacity", "market", "include_fm"}
    assert seed["include_fm"] is True  # bool default seeded as a boolean


def test_submit_button_carries_action_and_context():
    messages = elicitation_form_to_a2ui(_all_types_result())
    btn = _of_type(messages, "Button")[0]
    event = btn["action"]["event"]
    assert event["name"] == "confirm_delegation"
    assert event["context"] == {"target_skill_id": "some-job"}


def test_required_group_before_optional():
    """Required fields render in a card ahead of the optional card (the run's
    hard requirements are never buried)."""
    messages = elicitation_form_to_a2ui(_all_types_result())
    texts = [c for c in _components(messages) if c.get("component") == "Text"]
    labels = [t.get("text", "") for t in texts]
    assert "Required" in labels and "Optional" in labels
    assert labels.index("Required") < labels.index("Optional")


def test_non_elicitation_result_declines():
    assert elicitation_form_to_a2ui({"error": "boom"}) is None
    assert elicitation_form_to_a2ui({"some": "payload"}) is None


# --- read_submitted_values ---------------------------------------------------


def _ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(state=state)


def test_read_submitted_prefers_trigger_surface():
    ctx = _ctx(
        {
            "a2ui_action_trigger": {"surfaceId": "elicit:some-job:2"},
            "a2ui_surface_state": {
                "elicit:some-job:1": {"dataModel": {"capacity": "10"}},
                "elicit:some-job:2": {"dataModel": {"capacity": "42", "market": "ES"}},
            },
        }
    )
    values = read_submitted_values(ctx, expected_fields=["capacity"])
    assert values == {"capacity": "42", "market": "ES"}


def test_read_submitted_anchors_on_expected_fields():
    ctx = _ctx(
        {
            "a2ui_surface_state": {
                "some-other-surface": {"dataModel": {"unrelated": "x"}},
                "elicit:job:1": {"dataModel": {"capacity": "7"}},
            }
        }
    )
    assert read_submitted_values(ctx, expected_fields=["capacity"]) == {"capacity": "7"}
    # No matching field anywhere → None (never guess).
    assert read_submitted_values(ctx, expected_fields=["missing"]) is None


def test_read_submitted_none_without_state():
    assert read_submitted_values(None) is None
    assert read_submitted_values(_ctx({})) is None


# --- registry wiring ---------------------------------------------------------


def test_registry_renders_request_confirmation():
    """Importing adk.a2ui_elicitation_render registered the mapping, so the
    registry renders a request_confirmation result to a placement:chat surface."""
    from adk.a2ui_result_render import is_render_payload_tool, registered_mapping_names

    assert "elicitation_form" in registered_mapping_names()
    # request_confirmation output is a UI payload — never offloaded.
    assert is_render_payload_tool("request_confirmation")
    result = request_confirmation("Go ahead?")
    rendered = render_for_emit("request_confirmation", result)
    assert rendered is not None
    assert rendered.surface_id.startswith("elicit:")
    assert rendered.artifact and rendered.artifact.get("placement") == "chat"
    # Sub-type drives the compact-vs-form card sizing on the frontend.
    assert rendered.artifact.get("elicitationKind") == "confirm"


def test_artifact_meta_marks_confirm_with_fields():
    """A field form tags itself so the frontend renders the taller card, not the
    compact confirm layout."""
    rendered = render_for_emit("request_confirmation", _all_types_result())
    assert rendered is not None
    assert rendered.artifact and rendered.artifact.get("elicitationKind") == "confirm_with_fields"


def test_tree_choice_picker_and_checkbox_schema_valid():
    """The two new _Tree builders emit catalog-valid components."""
    from adk.a2ui_ppa_render import _Tree

    tree = _Tree()
    picker = tree.choice_picker(path="/market", options=["ES", ("AR", "Argentina")], label="Market")
    check = tree.checkbox(path="/fm", label="Force Majeure")
    tree.root([picker, check])
    _validator().validate(tree.messages())
