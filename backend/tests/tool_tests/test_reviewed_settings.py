"""Unit tests for the reviewed-settings overlay (PPA-OBLIGATION reviewed-defaults).

Resolves design open question 2: policy knobs + obligation prices can be
reviewed ONCE by a human, provenance-tracked. ``apply_reviewed_settings``
overlays reviewer-chosen values onto a base payload and flips the provenance
of exactly the reviewed knobs/prices to ``"reviewed"`` -- leaving everything
else at its original source. Loud-failure discipline throughout: an unknown
knob or obligation id is rejected, never silently accepted.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.schemas.ppa_obligations import (
    POLICY_KNOBS,
    PpaObligationPayload,
    ReviewedSettings,
    apply_reviewed_settings,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "ppa_obligations"
_REVIEWED_FIXTURE = _FIXTURES / "payload.democorp.reviewed.json"


def _policy_dict(**overrides) -> dict:
    base = {"penPerDay": 500, "penCap": 25000, "payWithin": 30, "cureDays": 30, "ratePct": 1, "ratePeriod": 30}
    base.update(overrides)
    return base


def _base_dict(**overrides) -> dict:
    """A minimal valid all-default PpaObligationPayload dict (democorp shape)."""
    base = {
        "doc_id": "doc-1",
        "effectiveDate": "2024-01-01",
        "effective_date_source": "provided",
        "obligations": [{"id": "COD", "deadline": 731, "price": 0}],
        "events": [],
        "policy": _policy_dict(),
        "policy_sources": dict.fromkeys(POLICY_KNOBS, "default"),
        "unmapped": [],
        "mapped_clauses": [],
    }
    base.update(overrides)
    return base


# --- provenance literal ------------------------------------------------------


def test_reviewed_is_a_valid_policy_source():
    payload = PpaObligationPayload.model_validate(
        _base_dict(policy_sources={**dict.fromkeys(POLICY_KNOBS, "default"), "penPerDay": "reviewed"})
    )
    assert payload.policy_sources["penPerDay"] == "reviewed"


def test_bogus_policy_source_still_rejected():
    with pytest.raises(ValidationError):
        PpaObligationPayload.model_validate(
            _base_dict(policy_sources={**dict.fromkeys(POLICY_KNOBS, "default"), "penPerDay": "invented"})
        )


def test_price_sources_keys_must_reference_known_obligations():
    with pytest.raises(ValidationError, match="price_sources"):
        PpaObligationPayload.model_validate(_base_dict(price_sources={"Q9": "reviewed"}))


def test_price_sources_accepts_known_obligation():
    payload = PpaObligationPayload.model_validate(_base_dict(price_sources={"COD": "reviewed"}))
    assert payload.price_sources == {"COD": "reviewed"}


# --- apply_reviewed_settings: policy knobs -----------------------------------


def test_override_flips_only_named_knob_provenance():
    base = PpaObligationPayload.model_validate(_base_dict())
    out = apply_reviewed_settings(base, ReviewedSettings(policy={"penPerDay": 1200, "penCap": 60000}))

    assert out.policy.penPerDay == 1200
    assert out.policy.penCap == 60000
    assert out.policy_sources["penPerDay"] == "reviewed"
    assert out.policy_sources["penCap"] == "reviewed"
    # Un-reviewed knobs untouched — value AND provenance.
    assert out.policy.payWithin == 30
    assert out.policy_sources["payWithin"] == "default"
    assert out.policy_sources["cureDays"] == "default"


def test_extracted_knob_survives_when_not_reviewed():
    base = PpaObligationPayload.model_validate(
        _base_dict(policy_sources={**dict.fromkeys(POLICY_KNOBS, "default"), "cureDays": "extracted"})
    )
    out = apply_reviewed_settings(base, ReviewedSettings(policy={"penPerDay": 999}))
    # cureDays was extracted from the contract — a reviewer touching a DIFFERENT
    # knob must not clobber its provenance.
    assert out.policy_sources["cureDays"] == "extracted"
    assert out.policy_sources["penPerDay"] == "reviewed"


def test_empty_overlay_is_identity():
    base = PpaObligationPayload.model_validate(_base_dict())
    out = apply_reviewed_settings(base, ReviewedSettings())
    assert out.model_dump(mode="json") == base.model_dump(mode="json")


def test_unknown_knob_rejected_loudly():
    base = PpaObligationPayload.model_validate(_base_dict())
    with pytest.raises(ValueError, match="unknown"):
        apply_reviewed_settings(base, ReviewedSettings(policy={"bogusKnob": 5}))


# --- apply_reviewed_settings: obligation prices ------------------------------


def test_obligation_price_override_flips_price_provenance():
    base = PpaObligationPayload.model_validate(_base_dict())
    out = apply_reviewed_settings(base, ReviewedSettings(obligation_prices={"COD": 5000000}))
    assert out.obligations[0].price == 5000000
    assert out.price_sources["COD"] == "reviewed"


def test_unknown_obligation_id_rejected_loudly():
    base = PpaObligationPayload.model_validate(_base_dict())
    with pytest.raises(ValueError, match="unknown obligation"):
        apply_reviewed_settings(base, ReviewedSettings(obligation_prices={"Q9": 100}))


# --- apply_reviewed_settings: metadata + combined ----------------------------


def test_reviewer_metadata_carried_onto_payload():
    base = PpaObligationPayload.model_validate(_base_dict())
    out = apply_reviewed_settings(
        base,
        ReviewedSettings(
            policy={"penPerDay": 1200},
            reviewed_by="owner@yourcompany.com",
            reviewed_at="2026-07-11",
        ),
    )
    assert out.reviewed_by == "owner@yourcompany.com"
    assert out.reviewed_at == "2026-07-11"


def test_combined_overlay_policy_and_price():
    base = PpaObligationPayload.model_validate(_base_dict())
    out = apply_reviewed_settings(
        base,
        ReviewedSettings(
            policy={"penPerDay": 1200, "penCap": 60000, "cureDays": 20},
            obligation_prices={"COD": 5000000},
        ),
    )
    # engine payload still emits exactly the api.ail subset (no provenance leak).
    engine = out.engine_payload()
    assert set(engine) == {"obligations", "events", "policy"}
    assert engine["obligations"][0]["price"] == 5000000
    assert engine["policy"]["cureDays"] == 20
    assert out.price_sources["COD"] == "reviewed"


def test_apply_accepts_plain_dicts():
    out = apply_reviewed_settings(_base_dict(), {"policy": {"penPerDay": 700}})
    assert out.policy.penPerDay == 700
    assert out.policy_sources["penPerDay"] == "reviewed"


def test_reviewed_settings_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ReviewedSettings.model_validate({"policy": {}, "bogus": 1})


def test_reviewed_at_may_be_iso_date_string():
    # The overlay carries reviewed_at as an opaque ISO string (audit stamp),
    # not a parsed date — keep it flexible for the artefact/host round-trip.
    ov = ReviewedSettings(reviewed_at=dt.date(2026, 7, 11).isoformat())
    assert ov.reviewed_at == "2026-07-11"


# --- DemoCorp reviewed demo fixture -------------------------------------


@pytest.mark.skipif(not _REVIEWED_FIXTURE.exists(), reason="confidential reviewed fixture not present")
def test_democorp_reviewed_fixture_is_schema_valid_and_provenance_tracked():
    """The flagship demo fixture: a partial human review of the one live corpus
    payload. Pins the reviewer-chosen values + their provenance so a
    regeneration that silently changes them is caught."""
    payload = PpaObligationPayload.model_validate_json(_REVIEWED_FIXTURE.read_text())

    # Reviewed knobs — value + "reviewed" provenance (REVIEWER-CHOSEN, see README).
    assert payload.policy.penPerDay == 5000
    assert payload.policy.penCap == 5000000
    assert payload.policy.payWithin == 45
    assert payload.policy.cureDays == 28
    for knob in ("penPerDay", "penCap", "payWithin", "cureDays"):
        assert payload.policy_sources[knob] == "reviewed", knob
    # ratePct / ratePeriod deliberately LEFT at default (partial review).
    assert payload.policy_sources["ratePct"] == "default"
    assert payload.policy_sources["ratePeriod"] == "default"

    # COD milestone price reviewed in (base contract attaches no fixed amount).
    assert payload.obligations[0].id == "COD"
    assert payload.obligations[0].price == 5000000
    assert payload.price_sources["COD"] == "reviewed"

    # Audit stamps present.
    assert payload.reviewed_by == "owner@yourcompany.com"
    assert payload.reviewed_at == "2026-07-11"

    # No invented execution history — reviewing knobs never adds events.
    assert payload.events == []


@pytest.mark.skipif(not _REVIEWED_FIXTURE.exists(), reason="confidential reviewed fixture not present")
def test_democorp_reviewed_fixture_reproducible_from_base_plus_overlay():
    """The reviewed fixture is exactly the base payload + the documented overlay
    (the human review is a pure, replayable transform, not a hand-edit)."""
    base = _FIXTURES / "payload.democorp.json"
    if not base.exists():
        pytest.skip("confidential base fixture not present")
    overlay = ReviewedSettings(
        policy={"penPerDay": 5000, "penCap": 5000000, "payWithin": 45, "cureDays": 28},
        obligation_prices={"COD": 5000000},
        reviewed_by="owner@yourcompany.com",
        reviewed_at="2026-07-11",
    )
    rebuilt = apply_reviewed_settings(json.loads(base.read_text()), overlay)
    committed = PpaObligationPayload.model_validate_json(_REVIEWED_FIXTURE.read_text())
    assert rebuilt.model_dump(mode="json") == committed.model_dump(mode="json")
