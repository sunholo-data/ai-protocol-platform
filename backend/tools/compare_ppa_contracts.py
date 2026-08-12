"""compare_ppa_contracts — pairwise PPA diff with commercial-implication reasoning.

v6.4.0 ONE-DEMO M3. Composes M2's extract_ppa_clauses across two documents
and returns a typed PpaComparison with one ClauseDifference per material
divergence. Each diff carries both `left_block_id` + `right_block_id`
citations, severity (material / moderate / cosmetic), and a one-sentence
`commercial_implication`.

Reads from tool_context.state["app:emitted:ppa_clauses:{doc_id}"] when
present (M2 stashes typed extractions there) to skip re-running extraction
on docs the agent already analysed this turn. Otherwise runs extraction
inline. This is the function-as-schema composition pattern from G24.

Rendered downstream as the KeyDifferencesPanel A2UI artefact on the
workspace surface (M3 frontend), with click-to-explain via surface-action.
"""

from __future__ import annotations

import json
import logging
import os

from google.adk.tools import ToolContext

from tools.extract_ppa_clauses import (
    cache_variant_suffix,
    clause_cache_key,
    extract_ppa_clauses,
    normalize_doc_id,
    validate_clause_subset,
)
from tools.resilient_genai import generate_content_resilient
from tools.schemas.ppa_clauses import PpaClauses, PpaComparison, PpaDifferences

log = logging.getLogger(__name__)

# Comparison uses a stronger TIER than extraction — the diff reasoning needs
# multi-step commercial analysis. Default to the `pro` tier (Gemini reasoning,
# purpose-built in models.yaml for literal JSON emission), resolved through the
# shared registry rather than a hardcoded id. Must resolve to a Gemini model:
# the diff call uses Vertex structured output (response_schema), Gemini-only —
# gemini_api_name_for() enforces that. (`smart` = claude-opus can't serve it.)
_COMPARISON_TIER = os.environ.get("PPA_COMPARISON_TIER", "pro")


_COMPARISON_PROMPT = """You are a precise PPA (Power Purchase Agreement) contract analyst.

Below are two extracted PpaClauses objects — `left` and `right` — covering
the same 12 standard PPA clause fields. Produce a `differences[]` list with
one entry per clause where the contracts diverge in a way that would affect
the deal. Do NOT echo the source clauses back — only emit the differences.

For each ClauseDifference:
  1. Set `clause_name` and `display_name` to match the source field
  2. Carry the left + right values AND both source `block_id` citations so
     the user can navigate to either contract's span
  3. Set `severity` based on commercial impact:
       material — changes economics, risk allocation, or settlement
                  mechanics. Examples: PaP vs PaN, different price formula,
                  different term length, different settlement form
                  (financially-settled vs physically-settled).
       moderate — changes process or who-does-what but not the deal
                  economics. Examples: different RtM provider for the
                  same product, different governing law jurisdiction,
                  different termination notice period.
       cosmetic — wording or definitional rephrasing without functional
                  effect. Examples: different counterparty name spelling,
                  Annex reference numbering.
  4. Write `commercial_implication` as ONE concise sentence explaining
     why this divergence matters in practical terms. No hedging, no
     bullet lists, no apologies. Be factual.

Identical clauses (same value on both sides) should NOT appear in
`differences[]`. Skip clauses where BOTH sides are null (clause not
present in either contract) — that's not a divergence.

Two extracted PpaClauses (JSON):
"""


async def compare_ppa_contracts(
    left_doc_id: str | None = None,
    right_doc_id: str | None = None,
    left_gs_url: str | None = None,
    right_gs_url: str | None = None,
    clauses: list[str] | None = None,
    max_other_clauses: int | None = None,
    tool_context: ToolContext = None,
) -> str:
    """Compare two PPA contracts clause-by-clause with commercial reasoning.

    Use this tool when the user asks to "compare these two PPAs", "what's
    different between contracts A and B", "show me a side-by-side", or
    similar comparison requests across two named documents.

    Two input modes per side — pass EXACTLY ONE of (doc_id, gs_url) per side:
      - `left_doc_id` / `right_doc_id`: parsed_documents/{doc_id} entries
      - `left_gs_url` / `right_gs_url`: direct `gs://bucket/path/file.docx`
        URLs. The tool runs AILANG Parse on the fly — useful when the agent
        discovered the contracts via `list_documents_in_bucket` and wants
        to compare without an explicit upload step.

    Left and right can mix modes (e.g. left from parsed_documents, right
    from a GCS URL) — each side is resolved independently.

    Output is a typed `PpaComparison` JSON containing:
      - `left` and `right`: full PpaClauses extractions for each contract
        (with block_id citations on every clause)
      - `differences`: ordered list of ClauseDifference rows covering
        every material / moderate / cosmetic divergence. Each diff
        includes both `left_block_id` and `right_block_id` for navigation,
        plus a one-sentence `commercial_implication`.

    Rendered downstream as the KeyDifferencesPanel workspace artefact —
    clicking any diff row in the UI triggers a `surface-action` that
    sends the diff descriptor back to the agent for follow-up explanation.

    Pre-run config (7.2-M2 compare launcher): pass `clauses` to run a
    SCOPED comparison — both sides are extracted for only those clauses and
    the `differences` list covers only them. Use the same `clauses` /
    `max_other_clauses` values you passed to any prior extract_ppa_clauses
    calls this turn so the extraction cache hits (the key includes them).

    Args:
        left_doc_id: First contract (parsed_documents path).
        right_doc_id: Second contract (parsed_documents path).
        left_gs_url: First contract (GCS URL, AILANG-parsed on the fly).
        right_gs_url: Second contract (GCS URL).
        clauses: Optional subset of the 12 standard clause field names to
            extract and diff (see extract_ppa_clauses for the vocabulary).
            Invalid names return a structured error listing the valid names —
            nothing is silently ignored.
        max_other_clauses: Per-call override for the non-standard clause cap,
            threaded to both extractions. `other_clauses_total` stays
            transparent on each side.

    Returns:
        JSON of a PpaComparison object. On error, JSON of
        `{"error": "...", "left_doc_id": ..., "right_doc_id": ...}`.
    """
    # Resolve identities up-front so the error/cache paths can reference them.
    left_id, left_err = _select_identity(left_doc_id, left_gs_url, "left")
    right_id, right_err = _select_identity(right_doc_id, right_gs_url, "right")
    if left_err:
        return json.dumps(
            {"error": left_err, "left_doc_id": left_doc_id or left_gs_url, "right_doc_id": right_doc_id or right_gs_url}
        )
    if right_err:
        return json.dumps(
            {
                "error": right_err,
                "left_doc_id": left_doc_id or left_gs_url,
                "right_doc_id": right_doc_id or right_gs_url,
            }
        )

    # Reject bad clause subsets LOUDLY before any extraction or cache read.
    subset_error = validate_clause_subset(clauses)
    if subset_error:
        return json.dumps({"error": subset_error, "left_doc_id": left_id, "right_doc_id": right_id})

    # Cache read: the comparison is deterministic per (left_id, right_id) pair of
    # immutable docs, so return a previously-computed comparison WITHOUT
    # re-resolving clauses or re-running the comparison LLM. The cached
    # PpaComparison already carries left+right clauses, so a hit short-circuits
    # the whole tool. Same app-scoped trust model as the clause cache in
    # _resolve_clauses; falls through on a parse failure so a post-deploy schema
    # bump self-heals into a fresh comparison. The key is VARIANT-AWARE
    # (7.2-M2): a subset / per-call-cap run reads and writes its own entry, so
    # it can never serve — or poison — the full-comparison one.
    comparison_key = (
        f"app:emitted:ppa_comparison:{left_id}:{right_id}{cache_variant_suffix(clauses, max_other_clauses)}"
    )
    if tool_context is not None:
        cached = tool_context.state.get(comparison_key)
        if cached:
            try:
                PpaComparison.model_validate_json(cached)  # guard: only serve valid
                log.info(
                    "compare_ppa_contracts: cache hit for %s vs %s (skipped comparison LLM)",
                    left_id,
                    right_id,
                )
                return cached
            except Exception:  # any parse failure → re-compare
                log.warning(
                    "compare_ppa_contracts: cached comparison for %s vs %s unparseable; re-comparing",
                    left_id,
                    right_id,
                )

    try:
        left_clauses = await _resolve_clauses(
            doc_id=left_doc_id,
            gs_url=left_gs_url,
            identity=left_id,
            tool_context=tool_context,
            clauses=clauses,
            max_other_clauses=max_other_clauses,
        )
    except _ExtractionError as exc:
        return _error(exc, left_id, right_id, side="left")

    try:
        right_clauses = await _resolve_clauses(
            doc_id=right_doc_id,
            gs_url=right_gs_url,
            identity=right_id,
            tool_context=tool_context,
            clauses=clauses,
            max_other_clauses=max_other_clauses,
        )
    except _ExtractionError as exc:
        return _error(exc, left_id, right_id, side="right")

    try:
        diffs_json = await _run_comparison(left_clauses, right_clauses, clauses)
    except Exception as exc:
        log.warning(
            "compare_ppa_contracts: comparison call failed for (%s, %s): %s",
            left_id,
            right_id,
            exc,
        )
        return json.dumps(
            {
                "error": f"Comparison failed: {exc}",
                "left_doc_id": left_id,
                "right_doc_id": right_id,
            }
        )

    # The model only emits the diff rows (slim schema — see PpaDifferences).
    # Assemble the full PpaComparison in Python from the extractions we
    # already hold, so the frontend still receives left + right + differences.
    try:
        diffs = PpaDifferences.model_validate_json(diffs_json)
        # Defence-in-depth for subset runs: the scoped extractions + prompt
        # restriction should already confine the diff, but drop any row the
        # model emitted outside the requested subset so the OUTPUT contract
        # ("diff covers only the subset") holds unconditionally.
        if clauses is not None:
            in_subset = [d for d in diffs.differences if d.clause_name in clauses]
            if len(in_subset) != len(diffs.differences):
                log.info(
                    "compare_ppa_contracts: dropped %d diff row(s) outside the requested clause subset",
                    len(diffs.differences) - len(in_subset),
                )
            diffs.differences = in_subset
        validated = PpaComparison(
            left=left_clauses,
            right=right_clauses,
            differences=diffs.differences,
        )
    except Exception as exc:
        log.warning(
            "compare_ppa_contracts: schema validation failed for (%s, %s): %s",
            left_id,
            right_id,
            exc,
        )
        return json.dumps(
            {
                "error": f"Comparison JSON did not match PpaDifferences schema: {exc}",
                "left_doc_id": left_id,
                "right_doc_id": right_id,
            }
        )

    if tool_context is not None:
        # Stash the comparison so a follow-up "explain this diff" turn can
        # read the typed structure without re-comparing.
        tool_context.state[comparison_key] = validated.model_dump_json()

    # Success log — so .dev-logs shows the tool returned a valid comparison
    # (the frontend workbench renders from this; a blank Workspace with this
    # line present means the render bug is frontend-side, not the tool).
    log.info(
        "compare_ppa_contracts: OK %s vs %s — %d differences (%d material)",
        left_id,
        right_id,
        len(validated.differences),
        sum(1 for d in validated.differences if d.severity == "material"),
    )
    return validated.model_dump_json()


def _select_identity(doc_id: str | None, gs_url: str | None, side: str) -> tuple[str | None, str | None]:
    """Validate and return (identity_string, error_or_None) for one side.

    Normalizes a `doc:{id}.json` artifact reference back to the bare id so the
    identity (and therefore the extraction cache key) matches what
    extract_ppa_clauses resolves. See extract_ppa_clauses.normalize_doc_id.
    """
    if doc_id and gs_url:
        return None, f"Pass exactly one of {side}_doc_id or {side}_gs_url, not both."
    if not doc_id and not gs_url:
        return None, f"Either {side}_doc_id or {side}_gs_url is required."
    identity = normalize_doc_id(doc_id) if doc_id else gs_url
    return identity, None


class _ExtractionError(RuntimeError):
    """Raised internally when extract_ppa_clauses returns a structured error
    rather than a typed PpaClauses. Carries the error dict so we surface it."""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("error", "Extraction failed"))


async def _resolve_clauses(
    *,
    doc_id: str | None,
    gs_url: str | None,
    identity: str,
    tool_context: ToolContext | None,
    clauses: list[str] | None = None,
    max_other_clauses: int | None = None,
) -> PpaClauses:
    """Use cached extraction from state if present, else extract fresh.

    Cache key matches extract_ppa_clauses's stash key — whichever identity
    the agent passed (doc_id or gs_url) is what we key on, PLUS the pre-run
    config variant (clause_cache_key), so a subset run can only reuse an
    extraction made with the exact same config. Reuse rather than burn a
    second Gemini call on the same identity in one turn.
    """
    if tool_context is not None:
        cached_key = clause_cache_key(identity, clauses, max_other_clauses)
        cached = tool_context.state.get(cached_key)
        if cached:
            try:
                return PpaClauses.model_validate_json(cached)
            except Exception:
                # Cache is stale/corrupt — fall through to re-extract
                log.info(
                    "compare_ppa_contracts: cached extraction for %s is unparseable; re-extracting",
                    identity,
                )

    # Thread the pre-run config only when supplied — keeps the call shape
    # identical to pre-M1 for default runs.
    extra_kwargs: dict = {}
    if clauses is not None:
        extra_kwargs["clauses"] = clauses
    if max_other_clauses is not None:
        extra_kwargs["max_other_clauses"] = max_other_clauses
    raw = await extract_ppa_clauses(doc_id=doc_id, gs_url=gs_url, tool_context=tool_context, **extra_kwargs)
    parsed = json.loads(raw)
    if "error" in parsed:
        raise _ExtractionError(parsed)
    return PpaClauses.model_validate(parsed)


def _error(exc: _ExtractionError, left_id: str | None, right_id: str | None, side: str) -> str:
    """Format an extraction error as the compare tool's structured error."""
    return json.dumps(
        {
            "error": f"Could not extract clauses for {side} document: {exc.payload.get('error')}",
            "left_doc_id": left_id,
            "right_doc_id": right_id,
            "failed_side": side,
            "failed_doc_id": exc.payload.get("doc_id"),
        }
    )


async def _run_comparison(left: PpaClauses, right: PpaClauses, clauses: list[str] | None = None) -> str:
    """Run Gemini structured-output comparison over two PpaClauses extractions.

    Returns JSON for a PpaDifferences (diff rows only) — NOT a full
    PpaComparison. The two source PpaClauses would overflow Vertex's
    constrained-decoding schema-state budget, so we keep the response schema
    slim and rebuild the full comparison in Python.
    """
    payload = {"left": left.model_dump(), "right": right.model_dump()}
    prompt = _COMPARISON_PROMPT
    if clauses is not None:
        prompt += (
            "SCOPED RUN — restrict the diff to ONLY these clauses (emit no row "
            "for any other field): " + ", ".join(clauses) + "\n\n"
        )
    prompt += json.dumps(payload, ensure_ascii=False, indent=2)

    schema_dict = PpaDifferences.model_json_schema()

    # Resilient: retry + Gemini region/model failover on a transient Vertex 429,
    # with a visible working-state + retry/fallback notices (2026-07-17).
    response = await generate_content_resilient(
        prompt=prompt,
        model_ref=_COMPARISON_TIER,
        config={
            "response_mime_type": "application/json",
            "response_schema": schema_dict,
        },
        progress_label="Comparing contracts…",
        label="contract-comparison",
    )
    return response.text or "{}"
