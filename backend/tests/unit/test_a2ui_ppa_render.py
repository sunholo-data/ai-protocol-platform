"""Unit tests for the PPA result → A2UI transforms (tool-results-as-a2ui / 7.3, M2).

Validates that ``ppa_comparison_to_a2ui`` / ``ppa_clauses_to_a2ui`` emit
**schema-valid A2UI v0.9** (checked against the real Basic catalog via
``a2ui.schema.validator.A2uiValidator``) and that the structure matches the
design: Tabs [Key Differences · Contract A · Contract B], a nested severity
filter (All/Material/Moderate/Cosmetic) built from shared row refs, block_id
citations, and error-shaped results declining to render.
"""

from __future__ import annotations

import functools

import pytest

import adk.a2ui_ppa_render as ppa_render
from adk.a2ui_ppa_render import (
    COMPARE_TOOL,
    EXTRACT_TOOL,
    ppa_clauses_to_a2ui,
    ppa_comparison_to_a2ui,
)
from adk.a2ui_result_render import render_for
from tools.schemas.ppa_clauses import (
    ClauseDifference,
    ClauseExtraction,
    PpaClauses,
    PpaComparison,
)


@pytest.fixture(autouse=True)
def _stub_doc_name_resolution(monkeypatch):
    """Keep the doc_id→filename resolver hermetic: derive a name from the id
    instead of hitting Firestore. Clears the resolver's lru_cache each test."""
    ppa_render._resolve_doc_name.cache_clear()
    monkeypatch.setattr(
        "db.firestore.get_document",
        lambda _collection, doc_id: {"originalFilename": f"{doc_id}.pdf"},
    )
    yield
    ppa_render._resolve_doc_name.cache_clear()


@functools.lru_cache(maxsize=1)
def _validator():
    from a2ui.basic_catalog import BasicCatalog
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.schema.validator import A2uiValidator

    config = BasicCatalog.get_config("0.9")
    catalog = A2uiSchemaManager(version="0.9", catalogs=[config])._supported_catalogs[0]
    return A2uiValidator(catalog)


def _assert_valid_v09(messages: list[dict]) -> None:
    """Schema-validate the message list against the real Basic catalog.

    A transform emits createSurface + updateComponents, optionally followed by an
    ``updateDataModel`` that stashes structured data for a bespoke workbench tab
    (6.11 — the Clauses table, Sources list, obligation payload)."""
    assert isinstance(messages, list) and len(messages) >= 2
    assert "createSurface" in messages[0]
    assert "updateComponents" in messages[1]
    assert all("updateDataModel" in m for m in messages[2:])
    _validator().validate(messages)


def _components(messages: list[dict]) -> list[dict]:
    return messages[1]["updateComponents"]["components"]


def _of_type(messages: list[dict], component: str) -> list[dict]:
    return [c for c in _components(messages) if c.get("component") == component]


def _all_text(messages: list[dict]) -> str:
    return " ".join(str(c.get("text", "")) for c in _of_type(messages, "Text"))


# --- fixtures ----------------------------------------------------------------


def _clause(name: str, display: str, value: str, block_id: str, confidence: str = "high") -> ClauseExtraction:
    return ClauseExtraction(
        clause_name=name,
        display_name=display,
        value=value,
        raw_excerpt=f"…{value}…",
        block_id=block_id,
        confidence=confidence,
    )


def _sample_clauses(doc_id: str) -> PpaClauses:
    return PpaClauses(
        doc_id=doc_id,
        settlement_type=_clause("settlement_type", "Settlement Type", "PaP", f"{doc_id}#b12"),
        contract_form=_clause("contract_form", "Contract Form", "Physical", f"{doc_id}#b7"),
        term_years=_clause("term_years", "Term (years)", "10", f"{doc_id}#b3"),
        other_clauses=[_clause("special", "Special Condition", "custom", f"{doc_id}#b99", "medium")],
        other_clauses_total=3,
        other_clauses_truncated=True,
    )


def _sample_comparison() -> dict:
    return PpaComparison(
        left=_sample_clauses("docA"),
        right=_sample_clauses("docB"),
        differences=[
            ClauseDifference(
                clause_name="settlement_type",
                display_name="Settlement Type",
                severity="material",
                left_value="PaP",
                right_value="PaN",
                left_block_id="docA#b12",
                right_block_id="docB#b12",
                commercial_implication="Shifts volume risk from buyer to seller.",
            ),
            ClauseDifference(
                clause_name="governing_law",
                display_name="Governing Law",
                severity="moderate",
                left_value="England",
                right_value="Scotland",
                left_block_id="docA#b40",
                right_block_id="docB#b41",
                commercial_implication="Different dispute jurisdiction.",
            ),
            ClauseDifference(
                clause_name="notice_address",
                display_name="Notice Address",
                severity="cosmetic",
                left_value="London",
                right_value="Edinburgh",
                left_block_id="docA#b60",
                right_block_id="docB#b61",
                commercial_implication="Administrative only.",
            ),
        ],
    ).model_dump()


# --- comparison transform ----------------------------------------------------


def test_comparison_emits_schema_valid_v09():
    messages = ppa_comparison_to_a2ui(_sample_comparison())
    _assert_valid_v09(messages)


def test_comparison_top_level_tabs():
    messages = ppa_comparison_to_a2ui(_sample_comparison())
    tabs = _of_type(messages, "Tabs")
    titles = [t["title"] for tabset in tabs for t in tabset["tabs"]]
    assert "Key Differences" in titles
    # Contract tabs keep the A/B role prefix AND carry the resolved filename
    # (stubbed to "<doc_id>.pdf" in tests).
    assert any(t.startswith("A · ") and "docA" in t for t in titles)
    assert any(t.startswith("B · ") and "docB" in t for t in titles)


def test_comparison_nested_severity_filter_tabs():
    messages = ppa_comparison_to_a2ui(_sample_comparison())
    titles = [t["title"] for tabset in _of_type(messages, "Tabs") for t in tabset["tabs"]]
    # One "All (3)" plus a tab per present severity.
    assert any(t.startswith("All (3)") for t in titles)
    assert any(t.startswith("Material (1)") for t in titles)
    assert any(t.startswith("Moderate (1)") for t in titles)
    assert any(t.startswith("Cosmetic (1)") for t in titles)


def test_comparison_rows_shared_between_all_and_severity_tabs():
    """Each diff card is built once; the All tab and its severity tab reference
    the SAME id (v0.9 shared child ref) — so 3 diffs → 3 cards, not 6."""
    messages = ppa_comparison_to_a2ui(_sample_comparison())
    diff_cards = [c for c in _of_type(messages, "Card") if str(c["id"]).startswith("d-card")]
    assert len(diff_cards) == 3


def test_comparison_shows_implications_and_values_not_raw_block_ids():
    text = _all_text(ppa_comparison_to_a2ui(_sample_comparison()))
    assert "Shifts volume risk from buyer to seller." in text
    assert "Contract A: PaP" in text
    assert "Contract B: PaN" in text
    assert "Severity: material" in text
    # Raw block_id UUIDs are NOT shown (kept in data for future click-to-source).
    assert "docA#b12" not in text
    assert "docB#b12" not in text


def test_comparison_diff_has_explain_chat_button():
    """Each diff card carries an 'Explain this difference' Button whose action is
    the generic `chat:send` (routes to the chat composer) with a ready-built
    prompt naming the clause + both values, so the agent's reply lands in chat."""
    messages = ppa_comparison_to_a2ui(_sample_comparison())
    buttons = _of_type(messages, "Button")
    assert len(buttons) == 3  # one per diff row
    action = buttons[0]["action"]["event"]
    assert action["name"] == "chat:send"
    prompt = action["context"]["prompt"]
    assert "Settlement Type" in prompt
    assert "PaP" in prompt and "PaN" in prompt  # left/right values in the prompt


def test_comparison_no_differences_still_valid():
    result = _sample_comparison()
    result["differences"] = []
    messages = ppa_comparison_to_a2ui(result)
    _assert_valid_v09(messages)
    assert "match on every clause" in _all_text(messages)


def test_comparison_error_result_returns_none():
    assert ppa_comparison_to_a2ui({"error": "extraction failed"}) is None


def test_comparison_missing_sides_returns_none():
    assert ppa_comparison_to_a2ui({"differences": []}) is None


# --- clauses transform -------------------------------------------------------


def test_clauses_emits_schema_valid_v09():
    messages = ppa_clauses_to_a2ui(_sample_clauses("docA").model_dump())
    _assert_valid_v09(messages)


def test_clauses_renders_clause_values_and_truncation():
    text = _all_text(ppa_clauses_to_a2ui(_sample_clauses("docA").model_dump()))
    assert "Settlement Type" in text
    assert "PaP" in text
    assert "Showing 1 of 3 non-standard clauses." in text
    assert "docA" in text


def test_clauses_error_returns_none():
    assert ppa_clauses_to_a2ui({"error": "no document"}) is None


def test_clauses_missing_doc_id_returns_none():
    assert ppa_clauses_to_a2ui({"settlement_type": None}) is None


def test_clauses_render_as_compact_rows_one_card_per_section():
    """Compact: clauses are Rows inside ONE section Card+List, not a Card each
    (the 'long list' fix)."""
    messages = ppa_clauses_to_a2ui(_sample_clauses("docA").model_dump())
    clause_rows = [c for c in _of_type(messages, "Row") if str(c["id"]).startswith("cl-row")]
    assert len(clause_rows) >= 3  # settlement_type, contract_form, term_years, + other
    # single extraction → exactly one section Card wrapping the List (not N cards)
    assert len(_of_type(messages, "Card")) == 1


def test_clauses_renders_single_doc_no_tabs():
    """7.5: each extraction is its own artifact surface, so the transform renders
    ONE document's clauses (no in-transform doc-tab accumulation)."""
    messages = ppa_clauses_to_a2ui(_sample_clauses("docA").model_dump(), None)
    _assert_valid_v09(messages)
    assert _of_type(messages, "Tabs") == []  # one doc → flat section, no tabs


# --- per-artifact routing (7.5) ----------------------------------------------


def test_extract_routes_to_per_document_surface_with_artifact():
    """render_for_emit routes an extraction to ppa_clauses:{doc_id} with clause
    artifact metadata — so each doc gets its own workbench tab."""
    from adk.a2ui_result_render import render_for_emit

    rendered = render_for_emit(EXTRACT_TOOL, _sample_clauses("docA").model_dump())
    assert rendered is not None
    assert rendered.surface_id == "ppa_clauses:docA"
    assert rendered.artifact["kind"] == "clauses"
    # Tab label is the tool/kind, NOT the filename (that duplicates the Document
    # tabs); the filename + count live in the tooltip (description).
    assert rendered.artifact["title"] == "Clauses"
    assert "docA.pdf" in rendered.artifact["description"]
    assert "clauses extracted" in rendered.artifact["description"]


def test_two_extractions_route_to_distinct_surfaces():
    from adk.a2ui_result_render import render_for_emit

    a = render_for_emit(EXTRACT_TOOL, _sample_clauses("docA").model_dump())
    b = render_for_emit(EXTRACT_TOOL, _sample_clauses("docB").model_dump())
    assert a.surface_id == "ppa_clauses:docA"
    assert b.surface_id == "ppa_clauses:docB"
    assert a.surface_id != b.surface_id  # no overwrite — two artifact tabs


def test_compare_routes_to_comparison_surface_with_artifact():
    from adk.a2ui_result_render import render_for_emit

    rendered = render_for_emit(COMPARE_TOOL, _sample_comparison())
    assert rendered.surface_id == "ppa_comparison"
    assert rendered.artifact["kind"] == "comparison"
    assert rendered.artifact["title"] == "Comparison"
    assert "differences" in rendered.artifact["description"]


# --- registry routing --------------------------------------------------------


def test_render_for_routes_comparison_and_clauses():
    """Both mappings are registered on import; render_for routes by tool name."""
    comp_msgs = render_for(COMPARE_TOOL, _sample_comparison())
    clause_msgs = render_for(EXTRACT_TOOL, _sample_clauses("docA").model_dump())
    assert comp_msgs is not None
    assert clause_msgs is not None
    _assert_valid_v09(comp_msgs)
    _assert_valid_v09(clause_msgs)
