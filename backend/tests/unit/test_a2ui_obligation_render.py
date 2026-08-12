"""Unit tests for the obligation result → A2UI transforms (7.6 M3).

Covers the two shapes ``map_ppa_obligations`` returns:

  * refusal (``{"error": ..., "doc_id": ..., "unmapped": [...],
    "needs_effective_date": ...}``) → an unmapped-list panel (the M2-finding
    integration: a template contract must render its per-clause reasons, never
    a crash) built from the envelope's STRUCTURED ``unmapped`` list — never by
    parsing the error prose — with the effective-date hint keyed off the
    ``needs_effective_date`` flag;
  * success (``PpaObligationPayload``) → a summary surface plus an
    ``updateDataModel`` message carrying the full wire payload to the artefact.

Both are schema-validated against the real A2UI v0.9 Basic catalog, and the
registry routing (error → refusal mapping, payload → analysis mapping) is
asserted end-to-end via ``render_for_emit``.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from adk.a2ui_obligation_render import (
    ANALYSIS_KIND,
    MAP_TOOL,
    REFUSAL_KIND,
    obligation_payload_to_a2ui,
    obligation_refusal_to_a2ui,
)
from adk.a2ui_result_render import is_render_payload_tool, render_for_emit

_FIXTURES = Path(__file__).parent.parent / "tool_tests" / "fixtures" / "ppa_obligations"


def _fixture(name: str) -> dict:
    path = _FIXTURES / name
    if not path.exists():
        pytest.skip("corpus fixtures not present (confidential; excluded from the public template)")
    return json.loads(path.read_text())


@functools.lru_cache(maxsize=1)
def _validator():
    from a2ui.basic_catalog import BasicCatalog
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.schema.validator import A2uiValidator

    config = BasicCatalog.get_config("0.9")
    catalog = A2uiSchemaManager(version="0.9", catalogs=[config])._supported_catalogs[0]
    return A2uiValidator(catalog)


def _assert_valid_v09(messages: list[dict]) -> None:
    assert isinstance(messages, list) and messages
    assert "createSurface" in messages[0]
    assert "updateComponents" in messages[1]
    _validator().validate(messages)


def _all_text(messages: list[dict]) -> str:
    comps = messages[1]["updateComponents"]["components"]
    return " ".join(str(c.get("text", "")) for c in comps if c.get("component") == "Text")


# --- refusal -----------------------------------------------------------------


def test_refusal_declines_on_a_success_payload():
    assert obligation_refusal_to_a2ui(_fixture("payload.democorp.json")) is None


def test_refusal_renders_unmapped_list_from_real_corpus_refusal():
    messages = obligation_refusal_to_a2ui(_fixture("error.demosolar.json"))
    assert messages is not None
    _assert_valid_v09(messages)
    text = _all_text(messages)
    assert "not modelled" in text
    # A sample of the real unmapped clauses must appear as rows with reasons.
    assert "price_formula" in text
    assert "late_payment_interest" in text
    assert "Euribor" in text  # the reason text is rendered, not just the clause


def test_refusal_effective_date_hint_keyed_off_structured_flag():
    """The confirmation hint is driven by the envelope's `needs_effective_date`
    flag, NOT by matching the error prose (the eval_round_1 fragility)."""
    err = {
        "error": "No effective date could be established for this contract.",
        "doc_id": "abc",
        "unmapped": [],
        "needs_effective_date": True,
    }
    messages = obligation_refusal_to_a2ui(err)
    assert messages is not None
    _assert_valid_v09(messages)
    assert "effective/start date" in _all_text(messages)

    # Same prose WITHOUT the flag → no hint: wording carries no behaviour.
    err_no_flag = {**err, "needs_effective_date": False}
    assert "effective/start date" not in _all_text(obligation_refusal_to_a2ui(err_no_flag))


def test_refusal_without_structured_unmapped_still_renders():
    messages = obligation_refusal_to_a2ui({"error": "Obligation mapping failed: boom", "doc_id": "x"})
    assert messages is not None
    _assert_valid_v09(messages)
    assert "boom" in _all_text(messages)


def test_refusal_renders_structured_rows_verbatim_including_semicolons():
    """Reasons render from the structured list untouched — a reason containing
    '; ' can no longer fracture into a fake clause row (the old string-parse
    hazard); malformed entries are skipped, not crashed on."""
    err = {
        "error": "The mapper found no obligations expressible in the engine's model.",
        "doc_id": "x",
        "unmapped": [
            {"clause": "a_clause", "reason": "first reason; still the first reason"},
            {"clause": "b_clause", "reason": "second reason"},
            {"clause": "", "reason": "no clause name — skipped"},
            "not-a-dict",
        ],
        "needs_effective_date": False,
    }
    messages = obligation_refusal_to_a2ui(err)
    assert messages is not None
    _assert_valid_v09(messages)
    text = _all_text(messages)
    assert "2 clause(s)" in text  # only the two well-formed rows counted
    assert "first reason; still the first reason" in text
    assert "b_clause" in text and "second reason" in text


def test_all_corpus_refusal_fixtures_carry_structured_unmapped():
    for name in ("error.demosolar.json", "error.demo-a2s.json", "error.demo-leap.json"):
        rows = _fixture(name)["unmapped"]
        assert len(rows) >= 15  # each corpus refusal lists many clauses
        # Every clause name is a snake_case token; every reason is non-empty.
        for row in rows:
            assert row["clause"] and " " not in row["clause"]
            assert row["reason"]


# --- success -----------------------------------------------------------------


def test_payload_declines_on_an_error_result():
    assert obligation_payload_to_a2ui({"error": "nope", "doc_id": "x"}) is None


def test_payload_renders_summary_and_carries_full_payload_datamodel():
    payload = _fixture("payload.democorp.json")
    messages = obligation_payload_to_a2ui(payload)
    assert messages is not None
    _assert_valid_v09(messages)
    # Last message injects the full wire payload for the artefact to boot from.
    dm = messages[-1]
    assert "updateDataModel" in dm
    assert dm["updateDataModel"]["value"]["payload"] == payload
    text = _all_text(messages)
    assert "1 obligation" in text
    # democorp has a provided effective date + all-default policy knobs.
    # Conversational chat card (7.8): the provided values are disclosed as
    # assumptions, defaults are named, and unmapped clauses stay visible.
    assert "assumptions you provided" in text
    assert "reviewed engine defaults" in text
    assert "unmapped" in text  # 22 unmapped clauses in the envelope


def test_payload_reports_extracted_policy_knobs_when_present():
    payload = _fixture("payload.democorp.json")
    payload["policy_sources"]["penPerDay"] = "extracted"
    messages = obligation_payload_to_a2ui(payload)
    assert messages is not None
    assert "penPerDay" in _all_text(messages)


# --- registry routing --------------------------------------------------------


def test_registry_routes_refusal_and_success_to_distinct_artifacts():
    refusal = render_for_emit(MAP_TOOL, _fixture("error.demosolar.json"))
    assert refusal is not None
    assert refusal.artifact["kind"] == REFUSAL_KIND
    assert refusal.surface_id.startswith("obligation_refusal:")

    success = render_for_emit(MAP_TOOL, _fixture("payload.democorp.json"))
    assert success is not None
    assert success.artifact["kind"] == ANALYSIS_KIND
    assert success.surface_id.startswith("obligation_analysis:")
    # 7.8: the settlement RESULT is the interactive WORKBENCH artefact
    # (ObligationArtefactTab → verified net + what-if), NOT a chat card — the
    # sandbox origins are whitelisted so the artefact boots. Workbench is the
    # default (no "chat" placement).
    assert success.artifact.get("placement") != "chat"
    # The datamodel message (full payload for the artefact to boot from) was
    # retargeted to the analysis surface too.
    dm = success.messages[-1]["updateDataModel"]
    assert dm["surfaceId"] == success.surface_id


def test_map_tool_is_render_payload_never_offloaded():
    assert is_render_payload_tool(MAP_TOOL) is True


# --- elicitation form (7.8 M1 — the DEMO UNBLOCK) ----------------------------

from adk.a2ui_obligation_render import (  # noqa: E402
    ELICITATION_FORM_KIND,
    obligation_elicitation_form_to_a2ui,
)
from tools.schemas.ppa_obligations import (  # noqa: E402
    REQUIRED_ASSUMPTION_FIELDS,
    build_obligation_elicitation,
)


def _elicitation_refusal(doc_id: str = "demo-leap", unmapped: list | None = None) -> dict:
    """A template-contract refusal carrying a structured elicitation envelope —
    the shape map_ppa_obligations returns for a placeholder contract."""
    return {
        "error": "The mapper found no obligations expressible ... no obligations expressible",
        "doc_id": doc_id,
        "unmapped": unmapped or [{"clause": "price_formula", "reason": "floating [●] price"}],
        "needs_effective_date": False,
        "needs_assumptions": True,
        "elicitation": build_obligation_elicitation(doc_id, reason="template contract").model_dump(),
    }


def test_form_declines_on_a_plain_error_without_elicitation():
    # A refusal with no elicitation envelope is NOT the form's case.
    assert obligation_elicitation_form_to_a2ui(_fixture("error.demosolar.json")) is None
    assert obligation_elicitation_form_to_a2ui({"error": "x", "doc_id": "d"}) is None


def test_form_declines_on_success_payload():
    assert obligation_elicitation_form_to_a2ui(_fixture("payload.democorp.json")) is None


def test_form_is_valid_a2ui_v09_with_inputs_and_submit():
    messages = obligation_elicitation_form_to_a2ui(_elicitation_refusal())
    assert messages is not None
    _assert_valid_v09(messages)
    comps = messages[1]["updateComponents"]["components"]
    kinds = [c["component"] for c in comps]
    # Dates → DateTimeInput; amounts → TextField; one submit Button.
    assert kinds.count("DateTimeInput") == 2  # effective_date, cod_date
    assert kinds.count("TextField") >= 4  # capacity, price, + policy knobs
    assert kinds.count("Button") == 1


def test_form_binds_every_field_to_its_datamodel_path():
    env = build_obligation_elicitation("demo-leap")
    messages = obligation_elicitation_form_to_a2ui(_elicitation_refusal())
    comps = messages[1]["updateComponents"]["components"]
    bound_paths = {c["value"]["path"] for c in comps if c.get("component") in ("DateTimeInput", "TextField")}
    for field in env.fields:
        assert f"/{field.name}" in bound_paths, f"{field.name} not bound to an input"
    # The seed data model covers every field so inputs resolve immediately.
    seed = messages[-1]["updateDataModel"]["value"]
    assert set(seed.keys()) == {f.name for f in env.fields}


def test_form_submit_fires_start_obligation_analysis_with_doc():
    messages = obligation_elicitation_form_to_a2ui(_elicitation_refusal("gs://bucket/leap.pdf"))
    comps = messages[1]["updateComponents"]["components"]
    button = next(c for c in comps if c["component"] == "Button")
    event = button["action"]["event"]
    assert event["name"] == "start_obligation_analysis"
    # doc identity is a FLAT string (v0.9 Action context — no nested object).
    assert event["context"]["doc"] == "gs://bucket/leap.pdf"


def test_form_discloses_capacity_formulas_in_help():
    """The capacity field's help must disclose BOTH LD formulas (delay-LD 150,
    COD-flex 200) so the user sees what one input drives."""
    messages = obligation_elicitation_form_to_a2ui(_elicitation_refusal())
    text = _all_text(messages).lower()
    assert "150" in text and "200" in text
    assert "assumption" in text  # never presented as a contract fact


def test_registry_routes_needs_assumptions_to_chat_placement_form():
    result = render_for_emit(MAP_TOOL, _elicitation_refusal())
    assert result is not None
    assert result.artifact["kind"] == ELICITATION_FORM_KIND
    assert result.artifact["placement"] == "chat"  # renders in chat, not a tab
    assert result.surface_id.startswith("obligation_elicitation:")
    # The seed data model was retargeted to the form surface.
    assert messages_datamodel_surface(result) == result.surface_id


def messages_datamodel_surface(result) -> str:
    for msg in reversed(result.messages):
        if "updateDataModel" in msg:
            return msg["updateDataModel"]["surfaceId"]
    return ""


def test_elicit_seq_makes_each_form_a_distinct_append_only_surface():
    """Append-only history (7.8): a re-refusal with a fresh elicit_seq emits a
    NEW surface, so the chat forms stack (the prior submission stays frozen in
    the transcript) rather than one form replacing the last."""
    first = _elicitation_refusal()
    first["elicit_seq"] = 1
    second = _elicitation_refusal()
    second["elicit_seq"] = 2
    r1 = render_for_emit(MAP_TOOL, first)
    r2 = render_for_emit(MAP_TOOL, second)
    assert r1.surface_id == "obligation_elicitation:demo-leap:1"
    assert r2.surface_id == "obligation_elicitation:demo-leap:2"
    assert r1.surface_id != r2.surface_id  # distinct → append, not replace
    # The seed data model is retargeted to each form's own surface.
    assert messages_datamodel_surface(r1) == r1.surface_id
    assert messages_datamodel_surface(r2) == r2.surface_id


def test_registry_plain_refusal_still_routes_to_workbench_panel():
    """An error WITHOUT an elicitation envelope must still reach the workbench
    refusal panel (first-match-wins order preserved)."""
    result = render_for_emit(MAP_TOOL, _fixture("error.demosolar.json"))
    assert result is not None
    assert result.artifact["kind"] == REFUSAL_KIND


def test_required_fields_marked_in_form():
    messages = obligation_elicitation_form_to_a2ui(_elicitation_refusal())
    text = _all_text(messages)
    # Required labels carry a '*' marker.
    assert "*" in text
    assert set(REQUIRED_ASSUMPTION_FIELDS)  # sanity: there ARE required fields
