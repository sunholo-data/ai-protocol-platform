"""Unit tests for the headless mapping-preview module (tool-results-as-a2ui / 7.3, M3).

``adk.a2ui_render_preview`` runs a registered result→A2UI mapping against a typed
tool-result JSON and schema-validates the output — the headless capability the
``aiplatform a2ui render`` CLI verb + ``make a2ui-render`` target wrap.
"""

from __future__ import annotations

import json

import pytest

from adk.a2ui_render_preview import main, render_and_validate
from tools.schemas.ppa_clauses import ClauseDifference, PpaClauses, PpaComparison


def _comparison() -> dict:
    return PpaComparison(
        left=PpaClauses(doc_id="docA"),
        right=PpaClauses(doc_id="docB"),
        differences=[
            ClauseDifference(
                clause_name="settlement_type",
                display_name="Settlement Type",
                severity="material",
                left_value="PaP",
                right_value="PaN",
                left_block_id="docA#b1",
                right_block_id="docB#b1",
                commercial_implication="Shifts volume risk.",
            )
        ],
    ).model_dump()


# --- render_and_validate ---


def test_render_and_validate_returns_valid_messages():
    messages = render_and_validate("ppa_comparison", _comparison())
    assert messages[0]["createSurface"]["surfaceId"] == "workspace"
    assert any("updateComponents" in m for m in messages)


def test_render_and_validate_clauses_mapping():
    messages = render_and_validate("ppa_clauses", PpaClauses(doc_id="docA").model_dump())
    assert messages[0]["createSurface"]["surfaceId"] == "workspace"


def test_render_and_validate_unknown_mapping_raises_keyerror():
    with pytest.raises(KeyError):
        render_and_validate("does_not_exist", {})


def test_render_and_validate_declined_result_raises_valueerror():
    # An error-shaped result makes the transform return None → ValueError.
    with pytest.raises(ValueError):
        render_and_validate("ppa_comparison", {"error": "extraction failed"})


# --- main() (CLI entrypoint the wrapper shells to) ---


def test_main_list_prints_registered_mappings(capsys):
    rc = main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ppa_comparison" in out
    assert "ppa_clauses" in out


def test_main_renders_result_file(tmp_path, capsys):
    result_file = tmp_path / "comparison.json"
    result_file.write_text(json.dumps(_comparison()), encoding="utf-8")
    rc = main(["--mapping", "ppa_comparison", "--result", str(result_file)])
    out = capsys.readouterr().out
    assert rc == 0
    printed = json.loads(out)  # stdout is the A2UI message list
    assert printed[0]["createSurface"]["surfaceId"] == "workspace"


def test_main_unknown_mapping_returns_2(tmp_path, capsys):
    result_file = tmp_path / "r.json"
    result_file.write_text("{}", encoding="utf-8")
    rc = main(["--mapping", "nope", "--result", str(result_file)])
    assert rc == 2
    assert "unknown mapping" in capsys.readouterr().err


def test_main_missing_result_file_returns_2(capsys):
    rc = main(["--mapping", "ppa_comparison", "--result", "/no/such/file.json"])
    assert rc == 2
    assert "could not read" in capsys.readouterr().err


def test_main_declined_result_returns_1(tmp_path, capsys):
    result_file = tmp_path / "err.json"
    result_file.write_text(json.dumps({"error": "boom"}), encoding="utf-8")
    rc = main(["--mapping", "ppa_comparison", "--result", str(result_file)])
    assert rc == 1
    assert "declined" in capsys.readouterr().err
