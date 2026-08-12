"""Tests for tools/map_ppa_obligations.py + schemas/ppa_obligations.py (7.6 M2).

Correctness-critical milestone: wrong-but-plausible day offsets or event
mappings pass shallow tests, and a wrong settlement number in a contract
tool is a trust-ending event. Coverage:

  - Wire-schema round-trip + engine_payload() subset separation
  - LOUD rejection of every malformed-event class (unknown kind, negative
    day, bad notice ref, force_majeure hi < amt, misplaced amt/hi, unknown
    obligation refs, duplicate ids, unsorted events, bad policy bounds)
  - Calendar -> day-offset conversion incl. edge cases (effective date is
    day 0, leap year, month lengths, event before effective date -> LOUD
    error -- pinned: the engine uses -1 as its not-delivered sentinel, so
    negative offsets are inexpressible)
  - Missing effective date -> loud error, never day-0 guessing
  - No-silent-drop: clauses the mapper doesn't account for are auto-flagged
    into `unmapped` (adversarial fixture)
  - Policy-knob source recording (extracted vs default)
  - Cache behaviour (validate-before-serve, self-heal on stale entries)
  - Engine validation: fixtures + the M1 payload run through the real
    v0.29.0 ailang CLI (skips cleanly when the CLI is absent)
  - Corpus fixtures (live-derived, CONFIDENTIAL -- excluded from the public
    template) validate against the wire schema with coverage recorded
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from tools.schemas.ppa_obligations import (
    COD_FLEX_LD_EUR_PER_MW_DAY,
    DEFAULT_POLICY,
    DELAY_LD_EUR_PER_MW_DAY,
    POLICY_KNOBS,
    REQUIRED_ASSUMPTION_FIELDS,
    AssumptionError,
    ElicitationEnvelope,
    ObligationMapping,
    PpaObligationPayload,
    WireEvent,
    WireObligation,
    WirePolicy,
    build_obligation_elicitation,
    business_days_to_calendar,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ppa_obligations"
M1_PAYLOAD_PATH = (
    Path(__file__).parents[3]
    / "infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1/assets/payload.demosolar.json"
)


@pytest.fixture(autouse=True)
def _clear_result_cache(monkeypatch):
    """The cross-session module result cache is global — clear it before each
    test so a cached refusal/payload never leaks across cases.

    Also neutralise the DURABLE (Firestore) cache tier by default: these are
    unit tests of the mapping + module-cache logic, and must not depend on
    ambient GCP creds or leak state across cases via a real Firestore. The one
    test that exercises the durable tier re-patches these with a fake store."""
    from tools.map_ppa_obligations import _reset_result_cache

    monkeypatch.setattr("tools.map_ppa_obligations._firestore_cache_get", lambda key: None)
    monkeypatch.setattr("tools.map_ppa_obligations._firestore_cache_set", lambda key, value, identity: None)
    _reset_result_cache()
    yield
    _reset_result_cache()


def _make_ctx(state: dict | None = None):
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    return ctx


def _policy_dict(**overrides) -> dict:
    base = {"penPerDay": 500, "penCap": 25000, "payWithin": 30, "cureDays": 30, "ratePct": 1, "ratePeriod": 30}
    base.update(overrides)
    return base


def _payload_dict(**overrides) -> dict:
    """A minimal valid PpaObligationPayload dict, overridable per test."""
    base = {
        "doc_id": "doc-1",
        "effectiveDate": "2024-01-15",
        "obligations": [{"id": "COD", "deadline": 240, "price": 250000}],
        "events": [],
        "policy": _policy_dict(),
        "policy_sources": dict.fromkeys(POLICY_KNOBS, "default"),
        "unmapped": [],
        "mapped_clauses": [],
    }
    base.update(overrides)
    return base


def _extraction_json(doc_id: str = "doc-1", populated: tuple[str, ...] = ("termination",)) -> str:
    """A PpaClauses extraction JSON with the given standard fields populated."""
    payload: dict = {"doc_id": doc_id, "other_clauses": []}
    for name in populated:
        payload[name] = {
            "clause_name": name,
            "display_name": name.replace("_", " ").title(),
            "value": f"{name} value",
            "raw_excerpt": f"verbatim {name} text",
            "block_id": f"blk-{name}",
            "confidence": "high",
        }
    return json.dumps(payload)


def _mapping(**overrides) -> ObligationMapping:
    """A minimal plausible LLM mapping output, overridable per test."""
    base: dict = {
        "effective_date": "2024-01-15",
        "obligations": [{"id": "COD", "deadline_date": "2024-09-11", "price": 250000, "block_id": "blk-termination"}],
        "events": [],
        "mapped_clauses": ["termination"],
        "unmapped": [],
    }
    base.update(overrides)
    return ObligationMapping.model_validate(base)


async def _run_mapper(mapping: ObligationMapping, extraction_json: str | None = None, ctx=None, doc_id: str = "doc-1"):
    """Drive map_ppa_obligations with mocked extraction + LLM, REAL assembly."""
    from tools import map_ppa_obligations as mod

    extraction = extraction_json if extraction_json is not None else _extraction_json(doc_id)
    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=extraction)),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"text": "PPA", "block_id": "b1"}])),
        patch.object(mod, "_run_obligation_mapping", new=AsyncMock(return_value=mapping.model_dump_json())),
    ):
        return await mod.map_ppa_obligations(doc_id=doc_id, tool_context=ctx)


# ---------------------------------------------------------------------------
# Wire schema -- round-trip + engine subset separation
# ---------------------------------------------------------------------------


def test_payload_round_trips_through_json():
    payload = PpaObligationPayload.model_validate(
        _payload_dict(
            events=[
                {"kind": "deliver", "day": 232, "ref": "COD"},
                {"kind": "pay", "day": 258, "ref": "COD"},
                {"kind": "force_majeure", "day": 320, "ref": "", "amt": 320, "hi": 340},
                {"kind": "notice", "day": 470, "ref": "COD-delivery"},
                {"kind": "terminate", "day": 520, "ref": "Buyer"},
            ],
        )
    )
    again = PpaObligationPayload.model_validate_json(payload.model_dump_json())
    assert again == payload
    assert again.effectiveDate == dt.date(2024, 1, 15)


def test_engine_payload_is_exactly_the_api_ail_subset():
    """effectiveDate / unmapped / policy_sources are artefact fields -- they
    must NEVER reach the engine. engine_payload() emits exactly the three
    analyzeContract arguments."""
    payload = PpaObligationPayload.model_validate(_payload_dict(events=[{"kind": "deliver", "day": 3, "ref": "COD"}]))
    wire = payload.engine_payload()
    assert set(wire.keys()) == {"obligations", "events", "policy"}
    assert wire["obligations"] == [{"id": "COD", "deadline": 240, "price": 250000}]
    assert wire["events"] == [{"kind": "deliver", "day": 3, "ref": "COD", "amt": 0, "hi": 0}]
    assert set(wire["policy"].keys()) == set(POLICY_KNOBS)
    # Must be JSON-serializable as-is (this is what goes over the wire).
    json.dumps(wire)


def test_m1_placeholder_payload_validates_against_wire_schema():
    """The hand-adapted M1 payload must satisfy this schema -- any drift
    between the Pydantic schema and api.ail fails loudly here, not in the UI."""
    if not M1_PAYLOAD_PATH.exists():
        pytest.skip("M1 placeholder payload not present (deleted in the public template)")
    raw = json.loads(M1_PAYLOAD_PATH.read_text())
    payload = PpaObligationPayload.model_validate(
        _payload_dict(
            doc_id="demosolar-m1-placeholder",
            effectiveDate=raw["effectiveDate"],
            obligations=raw["obligations"],
            events=raw["events"],
            policy=raw["policy"],
        )
    )
    assert payload.engine_payload()["policy"] == raw["policy"]


# ---------------------------------------------------------------------------
# LOUD rejection -- malformed events / payloads (one case per constraint)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, needle",
    [
        ({"kind": "teleport", "day": 1, "ref": "COD"}, "teleport"),  # unknown kind
        ({"kind": "deliver", "day": -3, "ref": "COD"}, "greater than or equal"),  # negative day
        ({"kind": "deliver", "day": 1, "ref": ""}, "obligation id"),  # deliver without ref
        ({"kind": "deliver", "day": 1, "ref": "COD", "amt": 99}, "amt/hi must be 0"),  # misplaced amt
        ({"kind": "pay", "day": 1, "ref": "COD", "hi": 7}, "amt/hi must be 0"),  # misplaced hi
        ({"kind": "amend_price", "day": 1, "ref": "", "amt": 5}, "obligation id"),  # amend without ref
        ({"kind": "amend_price", "day": 1, "ref": "COD", "amt": 5, "hi": 2}, "hi must be 0"),
        ({"kind": "force_majeure", "day": 10, "ref": "x", "amt": 10, "hi": 12}, "ref must be empty"),
        ({"kind": "force_majeure", "day": 10, "ref": "", "amt": 10, "hi": 8}, "hi=8 < amt=10"),  # hi < amt
        ({"kind": "force_majeure", "day": 9, "ref": "", "amt": 10, "hi": 12}, "must equal amt"),  # day != start
        ({"kind": "notice", "day": 1, "ref": "COD"}, "-delivery"),  # breach ref missing suffix
        ({"kind": "notice", "day": 1, "ref": "COD-shipping"}, "-delivery"),  # bad suffix
        ({"kind": "waive", "day": 1, "ref": "delivery"}, "-delivery"),  # suffix only, no id
        ({"kind": "notice", "day": 1, "ref": "COD-delivery", "amt": 3}, "amt/hi must be 0"),
        ({"kind": "terminate", "day": 1, "ref": ""}, "terminating party"),  # no party
        ({"kind": "terminate", "day": 1, "ref": "Buyer", "amt": 1}, "amt/hi must be 0"),
    ],
)
def test_malformed_event_rejected_loudly(event, needle):
    with pytest.raises(ValidationError) as exc_info:
        WireEvent.model_validate(event)
    assert needle in str(exc_info.value)


def test_event_ref_unknown_obligation_rejected():
    """api.ail would happily fold a deliver for an id the engine doesn't know
    (aGet returns -1 deadline) -- reject at the boundary instead."""
    with pytest.raises(ValidationError, match="unknown obligation 'Q9'"):
        PpaObligationPayload.model_validate(_payload_dict(events=[{"kind": "deliver", "day": 1, "ref": "Q9"}]))


def test_notice_for_unknown_obligation_rejected():
    with pytest.raises(ValidationError, match="unknown obligation"):
        PpaObligationPayload.model_validate(_payload_dict(events=[{"kind": "notice", "day": 1, "ref": "Q9-delivery"}]))


def test_duplicate_obligation_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate obligation id"):
        PpaObligationPayload.model_validate(
            _payload_dict(
                obligations=[
                    {"id": "COD", "deadline": 10, "price": 1},
                    {"id": "COD", "deadline": 20, "price": 2},
                ]
            )
        )


def test_unsorted_events_rejected():
    """The engine does not sort -- an unsorted timeline silently computes
    wrong numbers, so the schema refuses it outright."""
    with pytest.raises(ValidationError, match="non-decreasing day order"):
        PpaObligationPayload.model_validate(
            _payload_dict(
                events=[
                    {"kind": "pay", "day": 50, "ref": "COD"},
                    {"kind": "deliver", "day": 10, "ref": "COD"},
                ]
            )
        )


def test_empty_obligations_rejected():
    with pytest.raises(ValidationError):
        PpaObligationPayload.model_validate(_payload_dict(obligations=[]))


def test_negative_deadline_and_price_rejected():
    with pytest.raises(ValidationError):
        WireObligation.model_validate({"id": "COD", "deadline": -1, "price": 100})
    with pytest.raises(ValidationError):
        WireObligation.model_validate({"id": "COD", "deadline": 1, "price": -100})


def test_policy_bounds_mirror_z3_requires():
    """ratePeriod=0 would divide by zero inside the (proved) floorPeriods;
    negative knobs violate the Z3 requires clauses. All loud."""
    with pytest.raises(ValidationError):
        WirePolicy.model_validate(_policy_dict(ratePeriod=0))
    for knob in ("penPerDay", "penCap", "payWithin", "cureDays", "ratePct"):
        with pytest.raises(ValidationError):
            WirePolicy.model_validate(_policy_dict(**{knob: -1}))


def test_policy_sources_must_cover_all_six_knobs():
    with pytest.raises(ValidationError, match="policy_sources"):
        PpaObligationPayload.model_validate(_payload_dict(policy_sources={"penPerDay": "default"}))
    with pytest.raises(ValidationError, match="policy_sources"):
        PpaObligationPayload.model_validate(
            _payload_dict(policy_sources={**dict.fromkeys(POLICY_KNOBS, "default"), "bogusKnob": "default"})
        )


def test_effective_date_must_be_plain_date():
    with pytest.raises(ValidationError):
        PpaObligationPayload.model_validate(_payload_dict(effectiveDate="2024-01-15T10:00:00Z"))
    with pytest.raises(ValidationError):
        PpaObligationPayload.model_validate(_payload_dict(effectiveDate="not-a-date"))


# ---------------------------------------------------------------------------
# Calendar -> day-offset conversion (deterministic Python, never the LLM)
# ---------------------------------------------------------------------------


def test_effective_date_itself_is_day_zero():
    """PINNED: day 0 == the effective date (design doc date convention). A
    deadline ON the effective date is offset 0, not 1."""
    from tools.map_ppa_obligations import day_offset

    eff = dt.date(2024, 1, 15)
    assert day_offset("2024-01-15", eff, "test") == 0
    assert day_offset("2024-01-16", eff, "test") == 1


def test_offset_conversion_handles_leap_year():
    """2024 is a leap year: Jan 15 -> Mar 1 crosses Feb 29."""
    from tools.map_ppa_obligations import day_offset

    assert day_offset("2024-03-01", dt.date(2024, 1, 15), "test") == 46  # 16 (rest of Jan) + 29 (Feb) + 1
    # Non-leap 2023 for contrast: same span is one day shorter.
    assert day_offset("2023-03-01", dt.date(2023, 1, 15), "test") == 45


def test_offset_conversion_month_lengths():
    from tools.map_ppa_obligations import day_offset

    eff = dt.date(2024, 1, 31)
    assert day_offset("2024-02-01", eff, "test") == 1
    assert day_offset("2024-04-30", eff, "test") == 90  # 29 (Feb) + 31 (Mar) + 30 (Apr)
    assert day_offset("2025-01-31", eff, "test") == 366  # leap year spans Feb 29


def test_event_before_effective_date_is_loud_error():
    """PINNED: negative day offsets are REJECTED, not emitted. The engine uses
    -1 as its 'not delivered / not paid' sentinel (types.ail aGet, engine.ail
    `aGet(...) < 0` checks), so a day -1 event would silently read as 'never
    happened'. Loud failure over a silently wrong settlement."""
    from tools.map_ppa_obligations import MappingError, day_offset

    with pytest.raises(MappingError, match="predates the effective date"):
        day_offset("2024-01-10", dt.date(2024, 1, 15), "COD deadline")


def test_unparseable_date_is_loud_error():
    from tools.map_ppa_obligations import MappingError, day_offset

    with pytest.raises(MappingError, match="not a valid ISO date"):
        day_offset("Q3 2024", dt.date(2024, 1, 15), "COD deadline")
    with pytest.raises(MappingError, match="not a valid ISO date"):
        day_offset("2024-02-30", dt.date(2024, 1, 15), "COD deadline")


# ---------------------------------------------------------------------------
# Mapper tool -- input validation
# ---------------------------------------------------------------------------


async def test_neither_doc_id_nor_gs_url_returns_structured_error():
    from tools.map_ppa_obligations import map_ppa_obligations

    result = await map_ppa_obligations()
    parsed = json.loads(result)
    assert "error" in parsed
    assert "required" in parsed["error"].lower()


async def test_both_doc_id_and_gs_url_returns_structured_error():
    from tools.map_ppa_obligations import map_ppa_obligations

    result = await map_ppa_obligations(doc_id="doc-A", gs_url="gs://bucket/x.pdf")
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exactly one" in parsed["error"].lower()


async def test_doc_prefixed_id_normalized_like_sibling_tools():
    """`doc:{id}.json` artifact references must resolve to the bare id (the
    same online regression extract_ppa_clauses fixed)."""
    from tools import map_ppa_obligations as mod

    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json("c9e2b03a"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"text": "x"}])),
        patch.object(mod, "_run_obligation_mapping", new=AsyncMock(return_value=_mapping().model_dump_json())),
    ):
        result = await mod.map_ppa_obligations(doc_id="doc:c9e2b03a.json")

    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed["doc_id"] == "c9e2b03a"


async def test_extraction_error_propagates_as_structured_error():
    from tools import map_ppa_obligations as mod

    with patch.object(
        mod,
        "_resolve_extraction",
        new=AsyncMock(return_value=json.dumps({"error": "Document 'doc-x' not found", "doc_id": "doc-x"})),
    ):
        result = await mod.map_ppa_obligations(doc_id="doc-x")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "not found" in parsed["error"]


# ---------------------------------------------------------------------------
# Mapper tool -- assembly correctness
# ---------------------------------------------------------------------------


async def test_happy_path_produces_engine_valid_payload():
    mapping = _mapping(
        events=[
            {"kind": "deliver", "date": "2024-09-03", "ref": "COD"},
            {"kind": "force_majeure", "date": "2024-11-30", "ref": "", "window_end_date": "2024-12-20"},
        ],
    )
    result = await _run_mapper(mapping)
    parsed = json.loads(result)
    assert "error" not in parsed
    payload = PpaObligationPayload.model_validate(parsed)
    assert payload.effectiveDate == dt.date(2024, 1, 15)
    # 2024-09-11 is 240 days after 2024-01-15 (leap year) -- Python-computed.
    assert payload.obligations[0].deadline == 240
    deliver = payload.events[0]
    assert deliver.kind == "deliver"
    assert deliver.day == 232  # 2024-09-03
    fm = payload.events[1]
    assert fm.kind == "force_majeure"
    assert fm.day == fm.amt == 320  # 2024-11-30
    assert fm.hi == 340  # 2024-12-20


async def test_events_sorted_by_day_stably():
    """LLM event order is not trusted; the mapper sorts by day. The sort is
    STABLE so same-day events keep their relative order (notice arrival order
    grounds termination)."""
    mapping = _mapping(
        events=[
            {"kind": "pay", "date": "2024-03-01", "ref": "COD"},
            {"kind": "deliver", "date": "2024-02-01", "ref": "COD"},
            {"kind": "notice", "date": "2024-03-01", "ref": "COD-delivery"},
        ],
    )
    result = await _run_mapper(mapping)
    payload = PpaObligationPayload.model_validate_json(result)
    kinds = [e.kind for e in payload.events]
    assert kinds == ["deliver", "pay", "notice"]  # sorted by day; pay-before-notice preserved (same day)


async def test_missing_effective_date_is_loud_error_not_day_zero_guess():
    result = await _run_mapper(_mapping(effective_date=None))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "effective" in parsed["error"].lower()
    # The re-call signal is STRUCTURED — the agent/UI keys off this flag, not
    # off matching the error prose.
    assert parsed["needs_effective_date"] is True
    assert parsed["unmapped"] == []

    result2 = await _run_mapper(_mapping(effective_date=""))
    assert "error" in json.loads(result2)


async def test_event_before_effective_date_fails_whole_call():
    """PINNED: an event predating the effective date fails the WHOLE call --
    quietly moving it to `unmapped` would shift the settlement numbers of an
    otherwise-plausible payload. Anchor bugs must be un-missable."""
    mapping = _mapping(events=[{"kind": "deliver", "date": "2023-12-01", "ref": "COD"}])
    result = await _run_mapper(mapping)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "predates the effective date" in parsed["error"]
    assert "2023-12-01" in parsed["error"]


async def test_llm_emitted_wire_violation_is_structured_error():
    """The LLM inventing a breach ref the engine can't strip must surface as a
    structured error naming the violation -- never a half-valid payload."""
    mapping = _mapping(events=[{"kind": "notice", "date": "2024-06-01", "ref": "COD"}])
    result = await _run_mapper(mapping)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "-delivery" in parsed["error"]


async def test_no_obligations_is_loud_error():
    mapping = _mapping(obligations=[], mapped_clauses=[], unmapped=[{"clause": "termination", "reason": "n/a"}])
    result = await _run_mapper(mapping)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "no obligations" in parsed["error"].lower()
    # Per-clause reasons ride structured in the envelope (the refusal panel
    # consumes this list directly — never re-parsed out of the error prose).
    assert parsed["unmapped"] == [{"clause": "termination", "reason": "n/a"}]
    assert parsed["needs_effective_date"] is False


# ---------------------------------------------------------------------------
# Caller-provided effective date (user-confirmation loop for template PPAs)
# ---------------------------------------------------------------------------


async def _run_mapper_with_date(mapping: ObligationMapping, effective_date: str | None, ctx=None):
    from tools import map_ppa_obligations as mod

    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json("doc-1"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"text": "PPA"}])),
        patch.object(mod, "_run_obligation_mapping", new=AsyncMock(return_value=mapping.model_dump_json())),
    ):
        return await mod.map_ppa_obligations(doc_id="doc-1", effective_date=effective_date, tool_context=ctx)


async def test_provided_effective_date_anchors_template_contract():
    """Template PPAs have blank start dates; the user-confirmed date passed by
    the agent anchors the timeline and is recorded as 'provided'."""
    mapping = _mapping(effective_date=None)  # document determined nothing
    result = await _run_mapper_with_date(mapping, "2024-01-15")
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.effectiveDate == dt.date(2024, 1, 15)
    assert payload.effective_date_source == "provided"
    assert payload.obligations[0].deadline == 240  # anchored on the provided date


async def test_provided_effective_date_wins_over_extracted():
    """The user-confirmation path is explicit -- it beats whatever the LLM
    read out of the document, and the provenance says so."""
    mapping = _mapping(effective_date="2024-06-01")
    result = await _run_mapper_with_date(mapping, "2024-01-15")
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.effectiveDate == dt.date(2024, 1, 15)
    assert payload.effective_date_source == "provided"


async def test_document_extracted_date_recorded_as_extracted():
    result = await _run_mapper(_mapping())
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.effective_date_source == "extracted"


async def test_invalid_provided_effective_date_rejected_before_any_llm():
    from tools import map_ppa_obligations as mod

    extraction_spy = AsyncMock()
    with (
        patch.object(mod, "_resolve_extraction", new=extraction_spy),
        patch.object(mod, "_load_blocks", new=AsyncMock()),
        patch.object(mod, "_run_obligation_mapping", new=AsyncMock()),
    ):
        result = await mod.map_ppa_obligations(doc_id="doc-1", effective_date="15/01/2024")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "15/01/2024" in parsed["error"]
    extraction_spy.assert_not_awaited()  # rejected before any spend


async def test_effective_date_cache_key_is_variant_aware():
    """A provided-anchor run must never serve -- or poison -- the
    document-anchored cache entry (mirrors the clause-subset invariant)."""
    from tools import map_ppa_obligations as mod

    assert mod.obligation_cache_key("doc-1") != mod.obligation_cache_key("doc-1", "2024-01-15")

    # Seed the DEFAULT entry, then run with a provided date: the default
    # entry must be ignored (different variant) and left untouched.
    default_key = mod.obligation_cache_key("doc-1")
    default_entry = PpaObligationPayload.model_validate(_payload_dict()).model_dump_json()
    ctx = _make_ctx({default_key: default_entry})
    result = await _run_mapper_with_date(_mapping(effective_date=None), "2023-06-01", ctx=ctx)
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.effective_date_source == "provided"  # fresh mapping ran, not the cached default
    assert ctx.state[default_key] == default_entry  # default entry untouched
    assert ctx.state[mod.obligation_cache_key("doc-1", "2023-06-01")] == result  # own variant entry


# ---------------------------------------------------------------------------
# No-silent-drops -- coverage enforcement
# ---------------------------------------------------------------------------


async def test_unaccounted_clause_auto_flagged_into_unmapped():
    """ADVERSARIAL: the LLM 'forgets' governing_law and a bespoke other_clause
    entirely (neither mapped nor unmapped). The tool must auto-flag both into
    `unmapped` -- nothing silently dropped, ever."""
    extraction = json.dumps(
        {
            "doc_id": "doc-1",
            "termination": {
                "clause_name": "termination",
                "display_name": "Termination",
                "value": "60d cure",
                "raw_excerpt": "x",
                "block_id": "blk-1",
                "confidence": "high",
            },
            "governing_law": {
                "clause_name": "governing_law",
                "display_name": "Governing Law",
                "value": "Spain",
                "raw_excerpt": "x",
                "block_id": "blk-2",
                "confidence": "high",
            },
            "other_clauses": [
                {
                    "clause_name": "bespoke_hedge",
                    "display_name": "Bespoke Hedge",
                    "value": "exotic",
                    "raw_excerpt": "x",
                    "block_id": "blk-3",
                    "confidence": "low",
                }
            ],
        }
    )
    mapping = _mapping(mapped_clauses=["termination"], unmapped=[])  # LLM ignored the other two
    result = await _run_mapper(mapping, extraction_json=extraction)
    payload = PpaObligationPayload.model_validate_json(result)
    flagged = {u.clause for u in payload.unmapped}
    assert "governing_law" in flagged
    assert "bespoke_hedge" in flagged
    for u in payload.unmapped:
        assert u.reason  # every auto-flag carries a reason
    # Accounting is complete: mapped + unmapped covers every populated clause.
    assert set(payload.mapped_clauses) | flagged >= {"termination", "governing_law", "bespoke_hedge"}


async def test_llm_unmapped_entries_preserved_verbatim():
    mapping = _mapping(
        mapped_clauses=["termination"],
        unmapped=[{"clause": "governing_law", "reason": "no deontic semantics for jurisdiction"}],
    )
    extraction = _extraction_json(populated=("termination", "governing_law"))
    result = await _run_mapper(mapping, extraction_json=extraction)
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.unmapped[0].clause == "governing_law"
    assert payload.unmapped[0].reason == "no deontic semantics for jurisdiction"


# ---------------------------------------------------------------------------
# Policy-knob source recording
# ---------------------------------------------------------------------------


async def test_extracted_policy_knobs_recorded_with_source():
    mapping = _mapping(
        penPerDay={"value": 2500, "excerpt": "delay damages of EUR 2,500 per day"},
        cureDays={"value": 45, "excerpt": "forty-five (45) days to cure"},
    )
    result = await _run_mapper(mapping)
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.policy.penPerDay == 2500
    assert payload.policy.cureDays == 45
    assert payload.policy_sources["penPerDay"] == "extracted"
    assert payload.policy_sources["cureDays"] == "extracted"
    # Unstated knobs fall back to the engine baseline, recorded as such.
    assert payload.policy.payWithin == DEFAULT_POLICY.payWithin
    assert payload.policy_sources["payWithin"] == "default"
    assert payload.policy_sources["penCap"] == "default"


async def test_all_defaults_when_contract_states_nothing():
    result = await _run_mapper(_mapping())
    payload = PpaObligationPayload.model_validate_json(result)
    assert payload.policy == DEFAULT_POLICY
    assert all(payload.policy_sources[k] == "default" for k in POLICY_KNOBS)


async def test_invalid_extracted_knob_is_loud_error():
    """A contract 'stating' ratePeriod=0 (or the LLM hallucinating it) would
    divide by zero inside the proved arithmetic -- loud error, not clamping."""
    result = await _run_mapper(_mapping(ratePeriod={"value": 0, "excerpt": "instantaneous interest"}))
    parsed = json.loads(result)
    assert "error" in parsed


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


async def test_result_cached_app_scoped_and_served_on_second_call():
    from tools import map_ppa_obligations as mod

    ctx = _make_ctx()
    result = await _run_mapper(_mapping(), ctx=ctx)
    key = mod.obligation_cache_key("doc-1")
    assert key in ctx.state
    assert ctx.state[key] == result

    # Second call: everything mocked to explode -- must serve from cache.
    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(side_effect=AssertionError("should not run"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(side_effect=AssertionError("should not run"))),
        patch.object(mod, "_run_obligation_mapping", new=AsyncMock(side_effect=AssertionError("should not run"))),
    ):
        second = await mod.map_ppa_obligations(doc_id="doc-1", tool_context=ctx)
    assert second == result


async def test_stale_cache_self_heals_into_fresh_mapping():
    from tools import map_ppa_obligations as mod

    ctx = _make_ctx({mod.obligation_cache_key("doc-1"): '{"garbage": true}'})
    result = await _run_mapper(_mapping(), ctx=ctx)
    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed["doc_id"] == "doc-1"  # fresh mapping won


async def test_error_results_are_not_cached():
    from tools import map_ppa_obligations as mod

    ctx = _make_ctx()
    result = await _run_mapper(_mapping(effective_date=None), ctx=ctx)
    assert "error" in json.loads(result)
    assert mod.obligation_cache_key("doc-1") not in ctx.state


# ---------------------------------------------------------------------------
# Model routing -- tiered, never hardcoded
# ---------------------------------------------------------------------------


async def test_mapping_llm_routed_through_model_tiers():
    """The mapping call must resolve its model via gemini_api_name_for(tier),
    not a hardcoded model id (fork/deploy tier routing)."""
    from tools import map_ppa_obligations as mod

    captured: dict = {}

    async def _fake_generate(*, model, contents, config):
        captured["model"] = model
        captured["schema"] = config.get("response_schema")
        resp = MagicMock()
        resp.text = _mapping().model_dump_json()
        return resp

    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(side_effect=_fake_generate)

    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json())),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"text": "PPA"}])),
        patch("tools.resilient_genai.genai.Client", return_value=fake_client),
        patch("tools.resilient_genai.gemini_api_name_for", return_value="gemini-tier-resolved") as tier_spy,
    ):
        result = await mod.map_ppa_obligations(doc_id="doc-1")

    assert "error" not in json.loads(result)
    tier_spy.assert_called_once_with(mod._MAPPING_TIER)
    assert captured["model"] == "gemini-tier-resolved"
    assert captured["schema"] == ObligationMapping.model_json_schema()


# ---------------------------------------------------------------------------
# Engine validation -- real v0.29.0 CLI (skips cleanly when absent)
# ---------------------------------------------------------------------------


def _find_ailang_cli() -> str | None:
    env_bin = os.environ.get("AILANG_BIN")
    if env_bin and os.access(env_bin, os.X_OK):
        return env_bin
    import glob

    for candidate in glob.glob("/private/tmp/claude-*/*/*/scratchpad/release/cli/ailang"):
        if os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("ailang")


def _find_engine_dir() -> Path | None:
    for candidate in (
        Path.home() / ".ailang/cache/registry/sunholo/deontic/0.1.2",
        Path(__file__).parents[3] / "infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1/assets/engine",
    ):
        if (candidate / "api.ail").exists() and (candidate / "engine.ail").exists():
            return candidate
    return None


def _run_engine_cli(payload: dict) -> str:
    """Run a wire payload through the real ailang CLI; return the report text.

    Mirrors scripts/gate-obligation-artefact.sh: generate a runner module with
    structured constructor calls (avoids JSON-string escaping), stage the
    ./types ./settle ./engine ./api modules in a work dir, strip the CLI's
    progress glyphs from stdout.
    """
    cli = _find_ailang_cli()
    engine = _find_engine_dir()
    assert cli and engine  # guarded by skipif on the callers

    def q(s: str) -> str:
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    ctor = {
        "deliver": lambda e: f"Deliver({e['day']}, {q(e['ref'])})",
        "pay": lambda e: f"Pay({e['day']}, {q(e['ref'])})",
        "amend_price": lambda e: f"AmendPrice({e['day']}, {q(e['ref'])}, {e['amt']})",
        "force_majeure": lambda e: f"ForceMajeure({e['day']}, {e['amt']}, {e['hi']})",
        "notice": lambda e: f"Notice({e['day']}, {q(e['ref'])})",
        "waive": lambda e: f"Waive({e['day']}, {q(e['ref'])})",
        "terminate": lambda e: f"Terminate({e['day']}, {q(e['ref'])})",
    }
    obl = ",\n".join(f"    ({q(o['id'])}, {o['deadline']}, {o['price']})" for o in payload["obligations"])
    ids = ", ".join(q(o["id"]) for o in payload["obligations"])
    timeline = ",\n".join("    " + ctor[e["kind"]](e) for e in payload["events"])
    p = payload["policy"]
    runner = f"""module sunholo/deontic/runner

import std/io (println)
import ./types (Event, Deliver, Pay, AmendPrice, ForceMajeure, Notice, Waive, Terminate, Policy, initState)
import ./engine (runEvents, report)

func printAll(xs: [string]) -> () ! {{IO}} {{
  match xs {{ [] => (), h :: t => {{ println(h); printAll(t) }} }}
}}

export func main() -> () ! {{IO}} {{
  let pol = {{ penPerDay: {p["penPerDay"]}, penCap: {p["penCap"]}, payWithin: {p["payWithin"]},
              cureDays: {p["cureDays"]}, ratePct: {p["ratePct"]}, ratePeriod: {p["ratePeriod"]} }};
  let obligations = [
{obl}
  ];
  let timeline = [
{timeline}
  ];
  let st = runEvents(pol, initState(obligations), timeline);
  printAll(report(pol, st, [{ids}]))
}}
"""
    with tempfile.TemporaryDirectory(prefix="deontic_engine_test_") as work:
        work_path = Path(work)
        for name in ("types", "settle", "engine", "api"):
            (work_path / f"{name}.ail").write_text((engine / f"{name}.ail").read_text())
        if (engine / "ailang.toml").exists():
            (work_path / "ailang.toml").write_text((engine / "ailang.toml").read_text())
        (work_path / "runner.ail").write_text(runner)
        proc = subprocess.run(
            [cli, "run", "--caps", "IO", "runner.ail"],
            cwd=work,
            env={**os.environ, "AILANG_RELAX_MODULES": "1"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"ailang CLI failed:\n{proc.stderr}\n{proc.stdout}"
        # Strip progress glyphs (-> Type checking / check Running) like the gate script.
        return "\n".join(line for line in proc.stdout.splitlines() if not line.startswith(("→", "✓")))


_engine_available = pytest.mark.skipif(
    _find_ailang_cli() is None or _find_engine_dir() is None,
    reason="ailang CLI / deontic engine modules not available (set AILANG_BIN or run make fetch-ailang-wasm)",
)


@_engine_available
@pytest.mark.slow
def test_m1_placeholder_payload_accepted_by_real_engine():
    """Regression anchor: the M1 payload's known-good golden net line."""
    if not M1_PAYLOAD_PATH.exists():
        pytest.skip("M1 placeholder payload not present")
    report = _run_engine_cli(json.loads(M1_PAYLOAD_PATH.read_text()))
    assert "net: Vendor pays Client 125000" in report  # gate script's golden line


@_engine_available
@pytest.mark.slow
def test_corpus_fixture_payloads_accepted_by_real_engine():
    """Every corpus fixture payload must be accepted by the real engine and
    produce a settlement report (no 'error:' lines, has a net line)."""
    fixture_files = sorted(FIXTURES_DIR.glob("payload.*.json")) if FIXTURES_DIR.exists() else []
    if not fixture_files:
        pytest.skip("corpus fixtures not present (confidential; excluded from the public template)")
    for path in fixture_files:
        envelope = PpaObligationPayload.model_validate_json(path.read_text())  # schema-valid
        report = _run_engine_cli(envelope.engine_payload())
        assert "error:" not in report, f"{path.name}: engine rejected payload:\n{report}"
        assert "net:" in report, f"{path.name}: no net settlement line:\n{report}"
        for o in envelope.obligations:
            assert f"{o.id} effective:" in report, f"{path.name}: obligation {o.id} missing from report"


def test_corpus_fixtures_schema_valid_and_coverage_recorded():
    """Fixture envelopes validate and each records coverage + knob provenance
    (the acceptance criterion's per-fixture audit trail)."""
    fixture_files = sorted(FIXTURES_DIR.glob("payload.*.json")) if FIXTURES_DIR.exists() else []
    if not fixture_files:
        pytest.skip("corpus fixtures not present (confidential; excluded from the public template)")
    for path in fixture_files:
        envelope = PpaObligationPayload.model_validate_json(path.read_text())
        assert envelope.mapped_clauses or envelope.unmapped, f"{path.name}: no coverage record"
        assert set(envelope.policy_sources.keys()) == set(POLICY_KNOBS)


def test_corpus_refusal_fixtures_are_structured_and_reasoned():
    """The 2026-07-11 live-sweep finding: the corpus PPAs are template /
    pro-forma or market-formula contracts, and the mapper REFUSES the
    inexpressible ones loudly (with per-clause reasons) rather than
    inventing numbers. The refusal fixtures are the honest live record --
    pin their shape so a regeneration that silently changes the contract
    is caught."""
    refusal_files = sorted(FIXTURES_DIR.glob("error.*.json")) if FIXTURES_DIR.exists() else []
    if not refusal_files:
        pytest.skip("corpus refusal fixtures not present (confidential; excluded from the public template)")
    for path in refusal_files:
        record = json.loads(path.read_text())
        assert "error" in record, f"{path.name}: refusal fixture without an error field"
        assert record.get("doc_id"), f"{path.name}: refusal fixture without doc identity"
        # Refusals must be REASONED -- either the effective-date refusal or
        # the no-expressible-obligations refusal with its unmapped accounting.
        assert (
            "No effective date could be established" in record["error"]
            or "no obligations expressible" in record["error"]
        ), f"{path.name}: unexpected refusal class: {record['error'][:120]}"
        # The refusal envelope is STRUCTURED: per-clause reasons ride in
        # `unmapped` (the A2UI panel consumes them directly), and the
        # re-call signal is the `needs_effective_date` flag.
        assert "needs_effective_date" in record, f"{path.name}: refusal without needs_effective_date flag"
        if "no obligations expressible" in record["error"]:
            unmapped = record.get("unmapped")
            assert unmapped, f"{path.name}: refusal without structured per-clause reasons"
            for entry in unmapped:
                assert entry.get("clause") and entry.get("reason"), f"{path.name}: malformed unmapped entry: {entry}"


@_engine_available
@pytest.mark.slow
def test_synthetic_fixture_golden_settlement_lines():
    """The SYNTHETIC fixture exercises all seven event kinds; its settlement
    was hand-computed from the pinned engine semantics BEFORE running the
    engine. The real v0.29.0 CLI must reproduce every mechanic exactly:
    FM window [195,205] extends DEL1's deadline 200 -> 211 (span 11, only
    undelivered in-window deadlines move); DEL1 pays 35 days late -> one full
    30-day period -> interest 600 ACCRUED but WAIVED from owed; DEL2 noticed
    d310, uncured, termination d345 (> 310+30 strict) cancels it with
    daysLateAt(344, 300) = 44 days x 500 = 22000 damages under the 25000 cap.
    A drift in any of these lines means the engine or the wire mapping
    changed semantics -- fail loudly."""
    path = FIXTURES_DIR / "payload.synthetic-demo.json"
    if not path.exists():
        pytest.skip("synthetic fixture not present")
    envelope = PpaObligationPayload.model_validate_json(path.read_text())
    report = _run_engine_cli(envelope.engine_payload())
    for golden in (
        "DEL1 effective: deadline=211 price=60000",  # FM extension applied
        "DEL2 effective: deadline=300 price=66000",  # amend_price applied
        "COD payment: PAID day=150 late=10 interest=0",  # 10d late < 1 full period
        "DEL1 delivery: DELIVERED day=210 late=0 penalty=0",  # on time post-extension
        "DEL1 payment: PAID day=275 late=35 interest=600",  # accrued shown...
        "DEL1 payment breach: WAIVED",  # ...but excluded from owed
        "DEL2 delivery: CANCELLED late_days=44 penalty=22000",
        "DEL2 delivery breach: UNCURED",
        "termination: day=345 by=Buyer grounds=DEL2-delivery",
        "vendor_owes=22000",
        "client_owes=0",  # waived DEL1 interest excluded
        "net: Vendor pays Client 22000",
    ):
        assert golden in report, f"golden line missing from engine report: {golden!r}\n--- report ---\n{report}"


# ---------------------------------------------------------------------------
# 7.8 M1 — structured elicitation + build-from-assumptions (the DEMO UNBLOCK)
#
# Correctness-critical: the ONE PPA corpus is template contracts (all `[●]`),
# so the mapper refuses. Elicitation lets the user supply the blanks and the
# build RESOLVES the parsed formulas (delay-LD `penPerDay = 150 x capacity`)
# deterministically in Python. A wrong resolved LD/settlement number is
# trust-ending — these tests pin the formula resolution, provenance, engine
# validity, and the LOUD rejection of malformed assumptions.
# ---------------------------------------------------------------------------

from tools.map_ppa_obligations import build_payload_from_assumptions  # noqa: E402


def _leap_assumptions(**overrides) -> dict:
    """A complete Demo-LEAP-shaped assumption set (values arrive as strings
    from the A2UI TextField/DateTimeInput), overridable per test."""
    base = {
        "effective_date": "2026-01-01",
        "cod_date": "2027-07-01",  # 546 days after effective
        "contract_capacity_mw": "100",
        "contract_price": "250000",
        "pen_cap": "5000000",
        "pay_within_days": "42",
        "cure_days": "30",
        "rate_pct": "3",
        "rate_period_days": "30",
    }
    base.update(overrides)
    return base


def test_elicitation_envelope_is_structured_typed_fields():
    """The refusal→elicit contract is STRUCTURED typed fields (not prose): each
    field carries name/type/label/help/resolves; the required set is exactly the
    minimum to complete a settlement."""
    env = build_obligation_elicitation("demo-leap", reason="template contract")
    assert isinstance(env, ElicitationEnvelope)
    assert env.action == "start_obligation_analysis"
    assert env.doc_id == "demo-leap"
    by_name = {f.name: f for f in env.fields}
    # Every field is typed date|number with a human label + non-empty help.
    for f in env.fields:
        assert f.type in ("date", "number")
        assert f.label and f.help
    # Dates are DateTimeInput-bound; amounts are numeric.
    assert by_name["effective_date"].type == "date"
    assert by_name["cod_date"].type == "date"
    assert by_name["contract_capacity_mw"].type == "number"
    assert by_name["contract_price"].type == "number"
    # Minimum required set to COMPLETE a settlement.
    assert {f.name for f in env.fields if f.required} == set(REQUIRED_ASSUMPTION_FIELDS)
    assert set(REQUIRED_ASSUMPTION_FIELDS) == {
        "effective_date",
        "cod_date",
        "contract_capacity_mw",
        "contract_price",
    }


def test_capacity_field_documents_its_formula_resolutions():
    """The Contract Capacity field must DISCLOSE both per-MW LD formulas it
    drives — the delay-LD it resolves into an engine knob AND the COD-flex LD it
    surfaces but does NOT write to a knob (a daily rate is not a cap)."""
    env = build_obligation_elicitation("demo-leap")
    cap = next(f for f in env.fields if f.name == "contract_capacity_mw")
    joined = " ".join(cap.resolves).lower()
    assert "penperday" in joined and "150" in joined  # delay-LD → engine knob
    assert "200" in joined  # COD-flex LD disclosed
    # The COD-flex disclosure must be flagged as NOT a knob (trust discipline).
    assert "not a knob" in joined or "disclosed" in joined
    # pen_cap is a SEPARATE user field, NOT derived from capacity.
    pen_cap = next(f for f in env.fields if f.name == "pen_cap")
    assert "not derived from contract capacity" in pen_cap.help.lower()


def test_capacity_resolves_delay_ld_into_penperday():
    """THE formula-resolution correctness pin: Contract Capacity resolves the
    delay-LD into penPerDay = 150 x capacity, deterministically in Python."""
    for cap in (50, 100, 137):
        p = build_payload_from_assumptions("leap", _leap_assumptions(contract_capacity_mw=str(cap)))
        assert p.policy.penPerDay == DELAY_LD_EUR_PER_MW_DAY * cap == 150 * cap
    # The COD-flex LD (200 x capacity) is DISCLOSED but written to NO engine
    # knob — penCap comes from the user's field, not 200xcap.
    p = build_payload_from_assumptions("leap", _leap_assumptions(contract_capacity_mw="100", pen_cap="5000000"))
    assert p.policy.penCap == 5000000  # user's value, NOT 200x100=20000
    assert p.policy.penCap != COD_FLEX_LD_EUR_PER_MW_DAY * 100


def test_build_from_assumptions_marks_every_value_as_assumption():
    """EARNED TRUST: nothing masquerades as a contract fact. Every supplied /
    derived value is provenance-marked as an assumption — effective date
    'provided', policy + price 'reviewed', NEVER 'extracted'."""
    p = build_payload_from_assumptions("leap", _leap_assumptions())
    assert p.effective_date_source == "provided"
    assert set(p.policy_sources.keys()) == set(POLICY_KNOBS)
    assert all(v == "reviewed" for v in p.policy_sources.values())
    assert p.price_sources == {"COD": "reviewed"}
    assert "extracted" not in set(p.policy_sources.values())
    assert "extracted" not in set(p.price_sources.values())


def test_build_from_assumptions_resolves_dates_and_price():
    """The COD obligation deadline is a deterministic integer day offset from
    the supplied effective date; the price is the supplied amount."""
    p = build_payload_from_assumptions("leap", _leap_assumptions())
    assert len(p.obligations) == 1
    cod = p.obligations[0]
    assert cod.id == "COD"
    assert cod.deadline == 546  # 2026-01-01 → 2027-07-01
    assert cod.price == 250000
    assert str(p.effectiveDate) == "2026-01-01"


def test_business_days_to_calendar_default_payment_window():
    """'thirty Business Days' → a confirmable 42-calendar-day approximation."""
    assert business_days_to_calendar(30) == 42
    # Left blank, the build uses the calendar approximation (an assumption).
    a = _leap_assumptions()
    del a["pay_within_days"]
    p = build_payload_from_assumptions("leap", a)
    assert p.policy.payWithin == 42


def test_build_from_assumptions_is_engine_wire_valid():
    """The assembled payload passes the strict wire schema (so it can never
    reach the engine malformed)."""
    p = build_payload_from_assumptions("leap", _leap_assumptions())
    # Round-trips through the strict validator + emits exactly the engine subset.
    PpaObligationPayload.model_validate_json(p.model_dump_json())
    subset = p.engine_payload()
    assert set(subset.keys()) == {"obligations", "events", "policy"}
    assert subset["obligations"][0] == {"id": "COD", "deadline": 546, "price": 250000}


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"effective_date": ""}, "effective_date"),
        ({"contract_capacity_mw": ""}, "contract_capacity_mw"),
        ({"contract_price": ""}, "contract_price"),
        ({"cod_date": ""}, "cod_date"),
        ({"contract_capacity_mw": "0"}, ">= 1"),  # capacity must be positive
        ({"contract_capacity_mw": "abc"}, "whole number"),
        ({"contract_price": "12.5"}, "whole number"),  # no fractional amounts
        ({"contract_price": "-5"}, ">= 0"),
        ({"effective_date": "not-a-date"}, "valid ISO date"),
        ({"contract_capacity_mw": "true"}, "whole number"),
    ],
)
def test_malformed_assumptions_rejected_loudly(overrides, needle):
    """Every malformed / insufficient assumption is a LOUD AssumptionError —
    never silently coerced into a wrong settlement number."""
    with pytest.raises(AssumptionError) as exc:
        build_payload_from_assumptions("leap", _leap_assumptions(**overrides))
    assert needle in str(exc.value)


def test_cod_before_effective_date_is_loud_error():
    """A COD date before the effective date is inexpressible (engine -1
    sentinel) — LOUD MappingError, never a negative offset."""
    from tools.map_ppa_obligations import MappingError

    with pytest.raises(MappingError):
        build_payload_from_assumptions("leap", _leap_assumptions(cod_date="2025-06-01"))


def test_boolean_capacity_rejected():
    """A JSON boolean is not a number — reject rather than coerce True→1."""
    with pytest.raises(AssumptionError):
        build_payload_from_assumptions("leap", _leap_assumptions(contract_capacity_mw=True))


def test_non_dict_assumptions_rejected():
    with pytest.raises(AssumptionError):
        build_payload_from_assumptions("leap", ["not", "a", "dict"])  # type: ignore[arg-type]


# --- tool-level ingress: refusal carries elicitation; assumptions complete ---


async def _run_tool(*, assumptions=None, state=None):
    """Invoke map_ppa_obligations with an optional assumptions arg / state,
    stubbing extraction + blocks + the mapping LLM so the refusal path fires
    when no assumptions are supplied."""
    from tools import map_ppa_obligations as mod

    ctx = _make_ctx(state)
    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json("doc-1"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"block_id": "b1", "text": "x"}])),
        patch.object(
            mod,
            "_run_obligation_mapping",
            new=AsyncMock(return_value=ObligationMapping().model_dump_json()),  # empty → refusal
        ),
    ):
        return await mod.map_ppa_obligations(doc_id="doc-1", assumptions=assumptions, tool_context=ctx)


def test_next_elicit_seq_increments_monotonically_in_state():
    """Append-only forms (7.8): each emission bumps a per-session counter so the
    surface is unique. No state → 1 (single-form fallback)."""
    from tools.map_ppa_obligations import _next_elicit_seq

    assert _next_elicit_seq(None) == 1  # no context
    ctx = _make_ctx()
    assert _next_elicit_seq(ctx) == 1
    assert _next_elicit_seq(ctx) == 2
    assert _next_elicit_seq(ctx) == 3
    assert ctx.state["_oblig_elicit_seq"] == 3


async def test_refusal_carries_incrementing_elicit_seq_for_append_only():
    """Two successive template-contract refusals in one session carry DISTINCT
    elicit_seq values → distinct chat surfaces → append-only form history."""
    ctx_state: dict = {}
    first = json.loads(await _run_tool(state=ctx_state))
    second = json.loads(await _run_tool(state=ctx_state))
    assert first.get("needs_assumptions") and second.get("needs_assumptions")
    assert first["elicit_seq"] == 1
    assert second["elicit_seq"] == 2


async def test_result_cache_skips_re_extraction_and_refresh_bypasses():
    """The cross-session cache (the '2-min every test' fix): a second run of the
    same doc skips the extraction + mapping LLM; refresh=True forces a fresh run.
    Append-only holds — the cached refusal is re-served with a fresh elicit_seq."""
    from tools import map_ppa_obligations as mod

    ctx = _make_ctx()
    map_mock = AsyncMock(return_value=ObligationMapping().model_dump_json())  # empty → refusal
    with (
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json("doc-1"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"block_id": "b1", "text": "x"}])),
        patch.object(mod, "_run_obligation_mapping", new=map_mock),
    ):
        r1 = json.loads(await mod.map_ppa_obligations(doc_id="doc-1", tool_context=ctx))
        r2 = json.loads(await mod.map_ppa_obligations(doc_id="doc-1", tool_context=ctx))
        assert map_mock.await_count == 1  # second run hit the cache — no re-map
        assert r1.get("needs_assumptions") and r2.get("needs_assumptions")
        assert r2["elicit_seq"] == r1["elicit_seq"] + 1  # fresh seq → still append-only
        # refresh=True bypasses the cache and re-runs the mapping.
        await mod.map_ppa_obligations(doc_id="doc-1", refresh=True, tool_context=ctx)
        assert map_mock.await_count == 2


async def test_firestore_cache_serves_after_module_cache_clear():
    """The DURABLE tier (the fix for 'caching never works for me'): a cold-start /
    redeploy wipes the in-process module cache, but the Firestore tier still
    serves — so a re-test after a deploy is instant, not another 2-min wait.

    Simulated with a dict-backed fake Firestore (the autouse fixture's no-op
    patches are overridden here). First run computes + writes; we then clear the
    MODULE cache (as a cold start would) and assert the second run does NOT
    re-map — it came from the durable tier."""
    from tools import map_ppa_obligations as mod

    fake_fs: dict[str, str] = {}

    def fake_get(key: str) -> str | None:
        return fake_fs.get(key)

    def fake_set(key: str, value: str, identity: str) -> None:
        fake_fs[key] = value

    ctx = _make_ctx()
    map_mock = AsyncMock(return_value=ObligationMapping().model_dump_json())  # empty → refusal
    with (
        patch.object(mod, "_firestore_cache_get", new=fake_get),
        patch.object(mod, "_firestore_cache_set", new=fake_set),
        patch.object(mod, "_resolve_extraction", new=AsyncMock(return_value=_extraction_json("doc-1"))),
        patch.object(mod, "_load_blocks", new=AsyncMock(return_value=[{"block_id": "b1", "text": "x"}])),
        patch.object(mod, "_run_obligation_mapping", new=map_mock),
    ):
        r1 = json.loads(await mod.map_ppa_obligations(doc_id="doc-1", tool_context=ctx))
        assert map_mock.await_count == 1
        assert fake_fs, "durable tier should have been written on the first run"

        # Cold start / redeploy: the in-process module cache is gone.
        mod._reset_result_cache()

        # A brand-new session (fresh ctx) — the state cache is empty too. Only the
        # durable Firestore tier can save the re-map here.
        r2 = json.loads(await mod.map_ppa_obligations(doc_id="doc-1", tool_context=_make_ctx()))
        assert map_mock.await_count == 1  # NO re-map — served from Firestore
        assert r1.get("needs_assumptions") and r2.get("needs_assumptions")
        # Re-served with a freshly stamped seq (append-only holds per new session).
        assert isinstance(r2.get("elicit_seq"), int) and r2["elicit_seq"] >= 1


async def test_refusal_returns_structured_elicitation_envelope():
    """A template-contract refusal is NOT a prose dead-end: it carries a
    STRUCTURED elicitation envelope the app renders as a chat form."""
    raw = await _run_tool()
    result = json.loads(raw)
    assert "error" in result
    assert result.get("needs_assumptions") is True
    env = result.get("elicitation")
    assert env and env["action"] == "start_obligation_analysis"
    assert {f["name"] for f in env["fields"]} >= set(REQUIRED_ASSUMPTION_FIELDS)
    # Validates back into the typed envelope (wire contract).
    ElicitationEnvelope.model_validate(env)


async def test_assumptions_arg_completes_the_analysis():
    """Supplying assumptions (the CLI/LLM arg channel) COMPLETES the analysis —
    a success PpaObligationPayload, no refusal."""
    raw = await _run_tool(assumptions=json.dumps(_leap_assumptions()))
    result = json.loads(raw)
    assert "error" not in result
    assert result["effective_date_source"] == "provided"
    assert result["policy"]["penPerDay"] == 150 * 100
    assert result["obligations"][0]["id"] == "COD"


async def test_assumptions_read_authoritatively_from_surface_state():
    """The AUTHORITATIVE, no-LLM-transcription path: the form's data model in
    session state (seeded by surface-action-run) drives the build even with NO
    assumptions arg."""
    surface_id = "obligation_elicitation:doc-1"
    state = {
        "a2ui_action_trigger": {"surfaceId": surface_id, "name": "start_obligation_analysis"},
        "a2ui_surface_state": {surface_id: {"dataModel": _leap_assumptions(contract_capacity_mw="80")}},
    }
    raw = await _run_tool(state=state)
    result = json.loads(raw)
    assert "error" not in result
    assert result["policy"]["penPerDay"] == 150 * 80  # read from state, not the arg


async def test_unrelated_surface_state_does_not_trigger_build():
    """A bare launcher / unrelated surface (no elicitation fields) must NOT be
    mistaken for the form — the tool falls through to the refusal+elicit path."""
    state = {
        "a2ui_action_trigger": {"surfaceId": "workspace", "name": "start_obligation_analysis"},
        "a2ui_surface_state": {"workspace": {"dataModel": {"doc": "doc-1"}}},
    }
    raw = await _run_tool(state=state)
    result = json.loads(raw)
    assert result.get("needs_assumptions") is True  # refused → elicit


async def test_malformed_state_assumptions_resurface_elicitation():
    """Bad values from the form (e.g. a COD date before the start date) re-surface
    the elicitation form (NEVER-SILENT), not a dead-end."""
    surface_id = "obligation_elicitation:doc-1"
    state = {
        "a2ui_action_trigger": {"surfaceId": surface_id, "name": "start_obligation_analysis"},
        "a2ui_surface_state": {surface_id: {"dataModel": _leap_assumptions(cod_date="2025-01-01")}},
    }
    raw = await _run_tool(state=state)
    result = json.loads(raw)
    assert "error" in result
    assert result.get("needs_assumptions") is True
    assert result.get("elicitation")


@_engine_available
@pytest.mark.slow
def test_assumption_built_payload_accepted_by_real_engine():
    """THE engine-validation gate: the build-from-assumptions payload is
    accepted by the real v0.29.0 ailang engine and produces a settlement
    (net line), with the resolved delay-LD (penPerDay=15000) visibly applied."""
    p = build_payload_from_assumptions("demo-leap", _leap_assumptions())
    report = _run_engine_cli(p.engine_payload())
    assert "error:" not in report, f"engine rejected assumption-built payload:\n{report}"
    assert "net:" in report, f"no settlement line:\n{report}"
    assert "COD effective: deadline=546 price=250000" in report
