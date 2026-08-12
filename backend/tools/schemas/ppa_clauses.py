"""Structured PPA clause schema (v6.4.0 ONE-DEMO M2).

Single Pydantic schema drives:
  1. extract_ppa_clauses(doc_id) -> PpaClauses  — single-doc extraction
  2. compare_ppa_contracts(left, right) -> PpaComparison — pairwise diff
  3. ClauseExtractionCard (frontend) — A2UI rendering
  4. KeyDifferencesPanel (frontend) — A2UI rendering on the workbench

Define once, reuse four ways. Every clause carries a `block_id` citation
(aitana://doc/{docId}/block/{blockId}) and a confidence band so the UI
can render "earned trust" (Axiom #2) without re-prompting.

Why a typed schema over free-form prose:
  - Axiom #2 EARNED TRUST: provenance is mandatory not optional
  - Axiom #6 PROTOCOL OVER CUSTOM: ADK structured-output mode + Pydantic
    (already used by structured_extraction.py and skill_config.py)
  - Axiom #4 RIGHT MODEL: extraction runs once, rendering deterministic
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Confidence band intentionally three-bucket — high/medium/low maps 1:1 to
# UI badge colours and avoids the false precision of a numeric score.
Confidence = Literal["high", "medium", "low"]
Severity = Literal["material", "moderate", "cosmetic"]


class ClauseExtraction(BaseModel):
    """One extracted clause with provenance.

    Every populated field anchors to a single AILANG block in the source
    document. `value` is the normalised extracted value; `raw_excerpt` is
    the verbatim contract text the value came from (kept so the UI can
    show source-of-truth on demand and the user can verify without
    re-opening the document).
    """

    clause_name: str = Field(description="snake_case key, e.g. 'settlement_type'")
    display_name: str = Field(description='Human-readable, e.g. "Settlement Type"')
    value: str | None = Field(
        default=None,
        description="Normalised extracted value (e.g. 'PaP', 'Fixed €45/MWh CPI-indexed'). None if clause absent.",
    )
    raw_excerpt: str = Field(
        default="",
        description="Verbatim contract text the value came from. Empty if value is None.",
    )
    block_id: str = Field(
        default="",
        description="AILANG block id for the source span. Empty if value is None.",
    )
    confidence: Confidence = Field(default="low")
    notes: str | None = Field(
        default=None,
        description="Extractor caveats — e.g. 'definition references Annex A'.",
    )

    model_config = ConfigDict(populate_by_name=True)


class PpaClauses(BaseModel):
    """Standard structured output for a single PPA contract.

    Field order follows the chronological order of a typical PPA negotiation
    so the Clause Extraction Card reads like a checklist. Every field is
    `ClauseExtraction | None` — None means the extractor could not locate
    the clause (not that the contract lacks it). The notes field on each
    populated extraction is where the model surfaces uncertainty.
    """

    doc_id: str
    counterparty_buyer: ClauseExtraction | None = None
    counterparty_seller: ClauseExtraction | None = None
    volume_mwh: ClauseExtraction | None = None
    term_years: ClauseExtraction | None = None
    settlement_type: ClauseExtraction | None = Field(
        default=None,
        description="PaP | PaN | BL (Pay-as-Produced / Pay-as-Nominated / Baseload)",
    )
    contract_form: ClauseExtraction | None = Field(
        default=None,
        description="Physical | Financial-FS | Financial-PS",
    )
    price_formula: ClauseExtraction | None = None
    rtm_provider: ClauseExtraction | None = Field(
        default=None,
        description="Who provides route-to-market — Seller, Buyer, or third party",
    )
    force_majeure: ClauseExtraction | None = None
    change_of_law: ClauseExtraction | None = None
    termination: ClauseExtraction | None = None
    governing_law: ClauseExtraction | None = None
    other_clauses: list[ClauseExtraction] = Field(
        default_factory=list,
        description="Contract-specific clauses outside the standard 12-field shape.",
    )
    # Truncation metadata is set by the extraction TOOL after the model
    # returns (see extract_ppa_clauses._apply_other_clauses_cap), never by
    # the model itself — a long contract can surface dozens of bespoke
    # clauses and we cap `other_clauses` to keep the payload bounded. The
    # cap is made VISIBLE here (no silent truncation): the agent sees the
    # counts in the tool result and the UI renders a "showing N of M" note.
    other_clauses_total: int = Field(
        default=0,
        description="Total non-standard clauses found before the cap. Tool-set; leave 0.",
    )
    other_clauses_truncated: bool = Field(
        default=False,
        description="True when other_clauses was capped below total. Tool-set; leave false.",
    )

    model_config = ConfigDict(populate_by_name=True)


class ClauseDifference(BaseModel):
    """One diff row in a PpaComparison."""

    clause_name: str = Field(description="snake_case key matching a PpaClauses field")
    display_name: str = Field(description="Human-readable clause label")
    severity: Severity = Field(
        description=(
            "material — changes commercial economics or risk allocation. "
            "moderate — changes process / who-does-what but not the deal. "
            "cosmetic — wording/definition only, no functional change."
        )
    )
    left_value: str | None = None
    right_value: str | None = None
    left_block_id: str = Field(default="")
    right_block_id: str = Field(default="")
    commercial_implication: str = Field(
        description="One-sentence agent-generated explanation of why this diff matters."
    )

    model_config = ConfigDict(populate_by_name=True)


class PpaDifferences(BaseModel):
    """Model-facing response schema for the comparison LLM call.

    The comparison model emits ONLY the diff rows. The full PpaComparison
    (which embeds both source PpaClauses) is assembled in Python from the
    left/right extractions we already hold — the model never re-echoes them.

    Why this exists separately from PpaComparison: Vertex/Gemini compiles a
    `response_schema` into a constrained-decoding state machine. Two nested
    PpaClauses (24+ ClauseExtraction subschemas plus unbounded other_clauses
    arrays) overflow that with `400 INVALID_ARGUMENT: too many states for
    serving`. This flat list of ClauseDifference stays well under the limit.
    """

    differences: list[ClauseDifference] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class PpaComparison(BaseModel):
    """Output of compare_ppa_contracts — diff over two PpaClauses extractions.

    Assembled in Python (NOT via structured output): `left` and `right` come
    from the extraction calls; `differences` from the slim PpaDifferences
    response. Keep this in sync with PpaDifferences' `differences` field.
    """

    left: PpaClauses
    right: PpaClauses
    differences: list[ClauseDifference] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)
