"""extract_ppa_clauses — typed PPA-contract clause extraction (v6.4.0 ONE-DEMO M2).

ADK FunctionTool that takes a `doc_id` for an already-parsed PPA contract and
returns structured `PpaClauses` JSON with `block_id` citations on every
populated clause. Renders downstream as the A2UI `ClauseExtractionCard`.

Implementation pattern follows tools/structured_extraction.py:
  - Load AILANG blocks via build_document_context(doc_id, mode="blocks")
  - Run Gemini structured-output with response_schema=PpaClauses
  - Return JSON string (ADK FunctionTool contract requires str return)

Why this lives as a standalone FunctionTool rather than the existing
structured_extraction_callback path:
  - The callback fires AFTER agent response, useful when the agent
    organically reads a doc then we extract. This tool is INSTEAD: the
    skill calls it explicitly when the user asks "extract clauses".
  - Returning typed JSON to the agent lets it compose with
    compare_ppa_contracts (M3) without re-reading state.
  - Predictable A2UI artefact emission: tool result is the card payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from google.adk.tools import ToolContext

from tools.documents.ailang_parse import parse_gcs_file
from tools.documents.context import build_document_context
from tools.resilient_genai import generate_content_resilient
from tools.schemas.ppa_clauses import PpaClauses

log = logging.getLogger(__name__)

# Model TIER for the structured extraction call — resolved through the shared
# registry (config/models.yaml tier_defaults), never a hardcoded model id, so
# deploys/forks pick models via tiers like every skill. Must resolve to a
# Gemini model: extraction uses Vertex structured output (response_schema),
# which is Gemini-only — gemini_api_name_for() enforces that at call time.
_EXTRACTION_TIER = os.environ.get("PPA_EXTRACTION_TIER", "lite")
# Output-token budget for a single extraction. The default cap truncated large
# contracts mid-JSON, so we raise it — but the model's `maxOutputTokens` range is
# 1 (inclusive) to 65_536 (EXCLUSIVE), so the ceiling is 65_535. Passing 65_536
# is a hard 400 INVALID_ARGUMENT (the 2026-07-14 live failure on Google LEAP).
_EXTRACTION_MAX_OUTPUT_TOKENS = int(os.environ.get("PPA_EXTRACTION_MAX_TOKENS", "65535"))


def _raise_if_truncated(response: object, doc_id: str) -> None:
    """Raise a clear, actionable error if the model stopped on the output cap
    (finish_reason MAX_TOKENS). Without this the truncated JSON fails downstream
    as an opaque pydantic "EOF while parsing" (the 2026-07-14 live failure)."""
    try:
        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        reason = (getattr(finish, "name", None) or str(finish or "")).upper()
    except Exception:
        return
    if "MAX_TOKENS" in reason:
        raise ValueError(
            f"Clause extraction for {doc_id} hit the model output limit "
            f"({_EXTRACTION_MAX_OUTPUT_TOKENS} tokens) — this contract is too large to "
            "extract in a single pass. Extract a specific clause subset, or split the document."
        )


def normalize_doc_id(doc_id: str) -> str:
    """Strip the artifact-reference wrapper the model sees back to the bare id.

    The doc loader injects attachments as `[Attached document: doc:{id}.json …]`
    (adk/callbacks.py) — that's the ADK artifact name. Models faithfully pass
    that `doc:{id}.json` (or `doc:{id}`) form straight into `doc_id`, but
    parsed_documents is keyed by the BARE id. Without this, a correct model
    call fails with "not found in parsed_documents" and the agent flails.
    Idempotent: a bare id passes through unchanged.
    """
    return doc_id.strip().removeprefix("doc:").removesuffix(".json")


# The 12 standard clause field names — the ONLY values accepted in the
# `clauses` pre-run config (7.2-M2 PPA-COMPARE-LAUNCHER M1). Derived from the
# schema so the vocabulary can never drift from PpaClauses.
STANDARD_CLAUSE_FIELDS: tuple[str, ...] = tuple(
    name
    for name in PpaClauses.model_fields
    if name not in ("doc_id", "other_clauses", "other_clauses_total", "other_clauses_truncated")
)


def validate_clause_subset(clauses: list[str] | None) -> str | None:
    """Return an actionable error string when `clauses` is not a valid subset.

    None (no subset requested) is valid. An empty list and any name outside
    STANDARD_CLAUSE_FIELDS are rejected LOUDLY — a typo'd clause silently
    ignored would make a scoped comparison look complete when it isn't.
    """
    if clauses is None:
        return None
    valid = ", ".join(STANDARD_CLAUSE_FIELDS)
    if not clauses:
        return (
            "clauses=[] selects nothing. Omit the argument to extract all 12 "
            f"standard clauses, or pass a non-empty subset of: {valid}"
        )
    invalid = sorted(set(clauses) - set(STANDARD_CLAUSE_FIELDS))
    if invalid:
        return f"Invalid clause name(s): {', '.join(invalid)}. Valid standard clause names: {valid}"
    return None


def cache_variant_suffix(clauses: list[str] | None, max_other_clauses: int | None) -> str:
    """Cache-key suffix identifying the pre-run config variant.

    Empty for a default run (backward compatible with pre-M1 cache entries).
    The clause list is sorted so key identity is order-insensitive. Including
    `max_other_clauses` keeps a per-call-cap result from serving (or being
    served by) a default-cap run — the `other_clauses` payload differs.

    CORRECTNESS INVARIANT (7.5 caches vs 7.2-M2 subset runs): a subset run
    must NEVER return a full-extraction/full-comparison cache hit, and must
    never poison the full entry. Variant-keyed reads AND writes give both.
    """
    suffix = ""
    if clauses is not None:
        suffix += ":clauses=" + ",".join(sorted(clauses))
    if max_other_clauses is not None:
        suffix += f":max_other={max_other_clauses}"
    return suffix


def clause_cache_key(
    identity: str,
    clauses: list[str] | None = None,
    max_other_clauses: int | None = None,
) -> str:
    """State key for a cached extraction — variant-aware (see cache_variant_suffix).

    compare_ppa_contracts computes the SAME key for its read side, so an
    extraction cached by one tool is reusable by the other only when the
    pre-run config matches exactly.
    """
    return f"app:emitted:ppa_clauses:{identity}{cache_variant_suffix(clauses, max_other_clauses)}"


# Cap on non-standard clauses carried in `other_clauses`. A very long
# contract can surface dozens of bespoke clauses; without a bound the
# extraction payload (and the two-doc comparison built on top of it) grows
# unpredictably. The cap is TRANSPARENT — see _apply_other_clauses_cap: the
# full count is preserved in `other_clauses_total` and the agent/UI surface
# "showing N of M" rather than silently dropping the tail. Set to a negative
# value to disable capping entirely.
_MAX_OTHER_CLAUSES = int(os.environ.get("PPA_MAX_OTHER_CLAUSES", "20"))

_EXTRACTION_PROMPT = """You are a precise PPA (Power Purchase Agreement) contract analyst.

Extract the standard PPA clauses from the document blocks below into the
provided JSON schema. For every populated clause:

  1. Set `value` to the normalised extracted value (e.g. "PaP", "Fixed
     €45/MWh CPI-indexed", "20 years")
  2. Copy the verbatim contract text into `raw_excerpt`
  3. Set `block_id` to the AILANG block id of the source span. If a clause
     spans multiple blocks, pick the most representative one.
  4. Set `confidence`:
       high   — definition is explicit and the value is unambiguous
       medium — value is reasonable inference from context
       low    — clause referenced but value is unclear or partial
  5. Use `notes` to surface caveats (e.g. "definition references Annex A
     which is not included").

For any standard clause field NOT present in the document, leave it as
null. Do NOT invent or hallucinate clauses.

For non-standard contract-specific clauses (e.g. unusual hedge mechanics,
bespoke termination triggers), add them to `other_clauses[]` with a
descriptive `clause_name`.

Settlement type values: PaP | PaN | BL
Contract form values: Physical | Financial-FS | Financial-PS

Document blocks (JSON):
"""

# Scoped variant of the extraction prompt (7.2-M2 pre-run config): asks ONLY
# for the caller-selected clauses, which shrinks the prompt and the model's
# working set for a launcher run scoped to e.g. two clauses.
_SUBSET_EXTRACTION_PROMPT = """You are a precise PPA (Power Purchase Agreement) contract analyst.

Extract ONLY the following clauses from the document blocks below into the
provided JSON schema — the caller pre-selected them for a scoped run:

{clause_list}

Leave EVERY standard clause field that is NOT listed above null, even when
that clause is present in the document. For every populated clause:

  1. Set `value` to the normalised extracted value
  2. Copy the verbatim contract text into `raw_excerpt`
  3. Set `block_id` to the AILANG block id of the source span. If a clause
     spans multiple blocks, pick the most representative one.
  4. Set `confidence`: high (explicit and unambiguous) / medium (reasonable
     inference from context) / low (referenced but unclear or partial)
  5. Use `notes` to surface caveats

If a listed clause is NOT present in the document, leave it null too. Do
NOT invent or hallucinate clauses.

For non-standard contract-specific clauses (e.g. unusual hedge
mechanics, bespoke break-clause triggers), add them to `other_clauses[]`
with a descriptive `clause_name`.

{vocab}Document blocks (JSON):
"""


def _build_extraction_prompt(clauses: list[str] | None) -> str:
    """Full prompt for a default run; scoped prompt when a subset is given.

    The scoped prompt names ONLY the requested clauses — no other standard
    clause name appears — and carries the value vocabularies just for the
    requested enum-like clauses.
    """
    if clauses is None:
        return _EXTRACTION_PROMPT
    clause_list = "\n".join(f"  - {name}" for name in clauses)
    vocab_lines = []
    if "settlement_type" in clauses:
        vocab_lines.append("Settlement type values: PaP | PaN | BL")
    if "contract_form" in clauses:
        vocab_lines.append("Contract form values: Physical | Financial-FS | Financial-PS")
    vocab = ("\n".join(vocab_lines) + "\n\n") if vocab_lines else ""
    return _SUBSET_EXTRACTION_PROMPT.format(clause_list=clause_list, vocab=vocab)


async def extract_ppa_clauses(
    doc_id: str | None = None,
    gs_url: str | None = None,
    clauses: list[str] | None = None,
    max_other_clauses: int | None = None,
    tool_context: ToolContext = None,
) -> str:
    """Extract PPA clauses from a contract document with block_id citations.

    Use this tool when the user asks to "extract clauses", "summarise the
    contract terms", "show me the PPA structure", or similar requests on
    a specific document.

    Two input modes — pass EXACTLY ONE:
      - `doc_id`: an already-uploaded document in parsed_documents/{doc_id}
        (the path used after `list_documents` / `read_org_document`)
      - `gs_url`: a direct `gs://bucket/path/file.docx` URL. The tool runs
        AILANG Parse on the fly and never touches Firestore — useful when
        the agent discovers a PPA via `list_documents_in_bucket` and wants
        to analyse it without an explicit upload step. Requires the
        runtime SA to hold roles/storage.objectViewer on the bucket.

    Returns JSON for a `PpaClauses` object covering the 12 standard PPA
    clauses (counterparties, volume, term, settlement type, contract form,
    price formula, RtM provider, force majeure, change of law, termination,
    governing law) plus an `other_clauses` array for contract-specific items.

    Every populated clause carries a `block_id` citation pointing to the
    AILANG block in the source document. Empty clauses (null `value`) mean
    the clause was not located in the document, NOT that the contract lacks
    it — re-read with a different prompt or ask the user to point at the
    section.

    Pre-run config (7.2-M2 compare launcher): pass `clauses` to run a
    SCOPED extraction — only the requested clauses are asked for and
    returned; every other standard field comes back null. Use the same
    `clauses` / `max_other_clauses` values across related calls in a turn
    so the extraction cache hits (the cache key includes them).

    Args:
        doc_id: Firestore parsed_documents/{doc_id} of the contract.
        gs_url: gs://bucket/path GCS URL to parse on the fly.
        clauses: Optional subset of the 12 standard clause field names
            (counterparty_buyer, counterparty_seller, volume_mwh, term_years,
            settlement_type, contract_form, price_formula, rtm_provider,
            force_majeure, change_of_law, termination, governing_law).
            Invalid names return a structured error listing the valid
            vocabulary — fix the name and retry; nothing is silently ignored.
        max_other_clauses: Per-call override for the non-standard clause cap
            (default PPA_MAX_OTHER_CLAUSES, 20). 0 hides all other_clauses,
            negative disables the cap. `other_clauses_total` always reports
            the pre-cap count, so truncation stays visible.

    Returns:
        JSON string of a `PpaClauses` object. On error, a JSON string of
        `{"error": "...", "doc_id": ...}` (agent surfaces gracefully).
    """
    # Resolve identity for response payloads + caching keys. doc_id wins
    # for display; gs_url is the alternate when no Firestore record exists.
    if doc_id and gs_url:
        return json.dumps(
            {
                "error": "Pass exactly one of doc_id or gs_url, not both.",
                "doc_id": doc_id,
            }
        )
    if not doc_id and not gs_url:
        return json.dumps(
            {
                "error": "Either doc_id or gs_url is required.",
                "doc_id": None,
            }
        )

    # Reject bad clause subsets LOUDLY before touching the doc or the cache —
    # a silently-ignored typo would make a scoped run look complete when it isn't.
    subset_error = validate_clause_subset(clauses)
    if subset_error:
        return json.dumps({"error": subset_error, "doc_id": doc_id or gs_url})

    # Accept the `doc:{id}.json` artifact-reference form the model is shown, not
    # just the bare parsed_documents id. Normalize before the lookup + cache key.
    if doc_id is not None:
        doc_id = normalize_doc_id(doc_id)

    identity = doc_id or gs_url
    # Variant-aware cache key: a subset / per-call-cap run reads AND writes
    # its OWN entry, never the full-extraction one (see cache_variant_suffix).
    cache_key = clause_cache_key(identity, clauses, max_other_clauses) if identity else None
    blocks = None

    # Cache read: clause extraction is deterministic per (immutable) doc_id, so
    # if this identity was already extracted into app-scoped state, return it
    # WITHOUT re-running the LLM. We already WRITE this key below (~line 264) and
    # compare_ppa_contracts already READS it — this just gives extract the read
    # side of a cache it owns, so a follow-up turn doesn't re-pay the extraction
    # LLM. Same app-scoped trust model compare uses; keyed by doc_id, which is
    # what gates access upstream. Falls through on a parse failure so a stale
    # entry (e.g. a post-deploy schema bump) self-heals into a fresh extraction.
    if tool_context is not None and identity:
        cached = tool_context.state.get(cache_key)
        if cached:
            try:
                PpaClauses.model_validate_json(cached)  # guard: only serve valid
                log.info("extract_ppa_clauses: cache hit for %s (skipped extraction LLM)", identity)
                return cached
            except Exception:  # any parse failure → re-extract
                log.warning(
                    "extract_ppa_clauses: cached extraction for %s unparseable; re-extracting",
                    identity,
                )

    if doc_id is not None:
        try:
            _content, blocks = await asyncio.to_thread(build_document_context, doc_id, "blocks", None)
        except KeyError:
            return json.dumps(
                {
                    "error": (
                        f"Document '{doc_id}' not found in parsed_documents. Use "
                        "list_documents to see uploaded documents, or list_documents_in_bucket "
                        "to discover unparsed files in the tenant bucket."
                    ),
                    "doc_id": doc_id,
                }
            )
        except Exception as exc:
            log.warning("extract_ppa_clauses: build_document_context failed for %s: %s", doc_id, exc)
            return json.dumps(
                {
                    "error": f"Could not load document '{doc_id}': {exc}",
                    "doc_id": doc_id,
                }
            )
    else:
        # gs_url branch — parse on the fly via AILANG Parse. No Firestore write.
        try:
            outcome = await parse_gcs_file(gs_url, "blocks")
        except Exception as exc:
            log.warning("extract_ppa_clauses: parse_gcs_file raised for %s: %s", gs_url, exc)
            return json.dumps(
                {
                    "error": f"AILANG Parse failed for {gs_url}: {exc}",
                    "doc_id": gs_url,
                }
            )
        if outcome is None:
            return json.dumps(
                {
                    "error": (
                        f"AILANG Parse did not support {gs_url} (extension not in the "
                        "deterministic set). Convert to .docx/.pdf and re-try."
                    ),
                    "doc_id": gs_url,
                }
            )
        if outcome.error:
            return json.dumps(
                {
                    "error": f"AILANG Parse error on {gs_url}: {outcome.error}",
                    "doc_id": gs_url,
                    "error_code": outcome.error_code,
                }
            )
        blocks = outcome.blocks

    if not blocks:
        return json.dumps(
            {
                "error": (
                    f"Document '{identity}' has no parsed blocks. It may still be processing or "
                    "failed to parse. Ask the user to retry."
                ),
                "doc_id": identity,
            }
        )

    try:
        result_json = await _run_clause_extraction(blocks, identity, clauses)
    except Exception as exc:
        log.warning("extract_ppa_clauses: extraction call failed for %s: %s", identity, exc)
        return json.dumps(
            {
                "error": f"Clause extraction failed: {exc}",
                "doc_id": identity,
            }
        )

    # Round-trip validate so we never return malformed JSON to the agent.
    # On schema-violating output, we surface the raw text in `error` so the
    # agent can apologise and retry rather than emit half-formed A2UI.
    try:
        validated = PpaClauses.model_validate_json(result_json)
    except Exception as exc:
        log.warning("extract_ppa_clauses: schema validation failed for %s: %s", identity, exc)
        return json.dumps(
            {
                "error": f"Extracted JSON did not match PpaClauses schema: {exc}",
                "doc_id": identity,
            }
        )

    # Apply the transparent other_clauses cap AFTER validation so the counts
    # reflect what the model actually produced (not what it claimed). A per-call
    # max_other_clauses beats the env default; the module global is resolved at
    # call time (patchable in tests).
    effective_cap = _MAX_OTHER_CLAUSES if max_other_clauses is None else max_other_clauses
    _apply_other_clauses_cap(validated, identity, effective_cap)

    # Subset runs return ONLY the requested clauses — null out anything else
    # the model populated anyway, so the payload (and the cache entry written
    # from it) is exactly the scoped shape the caller asked for.
    if clauses is not None:
        for field in STANDARD_CLAUSE_FIELDS:
            if field not in clauses:
                setattr(validated, field, None)

    # If the tool was called via an ADK agent (tool_context present), stash
    # the typed result for compare_ppa_contracts (M3) to consume without
    # re-extracting. Keyed by whatever identity the agent used to call us
    # (doc_id or gs_url) — compare_ppa_contracts reads the SAME key.
    if tool_context is not None:
        tool_context.state[cache_key] = validated.model_dump_json()

    # Success log — .dev-logs shows the tool returned a valid extraction (the
    # frontend clause card renders from this). A blank Workspace with this line
    # present points the finger at the frontend, not the tool.
    _populated = sum(
        1
        for f, v in validated.model_dump().items()
        if f not in ("doc_id", "other_clauses", "other_clauses_total", "other_clauses_truncated") and v is not None
    )
    log.info("extract_ppa_clauses: OK %s — %d standard clauses populated", identity, _populated)
    return validated.model_dump_json()


def _apply_other_clauses_cap(clauses: PpaClauses, identity: str, cap: int = _MAX_OTHER_CLAUSES) -> PpaClauses:
    """Bound `other_clauses` to `cap`, recording the full count transparently.

    Mutates `clauses` in place and returns it. Always sets
    `other_clauses_total` to the pre-cap count so the agent and UI can render
    "showing N of M" — the tail is never dropped silently. A negative `cap`
    disables capping.
    """
    total = len(clauses.other_clauses)
    clauses.other_clauses_total = total
    if 0 <= cap < total:
        clauses.other_clauses = clauses.other_clauses[:cap]
        clauses.other_clauses_truncated = True
        log.info(
            "extract_ppa_clauses: capped other_clauses for %s at %d of %d (%d hidden)",
            identity,
            cap,
            total,
            total - cap,
        )
    else:
        clauses.other_clauses_truncated = False
    return clauses


async def _run_clause_extraction(blocks: list[dict], doc_id: str, clauses: list[str] | None = None) -> str:
    """Run Gemini structured-output extraction over AILANG blocks.

    Uses `response_mime_type=application/json` + `response_schema` to
    constrain Gemini's output to the PpaClauses shape. The doc_id is
    injected into the schema so the model populates it (saves a
    post-processing step).
    """
    # Inject doc_id into the prompt so the model can populate the field
    # (the schema marks it required).
    blocks_json = json.dumps({"docId": doc_id, "blocks": blocks}, ensure_ascii=False)
    prompt = _build_extraction_prompt(clauses) + blocks_json

    schema_dict = PpaClauses.model_json_schema()

    # Resilient: retry + Gemini region/model failover on a transient Vertex 429,
    # with a visible working-state + retry/fallback notices (2026-07-17).
    response = await generate_content_resilient(
        prompt=prompt,
        model_ref=_EXTRACTION_TIER,
        config={
            "response_mime_type": "application/json",
            "response_schema": schema_dict,
            # Large contracts (e.g. Google LEAP, 137pp) produce clause JSON well
            # past the model's default output cap, truncating mid-string →
            # "Invalid JSON: EOF while parsing" (live-hit 2026-07-14). Request the
            # flash tier's full 65_536-token output budget so extraction isn't cut off.
            "max_output_tokens": _EXTRACTION_MAX_OUTPUT_TOKENS,
        },
        progress_label="Extracting clauses…",
        label="clause-extraction",
    )
    # Surface truncation as a clear, actionable error rather than a raw pydantic
    # "EOF while parsing" downstream: MAX_TOKENS means the contract is too large
    # for a single-shot extraction even at the full budget.
    _raise_if_truncated(response, doc_id)
    return response.text or "{}"
