"""Tests for tools/extract_ppa_clauses.py (v6.4.0 ONE-DEMO M2).

Covers:
  - Happy path: valid blocks → Gemini extraction → typed PpaClauses JSON
  - Missing doc → structured error response, no exception
  - No blocks (still parsing) → structured error
  - Build failure → structured error
  - Schema-violating Gemini output → structured error with raw text
  - tool_context stash for downstream M3 compare tool
  - Clause-subset pre-run config (7.2-M2 PPA-COMPARE-LAUNCHER M1):
    `clauses` shrinks the extraction prompt + restricts the output,
    invalid names are rejected loudly, `max_other_clauses` is a per-call
    override, and the extraction cache key is variant-aware so a subset
    run never serves (or poisons) a full-extraction cache entry.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.schemas.ppa_clauses import PpaClauses


def _make_ctx(state: dict | None = None):
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    return ctx


def _sample_clauses_json(doc_id: str = "doc-1") -> str:
    """Return a valid PpaClauses JSON the mocked Gemini would produce."""
    return json.dumps(
        {
            "doc_id": doc_id,
            "counterparty_buyer": {
                "clause_name": "counterparty_buyer",
                "display_name": "Buyer",
                "value": "ACME Corp",
                "raw_excerpt": "ACME Corp (the Buyer)",
                "block_id": "blk-001",
                "confidence": "high",
            },
            "settlement_type": {
                "clause_name": "settlement_type",
                "display_name": "Settlement Type",
                "value": "PaP",
                "raw_excerpt": "settlement shall be Pay-as-Produced",
                "block_id": "blk-042",
                "confidence": "high",
            },
            "other_clauses": [],
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_validated_ppa_clauses_json():
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "ACME Corp (the Buyer)", "block_id": "blk-001"}]
    sample_json = _sample_clauses_json("doc-1")

    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-as-json-string", sample_blocks),
        ),
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(return_value=sample_json),
        ),
    ):
        result = await extract_ppa_clauses("doc-1")

    parsed = json.loads(result)
    assert parsed["doc_id"] == "doc-1"
    # round-trips through the Pydantic model — proves schema-compliance
    validated = PpaClauses.model_validate(parsed)
    assert validated.counterparty_buyer is not None
    assert validated.counterparty_buyer.value == "ACME Corp"
    assert validated.counterparty_buyer.block_id == "blk-001"
    assert validated.settlement_type.value == "PaP"


@pytest.mark.asyncio
async def test_stashes_result_in_tool_context_for_m3_consumer():
    """compare_ppa_contracts (M3) reads app:emitted:ppa_clauses:{doc_id} to skip re-extracting."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA", "block_id": "blk-x"}]
    sample_json = _sample_clauses_json("doc-2")
    ctx = _make_ctx()

    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", sample_blocks),
        ),
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(return_value=sample_json),
        ),
    ):
        await extract_ppa_clauses("doc-2", tool_context=ctx)

    assert "app:emitted:ppa_clauses:doc-2" in ctx.state
    stashed = json.loads(ctx.state["app:emitted:ppa_clauses:doc-2"])
    assert stashed["doc_id"] == "doc-2"


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_without_re_extracting():
    """A follow-up turn for an already-extracted doc must serve the cached
    PpaClauses from app-scoped state and NOT re-run the extraction LLM (the
    'we recalculate a lot' fix). build_document_context is not touched either."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    cached_json = _sample_clauses_json("doc-cached")
    ctx = _make_ctx({"app:emitted:ppa_clauses:doc-cached": cached_json})
    extract_spy = AsyncMock(return_value=_sample_clauses_json("SHOULD-NOT-RUN"))
    build_spy = MagicMock(return_value=("blocks-json", [{"text": "x"}]))

    with (
        patch("tools.extract_ppa_clauses.build_document_context", new=build_spy),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=extract_spy),
    ):
        result = await extract_ppa_clauses("doc-cached", tool_context=ctx)

    extract_spy.assert_not_awaited()  # LLM skipped
    build_spy.assert_not_called()  # doc load skipped too
    assert json.loads(result)["doc_id"] == "doc-cached"


@pytest.mark.asyncio
async def test_unparseable_cache_falls_through_to_fresh_extraction():
    """A stale/incompatible cache entry (e.g. post-deploy schema bump) must
    self-heal: the tool re-extracts rather than serving garbage."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    ctx = _make_ctx({"app:emitted:ppa_clauses:doc-stale": '{"garbage": true}'})
    fresh_json = _sample_clauses_json("doc-stale")

    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", [{"type": "paragraph", "text": "PPA", "block_id": "b"}]),
        ),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=AsyncMock(return_value=fresh_json)),
    ):
        result = await extract_ppa_clauses("doc-stale", tool_context=ctx)

    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed["doc_id"] == "doc-stale"  # fresh extraction won, cache ignored


# ---------------------------------------------------------------------------
# doc:{id}.json artifact-reference normalization
# ---------------------------------------------------------------------------


def test_normalize_doc_id_strips_artifact_wrapper():
    from tools.extract_ppa_clauses import normalize_doc_id

    assert normalize_doc_id("doc:c9e2b03a.json") == "c9e2b03a"
    assert normalize_doc_id("doc:c9e2b03a") == "c9e2b03a"
    assert normalize_doc_id("c9e2b03a") == "c9e2b03a"  # bare id unchanged
    assert normalize_doc_id("  doc:c9e2b03a.json  ") == "c9e2b03a"


@pytest.mark.asyncio
async def test_accepts_doc_prefixed_id_and_looks_up_bare():
    """The model is shown `doc:{id}.json`; passing that as doc_id must resolve
    to parsed_documents/{bare id}, not 404 (the online regression)."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA", "block_id": "b1"}]
    sample_json = _sample_clauses_json("c9e2b03a")

    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", sample_blocks),
        ) as mock_ctx,
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(return_value=sample_json),
        ),
    ):
        result = await extract_ppa_clauses("doc:c9e2b03a.json")

    parsed = json.loads(result)
    assert "error" not in parsed
    # build_document_context must be called with the BARE id.
    assert mock_ctx.call_args.args[0] == "c9e2b03a"


# ---------------------------------------------------------------------------
# Transparent other_clauses cap — no silent truncation
# ---------------------------------------------------------------------------


def _clauses_json_with_other(doc_id: str, n_other: int) -> str:
    """Valid PpaClauses JSON carrying `n_other` non-standard clauses."""
    return json.dumps(
        {
            "doc_id": doc_id,
            "other_clauses": [
                {
                    "clause_name": f"bespoke_{i}",
                    "display_name": f"Bespoke {i}",
                    "value": f"value {i}",
                    "raw_excerpt": f"clause {i} text",
                    "block_id": f"blk-{i:03d}",
                    "confidence": "medium",
                }
                for i in range(n_other)
            ],
        }
    )


@pytest.mark.asyncio
async def test_other_clauses_capped_transparently():
    """A contract with more bespoke clauses than the cap is truncated, but the
    full count is surfaced (other_clauses_total) — never dropped silently."""
    from tools import extract_ppa_clauses as mod

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    # 25 bespoke clauses, cap at 3 → 3 kept, total reported as 25.
    sample_json = _clauses_json_with_other("doc-big", 25)

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=AsyncMock(return_value=sample_json)),
        patch.object(mod, "_MAX_OTHER_CLAUSES", 3),
    ):
        result = await mod.extract_ppa_clauses("doc-big")

    parsed = json.loads(result)
    assert parsed["other_clauses_truncated"] is True
    assert parsed["other_clauses_total"] == 25
    assert len(parsed["other_clauses"]) == 3


@pytest.mark.asyncio
async def test_other_clauses_not_flagged_when_under_cap():
    """Under the cap: total is still reported, but truncated stays False."""
    from tools import extract_ppa_clauses as mod

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    sample_json = _clauses_json_with_other("doc-small", 2)

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=AsyncMock(return_value=sample_json)),
        patch.object(mod, "_MAX_OTHER_CLAUSES", 20),
    ):
        result = await mod.extract_ppa_clauses("doc-small")

    parsed = json.loads(result)
    assert parsed["other_clauses_truncated"] is False
    assert parsed["other_clauses_total"] == 2
    assert len(parsed["other_clauses"]) == 2


# ---------------------------------------------------------------------------
# Clause-subset pre-run config (7.2-M2 PPA-COMPARE-LAUNCHER M1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clause_subset_shrinks_extraction_prompt_and_restricts_output():
    """clauses=['settlement_type','price_formula'] must (a) produce a prompt
    that asks ONLY for those clauses — no other standard clause name appears —
    and (b) null out any non-requested standard field the model populated
    anyway. Drives the REAL _run_clause_extraction with a patched genai client
    so the assertion is on the actual wire prompt, not a test double."""
    from tools import extract_ppa_clauses as mod

    sample_blocks = [{"type": "paragraph", "text": "PPA body", "block_id": "b1"}]
    # Model "disobeys" and also populates counterparty_buyer — the tool must
    # strip it from the returned payload.
    model_json = _sample_clauses_json("doc-sub")

    captured: dict = {}

    async def _fake_generate(*, model, contents, config):
        captured["contents"] = contents
        resp = MagicMock()
        resp.text = model_json
        return resp

    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(side_effect=_fake_generate)

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.resilient_genai.genai.Client", return_value=fake_client),
        patch("tools.resilient_genai.gemini_api_name_for", return_value="gemini-test"),
    ):
        result = await mod.extract_ppa_clauses("doc-sub", clauses=["settlement_type", "price_formula"])

    prompt = captured["contents"]
    # (a) Prompt is scoped: requested clauses named, every other standard
    # clause name absent, and the ONLY-these instruction present.
    assert "settlement_type" in prompt
    assert "price_formula" in prompt
    for other in mod.STANDARD_CLAUSE_FIELDS:
        if other in ("settlement_type", "price_formula"):
            continue
        assert other not in prompt, f"non-requested clause {other!r} leaked into the subset prompt"
    assert "only" in prompt.lower()

    # (b) Output restricted to the subset.
    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed["settlement_type"]["value"] == "PaP"
    assert parsed["counterparty_buyer"] is None  # populated by model, stripped by tool
    assert parsed["price_formula"] is None  # requested but absent in doc — stays null


@pytest.mark.asyncio
async def test_clause_subset_threads_through_to_extraction_call():
    """The subset must reach _run_clause_extraction (i.e. the prompt builder),
    not just the post-processing."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA", "block_id": "b1"}]
    run_mock = AsyncMock(return_value=_sample_clauses_json("doc-1"))

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=run_mock),
    ):
        await extract_ppa_clauses("doc-1", clauses=["settlement_type", "price_formula"])

    assert run_mock.await_count == 1
    call = run_mock.await_args
    passed = list(call.args) + list(call.kwargs.values())
    assert ["settlement_type", "price_formula"] in passed


@pytest.mark.asyncio
async def test_invalid_clause_names_rejected_loudly():
    """A typo'd clause name must return an actionable structured error that
    names the offender AND the valid vocabulary — never silently ignored.
    Neither the doc load nor the LLM may run."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    build_spy = MagicMock()
    run_spy = AsyncMock()

    with (
        patch("tools.extract_ppa_clauses.build_document_context", new=build_spy),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=run_spy),
    ):
        result = await extract_ppa_clauses("doc-1", clauses=["settlement_type", "totally_bogus"])

    parsed = json.loads(result)
    assert "error" in parsed
    assert "totally_bogus" in parsed["error"]  # names the offender
    assert "settlement_type" in parsed["error"]  # lists the valid vocabulary
    assert "governing_law" in parsed["error"]
    build_spy.assert_not_called()
    run_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_clause_list_rejected_loudly():
    """clauses=[] selects nothing — reject with guidance, don't run a no-op LLM call."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    result = await extract_ppa_clauses("doc-1", clauses=[])
    parsed = json.loads(result)
    assert "error" in parsed
    assert "clauses" in parsed["error"]


@pytest.mark.asyncio
async def test_max_other_clauses_per_call_override_respected():
    """Per-call max_other_clauses beats the env-default cap; transparency
    fields (other_clauses_total / other_clauses_truncated) still populated."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    sample_json = _clauses_json_with_other("doc-override", 25)

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=AsyncMock(return_value=sample_json)),
    ):
        result = await extract_ppa_clauses("doc-override", max_other_clauses=2)

    parsed = json.loads(result)
    assert len(parsed["other_clauses"]) == 2  # override (2) won over env default (20)
    assert parsed["other_clauses_total"] == 25
    assert parsed["other_clauses_truncated"] is True


@pytest.mark.asyncio
async def test_max_other_clauses_negative_override_disables_cap():
    """Same disable semantics as the env var: negative → no cap."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    sample_json = _clauses_json_with_other("doc-nocap", 25)

    with (
        patch("tools.extract_ppa_clauses.build_document_context", return_value=("blocks-json", sample_blocks)),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=AsyncMock(return_value=sample_json)),
    ):
        result = await extract_ppa_clauses("doc-nocap", max_other_clauses=-1)

    parsed = json.loads(result)
    assert len(parsed["other_clauses"]) == 25
    assert parsed["other_clauses_truncated"] is False


@pytest.mark.asyncio
async def test_subset_run_never_serves_full_extraction_cache():
    """CORRECTNESS: a cached FULL extraction must not satisfy a subset run —
    the cache key includes the variant. The subset run re-extracts, stashes
    under its own key, and leaves the full-extraction entry untouched."""
    from tools.extract_ppa_clauses import clause_cache_key, extract_ppa_clauses

    full_key = clause_cache_key("doc-A")
    full_json = _sample_clauses_json("doc-A")
    ctx = _make_ctx({full_key: full_json})

    run_mock = AsyncMock(return_value=_sample_clauses_json("doc-A"))
    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", [{"type": "paragraph", "text": "PPA", "block_id": "b"}]),
        ),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=run_mock),
    ):
        result = await extract_ppa_clauses("doc-A", clauses=["settlement_type"], tool_context=ctx)

    run_mock.assert_awaited_once()  # cache was NOT served — subset re-extracted
    parsed = json.loads(result)
    assert parsed["settlement_type"]["value"] == "PaP"
    assert parsed["counterparty_buyer"] is None  # subset shape, not the full extraction

    subset_key = clause_cache_key("doc-A", clauses=["settlement_type"])
    assert subset_key != full_key
    assert subset_key in ctx.state  # stashed under the variant key
    assert ctx.state[full_key] == full_json  # full entry untouched


@pytest.mark.asyncio
async def test_subset_cache_hit_served_for_matching_subset():
    """Two identical subset runs → second is a cache hit under the variant key."""
    from tools.extract_ppa_clauses import clause_cache_key, extract_ppa_clauses

    subset = ["price_formula", "settlement_type"]
    cached = PpaClauses(doc_id="doc-A").model_dump_json()
    ctx = _make_ctx({clause_cache_key("doc-A", clauses=subset): cached})

    run_mock = AsyncMock()
    build_spy = MagicMock()
    with (
        patch("tools.extract_ppa_clauses.build_document_context", new=build_spy),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=run_mock),
    ):
        result = await extract_ppa_clauses("doc-A", clauses=subset, tool_context=ctx)

    run_mock.assert_not_awaited()
    build_spy.assert_not_called()
    assert json.loads(result)["doc_id"] == "doc-A"


@pytest.mark.asyncio
async def test_clause_cache_key_order_insensitive_and_cap_aware():
    """Key must be stable across clause ordering, and a per-call cap variant
    must not collide with the default-cap entry."""
    from tools.extract_ppa_clauses import clause_cache_key

    a = clause_cache_key("doc-A", clauses=["settlement_type", "price_formula"])
    b = clause_cache_key("doc-A", clauses=["price_formula", "settlement_type"])
    assert a == b
    assert clause_cache_key("doc-A", max_other_clauses=3) != clause_cache_key("doc-A")
    assert clause_cache_key("doc-A", clauses=["settlement_type"]) != clause_cache_key("doc-A")


@pytest.mark.asyncio
async def test_full_run_ignores_subset_cache_entry():
    """The inverse hazard: a cached SUBSET extraction must not satisfy a full run."""
    from tools.extract_ppa_clauses import clause_cache_key, extract_ppa_clauses

    subset_cached = PpaClauses(doc_id="doc-A").model_dump_json()
    ctx = _make_ctx({clause_cache_key("doc-A", clauses=["settlement_type"]): subset_cached})

    run_mock = AsyncMock(return_value=_sample_clauses_json("doc-A"))
    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", [{"type": "paragraph", "text": "PPA", "block_id": "b"}]),
        ),
        patch("tools.extract_ppa_clauses._run_clause_extraction", new=run_mock),
    ):
        result = await extract_ppa_clauses("doc-A", tool_context=ctx)

    run_mock.assert_awaited_once()  # full extraction ran, subset entry ignored
    assert json.loads(result)["counterparty_buyer"]["value"] == "ACME Corp"


def test_standard_clause_fields_match_schema():
    """STANDARD_CLAUSE_FIELDS is the schema-derived vocabulary — exactly the
    12 standard clause fields, no bookkeeping fields."""
    from tools.extract_ppa_clauses import STANDARD_CLAUSE_FIELDS

    assert len(STANDARD_CLAUSE_FIELDS) == 12
    assert "settlement_type" in STANDARD_CLAUSE_FIELDS
    assert "governing_law" in STANDARD_CLAUSE_FIELDS
    for bookkeeping in ("doc_id", "other_clauses", "other_clauses_total", "other_clauses_truncated"):
        assert bookkeeping not in STANDARD_CLAUSE_FIELDS


# ---------------------------------------------------------------------------
# Error paths — all return structured JSON, NEVER raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_doc_returns_structured_error():
    from tools.extract_ppa_clauses import extract_ppa_clauses

    with patch(
        "tools.extract_ppa_clauses.build_document_context",
        side_effect=KeyError("doc-missing"),
    ):
        result = await extract_ppa_clauses("doc-missing")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "doc-missing" in parsed["error"]
    assert parsed["doc_id"] == "doc-missing"


@pytest.mark.asyncio
async def test_unparsed_doc_returns_structured_error():
    """When build_document_context returns no blocks (still parsing / failed)."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    with patch(
        "tools.extract_ppa_clauses.build_document_context",
        return_value=("status message string", None),
    ):
        result = await extract_ppa_clauses("doc-pending")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "no parsed blocks" in parsed["error"].lower() or "still" in parsed["error"].lower()
    assert parsed["doc_id"] == "doc-pending"


@pytest.mark.asyncio
async def test_gemini_call_failure_returns_structured_error():
    """Network error / quota / etc. on the Gemini call → structured error, no exception."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", sample_blocks),
        ),
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(side_effect=RuntimeError("503 Service Unavailable")),
        ),
    ):
        result = await extract_ppa_clauses("doc-quota")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "503" in parsed["error"]
    assert parsed["doc_id"] == "doc-quota"


@pytest.mark.asyncio
async def test_neither_doc_id_nor_gs_url_returns_structured_error():
    from tools.extract_ppa_clauses import extract_ppa_clauses

    result = await extract_ppa_clauses()
    parsed = json.loads(result)
    assert "error" in parsed
    assert "required" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_both_doc_id_and_gs_url_returns_structured_error():
    from tools.extract_ppa_clauses import extract_ppa_clauses

    result = await extract_ppa_clauses(doc_id="doc-A", gs_url="gs://bucket/file.docx")
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exactly one" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_gs_url_path_parses_on_the_fly_and_returns_clauses():
    """Self-discovery path: agent calls list_bucket_documents → picks a file →
    calls extract_ppa_clauses(gs_url=...) and gets typed clauses without any
    upload step. Mocks AILANG Parse to confirm the wiring."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "ACME Corp (the Buyer)", "block_id": "blk-001"}]
    sample_json = _sample_clauses_json("gs://bucket/X.pdf")

    fake_outcome = MagicMock()
    fake_outcome.blocks = sample_blocks
    fake_outcome.error = None

    with (
        patch(
            "tools.extract_ppa_clauses.parse_gcs_file",
            new=AsyncMock(return_value=fake_outcome),
        ),
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(return_value=sample_json),
        ),
    ):
        result = await extract_ppa_clauses(gs_url="gs://bucket/X.pdf")

    parsed = json.loads(result)
    validated = PpaClauses.model_validate(parsed)
    assert validated.doc_id == "gs://bucket/X.pdf"


@pytest.mark.asyncio
async def test_gs_url_path_unsupported_extension_returns_structured_error():
    """parse_gcs_file returns None for unsupported extensions — we should
    not crash; surface a clear error pointing the user at the convert step."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    with patch(
        "tools.extract_ppa_clauses.parse_gcs_file",
        new=AsyncMock(return_value=None),
    ):
        result = await extract_ppa_clauses(gs_url="gs://bucket/scanned.tif")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "extension" in parsed["error"].lower() or "deterministic" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_gs_url_path_ailang_error_returns_structured_error():
    """When AILANG Parse rejects the file (auth, quota, api error), surface
    the outcome's error+code so the operator can debug."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    fake_outcome = MagicMock()
    fake_outcome.blocks = None
    fake_outcome.error = "Quota exceeded"
    fake_outcome.error_code = "quota"

    with patch(
        "tools.extract_ppa_clauses.parse_gcs_file",
        new=AsyncMock(return_value=fake_outcome),
    ):
        result = await extract_ppa_clauses(gs_url="gs://bucket/X.pdf")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "Quota" in parsed["error"] or "quota" in parsed["error"]
    assert parsed.get("error_code") == "quota"


@pytest.mark.asyncio
async def test_schema_violating_output_returns_structured_error():
    """Gemini occasionally returns JSON that doesn't match the schema → structured error.

    This is the Axiom #2 guardrail: rather than emit half-formed A2UI cards
    with missing fields, surface the failure so the agent can apologise
    and retry. Better to feel reliable than magical.
    """
    from tools.extract_ppa_clauses import extract_ppa_clauses

    sample_blocks = [{"type": "paragraph", "text": "PPA"}]
    bogus_json = json.dumps({"not": "matching", "the_schema": True})  # missing doc_id

    with (
        patch(
            "tools.extract_ppa_clauses.build_document_context",
            return_value=("blocks-json", sample_blocks),
        ),
        patch(
            "tools.extract_ppa_clauses._run_clause_extraction",
            new=AsyncMock(return_value=bogus_json),
        ),
    ):
        result = await extract_ppa_clauses("doc-bogus")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "schema" in parsed["error"].lower() or "validation" in parsed["error"].lower()
    assert parsed["doc_id"] == "doc-bogus"
