"""Obligation result → A2UI transforms (tool-results-as-a2ui / 7.5, Model B — 7.6 M3).

Registers the ``map_ppa_obligations`` result→A2UI mappings against the shared
registry in ``adk.a2ui_result_render``. The tool returns ONE of two shapes and
each gets its own transform (matched by ``result_matcher``, first match wins):

  * **Success** (``PpaObligationPayload`` envelope) → an ``obligation-analysis``
    artifact. The A2UI component tree is a compact summary (obligation count,
    timeline span, policy provenance, unmapped count); the FULL wire payload
    rides an ``updateDataModel`` message so the frontend can boot the M1
    Obligation Analysis MCP-App artefact with the real scenario (the artefact
    reads the injected payload instead of its static DemoSolar demo asset).
    The payload persists via the 7.5 surface stash, so it survives resume.

  * **Refusal** (``{"error": ..., "doc_id": ..., "unmapped": [...],
    "needs_effective_date": ...}``) → an ``obligation-refusal`` panel. This is
    the M2-finding integration: 4/5 corpus PPAs are templates, so the mapper
    REFUSES rather than emit a plausible-but-wrong ``net: settled`` report.
    The refusal must never look like a crash — the structured ``unmapped``
    list the tool ships alongside its loud error message renders as a
    scannable per-clause panel (no error-prose parsing here — the envelope is
    the contract), plus the effective-date confirmation hint when the tool
    flags ``needs_effective_date`` (the template-contract re-call case).

Importing this module registers the mappings as a side effect (the composition
root — ``adk.agent`` — imports it once at startup, alongside ``a2ui_ppa_render``).
The tool keeps returning its typed JSON unchanged; these transforms run
server-side, out of the model's context, and are unit-tested for A2UI v0.9
schema validity.
"""

from __future__ import annotations

import logging
from typing import Any

from adk.a2ui_elicitation_render import _field_block
from adk.a2ui_ppa_render import _Tree
from adk.a2ui_result_render import WORKSPACE_SURFACE_ID, register

logger = logging.getLogger(__name__)

MAP_TOOL = "map_ppa_obligations"

# Artifact ``kind`` values. The frontend keys the Obligation Analysis MCP-App
# artefact mount (StaticArtefactFrame) off ``obligation-analysis``; the refusal
# renders as a plain A2UI panel like every other artifact tab.
ANALYSIS_KIND = "obligation-analysis"
REFUSAL_KIND = "obligation-refusal"
# The template-contract completion form renders as an INTERACTIVE A2UI form in
# the CHAT AREA (7.8 M1) — ``placement: "chat"`` routes it to ChatPlacementForms
# rather than a workbench tab, so the user supplies the missing values where
# they are reading.
ELICITATION_FORM_KIND = "obligation-elicitation-form"


def _is_error(result: Any) -> bool:
    """A refusal / tool error: a dict carrying an ``error`` key."""
    return isinstance(result, dict) and "error" in result


def _is_payload(result: Any) -> bool:
    """A successful mapping: a dict with no ``error`` and an obligations list."""
    return isinstance(result, dict) and "error" not in result and isinstance(result.get("obligations"), list)


def _doc_id(result: Any) -> str:
    return str(result.get("doc_id") or "").strip() if isinstance(result, dict) else ""


def _doc_identity(result: Any) -> str:
    """The doc identity the form's action carries back to the re-run — the SAME
    flat-string shape the workbench launcher fires (``{"doc": doc.identity}``).
    A2UI Action context values must be flat ``DynamicValue``s (literal or
    ``{"path": ...}``); a nested ``{"doc_id": ...}`` object fails v0.9
    validation, so ``doc`` is the raw identity string — the SKILL.md maps a
    ``gs://`` value to ``gs_url=`` and anything else to ``doc_id=``."""
    return _doc_id(result)


# --- refusal envelope --------------------------------------------------------


def _unmapped_rows(result: Any) -> list[tuple[str, str]]:
    """The refusal envelope's structured ``unmapped`` list as (clause, reason)
    rows — the tool ships it alongside the human-readable error (its
    ``MappingError`` context), so the panel never parses the prose."""
    if not isinstance(result, dict):
        return []
    rows: list[tuple[str, str]] = []
    for entry in result.get("unmapped") or []:
        if not isinstance(entry, dict):
            continue
        clause = str(entry.get("clause") or "").strip()
        if clause:
            rows.append((clause, str(entry.get("reason") or "").strip()))
    return rows


# --- transforms --------------------------------------------------------------


def obligation_refusal_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform a ``map_ppa_obligations`` refusal into an A2UI panel.

    Graceful degradation: the unmapped list with reasons renders visibly so the
    user sees WHY no settlement was computed (never a silent drop, never a
    crash). Adds the effective-date confirmation hint for the template-contract
    "no effective date" case (the mapper's ``effective_date`` re-call path).
    """
    if not _is_error(result):
        return None
    headline = str(result.get("error") or "").strip()
    unmapped = _unmapped_rows(result)

    tree = _Tree()
    children = [tree.text("Obligation analysis — not modelled", variant="h3", prefix="or-h")]
    if headline:
        children.append(tree.text(headline, prefix="or-msg"))

    if result.get("needs_effective_date"):
        children.append(
            tree.text(
                "This looks like a template contract with a blank start date. Tell the "
                "assistant the contract's effective/start date and it can re-run the "
                "analysis anchored on that date (the result records it as an assumption, "
                "not a contract fact).",
                prefix="or-hint",
            )
        )

    if unmapped:
        children.append(
            tree.text(
                f"{len(unmapped)} clause(s) could not be expressed in the verified engine's model:",
                variant="h5",
                prefix="or-uh",
            )
        )
        rows = [
            tree.row(
                [
                    tree.text(clause, variant="h5", prefix="or-cl"),
                    tree.text(reason or "—", prefix="or-rn"),
                ],
                justify="spaceBetween",
                prefix="or-row",
            )
            for clause, reason in unmapped
        ]
        children.append(tree.card(tree.list_(rows, prefix="or-list"), prefix="or-card"))

    tree.root(children)
    return tree.messages()


def _is_needs_assumptions(result: Any) -> bool:
    """A template-contract refusal that ships a structured ``elicitation``
    envelope — the case that gets an interactive A2UI form in chat (7.8 M1)."""
    return _is_error(result) and isinstance(result.get("elicitation"), dict) and bool(result.get("needs_assumptions"))


def obligation_elicitation_form_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform a ``needs_assumptions`` refusal into an INTERACTIVE A2UI form
    rendered IN THE CHAT AREA (7.8 M1 — the DEMO UNBLOCK, Model B out-of-model).

    Graceful degradation + protocols-first: a template contract whose values are
    all ``[●]`` placeholders no longer dead-ends in prose. Each field the mapper
    identified becomes a typed input (DateTimeInput for dates, numeric TextField
    for amounts) with its label + the source-clause/formula help; a submit Button
    fires ``start_obligation_analysis`` carrying the doc. The field values ride
    the surface data model (``readA2uiSurfaceState`` snapshot →
    ``a2ui_surface_state``) into the re-run, where ``map_ppa_obligations`` reads
    them AUTHORITATIVELY (no LLM transcription of the trust-critical numbers) and
    COMPLETES the analysis from those assumptions.
    """
    if not _is_needs_assumptions(result):
        return None
    envelope = result.get("elicitation") or {}
    fields = envelope.get("fields") or []
    if not fields:
        return None
    action = str(envelope.get("action") or "start_obligation_analysis")
    doc = _doc_identity(result)

    tree = _Tree()
    seed: dict[str, Any] = {}

    # Lead with a SHORT why (one line). The old 4-line paragraph + a flat
    # 9-field stack (each with its own multi-line help, and a DUPLICATED SDK
    # label) was the "can't see anything / styling so bad" wall — and it buried
    # the 4 required fields at the very top above the optional overrides, which
    # is ALSO the functional bug (a blank required field re-refuses). The
    # per-clause detail stays in the workbench refusal tab; here we optimise for
    # "fill these and go".
    children = [
        tree.text("Complete the obligation analysis", variant="h3", prefix="ef-h"),
        tree.text(
            "This is a template contract: the values that drive a settlement are blank placeholders. "
            "Supply them below — each is recorded as an assumption you provided, never a contract fact.",
            variant="body",
            prefix="ef-why",
        ),
    ]

    # Field rendering (date→DateTimeInput, number→numeric TextField, help line +
    # "Used to derive:" disclosure) is the shared elicitation primitive's `_field_block`
    # (8.1) — this transform keeps only the obligation-specific copy/grouping +
    # the `{doc}` submit context. Identical output for date/number fields.
    required_fields = [f for f in fields if isinstance(f, dict) and f.get("required")]
    optional_fields = [f for f in fields if isinstance(f, dict) and not f.get("required")]

    # REQUIRED group FIRST, in its own Card — the run's hard requirements are
    # never buried below the optional overrides.
    req_blocks = [b for f in required_fields if (b := _field_block(tree, f, seed, mark_required=True))]
    if req_blocks:
        children.append(tree.text("Required to run the analysis", variant="h5", prefix="ef-reqh"))
        children.append(tree.card(tree.column(req_blocks, prefix="ef-reqcol"), prefix="ef-reqcard"))

    # OPTIONAL group — engine defaults prefilled; leaving them accepts the
    # reviewed baseline. Its own heading + Card so it reads as clearly secondary.
    opt_blocks = [b for f in optional_fields if (b := _field_block(tree, f, seed, mark_required=False))]
    if opt_blocks:
        children.append(
            tree.text("Optional — engine defaults shown (leave as-is to accept)", variant="h5", prefix="ef-opth")
        )
        children.append(tree.card(tree.column(opt_blocks, prefix="ef-optcol"), prefix="ef-optcard"))

    # NEVER-SILENT: what submit does + what a blank required field does.
    children.append(
        tree.text(
            "Click Run the analysis. If a required field (*) is blank the form re-appears with a note — "
            "the run never fails silently.",
            variant="body",
            prefix="ef-note",
        )
    )
    children.append(
        tree.button(
            "Run the analysis",
            {"event": {"name": action, "context": {"doc": doc}}},
            variant="primary",
            prefix="ef-btn",
        )
    )
    tree.root(children)

    messages = tree.messages()
    # Seed every bound path so the inputs resolve against a live data model
    # (empty string / baseline = not yet edited). _retarget_surface rewrites the
    # surfaceId to the emit surface — the SAME surface the components render on,
    # so readA2uiSurfaceState snapshots these filled values back on submit.
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {"surfaceId": WORKSPACE_SURFACE_ID, "value": seed},
        }
    )
    return messages


def _timeline_span(obligations: list[dict], events: list[dict]) -> str:
    """Human day-offset span across obligation deadlines + event days."""
    days: list[int] = []
    for o in obligations:
        if isinstance(o, dict) and isinstance(o.get("deadline"), int):
            days.append(o["deadline"])
    for e in events:
        if isinstance(e, dict) and isinstance(e.get("day"), int):
            days.append(e["day"])
    if not days:
        return "day 0"
    lo, hi = min([*days, 0]), max(days)
    return f"day {lo} to {hi}"


def _eur(value: Any) -> str:
    """Format an integer EUR amount with thousands separators ('EUR 15,000').
    ASCII 'EUR' (not the € glyph) keeps the source lint-clean."""
    try:
        return f"EUR {int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def obligation_payload_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform a successful ``PpaObligationPayload`` into a CONVERSATIONAL chat
    summary card (7.8 — ``placement: "chat"``).

    The submit's result appears IN THE CHAT as the RESOLVED SCENARIO the verified
    engine will settle: the obligation milestones (deadline + amount), the
    resolved engine parameters (the delay-LD the contract stated per-MW resolves
    to a concrete EUR/day here), the effective date + provenance, and the
    unmapped-clause honesty line. Plain A2UI — always renders, no MCP-App sandbox
    dependency. The FULL wire payload still rides an ``updateDataModel`` so the
    interactive what-if (verified net + sliders, Z3-proved engine) can boot from
    the real scenario when that view is opened.
    """
    if not _is_payload(result):
        return None
    obligations = result.get("obligations") or []
    events = result.get("events") or []
    unmapped = result.get("unmapped") or []
    policy = result.get("policy") or {}
    policy_sources = result.get("policy_sources") or {}
    extracted = sorted(k for k, v in policy_sources.items() if v == "extracted")
    eff = result.get("effectiveDate")
    eff_source = result.get("effective_date_source") or "extracted"

    tree = _Tree()
    children = [
        tree.text("Analysis ready — settlement scenario", variant="h3", prefix="oa-h"),
        tree.text(
            f"{len(obligations)} obligation(s) · timeline {_timeline_span(obligations, events)} · "
            f"effective {eff} ({eff_source}).",
            variant="body",
            prefix="oa-sum",
        ),
    ]
    if eff_source == "provided":
        children.append(
            tree.text(
                "These are the assumptions you provided — recorded as assumptions the analysis is anchored "
                "on, never contract facts.",
                variant="body",
                prefix="oa-effnote",
            )
        )

    # The resolved obligation milestones the engine will settle.
    if obligations:
        rows = []
        for o in obligations:
            if not isinstance(o, dict):
                continue
            oid = str(o.get("id") or "obligation")
            bits = []
            if isinstance(o.get("deadline"), int):
                bits.append(f"deadline day {o['deadline']}")
            price = o.get("price")
            if isinstance(price, (int, float)) and price:
                bits.append(_eur(price))
            rows.append(tree.text(f"{oid} — {', '.join(bits)}" if bits else oid, variant="body", prefix="oa-ob"))
        children.append(tree.text("Obligations", variant="h5", prefix="oa-obh"))
        children.append(tree.card(tree.column(rows, prefix="oa-obcol"), prefix="oa-obcard"))

    # The resolved engine parameters — the delay-LD the contract stated per-MW
    # becomes a concrete EUR/day here (the trust-critical resolution).
    knob_bits = []
    if policy.get("penPerDay"):
        knob_bits.append(f"delay damages {_eur(policy['penPerDay'])}/day (penPerDay)")
    if policy.get("penCap"):
        knob_bits.append(f"cap {_eur(policy['penCap'])} per obligation (penCap)")
    if policy.get("payWithin"):
        knob_bits.append(f"payment window {policy['payWithin']} days (payWithin)")
    if knob_bits:
        children.append(tree.text("Engine parameters", variant="h5", prefix="oa-polh"))
        children.append(
            tree.card(
                tree.column([tree.text(b, variant="body", prefix="oa-pol") for b in knob_bits], prefix="oa-polcol"),
                prefix="oa-polcard",
            )
        )

    if extracted:
        children.append(
            tree.text(
                "Stated in the contract: " + ", ".join(extracted) + ". Other knobs use reviewed engine defaults.",
                variant="body",
                prefix="oa-prov",
            )
        )
    else:
        children.append(
            tree.text(
                "All policy knobs use the reviewed engine defaults (none stated in the contract).",
                variant="body",
                prefix="oa-prov",
            )
        )
    if unmapped:
        children.append(
            tree.text(
                f"{len(unmapped)} clause(s) are unmapped — not reflected in the settlement.",
                variant="body",
                prefix="oa-un",
            )
        )
    children.append(
        tree.text(
            "The verified net settlement and what-if run in the interactive engine (Z3-proved, no LLM after "
            "mapping) — computed from exactly this scenario.",
            variant="body",
            prefix="oa-note",
        )
    )
    tree.root(children)

    messages = tree.messages()
    # The full wire payload as the surface data model. Rehydrates via the 7.5
    # surface stash (survives resume/refresh); the interactive what-if reads
    # value.payload to boot from this exact scenario.
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {"surfaceId": WORKSPACE_SURFACE_ID, "value": {"payload": result}},
        }
    )
    return messages


# --- surface + artifact metadata ---------------------------------------------


def _analysis_surface(result: Any) -> str:
    doc_id = _doc_id(result)
    return f"obligation_analysis:{doc_id}" if doc_id else WORKSPACE_SURFACE_ID


def _refusal_surface(result: Any) -> str:
    doc_id = _doc_id(result)
    return f"obligation_refusal:{doc_id}" if doc_id else WORKSPACE_SURFACE_ID


def _elicitation_form_surface(result: Any) -> str:
    # A distinct per-doc, per-EMISSION surface so the chat form is addressable +
    # rehydratable (7.5 stash) and never collides with the workbench refusal/
    # analysis tabs. The ``elicit_seq`` suffix makes each emission UNIQUE so a
    # re-refusal APPENDS a new form (append-only history) rather than replacing
    # the one the user just submitted (which stays frozen in the transcript).
    # Its data model (the filled fields) is what map_ppa_obligations reads back.
    doc_id = _doc_id(result)
    if not doc_id:
        return WORKSPACE_SURFACE_ID
    seq = result.get("elicit_seq") if isinstance(result, dict) else None
    base = f"obligation_elicitation:{doc_id}"
    return f"{base}:{seq}" if seq else base


def _elicitation_form_artifact(result: Any) -> dict[str, Any]:
    # ``placement: "chat"`` routes this to the chat thread (ChatPlacementForms),
    # NOT a workbench tab — the user acts where they are reading.
    return {
        "kind": ELICITATION_FORM_KIND,
        "title": "Complete the analysis",
        "description": "Template contract — supply the missing values to run the obligation analysis.",
        "placement": "chat",
    }


def _analysis_artifact(result: Any) -> dict[str, Any]:
    n = len(result.get("obligations") or []) if isinstance(result, dict) else 0
    un = len(result.get("unmapped") or []) if isinstance(result, dict) else 0
    detail = f"{n} obligation(s)" + (f" · {un} unmapped" if un else "")
    # WORKBENCH placement (7.8, restored after the sandbox origins were fixed):
    # the settlement RESULT is the interactive Obligation Analysis artefact
    # (ObligationArtefactTab → the Z3-proved WASM engine: verified net + what-if
    # sliders), which auto-focuses in the Workspace (principle #7). Booting it
    # needs the sandbox to accept the host referrer — now whitelisted per env
    # (see infrastructure/mcp-sandbox/cloudbuild.yaml _ALLOWED_HOST_ORIGINS). A
    # chat-only summary was the stop-gap while the sandbox rejected the iframe;
    # the real analysis belongs in the Workspace, not an echo card in chat.
    return {"kind": ANALYSIS_KIND, "title": "Obligation Analysis", "description": detail}


def _refusal_artifact(result: Any) -> dict[str, Any]:
    unmapped = _unmapped_rows(result)
    detail = f"{len(unmapped)} clause(s) could not be modelled" if unmapped else "could not be modelled"
    return {"kind": REFUSAL_KIND, "title": "Obligations — unmapped", "description": detail}


# Registration ORDER matters (first match wins). The elicitation FORM is
# registered FIRST with the narrower ``_is_needs_assumptions`` matcher, so a
# template-contract refusal (carrying a structured ``elicitation`` envelope)
# becomes the interactive CHAT form; every OTHER error falls through to the
# workbench refusal panel; a successful payload fails both error matchers and
# reaches ``obligation_analysis``. Declaring ``tool_names=[MAP_TOOL]`` on all
# three also marks the tool render-payload (its output is never offloaded by
# ``_handle_large_output`` — that would strand the artefact / refusal / form).
register(
    obligation_elicitation_form_to_a2ui,
    tool_names=[MAP_TOOL],
    result_matcher=_is_needs_assumptions,
    name="obligation_elicitation_form",
    surface=_elicitation_form_surface,
    artifact_meta=_elicitation_form_artifact,
)
register(
    obligation_refusal_to_a2ui,
    tool_names=[MAP_TOOL],
    result_matcher=_is_error,
    name="obligation_refusal",
    surface=_refusal_surface,
    artifact_meta=_refusal_artifact,
)
register(
    obligation_payload_to_a2ui,
    tool_names=[MAP_TOOL],
    result_matcher=_is_payload,
    name="obligation_analysis",
    surface=_analysis_surface,
    artifact_meta=_analysis_artifact,
)
