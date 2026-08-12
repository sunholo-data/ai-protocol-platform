"""map_ppa_obligations -- extraction output -> deontic engine wire JSON (7.6 M2).

ADK FunctionTool that takes one PPA document identity (doc_id | gs_url, same
duality as extract_ppa_clauses), reuses/runs the M1 clause extraction, then
maps clause text into the sunholo/deontic wire format (obligations / events /
policy) via a schema-enforced Gemini structured-output call.

Correctness architecture (this is the trust-critical step of the pipeline --
a wrong settlement number in a contract tool is a trust-ending event):

  * The LLM emits CALENDAR DATES ONLY (ISO YYYY-MM-DD). All integer
    day-offset arithmetic -- leap years, month lengths, the day-0 anchor --
    is deterministic Python (``day_offset``). LLM arithmetic on offsets is
    exactly the wrong-but-plausible failure mode this design eliminates.
  * Day 0 == the contract's effective date (design-doc date convention).
    ``effectiveDate`` is recorded in the payload so the UI renders calendar
    dates while the engine stays pure.
  * No effective date found -> LOUD structured error. Never day-0 guessing.
  * Any date predating the effective date -> LOUD error for the WHOLE call
    (the engine uses -1 as its not-delivered sentinel, so negative offsets
    are inexpressible; quietly dropping the event would silently shift the
    settlement).
  * No silent drops: every populated extraction clause must be accounted for
    in ``mapped_clauses`` or ``unmapped``. Anything the LLM forgets is
    auto-flagged into ``unmapped`` with an explicit reason.
  * Policy knobs are used only when the contract states them (source
    "extracted", with a verbatim excerpt requirement in the prompt);
    otherwise the engine baseline applies (source "default"). Per-knob
    provenance ships in the envelope.
  * The assembled payload must pass the strict PpaObligationPayload
    validators (schemas/ppa_obligations.py) before it is returned or cached;
    a schema violation surfaces as a structured error naming the constraint.

Result cached app-scoped like the sibling PPA tools, keyed by doc identity
(``app:emitted:ppa_obligations:{identity}``), validate-before-serve.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import threading
import time

from google.adk.tools import ToolContext
from pydantic import ValidationError

from adk.elicitation import next_elicit_seq, read_submitted_values
from tools.documents.ailang_parse import parse_gcs_file
from tools.documents.context import build_document_context
from tools.extract_ppa_clauses import extract_ppa_clauses, normalize_doc_id
from tools.resilient_genai import generate_content_resilient
from tools.schemas.ppa_clauses import PpaClauses
from tools.schemas.ppa_obligations import (
    COD_FLEX_LD_EUR_PER_MW_DAY,
    DEFAULT_POLICY,
    DELAY_LD_EUR_PER_MW_DAY,
    POLICY_KNOBS,
    REQUIRED_ASSUMPTION_FIELDS,
    AssumptionError,
    ObligationMapping,
    PpaObligationPayload,
    UnmappedClause,
    build_obligation_elicitation,
    business_days_to_calendar,
)

log = logging.getLogger(__name__)

# Model TIER for the mapping call -- resolved through the shared registry
# (config/models.yaml tier_defaults), never a hardcoded model id. Defaults to
# `pro` like compare_ppa_contracts: the clause->event mapping needs multi-step
# legal reasoning, and this is the correctness-critical call of the pipeline.
# Must resolve to a Gemini model (Vertex structured output is Gemini-only) --
# gemini_api_name_for() enforces that at call time.
_MAPPING_TIER = os.environ.get("PPA_OBLIGATION_TIER", "pro")


def obligation_cache_key(identity: str, effective_date: str | None = None) -> str:
    """App-scoped state key for a cached obligation mapping, keyed by the doc
    identity (doc_id or gs_url) -- the same trust model as the clause cache.

    VARIANT-KEYED (mirrors clause_cache_key's invariant): a run with a
    caller-provided effective_date anchor reads and writes its OWN entry, so
    it can never serve -- or poison -- the document-anchored one.
    """
    suffix = f":eff={effective_date}" if effective_date else ""
    return f"app:emitted:ppa_obligations:{identity}{suffix}"


# Cross-session result cache (the "2-min every test" fix). The extraction +
# mapping LLM calls are the slow part; ADK app-scoped (`app:`) state does NOT
# reliably persist across sessions with VertexAiSessionService, so re-running the
# SAME document re-paid the ~2-min cost on every fresh session/refresh.
#
# TWO tiers, checked module-first then Firestore:
#   1. `_RESULT_CACHE` (below) — in-process dict, ~0ms, but dies on cold-start /
#      redeploy / new instance. Fast path for back-to-back tests on one instance.
#   2. `_firestore_cache_*` — a Firestore doc per (identity, effective_date).
#      SURVIVES cold starts, redeploys and cross-instance fan-out, so a demo that
#      re-tests the same contract minutes (or a deploy) later is still instant.
#      Stays inside the Aitana GCP edge (same access model as sessions / the
#      clause cache) — a derived artefact of confidential PPAs is NEVER public.
# Both keyed by the SAME (identity, effective_date) key as the state cache; both
# cache BOTH a success payload AND a template refusal (both ran the LLM).
# `refresh=True` bypasses every tier and re-maps from scratch.
_RESULT_CACHE: dict[str, tuple[float, str]] = {}
_RESULT_CACHE_TTL = 6 * 3600  # 6h
_RESULT_CACHE_LOCK = threading.Lock()

# Firestore-backed durable tier. Collection holds one doc per cache key (doc id
# is a hash of the key, since the key contains gs:// slashes Firestore forbids).
_FS_CACHE_COLLECTION = "obligation_result_cache"


def _result_cache_get(key: str) -> str | None:
    with _RESULT_CACHE_LOCK:
        entry = _RESULT_CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del _RESULT_CACHE[key]
            return None
        return value


def _result_cache_set(key: str, value: str) -> None:
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE[key] = (time.time() + _RESULT_CACHE_TTL, value)


def _reset_result_cache() -> None:
    """Clear the cross-session result cache — for tests (autouse fixture) so a
    cached result never leaks across cases."""
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE.clear()


def _fs_cache_doc_id(key: str) -> str:
    """Firestore-safe doc id for a cache key (the key holds gs:// slashes)."""
    import hashlib

    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _firestore_cache_get(key: str) -> str | None:
    """Durable cache read. Any Firestore error → treat as a miss (never break
    the tool over a cache lookup). Honours the same 6h TTL as the module tier."""
    try:
        from db.firestore import get_document

        doc = get_document(_FS_CACHE_COLLECTION, _fs_cache_doc_id(key))
        if not doc:
            return None
        expires_at = doc.get("expires_at")
        value = doc.get("value")
        if not isinstance(value, str):
            return None
        if isinstance(expires_at, (int, float)) and time.time() > expires_at:
            return None
        return value
    except Exception as exc:  # cache best-effort, never fatal
        log.debug("map_ppa_obligations: firestore cache read failed for %s: %s", key, exc)
        return None


def _firestore_cache_set(key: str, value: str, identity: str) -> None:
    """Durable cache write. Best-effort — a Firestore failure must not fail the
    tool (the result is already computed and returned to the caller)."""
    try:
        from db.firestore import set_document

        set_document(
            _FS_CACHE_COLLECTION,
            _fs_cache_doc_id(key),
            {
                "value": value,
                "identity": identity,
                "key": key,
                "created_at": time.time(),
                "expires_at": time.time() + _RESULT_CACHE_TTL,
            },
        )
    except Exception as exc:  # cache best-effort, never fatal
        log.debug("map_ppa_obligations: firestore cache write failed for %s: %s", key, exc)


class MappingError(RuntimeError):
    """A mapping cannot be expressed correctly -- LOUD failure, never a guess.

    Carries the structured refusal context alongside the human-readable
    message, so the returned envelope (and the A2UI refusal panel built from
    it) never has to parse per-clause reasons back out of the prose:

      * ``unmapped``: the per-clause accounting behind a "no expressible
        obligations" refusal.
      * ``needs_effective_date``: True for the template-contract "no effective
        date" refusal -- the signal for the user-confirmation re-call loop.
    """

    def __init__(
        self,
        message: str,
        *,
        unmapped: list[UnmappedClause] | None = None,
        needs_effective_date: bool = False,
    ) -> None:
        super().__init__(message)
        self.unmapped = list(unmapped or [])
        self.needs_effective_date = needs_effective_date


def day_offset(iso_date: str, effective: _dt.date, what: str) -> int:
    """Deterministic calendar -> integer-day-offset conversion.

    Day 0 == the effective date. Leap years and month lengths come from
    ``datetime.date`` arithmetic, not the LLM.

    Raises:
        MappingError: on an unparseable date, or a date BEFORE the effective
            date -- the engine's -1 sentinel makes negative offsets
            inexpressible (types.ail aGet / engine.ail ``< 0`` checks), and
            silently dropping the item would silently change the settlement.
    """
    try:
        d = _dt.date.fromisoformat(str(iso_date).strip())
    except ValueError as exc:
        raise MappingError(f"{what}: {iso_date!r} is not a valid ISO date (YYYY-MM-DD): {exc}") from exc
    offset = (d - effective).days
    if offset < 0:
        raise MappingError(
            f"{what} on {d.isoformat()} predates the effective date {effective.isoformat()} -- the engine "
            "cannot express negative day offsets (day -1 is its not-delivered sentinel). Either the effective "
            "date was mis-extracted or the event does not belong to this contract's timeline."
        )
    return offset


_MAPPING_PROMPT = """You are a precise PPA (Power Purchase Agreement) contract analyst mapping
extracted contract clauses into a formal deontic obligation model.

The target engine models a contract as:
  - obligations: things one party must DELIVER by a deadline, each with an
    amount (price) the other party must then PAY within a payment window.
    Typical PPA obligations: COD (commercial operation date), periodic
    contracted-energy deliveries (Q1, Q2, ...), milestone completions.
  - events: dated occurrences the DOCUMENT ITSELF records or schedules:
      deliver       -- a delivery/completion actually recorded as having happened
      pay           -- a payment actually recorded as having happened
      amend_price   -- a price change WITH an explicit date and explicit new
                       amount stated in the contract (e.g. a fixed indexation
                       step table). ref = the obligation whose price changes.
      force_majeure -- an FM/curtailment WINDOW with explicit start and end
                       dates recorded in the document
      notice        -- a recorded breach notice. ref MUST be
                       "<obligationId>-delivery" or "<obligationId>-payment"
      waive         -- a recorded waiver of a breach. ref like notice.
      terminate     -- a recorded termination. ref = the terminating party.
    A contract that merely DEFINES remedies has NO events -- do NOT invent
    hypothetical events. An empty events list is normal and correct.
  - policy knobs (integers): penPerDay (delay damages per day late),
    penCap (damages cap per obligation), payWithin (payment due N days after
    delivery), cureDays (cure window after notice), ratePct + ratePeriod
    (late-payment interest: ratePct percent of price per FULL ratePeriod
    days late, simple, floor division).

STRICT RULES -- correctness over completeness:

1. DATES: emit ISO calendar dates (YYYY-MM-DD) ONLY. NEVER compute day
   numbers or offsets -- the caller does all date arithmetic. If the
   contract states a deadline relative to another date (e.g. "COD no later
   than 18 months after the Effective Date"), resolve it to a calendar date
   ONLY when both the anchor date and the offset are explicit; note the
   derivation in `note`. If you cannot resolve a date to a specific
   calendar day, the clause belongs in `unmapped`.
2. effective_date: the contract's effective/commencement date. Set null if
   the document does not determine one -- do NOT guess or substitute a
   signature year.
3. AMOUNTS: integers only, whole currency units, no decimals or thousands
   separators. Use amounts the contract states explicitly (or that follow
   from an explicitly stated quantity x unit price -- record the basis in
   `note`). NEVER invent a price. If a delivery obligation has an explicit
   calendar deadline but the contract attaches NO fixed amount to it (e.g.
   a COD milestone in a market-priced PPA), you MAY emit it with price 0
   and a `note` saying "no fixed price in contract" -- price 0 means no
   payment leg, while the deadline still drives delay analysis. Do NOT do
   this when the deadline itself is unresolved. If neither a fixed amount
   nor price-0 treatment fits, put the clause in `unmapped`.
4. POLICY KNOBS: set a knob's `value` ONLY when the contract states it in a
   form directly expressible in the engine's semantics, and copy the
   verbatim contract text into `excerpt`. Otherwise leave `value` null --
   the caller applies reviewed defaults and records their provenance. If
   the contract has a remedy the engine's knob CANNOT faithfully express
   (e.g. percentage-based damages, compounding interest), leave the knob
   null AND add an `unmapped` entry explaining the mismatch.
5. COVERAGE -- NOTHING SILENTLY DROPPED: every clause listed in the
   extraction input must appear either in `mapped_clauses` (its content is
   reflected in obligations/events/policy) or in `unmapped` with a concrete
   reason (e.g. "governing_law: no deontic semantics", "price_formula:
   floating market-indexed price, no fixed amount to attach"). Clauses like
   counterparties, governing law, or settlement type usually belong in
   `unmapped` -- that is expected and correct.
6. IDs: short uppercase ids (COD, Q1, DEL2026). Every event `ref` must
   reference an obligation you emitted (or its "-delivery"/"-payment"
   breach id).
7. Cite the AILANG `block_id` of the source span on every obligation and
   event you emit.

The extraction below is the 12-clause structured summary; the document
blocks are the full parsed contract. Ground every value in the blocks.

Extracted clauses (JSON):
{extraction}

Document blocks (JSON):
{blocks}
"""


async def map_ppa_obligations(
    doc_id: str | None = None,
    gs_url: str | None = None,
    effective_date: str | None = None,
    assumptions: str | None = None,
    refresh: bool = False,
    tool_context: ToolContext = None,
) -> str:
    """Map a PPA contract into the verified obligation engine's wire format.

    Use this tool when the user asks to "analyze obligations", "who owes
    what", "what happens if a deadline slips", "compute the settlement", or
    wants the Obligation Analysis artefact for a specific contract. It runs
    (or reuses) the clause extraction for the document, then produces the
    deontic engine payload: obligations (id / deadline / price), timeline
    events, and policy knobs, all as integer day offsets anchored on the
    contract's effective date (day 0).

    Two input modes -- pass EXACTLY ONE:
      - `doc_id`: an already-parsed document in parsed_documents/{doc_id}
      - `gs_url`: a direct `gs://bucket/path/file.pdf` URL (AILANG-parsed
        on the fly, no upload step)

    Returns JSON for a `PpaObligationPayload` envelope:
      - `effectiveDate` (ISO date): the day-0 anchor -- the UI renders
        calendar dates from it; the engine consumes only integer days.
      - `obligations` / `events` / `policy`: the engine-facing wire subset.
      - `policy_sources`: per-knob provenance -- "extracted" (stated in the
        contract, used verbatim) or "default" (engine baseline applied).
      - `unmapped`: every extracted clause the mapping could NOT express,
        each with a reason. ALWAYS surface this list to the user -- it is
        the honest boundary of the model, and hiding it would overstate
        what the settlement covers.
      - `mapped_clauses`: extraction clause names the mapping did cover.

    On failure returns `{"error": "...", "doc_id": ...}` -- including when
    no effective date can be established (the tool refuses to guess a
    timeline anchor) or when the mapping violates the engine's wire
    constraints. Relay such errors verbatim; do not retry silently. A
    mapping REFUSAL additionally carries structured context alongside the
    message: `unmapped` (the per-clause accounting behind a "no expressible
    obligations" refusal -- surface these reasons to the user) and
    `needs_effective_date` (true for the template-contract case below).

    Template-form PPAs (blank "[dd MM yyyy]" start dates, `[●]` prices) cannot
    anchor OR value a timeline from the document alone. Rather than dead-end,
    the tool then returns a STRUCTURED ELICITATION envelope: `error` +
    `needs_assumptions: true` + `elicitation` (a typed field set — effective
    date, Guaranteed COD date, Contract Capacity, price, and policy knobs, each
    with the source clause + the formula it feeds). The app renders it as an
    A2UI form in chat; when the user fills it and re-triggers
    `start_obligation_analysis`, this tool COMPLETES the analysis from those
    assumptions (`build_payload_from_assumptions`): it resolves the formulas the
    mapper parsed (delay-LD `penPerDay = 150 x Contract Capacity`), builds a
    valid payload, and marks every supplied value as an ASSUMPTION
    (`effective_date_source: "provided"`, policy/price provenance `"reviewed"`)
    — never a contract fact.

    Args:
        doc_id: Firestore parsed_documents/{doc_id} of the contract.
        gs_url: gs://bucket/path GCS URL to parse on the fly.
        effective_date: Optional ISO date (YYYY-MM-DD) confirmed by the
            user, used as the day-0 anchor. Takes precedence over any date
            found in the document (it is the explicit user-confirmation
            path). Never invent this value -- only pass what the user gave.
        assumptions: Optional JSON object of the elicitation form's field
            values (`{effective_date, cod_date, contract_capacity_mw,
            contract_price, pen_cap, pay_within_days, cure_days, rate_pct,
            rate_period_days}`). Triggers the build-from-assumptions completion
            path. In the app the values also arrive authoritatively via the
            surface data model (no LLM transcription of the trust-critical
            numbers); this arg is the explicit CLI/test channel.

    Returns:
        JSON string of a `PpaObligationPayload`, or a structured error /
        elicitation envelope.
    """
    if doc_id and gs_url:
        return json.dumps({"error": "Pass exactly one of doc_id or gs_url, not both.", "doc_id": doc_id})
    if not doc_id and not gs_url:
        return json.dumps({"error": "Either doc_id or gs_url is required.", "doc_id": None})

    if doc_id is not None:
        doc_id = normalize_doc_id(doc_id)
    identity = doc_id or gs_url

    # A caller-provided anchor must parse BEFORE any LLM spend -- and before
    # the cache read, since the key is variant-keyed on it.
    if effective_date is not None:
        try:
            _dt.date.fromisoformat(effective_date.strip())
        except ValueError as exc:
            return json.dumps(
                {
                    "error": f"effective_date {effective_date!r} is not a valid ISO date (YYYY-MM-DD): {exc}",
                    "doc_id": identity,
                }
            )
        effective_date = effective_date.strip()

    # Build-from-assumptions path (7.8 M1 — the DEMO UNBLOCK). If the user has
    # supplied the elicitation form's values (authoritatively via the surface
    # data model in state, or explicitly via the `assumptions` arg), COMPLETE
    # the analysis deterministically from those assumptions — no extraction, no
    # mapping LLM, no LLM arithmetic on the trust-critical numbers. Runs BEFORE
    # the cache/extraction so a template refusal never blocks completion.
    try:
        resolved_assumptions = _resolve_assumptions(assumptions, tool_context)
    except AssumptionError as exc:
        return json.dumps(
            {
                "error": f"Could not read the supplied values: {exc}",
                "doc_id": identity,
                "elicitation": build_obligation_elicitation(identity, reason=str(exc)).model_dump(),
                "needs_assumptions": True,
            }
        )
    if resolved_assumptions is not None:
        try:
            payload = build_payload_from_assumptions(identity, resolved_assumptions)
        except (AssumptionError, MappingError) as exc:
            # LOUD + never-silent: re-surface the elicitation form so the user
            # can fix the offending value (e.g. a COD date before the start
            # date), rather than a dead-end prose error.
            log.info("map_ppa_obligations: build-from-assumptions rejected for %s: %s", identity, exc)
            return json.dumps(
                {
                    "error": f"Could not complete the analysis from the supplied values: {exc}",
                    "doc_id": identity,
                    "elicitation": build_obligation_elicitation(identity, reason=str(exc)).model_dump(),
                    "needs_assumptions": True,
                    # Append-only: a fresh sequence → a NEW form below the frozen
                    # one the user just submitted (never replaces it).
                    "elicit_seq": _next_elicit_seq(tool_context),
                }
            )
        except ValidationError as exc:
            return json.dumps(
                {
                    "error": (
                        "Assumption-built payload violates the engine wire schema (rejecting rather than "
                        f"sending a malformed scenario to the engine): {exc}"
                    ),
                    "doc_id": identity,
                }
            )
        return payload.model_dump_json()

    cache_key = obligation_cache_key(identity, effective_date)

    # Cross-session module cache (fast re-tests) — checked FIRST since the state
    # cache below doesn't survive a new session. Serves a success payload OR a
    # template refusal (both paid the ~2-min LLM cost). `refresh=True` bypasses
    # it to force a fresh extraction + mapping.
    if not refresh:
        cached_result = _result_cache_get(cache_key)
        if cached_result is not None:
            log.info("map_ppa_obligations: RESULT cache hit for %s (skipped extraction + mapping LLM)", identity)
            return _restamp_elicit_seq(cached_result, tool_context)
        # Durable tier — survives cold starts / redeploys / other instances, so a
        # re-test minutes (or a deploy) later is still instant. Off-thread so the
        # blocking Firestore read doesn't stall the event loop.
        fs_cached = await asyncio.to_thread(_firestore_cache_get, cache_key)
        if fs_cached is not None:
            log.info("map_ppa_obligations: FIRESTORE cache hit for %s (skipped extraction + mapping LLM)", identity)
            _result_cache_set(cache_key, fs_cached)  # warm the fast in-process tier
            return _restamp_elicit_seq(fs_cached, tool_context)

    # Cache read: the mapping is deterministic-enough per (immutable) doc to
    # reuse across turns -- same app-scoped trust model as the clause cache.
    # Validate before serving; fall through on any parse failure so a stale
    # entry (post-deploy schema bump) self-heals into a fresh mapping.
    if tool_context is not None and not refresh:
        cached = tool_context.state.get(cache_key)
        if cached:
            try:
                PpaObligationPayload.model_validate_json(cached)
                log.info("map_ppa_obligations: cache hit for %s (skipped extraction + mapping LLM)", identity)
                return cached
            except Exception:
                log.warning("map_ppa_obligations: cached mapping for %s unparseable; re-mapping", identity)

    # 1. Extraction (reuses the M1 extraction cache inside extract_ppa_clauses).
    extraction_raw = await _resolve_extraction(doc_id=doc_id, gs_url=gs_url, tool_context=tool_context)
    try:
        extraction_parsed = json.loads(extraction_raw)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Extraction returned unparseable JSON: {exc}", "doc_id": identity})
    if "error" in extraction_parsed:
        return json.dumps({"error": f"Clause extraction failed: {extraction_parsed['error']}", "doc_id": identity})
    try:
        extraction = PpaClauses.model_validate(extraction_parsed)
    except ValidationError as exc:
        return json.dumps({"error": f"Extraction did not match PpaClauses schema: {exc}", "doc_id": identity})

    # 2. Document blocks -- dates and amounts live in the full text, not only
    # in the 12-clause summary, so the mapping call gets both.
    try:
        blocks = await _load_blocks(doc_id=doc_id, gs_url=gs_url)
    except Exception as exc:
        log.warning("map_ppa_obligations: block load failed for %s: %s", identity, exc)
        return json.dumps({"error": f"Could not load document blocks: {exc}", "doc_id": identity})
    if not blocks:
        return json.dumps(
            {"error": f"Document '{identity}' has no parsed blocks; cannot map obligations.", "doc_id": identity}
        )

    # 3. Mapping LLM call (calendar form, schema-enforced).
    try:
        mapping_json = await _run_obligation_mapping(extraction, blocks, identity)
    except Exception as exc:
        log.warning("map_ppa_obligations: mapping call failed for %s: %s", identity, exc)
        return json.dumps({"error": f"Obligation mapping failed: {exc}", "doc_id": identity})
    try:
        mapping = ObligationMapping.model_validate_json(mapping_json)
    except ValidationError as exc:
        log.warning("map_ppa_obligations: mapping schema validation failed for %s: %s", identity, exc)
        return json.dumps({"error": f"Mapping JSON did not match ObligationMapping schema: {exc}", "doc_id": identity})

    # 4. Deterministic assembly: calendar -> offsets, policy defaults +
    # provenance, coverage enforcement, strict wire validation. Every failure
    # mode here is a LOUD structured error, never a silent guess.
    try:
        payload = _assemble_payload(identity, extraction, mapping, provided_effective_date=effective_date)
    except MappingError as exc:
        log.warning(
            "map_ppa_obligations: mapping not expressible for %s: %s%s",
            identity,
            exc,
            (" (unmapped: " + "; ".join(f"{u.clause}: {u.reason}" for u in exc.unmapped) + ")") if exc.unmapped else "",
        )
        refusal = {
            "error": str(exc),
            "doc_id": identity,
            "unmapped": [u.model_dump() for u in exc.unmapped],
            "needs_effective_date": exc.needs_effective_date,
            # A template-contract refusal is NOT a dead-end: return the
            # structured elicitation so the app renders an A2UI form the user
            # can complete (7.8 M1). The workbench refusal panel still shows
            # the per-clause "why"; the chat form is the "how to proceed".
            "needs_assumptions": True,
            "elicitation": build_obligation_elicitation(identity, reason=str(exc)).model_dump(),
            # Append-only: unique sequence so this form appends in chat.
            "elicit_seq": _next_elicit_seq(tool_context),
        }
        result_str = json.dumps(refusal)
        # Cache the SLOW template path too (templates are the common demo case);
        # a cache hit re-stamps a fresh elicit_seq so append-only still holds.
        _result_cache_set(cache_key, result_str)
        await asyncio.to_thread(_firestore_cache_set, cache_key, result_str, identity)
        return result_str
    except ValidationError as exc:
        log.warning("map_ppa_obligations: assembled payload failed wire validation for %s: %s", identity, exc)
        return json.dumps(
            {
                "error": (
                    "Mapped payload violates the engine wire schema (rejecting rather than sending a "
                    f"malformed scenario to the engine): {exc}"
                ),
                "doc_id": identity,
            }
        )

    result = payload.model_dump_json()
    _result_cache_set(cache_key, result)
    await asyncio.to_thread(_firestore_cache_set, cache_key, result, identity)
    if tool_context is not None:
        tool_context.state[cache_key] = result

    log.info(
        "map_ppa_obligations: OK %s -- %d obligations, %d events, %d unmapped, policy sources %s",
        identity,
        len(payload.obligations),
        len(payload.events),
        len(payload.unmapped),
        dict(sorted(payload.policy_sources.items())),
    )
    return result


# --- build-from-assumptions ingress ------------------------------------------


def _read_assumptions_from_state(tool_context: ToolContext | None) -> dict | None:
    """Read the elicitation form's data model straight from session state — the
    AUTHORITATIVE, no-LLM-transcription source for the trust-critical numbers.

    Delegates to the shared elicitation primitive (8.1), anchoring on the
    required assumption fields so a bare launcher / unrelated surface is never
    mistaken for the form. Same semantics as the pre-8.1 local implementation —
    ``_is_blank`` is byte-identical across both modules."""
    return read_submitted_values(tool_context, expected_fields=list(REQUIRED_ASSUMPTION_FIELDS))


def _next_elicit_seq(tool_context: ToolContext | None) -> int:
    """Monotonic per-session counter so each elicitation emission gets a UNIQUE
    surface (``obligation_elicitation:{doc}:{seq}``) — making the chat forms
    APPEND (a re-refusal adds a NEW form below the last; prior submissions stay
    frozen) rather than replacing the previous. Delegates to the shared primitive
    (8.1), keeping the obligation-specific state key so append behaviour is
    unchanged."""
    return next_elicit_seq(tool_context, key="_oblig_elicit_seq")


def _restamp_elicit_seq(result_str: str, tool_context: ToolContext | None) -> str:
    """Re-serve a CACHED result: a template REFUSAL gets a FRESH ``elicit_seq``
    so its chat form still appends (append-only unique surface), instead of
    reusing the surface id it had when first computed. Success payloads (no
    ``needs_assumptions``) pass through unchanged."""
    try:
        data = json.loads(result_str)
    except (TypeError, json.JSONDecodeError):
        return result_str
    if isinstance(data, dict) and data.get("needs_assumptions"):
        data["elicit_seq"] = _next_elicit_seq(tool_context)
        return json.dumps(data)
    return result_str


def _resolve_assumptions(assumptions_arg: str | dict | None, tool_context: ToolContext | None) -> dict | None:
    """Resolve the assumptions to build from: the authoritative form data model
    in state (preferred — no LLM transcription), else the explicit
    ``assumptions`` arg (CLI/test/LLM channel). Returns None when neither is
    present. Raises ``AssumptionError`` on an unparseable arg."""
    from_state = _read_assumptions_from_state(tool_context)
    if from_state is not None:
        return from_state
    if _is_blank(assumptions_arg):
        return None
    if isinstance(assumptions_arg, dict):
        return assumptions_arg
    try:
        parsed = json.loads(assumptions_arg)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssumptionError(f"assumptions must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AssumptionError("assumptions must be a JSON object of {field_name: value}")
    return parsed


# --- internals ---------------------------------------------------------------


async def _resolve_extraction(*, doc_id: str | None, gs_url: str | None, tool_context: ToolContext | None) -> str:
    """Run/reuse the clause extraction. extract_ppa_clauses owns its own
    app-scoped cache (variant-keyed; we always want the FULL extraction, so no
    clauses subset is passed -- a subset entry can never be served here)."""
    return await extract_ppa_clauses(doc_id=doc_id, gs_url=gs_url, tool_context=tool_context)


async def _load_blocks(*, doc_id: str | None, gs_url: str | None) -> list[dict] | None:
    """Load the parsed AILANG blocks for either identity mode (same duality
    as extract_ppa_clauses)."""
    if doc_id is not None:
        _content, blocks = await asyncio.to_thread(build_document_context, doc_id, "blocks", None)
        return blocks
    outcome = await parse_gcs_file(gs_url, "blocks")
    if outcome is None:
        raise RuntimeError(f"AILANG Parse does not support {gs_url} (extension not in the deterministic set)")
    if outcome.error:
        raise RuntimeError(f"AILANG Parse error on {gs_url}: {outcome.error}")
    return outcome.blocks


async def _run_obligation_mapping(extraction: PpaClauses, blocks: list[dict], identity: str) -> str:
    """Run the Gemini structured-output mapping call (calendar form)."""
    prompt = _MAPPING_PROMPT.format(
        extraction=extraction.model_dump_json(),
        blocks=json.dumps({"docId": identity, "blocks": blocks}, ensure_ascii=False),
    )
    # Resilient: retry + Gemini region/model failover on a transient Vertex 429,
    # with a visible working-state + retry/fallback notices (the raw call used to
    # hang then dead-end on a 429 — 2026-07-17).
    response = await generate_content_resilient(
        prompt=prompt,
        model_ref=_MAPPING_TIER,
        config={
            "response_mime_type": "application/json",
            "response_schema": ObligationMapping.model_json_schema(),
        },
        progress_label="Mapping obligations…",
        label="obligation-mapping",
    )
    return response.text or "{}"


def _populated_clause_names(extraction: PpaClauses) -> list[str]:
    """Names of every clause the extraction actually populated -- the set the
    mapping must account for (standard fields with a value + other_clauses)."""
    from tools.extract_ppa_clauses import STANDARD_CLAUSE_FIELDS

    names = [
        name
        for name in STANDARD_CLAUSE_FIELDS
        if getattr(extraction, name) is not None and getattr(extraction, name).value is not None
    ]
    names.extend(c.clause_name for c in extraction.other_clauses)
    return names


def _assemble_payload(
    identity: str,
    extraction: PpaClauses,
    mapping: ObligationMapping,
    provided_effective_date: str | None = None,
) -> PpaObligationPayload:
    """Deterministic assembly of the wire payload from the calendar-form mapping.

    Raises MappingError (inexpressible) or pydantic.ValidationError (wire
    violation) -- both surfaced as structured errors by the caller.
    """
    # Effective date: the one non-negotiable anchor. A caller-provided date
    # (the explicit user-confirmation path) takes precedence and is recorded
    # as such; otherwise the document must determine one. No date -> no
    # payload -- NEVER a day-0 guess.
    if provided_effective_date:
        effective = _dt.date.fromisoformat(provided_effective_date)  # pre-validated by the tool entry
        effective_source = "provided"
    else:
        if not mapping.effective_date or not str(mapping.effective_date).strip():
            raise MappingError(
                "No effective date could be established for this contract -- refusing to anchor day offsets on "
                "a guess (template-form PPAs often leave the start date blank). Ask the user to confirm the "
                "contract's effective/start date, then re-call this tool with effective_date=YYYY-MM-DD.",
                needs_effective_date=True,
            )
        try:
            effective = _dt.date.fromisoformat(str(mapping.effective_date).strip())
        except ValueError as exc:
            raise MappingError(
                f"Extracted effective date {mapping.effective_date!r} is not a valid ISO date (YYYY-MM-DD): {exc}"
            ) from exc
        effective_source = "extracted"

    if not mapping.obligations:
        # Per-clause reasons ride STRUCTURED in the error envelope's `unmapped`
        # list (never re-parsed out of this prose); the message stays loud.
        detail = (
            f"{len(mapping.unmapped)} clause(s) could not be expressed -- per-clause reasons are in this "
            "result's `unmapped` list."
            if mapping.unmapped
            else "The mapper reported no unmapped accounting either."
        )
        raise MappingError(
            "The mapper found no obligations expressible in the engine's model -- refusing to emit an empty "
            f"scenario. {detail}",
            unmapped=list(mapping.unmapped),
        )

    obligations = [
        {
            "id": o.id,
            "deadline": day_offset(o.deadline_date, effective, f"obligation {o.id!r} deadline"),
            "price": o.price,
        }
        for o in mapping.obligations
    ]

    events: list[dict] = []
    for e in mapping.events:
        day = day_offset(e.date, effective, f"{e.kind} event")
        wire: dict = {"kind": e.kind, "day": day, "ref": e.ref, "amt": 0, "hi": 0}
        if e.kind == "amend_price":
            wire["amt"] = e.amt
        elif e.kind == "force_majeure":
            if not e.window_end_date:
                raise MappingError(
                    f"force_majeure event on {e.date} has no window_end_date -- the engine needs an explicit "
                    "inclusive window [start, end]."
                )
            wire["ref"] = ""
            wire["amt"] = day  # wire convention: event day IS the window start
            wire["hi"] = day_offset(e.window_end_date, effective, f"force_majeure window end ({e.date})")
        events.append(wire)
    # The engine does not sort; sort by day, STABLY, so same-day events keep
    # the LLM's relative order (notice arrival order grounds termination).
    events.sort(key=lambda w: w["day"])

    # Policy: extracted knobs win (provenance "extracted"); everything else is
    # the reviewed engine baseline (provenance "default").
    knob_values: dict[str, int] = {}
    policy_sources: dict[str, str] = {}
    for knob in POLICY_KNOBS:
        mapped_knob = getattr(mapping, knob)
        if mapped_knob.value is not None:
            knob_values[knob] = mapped_knob.value
            policy_sources[knob] = "extracted"
        else:
            knob_values[knob] = getattr(DEFAULT_POLICY, knob)
            policy_sources[knob] = "default"

    # Coverage enforcement -- no silent drops. Any populated extraction clause
    # the LLM neither mapped nor listed as unmapped is auto-flagged so the
    # artefact ALWAYS shows the honest boundary of the model.
    accounted = set(mapping.mapped_clauses) | {u.clause for u in mapping.unmapped}
    unmapped = list(mapping.unmapped)
    for name in _populated_clause_names(extraction):
        if name not in accounted:
            log.warning("map_ppa_obligations: clause %r unaccounted for by the mapper -- auto-flagged", name)
            unmapped.append(
                UnmappedClause(
                    clause=name,
                    reason=(
                        "Not accounted for by the mapper (auto-flagged by the coverage check) -- treat this "
                        "clause as NOT reflected in the settlement model."
                    ),
                )
            )

    return PpaObligationPayload(
        doc_id=identity,
        effectiveDate=effective,
        effective_date_source=effective_source,
        obligations=obligations,
        events=events,
        policy=knob_values,
        policy_sources=policy_sources,
        unmapped=unmapped,
        mapped_clauses=list(mapping.mapped_clauses),
    )


# --- build-from-assumptions (7.8 M1 — the DEMO UNBLOCK) ----------------------


def _is_blank(value: object) -> bool:
    """A missing/empty assumption value (None or an all-whitespace string)."""
    return value is None or (isinstance(value, str) and not value.strip())


def _assumption_int(assumptions: dict, key: str, *, required: bool, minimum: int, default: int | None) -> int | None:
    """Parse one integer assumption LOUDLY.

    Values arrive from an A2UI TextField (strings) or a JSON number. Booleans,
    floats-with-fraction, and non-numeric strings are rejected — a mistyped
    amount must never be silently coerced into a wrong settlement number.
    """
    raw = assumptions.get(key)
    if _is_blank(raw):
        if required:
            raise AssumptionError(f"assumption {key!r} is required and was not supplied")
        return default
    if isinstance(raw, bool):
        raise AssumptionError(f"assumption {key!r} must be a whole number, got a boolean {raw!r}")
    text = str(raw).strip().replace(",", "")
    try:
        # Reject fractional numbers explicitly (int('3.5') raises; float→int would
        # silently truncate a wrong amount). Engine knobs/prices are integers.
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise AssumptionError(
            f"assumption {key!r} must be a whole number (no decimals/separators), got {raw!r}"
        ) from exc
    if value < minimum:
        raise AssumptionError(f"assumption {key!r} must be >= {minimum}, got {value}")
    return value


def _assumption_date(assumptions: dict, key: str) -> _dt.date:
    """Parse one required ISO date assumption LOUDLY (YYYY-MM-DD)."""
    raw = assumptions.get(key)
    if _is_blank(raw):
        raise AssumptionError(f"assumption {key!r} is required and was not supplied")
    try:
        return _dt.date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise AssumptionError(f"assumption {key!r} is not a valid ISO date (YYYY-MM-DD): {raw!r}") from exc


def build_payload_from_assumptions(identity: str, assumptions: dict) -> PpaObligationPayload:
    """Construct a valid, engine-ready ``PpaObligationPayload`` from the user's
    supplied assumptions — the completion path for a template contract whose
    values are all ``[●]`` placeholders (7.8 M1).

    Correctness discipline (this is the trust-critical step):
      * ALL formula resolution is deterministic Python — ``penPerDay =
        150 x capacity`` (the delay-LD the mapper already parsed). LLM
        arithmetic on these numbers is exactly the wrong-but-plausible failure
        mode this design eliminates.
      * The COD-flexibility LD (``200 x capacity``, EUR/MW/**day**) is computed
        and DISCLOSED but written to NO engine knob — a daily rate is not a cap,
        and conflating them would silently change the settlement.
      * Every value is an ASSUMPTION: ``effective_date_source="provided"``,
        policy + price provenance ``"reviewed"``. NOTHING is ``"extracted"`` —
        the build cannot present a user's guess as a contract fact.
      * Malformed / insufficient input raises ``AssumptionError`` (LOUD);
        a COD date before the effective date raises ``MappingError`` (the
        engine cannot express negative offsets). Never a silent guess.

    Args:
        identity: the doc identity (doc_id or gs_url) for the payload.
        assumptions: ``{field_name: value}`` from the elicitation form's data
            model (see schemas.ppa_obligations elicitation field set).

    Returns:
        A validated ``PpaObligationPayload`` the WASM/CLI engine accepts.
    """
    if not isinstance(assumptions, dict):
        raise AssumptionError("assumptions must be a JSON object of {field_name: value}")

    missing = [name for name in REQUIRED_ASSUMPTION_FIELDS if _is_blank(assumptions.get(name))]
    if missing:
        raise AssumptionError(
            "cannot complete an obligation analysis — missing required assumption(s): " + ", ".join(sorted(missing))
        )

    effective = _assumption_date(assumptions, "effective_date")
    cod_date = _assumption_date(assumptions, "cod_date")
    capacity = _assumption_int(assumptions, "contract_capacity_mw", required=True, minimum=1, default=None)
    price = _assumption_int(assumptions, "contract_price", required=True, minimum=0, default=None)

    pen_cap = _assumption_int(assumptions, "pen_cap", required=False, minimum=0, default=DEFAULT_POLICY.penCap)
    pay_within = _assumption_int(
        assumptions,
        "pay_within_days",
        required=False,
        minimum=0,
        default=business_days_to_calendar(DEFAULT_POLICY.payWithin),
    )
    cure_days = _assumption_int(assumptions, "cure_days", required=False, minimum=0, default=DEFAULT_POLICY.cureDays)
    rate_pct = _assumption_int(assumptions, "rate_pct", required=False, minimum=0, default=DEFAULT_POLICY.ratePct)
    rate_period = _assumption_int(
        assumptions, "rate_period_days", required=False, minimum=1, default=DEFAULT_POLICY.ratePeriod
    )

    # Formula resolution — deterministic Python. The delay-LD (150 EUR/MW/day)
    # maps EXACTLY onto the engine's per-day delay-damages knob.
    pen_per_day = DELAY_LD_EUR_PER_MW_DAY * capacity
    # The COD-flexibility LD (200 EUR/MW/day) is DISCLOSED, never written to a
    # knob (a daily rate is not a cap). Surfaced in the form help + logged.
    cod_flex_ld_per_day = COD_FLEX_LD_EUR_PER_MW_DAY * capacity

    # COD obligation deadline as an integer day offset (day 0 == effective).
    # day_offset raises MappingError if the COD date predates the effective date
    # (the engine's -1 sentinel makes negative offsets inexpressible).
    cod_deadline = day_offset(cod_date.isoformat(), effective, "COD obligation deadline")

    knob_values = {
        "penPerDay": pen_per_day,
        "penCap": pen_cap,
        "payWithin": pay_within,
        "cureDays": cure_days,
        "ratePct": rate_pct,
        "ratePeriod": rate_period,
    }
    # Every knob is an ASSUMPTION the user reviewed on the form (or a derived
    # value from a supplied one) — "reviewed", NEVER "extracted". This is the
    # EARNED-TRUST invariant: no assumption masquerades as a contract fact.
    policy_sources = dict.fromkeys(POLICY_KNOBS, "reviewed")

    log.info(
        "map_ppa_obligations: build-from-assumptions %s -- capacity=%dMW -> penPerDay=%d (delay-LD 150x); "
        "cod-flex LD %d/day (200x, disclosed, not a knob); penCap=%d; COD deadline=day %d; price=%d",
        identity,
        capacity,
        pen_per_day,
        cod_flex_ld_per_day,
        pen_cap,
        cod_deadline,
        price,
    )

    return PpaObligationPayload(
        doc_id=identity,
        effectiveDate=effective,
        effective_date_source="provided",
        obligations=[{"id": "COD", "deadline": cod_deadline, "price": price}],
        events=[],
        policy=knob_values,
        policy_sources=policy_sources,
        price_sources={"COD": "reviewed"},
        unmapped=[],
        mapped_clauses=[],
    )
