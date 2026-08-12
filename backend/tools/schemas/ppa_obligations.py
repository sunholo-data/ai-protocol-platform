"""Wire schema for the sunholo/deontic obligation engine (v6.7.0 PPA-OBLIGATION M2).

Mirrors the `api.ail` boundary EXACTLY (sunholo/deontic@0.1.2):

    analyzeContract(obligations, events, policy) -> [string]

    obligations: [{id, deadline: int, price: int}]
    events:      [{kind, day: int, ref: str, amt: int, hi: int}]
    policy:      {penPerDay, penCap, payWithin, cureDays, ratePct, ratePeriod}

Two layers, cleanly separated:

  1. Engine-facing wire subset -- ``WireObligation`` / ``WireEvent`` /
     ``WirePolicy``. Only these reach the engine
     (``PpaObligationPayload.engine_payload()`` emits exactly this shape and
     nothing else).
  2. Tool/artefact envelope -- ``PpaObligationPayload`` adds ``effectiveDate``
     (the calendar anchor: day 0 == effective date, so the UI can render
     calendar dates while the engine stays pure), the ``unmapped`` clause
     list (no-silent-drops discipline), and per-knob policy provenance
     (``policy_sources``: "extracted" | "default" | "reviewed").

Reviewed-settings overlay (design open question 2) -- ``ReviewedSettings`` is a
human-in-the-loop overlay: a reviewer, having read the mapper's ``unmapped``
reasons, chooses defensible values for the knobs/prices the contract states in
an inexpressible form (Spot-Price penalty formulas, business-day cure windows,
pay-as-forecast prices). ``apply_reviewed_settings`` overlays those onto a base
payload and flips the provenance of exactly the reviewed knobs/prices to
``"reviewed"`` -- so the artefact can always show which numbers came from the
contract ("extracted"), which are the engine baseline ("default"), and which a
human chose ("reviewed"). Same loud-failure discipline: an unknown knob or
obligation id is rejected, never silently accepted.

Validation philosophy -- LOUD failure over silent guessing. Every constraint
below is derived from the engine's pinned semantics (deontic README /
engine.ail / settle.ail Z3 contracts), not invented:

  * ``day >= 0`` / ``deadline >= 0`` -- the engine's association lists use
    ``-1`` as the "not delivered / not paid" sentinel (types.ail ``aGet``;
    engine.ail checks ``aGet(s.delivered, id) < 0`` and ``dDay >= 0``), so a
    negative day offset is INEXPRESSIBLE: a delivery on day -1 would read as
    "never delivered". Events before the effective date must be rejected
    upstream, never encoded.
  * ``price >= 0``, all policy knobs ``>= 0``, ``ratePeriod > 0`` -- the Z3
    ``requires`` clauses on settle.ail's money functions (``interestFor``
    requires ``price >= 0 && ratePct >= 0 && period > 0``; ``penaltyFor``
    requires ``perDay >= 0 && cap >= 0``). A payload violating them voids
    the proved-arithmetic guarantee.
  * notice/waive ``ref`` must be ``<obligationId>-delivery`` or
    ``<obligationId>-payment`` for a KNOWN obligation -- engine.ail derives
    the obligation id by suffix-stripping (``isPaymentBreach`` /
    ``obligationOf``); any other shape silently never matches a breach.
  * force_majeure carries its window in ``amt``/``hi`` (start/end day,
    INCLUSIVE; span = hi - amt + 1) and by design-doc convention
    ``day == amt``. ``hi >= amt`` or the window is negative-length.
  * events must be in non-decreasing day order -- the engine "does not sort;
    callers supply ordered events" (types.ail header). Same-day relative
    order is preserved and significant (notices ground termination in
    ARRIVAL order).
  * kinds outside the seven-member vocabulary are rejected here even though
    ``api.ail`` documents "unknown kinds are ignored" -- an ignored event is
    a silently-changed settlement number, which is exactly the failure mode
    this tool must never exhibit.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventKind = Literal[
    "deliver",
    "pay",
    "amend_price",
    "force_majeure",
    "notice",
    "waive",
    "terminate",
]

# Provenance of a policy knob / obligation price:
#   "extracted" -- stated in the contract (LLM found a verbatim value)
#   "default"   -- engine baseline (contract stated nothing expressible)
#   "reviewed"  -- a human chose it (reviewed-once overlay; open question 2)
PolicySource = Literal["extracted", "default", "reviewed"]
PriceSource = PolicySource

# Breach-id shape engine.ail suffix-strips (isPaymentBreach / obligationOf).
_BREACH_REF_RE = re.compile(r"^(?P<obligation>.+)-(?P<what>delivery|payment)$")

# The six policy knobs, in api.ail order. policy_sources must cover exactly these.
POLICY_KNOBS: tuple[str, ...] = ("penPerDay", "penCap", "payWithin", "cureDays", "ratePct", "ratePeriod")


class WireObligation(BaseModel):
    """One obligation triple -- initState(id, deadline, price)."""

    id: str = Field(min_length=1, description="Obligation id, e.g. 'COD', 'Q1'. Free-form, unique per payload.")
    deadline: int = Field(ge=0, description="Deadline as day offset from the effective date (day 0).")
    price: int = Field(ge=0, description="Amount due on delivery, integer currency units (settle.ail requires >= 0).")

    model_config = ConfigDict(extra="forbid")


class WireEvent(BaseModel):
    """One timeline event in api.ail's flat record shape.

    Fields are used per kind; unused fields MUST be zero / empty -- a nonzero
    ``amt`` on a ``deliver`` event means the producer misplaced a price, and
    we reject rather than let the engine silently ignore it.
    """

    kind: EventKind
    day: int = Field(ge=0, description="Day offset from the effective date. Never negative (engine -1 sentinel).")
    ref: str = Field(default="", description="Obligation id / breach id / party, per kind.")
    amt: int = Field(default=0, ge=0, description="amend_price: new price. force_majeure: window start day.")
    hi: int = Field(default=0, ge=0, description="force_majeure only: window end day (inclusive).")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_per_kind(self) -> WireEvent:
        k = self.kind
        if k in ("deliver", "pay"):
            if not self.ref:
                raise ValueError(f"{k} event on day {self.day} needs ref = an obligation id")
            if self.amt != 0 or self.hi != 0:
                raise ValueError(f"{k} event on day {self.day}: amt/hi must be 0 (got amt={self.amt}, hi={self.hi})")
        elif k == "amend_price":
            if not self.ref:
                raise ValueError(f"amend_price event on day {self.day} needs ref = an obligation id")
            if self.hi != 0:
                raise ValueError(f"amend_price event on day {self.day}: hi must be 0 (got {self.hi})")
        elif k == "force_majeure":
            if self.ref != "":
                raise ValueError(f"force_majeure event on day {self.day}: ref must be empty (got {self.ref!r})")
            if self.hi < self.amt:
                raise ValueError(
                    f"force_majeure window on day {self.day} has hi={self.hi} < amt={self.amt} "
                    "(window end before start -- inclusive window must satisfy hi >= amt)"
                )
            if self.amt != self.day:
                raise ValueError(
                    f"force_majeure event day ({self.day}) must equal amt ({self.amt}) -- by wire convention "
                    "the event day IS the window start (design doc v6.7.0 wire examples)"
                )
        elif k in ("notice", "waive"):
            if not _BREACH_REF_RE.match(self.ref):
                raise ValueError(
                    f"{k} event on day {self.day}: ref {self.ref!r} must match "
                    "'<obligationId>-delivery' or '<obligationId>-payment' "
                    "(engine.ail suffix-strips the breach id; any other shape never matches)"
                )
            if self.amt != 0 or self.hi != 0:
                raise ValueError(f"{k} event on day {self.day}: amt/hi must be 0 (got amt={self.amt}, hi={self.hi})")
        elif k == "terminate":
            if not self.ref:
                raise ValueError(f"terminate event on day {self.day} needs ref = the terminating party")
            if self.amt != 0 or self.hi != 0:
                raise ValueError(
                    f"terminate event on day {self.day}: amt/hi must be 0 (got amt={self.amt}, hi={self.hi})"
                )
        return self


class WirePolicy(BaseModel):
    """Policy knobs. Bounds mirror settle.ail's Z3 ``requires`` clauses --
    violating them voids the proved-arithmetic guarantee."""

    penPerDay: int = Field(ge=0, description="Delivery delay penalty per day late.")
    penCap: int = Field(ge=0, description="Penalty cap per obligation.")
    payWithin: int = Field(ge=0, description="Payment due = delivery day + payWithin.")
    cureDays: int = Field(ge=0, description="Cure window after notice, INCLUSIVE of notice day + cureDays.")
    ratePct: int = Field(ge=0, description="Interest: ratePct percent of price per FULL ratePeriod days late.")
    ratePeriod: int = Field(gt=0, description="Interest period in days (floor division; Z3 requires > 0).")

    model_config = ConfigDict(extra="forbid")


# Engine defaults used when a contract does not state a knob. Values are the
# deontic package's canonical PPA policy (ppa_demo.ail / design doc v6.7.0
# wire example) -- the reviewed-once baseline, NOT extracted values. Every
# defaulted knob is recorded as "default" in PpaObligationPayload.policy_sources
# so a reviewer can see exactly which numbers came from the contract.
DEFAULT_POLICY = WirePolicy(penPerDay=500, penCap=25000, payWithin=30, cureDays=30, ratePct=1, ratePeriod=30)


class UnmappedClause(BaseModel):
    """One clause the mapper could not express in the wire format -- surfaced,
    never dropped (design doc: the visible escape hatch)."""

    clause: str = Field(min_length=1, description="Clause name from the extraction (e.g. 'governing_law').")
    reason: str = Field(min_length=1, description="Why it could not be mapped -- shown verbatim in the artefact.")

    model_config = ConfigDict(extra="forbid")


class PpaObligationPayload(BaseModel):
    """The tool's output envelope: engine wire subset + artefact-payload fields.

    ``effectiveDate``/``unmapped``/``policy_sources``/``mapped_clauses`` are
    artefact-payload fields -- they are NOT sent to the engine. Use
    ``engine_payload()`` for the engine-facing subset.
    """

    doc_id: str = Field(min_length=1)
    effectiveDate: _dt.date = Field(
        description="Contract effective date -- the day-0 anchor for every integer day offset."
    )
    # Template-form PPAs (blank "[dd MM yyyy]" start dates) cannot anchor a
    # timeline from the document alone; the tool then refuses until the user
    # confirms a date, which the agent passes back in via the tool's
    # effective_date argument. Provenance is recorded so a user-assumed
    # anchor is never presented as a contract fact.
    effective_date_source: Literal["extracted", "provided"] = Field(
        default="extracted",
        description="'extracted' (stated in the document) or 'provided' (user-confirmed anchor from the caller).",
    )
    obligations: list[WireObligation] = Field(min_length=1)
    events: list[WireEvent] = Field(default_factory=list)
    policy: WirePolicy
    policy_sources: dict[str, PolicySource] = Field(
        description="Per-knob provenance: 'extracted' (stated in the contract), 'default' "
        "(engine baseline), or 'reviewed' (a human chose it via the reviewed-once overlay)."
    )
    price_sources: dict[str, PriceSource] = Field(
        default_factory=dict,
        description="Per-obligation price provenance, keyed by obligation id. Optional: an "
        "absent key means the price carries no explicit provenance (treated as extracted/"
        "default per the obligation's note). A reviewed price override sets it to 'reviewed'.",
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Identity of the human who applied the reviewed-settings overlay (audit stamp).",
    )
    reviewed_at: str | None = Field(
        default=None,
        description="When the reviewed-settings overlay was applied (opaque ISO string, audit stamp).",
    )
    unmapped: list[UnmappedClause] = Field(default_factory=list)
    mapped_clauses: list[str] = Field(
        default_factory=list,
        description="Extraction clause names the mapping accounted for (coverage record).",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("effectiveDate", mode="before")
    @classmethod
    def _iso_date_only(cls, v: object) -> object:
        # Accept ISO 'YYYY-MM-DD' strings / date objects; reject datetimes so a
        # timestamp can't smuggle in timezone semantics the engine doesn't have.
        if isinstance(v, _dt.datetime):
            raise ValueError("effectiveDate must be a plain ISO date (YYYY-MM-DD), not a datetime")
        return v

    @model_validator(mode="after")
    def _validate_cross_references(self) -> PpaObligationPayload:
        ids = [o.id for o in self.obligations]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate obligation id(s): {dupes} -- engine association lists key by id")
        known = set(ids)

        for e in self.events:
            if e.kind in ("deliver", "pay", "amend_price") and e.ref not in known:
                raise ValueError(
                    f"{e.kind} event on day {e.day} references unknown obligation {e.ref!r} (known: {sorted(known)})"
                )
            if e.kind in ("notice", "waive"):
                m = _BREACH_REF_RE.match(e.ref)
                # Shape already validated per-event; here we check the obligation exists.
                if m and m.group("obligation") not in known:
                    raise ValueError(
                        f"{e.kind} event on day {e.day} references breach of unknown obligation "
                        f"{m.group('obligation')!r} (known: {sorted(known)})"
                    )

        # Engine "does not sort -- callers supply ordered events" (types.ail).
        days = [e.day for e in self.events]
        if days != sorted(days):
            raise ValueError(f"events must be in non-decreasing day order (engine does not sort); got days {days}")

        missing = [k for k in POLICY_KNOBS if k not in self.policy_sources]
        extra = [k for k in self.policy_sources if k not in POLICY_KNOBS]
        if missing or extra:
            raise ValueError(
                f"policy_sources must cover exactly the six knobs {list(POLICY_KNOBS)}; "
                f"missing={missing} unexpected={extra}"
            )

        # price_sources is optional but, when present, may only key KNOWN
        # obligations (a stray id is a mislabelled provenance record).
        stray_prices = sorted(oid for oid in self.price_sources if oid not in known)
        if stray_prices:
            raise ValueError(f"price_sources references unknown obligation(s) {stray_prices} (known: {sorted(known)})")
        return self

    def engine_payload(self) -> dict:
        """The engine-facing subset -- EXACTLY api.ail's analyzeContract arguments,
        nothing else. Envelope fields (effectiveDate, unmapped, provenance) are
        deliberately absent: the engine speaks integer days only."""
        return {
            "obligations": [o.model_dump() for o in self.obligations],
            "events": [e.model_dump() for e in self.events],
            "policy": self.policy.model_dump(),
        }


# ---------------------------------------------------------------------------
# Reviewed-settings overlay (design open question 2).
#
# A human-in-the-loop overlay applied ONCE after the automated mapping. The
# reviewer, having read the mapper's per-clause ``unmapped`` reasons, chooses
# defensible integer values for the knobs / obligation prices the contract
# states in a form the engine cannot express (Spot-Price penalty formulas,
# business-day cure windows, pay-as-forecast prices). Applying the overlay
# overrides the named knobs/prices and flips THEIR provenance to "reviewed",
# leaving every un-reviewed knob at its original source. Pure + total: no IO,
# no LLM; the same base + overlay always yields the same payload.
# ---------------------------------------------------------------------------


class ReviewedSettings(BaseModel):
    """A reviewer's chosen overrides for policy knobs and/or obligation prices.

    Every field is optional; an all-empty overlay applied to a payload is the
    identity. ``policy`` keys MUST be members of ``POLICY_KNOBS`` and
    ``obligation_prices`` keys MUST reference known obligation ids -- both are
    checked LOUDLY in ``apply_reviewed_settings`` (an unknown knob is never
    silently dropped)."""

    policy: dict[str, int] = Field(
        default_factory=dict,
        description="Reviewer-chosen policy knob values, keyed by knob name (POLICY_KNOBS).",
    )
    obligation_prices: dict[str, int] = Field(
        default_factory=dict,
        description="Reviewer-chosen obligation prices, keyed by obligation id.",
    )
    reviewed_by: str | None = Field(default=None, description="Who reviewed (audit stamp).")
    reviewed_at: str | None = Field(default=None, description="When reviewed (opaque ISO string).")

    model_config = ConfigDict(extra="forbid")


def apply_reviewed_settings(
    base: PpaObligationPayload | dict,
    overlay: ReviewedSettings | dict,
) -> PpaObligationPayload:
    """Overlay a reviewer's chosen knobs/prices onto a base payload.

    Overrides the named policy knobs and obligation prices, flips THEIR
    provenance to "reviewed", and stamps ``reviewed_by``/``reviewed_at`` when
    the overlay carries them. Un-reviewed knobs/prices keep their original value
    AND provenance. An empty overlay is the identity.

    Loud failure: an ``overlay.policy`` key outside ``POLICY_KNOBS`` or an
    ``overlay.obligation_prices`` key that is not a known obligation id raises
    ``ValueError`` -- a reviewed value is never silently attached to a phantom
    knob/obligation.

    Args:
        base: the base payload (validated ``PpaObligationPayload`` or its dict).
        overlay: the reviewer's overrides (``ReviewedSettings`` or its dict).

    Returns:
        A NEW validated ``PpaObligationPayload`` with the overrides applied.
    """
    base_payload = base if isinstance(base, PpaObligationPayload) else PpaObligationPayload.model_validate(base)
    ov = overlay if isinstance(overlay, ReviewedSettings) else ReviewedSettings.model_validate(overlay)

    data = base_payload.model_dump(mode="json")

    # --- policy knobs ---
    unknown_knobs = sorted(k for k in ov.policy if k not in POLICY_KNOBS)
    if unknown_knobs:
        raise ValueError(
            f"reviewed_settings.policy has unknown knob(s) {unknown_knobs}; valid knobs are {list(POLICY_KNOBS)}"
        )
    for knob, value in ov.policy.items():
        data["policy"][knob] = value
        data["policy_sources"][knob] = "reviewed"

    # --- obligation prices ---
    known_ids = {o["id"] for o in data["obligations"]}
    unknown_ids = sorted(oid for oid in ov.obligation_prices if oid not in known_ids)
    if unknown_ids:
        raise ValueError(
            f"reviewed_settings.obligation_prices references unknown obligation(s) {unknown_ids}; "
            f"known ids are {sorted(known_ids)}"
        )
    price_sources = dict(data.get("price_sources") or {})
    for oid, price in ov.obligation_prices.items():
        for o in data["obligations"]:
            if o["id"] == oid:
                o["price"] = price
        price_sources[oid] = "reviewed"
    if price_sources:
        data["price_sources"] = price_sources

    # --- audit stamps ---
    if ov.reviewed_by is not None:
        data["reviewed_by"] = ov.reviewed_by
    if ov.reviewed_at is not None:
        data["reviewed_at"] = ov.reviewed_at

    return PpaObligationPayload.model_validate(data)


# ---------------------------------------------------------------------------
# Elicitation envelope + build-from-assumptions (v6.7.0 OBLIGATION-ELICITATION
# 7.8 M1 — THE DEMO UNBLOCK).
#
# The ONE PPA corpus is TEMPLATE contracts: every price / date / amount is a
# ``[●]`` placeholder, so ``map_ppa_obligations`` correctly REFUSES to invent a
# settlement (a wrong LD/settlement number is trust-ending). But the refusal's
# ``unmapped`` list proves the AI extraction WORKED — it found the full
# structure AND the per-MW LD *formulas* (Google LEAP: delay-LD "EUR 150/MW of
# Contract Capacity per day", COD-flex LD "EUR 200/MW…"). It refuses ONLY
# because the concrete VALUES are placeholders.
#
# So instead of a prose dead-end, the mapper returns a STRUCTURED elicitation
# envelope (below): a typed field per placeholder the mapper identified, each
# with the source clause + the formula it feeds and the engine knobs it
# ``resolves``. The user fills a handful of real blanks in an A2UI chat form;
# ``build_payload_from_assumptions`` (in map_ppa_obligations.py) then RESOLVES
# the parsed formulas deterministically in Python — ``penPerDay = 150 x
# capacity`` etc., LLM arithmetic is exactly the wrong-but-plausible failure
# mode this design eliminates — and constructs a valid ``PpaObligationPayload``.
#
# Every supplied/derived value is provenance-marked as an ASSUMPTION
# (``effective_date_source="provided"``, policy/price sources ``"reviewed"``),
# NEVER ``"extracted"`` — the build-from-assumptions path cannot present a
# user's guess as a contract fact.
# ---------------------------------------------------------------------------

# The two per-MW LD rates the mapper parses out of the standard PPA COD clause.
# These are the FORMULAS the AI already extracted (they float per-MW, which is
# why the engine's fixed integer knobs can't hold them until Contract Capacity
# is supplied). ONE Contract-Capacity input resolves the delay-LD into the
# engine's penPerDay knob (an EXACT semantic match: EUR/MW/day → per-day delay
# damages). The COD-flexibility LD is ALSO EUR/MW/day — a daily rate, NOT a
# total cap — so it is DELIBERATELY NOT written into penCap (or any knob it does
# not semantically match): conflating a daily rate with a cap is exactly the
# wrong-but-plausible, trust-ending number this design refuses. It is surfaced
# as a DISCLOSED capacity-derived assumption (form help + resolves list) so the
# user sees the value without it silently corrupting the settlement.
DELAY_LD_EUR_PER_MW_DAY = 150  # delay-LD: EUR 150 / MW of Contract Capacity / day → penPerDay (engine knob)
COD_FLEX_LD_EUR_PER_MW_DAY = 200  # COD-flexibility LD: EUR 200 / MW / day — DISCLOSED value, NOT an engine knob

# "thirty Business Days" → a confirmable calendar-day approximation (5-day
# weeks: 30 x 7 / 5 = 42 calendar days). Exposed as an editable field so the
# business-day→calendar assumption is visible and overridable, never silent.
_BUSINESS_WEEK_DAYS = 5
_CALENDAR_WEEK_DAYS = 7


def business_days_to_calendar(business_days: int) -> int:
    """Approximate a business-day count as calendar days (5-day weeks).

    Pure + deterministic. 30 business days → 42 calendar days. The result is
    an ASSUMPTION surfaced in the elicitation form for the user to confirm or
    override — the engine speaks calendar days only.
    """
    return -(-business_days * _CALENDAR_WEEK_DAYS // _BUSINESS_WEEK_DAYS)  # ceil division


# Field type vocabulary for M1 (minimum viable to COMPLETE a settlement). The
# A2UI renderer maps date→DateTimeInput, number→TextField(numeric regexp).
ElicitationFieldType = Literal["date", "number"]


class ElicitationField(BaseModel):
    """One typed blank the user must supply to complete an obligation analysis.

    Driven by what the mapper extracted (A0): ``help`` names the source clause
    AND the formula the value feeds; ``resolves`` lists the engine knob(s) this
    single input populates (so the UI can show that Contract Capacity resolves
    both delay-LD and COD-flex LD). NOT prose — a machine-checkable field the
    A2UI form and ``build_payload_from_assumptions`` both key off ``name``.
    """

    name: str = Field(min_length=1, description="dataModel key + assumptions key (snake_case). Closed loop.")
    type: ElicitationFieldType = Field(description="'date' (→DateTimeInput) or 'number' (→numeric TextField).")
    label: str = Field(min_length=1, description="Human field label.")
    help: str = Field(default="", description="Source clause + the formula/knob this value feeds.")
    default: str | int | None = Field(default=None, description="Prefilled engine baseline where sensible; else null.")
    resolves: list[str] = Field(
        default_factory=list,
        description="Engine knob(s)/derivation(s) this one input populates (e.g. penPerDay=150xcapacity).",
    )
    required: bool = Field(default=False, description="True = cannot complete a settlement without it.")
    unit: str = Field(default="", description="Display unit, e.g. 'MW', 'EUR', 'days'.")

    model_config = ConfigDict(extra="forbid")


class ElicitationEnvelope(BaseModel):
    """The structured refusal→elicit contract a tool returns instead of a prose
    dead-end. Rendered as an A2UI chat form; its ``action`` re-runs the tool
    with the collected values."""

    action: str = Field(min_length=1, description="Surface action the submit button fires (re-run trigger).")
    doc_id: str = Field(min_length=1, description="Document identity the re-run carries back.")
    reason: str = Field(default="", description="Why elicitation is needed (the refusal headline).")
    fields: list[ElicitationField] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


# The obligation-analysis field set: the MINIMUM viable blanks to complete a
# settlement (COD obligation + Contract Capacity + policy). ``name`` values are
# the dataModel/assumptions keys — the closed loop shared by the form binding
# and build_payload_from_assumptions.
START_OBLIGATION_ACTION = "start_obligation_analysis"

_ELICITATION_FIELDS: tuple[ElicitationField, ...] = (
    ElicitationField(
        name="effective_date",
        type="date",
        label="Contract effective / start date",
        help=(
            "The contract's effective/commencement date. Anchors day 0 — every deadline is measured "
            "from it. Template PPAs leave this a [●] placeholder, so it cannot be read from the document."
        ),
        resolves=["effectiveDate (day-0 anchor)"],
        required=True,
    ),
    ElicitationField(
        name="cod_date",
        type="date",
        label="Guaranteed Commercial Operation Date (COD)",
        help=(
            "The Guaranteed COD milestone deadline. The delay-LD is measured against a slip past this "
            "date. Stated in the contract as a relative deadline with a [●] anchor."
        ),
        resolves=["COD obligation deadline"],
        required=True,
    ),
    ElicitationField(
        name="contract_capacity_mw",
        type="number",
        label="Contract Capacity",
        unit="MW",
        help=(
            "Contract Capacity in MW. Resolves the delay-LD the contract states per-MW — 'EUR 150/MW of "
            "Contract Capacity per day' → penPerDay = 150 x capacity (the engine's per-day delay-damages "
            "knob, an exact match). It ALSO implies the COD-flexibility LD 'EUR 200/MW per day' = 200 x "
            "capacity — a DISCLOSED daily rate, surfaced as an assumption but NOT written to any engine "
            "knob (a daily rate is not a cap; conflating them would misstate the settlement)."
        ),
        resolves=[
            "penPerDay = 150 x capacity (delay-LD/day — engine knob)",
            "COD-flex LD = 200 x capacity /day (disclosed value, not a knob)",
        ],
        required=True,
    ),
    ElicitationField(
        name="contract_price",
        type="number",
        label="COD milestone price / amount",
        unit="EUR",
        help=(
            "The amount attached to the COD milestone (EUR). The contract states 'EUR [●]/MWh'; supply "
            "the milestone amount to model the payment leg. Enter 0 for a pure delivery deadline with no "
            "payment leg (delay analysis still runs)."
        ),
        resolves=["COD obligation price"],
        required=True,
    ),
    ElicitationField(
        name="pen_cap",
        type="number",
        label="Delay-damages cap per obligation",
        unit="EUR",
        default=DEFAULT_POLICY.penCap,
        help=(
            "Cap on TOTAL delay damages for one obligation (EUR) — e.g. a termination/liability cap. This "
            "is a separate stated figure, NOT derived from Contract Capacity (the COD-flex LD is a daily "
            "rate, not a cap). Engine baseline shown; override to the contract's cap."
        ),
        resolves=["penCap"],
        required=False,
    ),
    ElicitationField(
        name="pay_within_days",
        type="number",
        label="Payment window (calendar days)",
        unit="days",
        default=business_days_to_calendar(DEFAULT_POLICY.payWithin),
        help=(
            "Days after delivery a payment is due. The contract says 'thirty Business Days'; the default "
            f"{business_days_to_calendar(30)} is the calendar-day approximation (30 business days ≈ 42 "
            "calendar days) — confirm or override it."
        ),
        resolves=["payWithin"],
        required=False,
    ),
    ElicitationField(
        name="cure_days",
        type="number",
        label="Cure window after a breach notice",
        unit="days",
        default=DEFAULT_POLICY.cureDays,
        help="Days after a breach notice within which a cure avoids termination. Engine baseline shown; override if stated.",
        resolves=["cureDays"],
        required=False,
    ),
    ElicitationField(
        name="rate_pct",
        type="number",
        label="Late-payment interest (% per period)",
        unit="%",
        default=DEFAULT_POLICY.ratePct,
        help=(
            "Late-payment interest percent per period. Stands in for 'EURIBOR + 2%' as a fixed % "
            "assumption (the engine cannot track a floating index). Override to match a fixed rate."
        ),
        resolves=["ratePct"],
        required=False,
    ),
    ElicitationField(
        name="rate_period_days",
        type="number",
        label="Interest period",
        unit="days",
        default=DEFAULT_POLICY.ratePeriod,
        help="The period (days) the late-payment interest percent applies over. Engine baseline shown.",
        resolves=["ratePeriod"],
        required=False,
    ),
)

# Required field names (cannot complete a settlement without them).
REQUIRED_ASSUMPTION_FIELDS: tuple[str, ...] = tuple(f.name for f in _ELICITATION_FIELDS if f.required)


class AssumptionError(ValueError):
    """Supplied assumptions are malformed or insufficient — LOUD, never a
    silent guess. Carries a human-readable message naming the offending
    field(s). Raised by ``build_payload_from_assumptions``."""


def obligation_elicitation_fields() -> list[ElicitationField]:
    """The M1 obligation-analysis elicitation field set (fresh copies)."""
    return [f.model_copy(deep=True) for f in _ELICITATION_FIELDS]


def build_obligation_elicitation(doc_id: str, *, reason: str = "") -> ElicitationEnvelope:
    """Build the structured elicitation envelope a template-contract refusal
    returns instead of a prose dead-end.

    The field set is the minimum viable to COMPLETE a settlement (COD
    obligation + Contract Capacity resolving the LD formulas + policy knobs),
    each field carrying the source clause + formula it feeds (A0)."""
    return ElicitationEnvelope(
        action=START_OBLIGATION_ACTION,
        doc_id=doc_id,
        reason=reason,
        fields=obligation_elicitation_fields(),
    )


# ---------------------------------------------------------------------------
# LLM-facing mapping schema (calendar form).
#
# The structured-output call emits CALENDAR DATES ONLY (ISO YYYY-MM-DD) --
# never integer day offsets. All date arithmetic (calendar -> day offset,
# leap years, month lengths) is done deterministically in Python by
# map_ppa_obligations: LLM arithmetic on offsets is exactly the
# wrong-but-plausible failure mode this design exists to eliminate.
# Kept deliberately flat/small: Vertex compiles response_schema into a
# constrained-decoding state machine with a hard state budget (see
# PpaDifferences in ppa_clauses.py for the precedent).
# ---------------------------------------------------------------------------


class MappedObligation(BaseModel):
    """One obligation in calendar form, as emitted by the mapping LLM."""

    id: str = Field(description="Short obligation id, e.g. 'COD', 'Q1'.")
    deadline_date: str = Field(description="Deadline as ISO date YYYY-MM-DD.")
    price: int = Field(description="Amount due on delivery, integer currency units (no decimals/separators).")
    block_id: str = Field(default="", description="AILANG block id of the clause this obligation came from.")
    note: str = Field(default="", description="How the amount/date was derived (verbatim basis, caveats).")

    model_config = ConfigDict(populate_by_name=True)


class MappedEvent(BaseModel):
    """One timeline event in calendar form, as emitted by the mapping LLM."""

    kind: EventKind
    date: str = Field(description="Event date as ISO date YYYY-MM-DD.")
    ref: str = Field(default="", description="Obligation id / '<id>-delivery|-payment' / party, per kind.")
    amt: int = Field(default=0, description="amend_price only: the new price. 0 otherwise.")
    window_end_date: str = Field(
        default="",
        description="force_majeure only: window end as ISO date (event date is the window start).",
    )
    block_id: str = Field(default="", description="AILANG block id of the source span.")
    note: str = Field(default="", description="Derivation caveats.")

    model_config = ConfigDict(populate_by_name=True)


class MappedPolicyKnob(BaseModel):
    """One policy knob: null value = not stated in the contract (default applies)."""

    value: int | None = Field(default=None)
    excerpt: str = Field(default="", description="Verbatim contract text the value came from. Empty when null.")

    model_config = ConfigDict(populate_by_name=True)


class ObligationMapping(BaseModel):
    """Response schema for the mapping LLM call (calendar form, pre-conversion).

    Deliberately lenient -- strict validation happens on the assembled
    PpaObligationPayload after Python owns the calendar->offset conversion.
    """

    effective_date: str | None = Field(
        default=None,
        description="Contract effective date, ISO YYYY-MM-DD. null when not determinable from the document.",
    )
    obligations: list[MappedObligation] = Field(default_factory=list)
    events: list[MappedEvent] = Field(default_factory=list)
    penPerDay: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    penCap: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    payWithin: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    cureDays: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    ratePct: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    ratePeriod: MappedPolicyKnob = Field(default_factory=MappedPolicyKnob)
    mapped_clauses: list[str] = Field(default_factory=list)
    unmapped: list[UnmappedClause] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)
