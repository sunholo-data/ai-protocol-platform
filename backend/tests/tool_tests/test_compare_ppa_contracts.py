"""Tests for tools/compare_ppa_contracts.py (v6.4.0 ONE-DEMO M3).

Covers:
  - Happy path: two PpaClauses → Gemini comparison → typed PpaComparison
  - Cached extraction reuse from app:emitted:ppa_clauses:* state
  - Failed extraction on left side → structured error
  - Failed extraction on right side → structured error
  - Gemini call failure → structured error (no exception)
  - Schema-violating output → structured error
  - Clause-subset pre-run config (7.2-M2 PPA-COMPARE-LAUNCHER M1):
    `clauses` + `max_other_clauses` thread through to both extractions,
    the diff output is restricted to the subset, invalid names are
    rejected loudly, and the comparison cache key is variant-aware so a
    subset run never serves a full-comparison cache hit.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.schemas.ppa_clauses import (
    ClauseExtraction,
    PpaClauses,
    PpaComparison,
    PpaDifferences,
)


def _make_ctx(state: dict | None = None):
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    return ctx


def _sample_clauses(doc_id: str, settlement: str = "PaP", price: str = "Fixed €45/MWh") -> PpaClauses:
    return PpaClauses(
        doc_id=doc_id,
        settlement_type=ClauseExtraction(
            clause_name="settlement_type",
            display_name="Settlement Type",
            value=settlement,
            raw_excerpt=f"settlement shall be {settlement}",
            block_id=f"blk-set-{doc_id}",
            confidence="high",
        ),
        price_formula=ClauseExtraction(
            clause_name="price_formula",
            display_name="Price Formula",
            value=price,
            raw_excerpt=price,
            block_id=f"blk-price-{doc_id}",
            confidence="high",
        ),
    )


def _sample_diffs_json(left_id: str = "doc-A", right_id: str = "doc-B") -> str:
    """Diff-rows-only payload, matching what `_run_comparison` now returns
    (a PpaDifferences, not a full PpaComparison — the tool assembles left +
    right in Python)."""
    diffs = PpaDifferences(
        differences=[
            {
                "clause_name": "settlement_type",
                "display_name": "Settlement Type",
                "severity": "material",
                "left_value": "PaP",
                "right_value": "PaN",
                "left_block_id": "blk-set-doc-A",
                "right_block_id": "blk-set-doc-B",
                "commercial_implication": (
                    "Under right contract the Seller takes forecasting-error risk, "
                    "shifting balancing cost away from the Buyer."
                ),
            },
            {
                "clause_name": "price_formula",
                "display_name": "Price Formula",
                "severity": "material",
                "left_value": "Fixed €45/MWh",
                "right_value": "CPI-indexed",
                "left_block_id": "blk-price-doc-A",
                "right_block_id": "blk-price-doc-B",
                "commercial_implication": ("Right contract exposes Buyer to inflation; left contract caps it."),
            },
        ],
    )
    return diffs.model_dump_json()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_typed_comparison_with_diffs():
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN", price="CPI-indexed")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        if doc_id == "doc-A":
            return left.model_dump_json()
        return right.model_dump_json()

    comparison_json = _sample_diffs_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(return_value=comparison_json),
        ),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B")

    parsed = json.loads(result)
    validated = PpaComparison.model_validate(parsed)
    assert validated.left.doc_id == "doc-A"
    assert validated.right.doc_id == "doc-B"
    assert len(validated.differences) == 2
    settlement_diff = next(d for d in validated.differences if d.clause_name == "settlement_type")
    assert settlement_diff.severity == "material"
    assert settlement_diff.left_block_id == "blk-set-doc-A"
    assert settlement_diff.right_block_id == "blk-set-doc-B"
    assert "balancing" in settlement_diff.commercial_implication.lower()


@pytest.mark.asyncio
async def test_uses_cached_extractions_when_available():
    """When M2 already stashed app:emitted:ppa_clauses:* for both docs, skip re-extracting."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN")
    ctx = _make_ctx(
        {
            "app:emitted:ppa_clauses:doc-A": left.model_dump_json(),
            "app:emitted:ppa_clauses:doc-B": right.model_dump_json(),
        }
    )

    mock_extract = AsyncMock()  # should NOT be called
    comparison_json = _sample_diffs_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=mock_extract),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(return_value=comparison_json),
        ),
    ):
        await compare_ppa_contracts("doc-A", "doc-B", tool_context=ctx)

    assert mock_extract.call_count == 0, "extract_ppa_clauses should not be called when state has cached extractions"
    # Stashes the comparison for follow-up "explain this diff" turns
    assert "app:emitted:ppa_comparison:doc-A:doc-B" in ctx.state


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_comparison_without_recomparing():
    """When the same pair was already compared this app, serve the cached
    PpaComparison and skip BOTH clause resolution and the comparison LLM (the
    'we recalculate a lot' fix). The whole tool short-circuits."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    cached = PpaComparison(
        left=_sample_clauses("doc-A"),
        right=_sample_clauses("doc-B", settlement="PaN"),
        differences=PpaDifferences.model_validate_json(_sample_diffs_json()).differences,
    )
    ctx = _make_ctx({"app:emitted:ppa_comparison:doc-A:doc-B": cached.model_dump_json()})

    mock_extract = AsyncMock()  # must NOT be called
    mock_compare = AsyncMock()  # must NOT be called

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=mock_extract),
        patch("tools.compare_ppa_contracts._run_comparison", new=mock_compare),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", tool_context=ctx)

    mock_extract.assert_not_called()
    mock_compare.assert_not_awaited()
    validated = PpaComparison.model_validate_json(result)
    assert len(validated.differences) == 2  # served straight from cache


@pytest.mark.asyncio
async def test_unparseable_comparison_cache_falls_through_to_fresh():
    """A stale comparison cache entry must self-heal: re-resolve + re-compare."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN")
    ctx = _make_ctx({"app:emitted:ppa_comparison:doc-A:doc-B": '{"garbage": true}'})

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch("tools.compare_ppa_contracts._run_comparison", new=AsyncMock(return_value=_sample_diffs_json())),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", tool_context=ctx)

    validated = PpaComparison.model_validate_json(result)  # fresh comparison won
    assert len(validated.differences) == 2


# ---------------------------------------------------------------------------
# Error paths — all return structured JSON, NEVER raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_left_extraction_failure_returns_structured_error():
    from tools.compare_ppa_contracts import compare_ppa_contracts

    error_payload = json.dumps({"error": "Document 'doc-missing' not found.", "doc_id": "doc-missing"})

    with patch(
        "tools.compare_ppa_contracts.extract_ppa_clauses",
        new=AsyncMock(return_value=error_payload),
    ):
        result = await compare_ppa_contracts("doc-missing", "doc-B")

    parsed = json.loads(result)
    assert "error" in parsed
    assert parsed["failed_side"] == "left"
    assert parsed["failed_doc_id"] == "doc-missing"
    assert parsed["left_doc_id"] == "doc-missing"


@pytest.mark.asyncio
async def test_right_extraction_failure_returns_structured_error():
    """Left succeeds, right fails — error names the right side."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right_error = json.dumps({"error": "Schema mismatch", "doc_id": "doc-broken"})

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        if doc_id == "doc-A":
            return left.model_dump_json()
        return right_error

    with patch(
        "tools.compare_ppa_contracts.extract_ppa_clauses",
        new=AsyncMock(side_effect=_fake_extract),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-broken")

    parsed = json.loads(result)
    assert "error" in parsed
    assert parsed["failed_side"] == "right"
    assert parsed["failed_doc_id"] == "doc-broken"


@pytest.mark.asyncio
async def test_gemini_call_failure_returns_structured_error():
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(side_effect=RuntimeError("429 Too Many Requests")),
        ),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "429" in parsed["error"]


@pytest.mark.asyncio
async def test_gs_url_pair_path_compares_bucket_resident_contracts():
    """Self-discovery path: agent discovers two PPAs in the tenant bucket via
    list_bucket_documents, then passes their gs:// URLs directly to
    compare_ppa_contracts. No parsed_documents/ entries required."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left_url = "gs://multivac-acme-energy-bucket/PPAs/longform/contract-A.pdf"
    right_url = "gs://multivac-acme-energy-bucket/PPAs/longform/contract-B.pdf"
    left = _sample_clauses(left_url)
    right = _sample_clauses(right_url, settlement="PaN", price="CPI-indexed")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        if gs_url == left_url:
            return left.model_dump_json()
        if gs_url == right_url:
            return right.model_dump_json()
        raise AssertionError(f"Unexpected call: doc_id={doc_id} gs_url={gs_url}")

    comparison_json = _sample_diffs_json(left_url, right_url)

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(return_value=comparison_json),
        ),
    ):
        result = await compare_ppa_contracts(left_gs_url=left_url, right_gs_url=right_url)

    parsed = json.loads(result)
    from tools.schemas.ppa_clauses import PpaComparison as _PpaComparison

    validated = _PpaComparison.model_validate(parsed)
    assert validated.left.doc_id == left_url
    assert validated.right.doc_id == right_url
    assert len(validated.differences) >= 1


@pytest.mark.asyncio
async def test_mixed_mode_doc_id_left_gs_url_right():
    """Mix modes: one side uploaded (doc_id), one side from bucket (gs_url)."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left_id = "doc-uploaded"
    right_url = "gs://bucket/PPAs/contract-B.pdf"
    left = _sample_clauses(left_id)
    right = _sample_clauses(right_url, settlement="PaN")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        if doc_id == left_id:
            return left.model_dump_json()
        if gs_url == right_url:
            return right.model_dump_json()
        raise AssertionError(f"Unexpected call: doc_id={doc_id} gs_url={gs_url}")

    comparison_json = _sample_diffs_json(left_id, right_url)

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(return_value=comparison_json),
        ),
    ):
        result = await compare_ppa_contracts(left_doc_id=left_id, right_gs_url=right_url)

    parsed = json.loads(result)
    assert "error" not in parsed


@pytest.mark.asyncio
async def test_both_modes_for_one_side_returns_structured_error():
    """Passing both doc_id and gs_url for the same side is a contract violation."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    result = await compare_ppa_contracts(left_doc_id="doc-A", left_gs_url="gs://bucket/A.pdf", right_doc_id="doc-B")
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exactly one" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_no_identity_for_one_side_returns_structured_error():
    """Neither doc_id nor gs_url for one side → structured error."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    result = await compare_ppa_contracts(left_doc_id="doc-A")
    parsed = json.loads(result)
    assert "error" in parsed
    assert "required" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_schema_violating_output_returns_structured_error():
    """Gemini occasionally drops required fields — surface as structured error.

    Same EARNED TRUST guardrail as extract_ppa_clauses: never emit a
    half-formed PpaComparison through to the KeyDifferencesPanel.
    """
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B")
    # A diff row missing required fields (display_name, severity,
    # commercial_implication) — violates PpaDifferences.
    bogus = json.dumps({"differences": [{"clause_name": "settlement_type"}]})

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch(
            "tools.compare_ppa_contracts._run_comparison",
            new=AsyncMock(return_value=bogus),
        ),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "schema" in parsed["error"].lower() or "validation" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# Clause-subset pre-run config (7.2-M2 PPA-COMPARE-LAUNCHER M1)
# ---------------------------------------------------------------------------


def _diffs_with_out_of_subset_row() -> str:
    """Two in-subset rows plus one governing_law row the model shouldn't have
    emitted for a subset run — the tool must filter it out."""
    diffs = PpaDifferences.model_validate_json(_sample_diffs_json())
    diffs.differences.append(
        diffs.differences[0].model_copy(
            update={
                "clause_name": "governing_law",
                "display_name": "Governing Law",
                "severity": "moderate",
                "left_value": "England",
                "right_value": "Spain",
                "commercial_implication": "Different dispute venue.",
            }
        )
    )
    return diffs.model_dump_json()


@pytest.mark.asyncio
async def test_clause_subset_threads_to_both_extractions_and_restricts_diff():
    """clauses + max_other_clauses must reach BOTH extract calls, and the
    returned differences must cover ONLY the subset — an out-of-subset diff
    row from the model is dropped."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN", price="CPI-indexed")
    subset = ["settlement_type", "price_formula"]
    extract_calls: list[dict] = []

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None, **kwargs):
        extract_calls.append({"doc_id": doc_id, **kwargs})
        return (left if doc_id == "doc-A" else right).model_dump_json()

    compare_mock = AsyncMock(return_value=_diffs_with_out_of_subset_row())
    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch("tools.compare_ppa_contracts._run_comparison", new=compare_mock),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", clauses=subset, max_other_clauses=5)

    # Both sides extracted with the same pre-run config.
    assert len(extract_calls) == 2
    for call in extract_calls:
        assert call["clauses"] == subset
        assert call["max_other_clauses"] == 5

    # Diff output restricted to the subset — governing_law row dropped.
    validated = PpaComparison.model_validate_json(result)
    diff_names = {d.clause_name for d in validated.differences}
    assert diff_names == {"settlement_type", "price_formula"}


@pytest.mark.asyncio
async def test_compare_invalid_clause_names_rejected_loudly():
    """Typo'd clause names fail fast — before any extraction or LLM call."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    extract_spy = AsyncMock()
    compare_spy = AsyncMock()
    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=extract_spy),
        patch("tools.compare_ppa_contracts._run_comparison", new=compare_spy),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", clauses=["settlement_type", "not_a_clause"])

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not_a_clause" in parsed["error"]
    assert "governing_law" in parsed["error"]  # valid vocabulary listed
    extract_spy.assert_not_awaited()
    compare_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_subset_run_never_serves_full_comparison_cache():
    """CORRECTNESS: a cached FULL comparison must not satisfy a subset run.
    The subset run recomputes, stashes under a variant key, and leaves the
    full-comparison entry untouched."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    full_key = "app:emitted:ppa_comparison:doc-A:doc-B"
    full_cached = PpaComparison(
        left=_sample_clauses("doc-A"),
        right=_sample_clauses("doc-B", settlement="PaN"),
        differences=PpaDifferences.model_validate_json(_sample_diffs_json()).differences,
    ).model_dump_json()
    ctx = _make_ctx({full_key: full_cached})

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None, **kwargs):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    extract_mock = AsyncMock(side_effect=_fake_extract)
    compare_mock = AsyncMock(return_value=_sample_diffs_json())
    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=extract_mock),
        patch("tools.compare_ppa_contracts._run_comparison", new=compare_mock),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", clauses=["settlement_type"], tool_context=ctx)

    compare_mock.assert_awaited_once()  # cache NOT served — subset recomputed
    validated = PpaComparison.model_validate_json(result)
    assert {d.clause_name for d in validated.differences} == {"settlement_type"}

    assert ctx.state[full_key] == full_cached  # full entry untouched
    variant_keys = [k for k in ctx.state if k.startswith(f"{full_key}:")]
    assert variant_keys, "subset comparison was not stashed under a variant key"


@pytest.mark.asyncio
async def test_subset_comparison_cache_hit_short_circuits():
    """Two identical subset runs → the second serves the variant cache entry
    without touching extraction or the comparison LLM."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    subset = ["settlement_type"]
    ctx = _make_ctx()

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN")

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None, **kwargs):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    # First run populates the variant key.
    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch("tools.compare_ppa_contracts._run_comparison", new=AsyncMock(return_value=_sample_diffs_json())),
    ):
        await compare_ppa_contracts("doc-A", "doc-B", clauses=subset, tool_context=ctx)

    # Second identical run must be a pure cache hit.
    extract_spy = AsyncMock()
    compare_spy = AsyncMock()
    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=extract_spy),
        patch("tools.compare_ppa_contracts._run_comparison", new=compare_spy),
    ):
        result = await compare_ppa_contracts("doc-A", "doc-B", clauses=subset, tool_context=ctx)

    extract_spy.assert_not_awaited()
    compare_spy.assert_not_awaited()
    PpaComparison.model_validate_json(result)  # served entry is valid


@pytest.mark.asyncio
async def test_default_run_keeps_legacy_cache_key():
    """Backward compat: a no-config run reads/writes the pre-M1 key so
    existing cached sessions keep hitting."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    left = _sample_clauses("doc-A")
    right = _sample_clauses("doc-B", settlement="PaN")
    ctx = _make_ctx()

    async def _fake_extract(doc_id=None, gs_url=None, tool_context=None, **kwargs):
        return (left if doc_id == "doc-A" else right).model_dump_json()

    with (
        patch("tools.compare_ppa_contracts.extract_ppa_clauses", new=AsyncMock(side_effect=_fake_extract)),
        patch("tools.compare_ppa_contracts._run_comparison", new=AsyncMock(return_value=_sample_diffs_json())),
    ):
        await compare_ppa_contracts("doc-A", "doc-B", tool_context=ctx)

    assert "app:emitted:ppa_comparison:doc-A:doc-B" in ctx.state
