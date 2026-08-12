"""Generic elicitation-envelope → A2UI chat-form transform (v6.8.0 8.1).

Domain-agnostic promotion of ``obligation_elicitation_form_to_a2ui`` (7.8 M1):
turns any :class:`adk.elicitation.ElicitationEnvelope` (carried on a tool result
as ``{"needs_input": true, "elicitation": {...}, "placement": "chat"}``) into an
A2UI v0.9 form rendered IN THE CHAT AREA. Two shapes:

  * ``kind="confirm"`` → a message + a primary Button (+ optional cancel).
  * ``kind="confirm_with_fields"`` → required-first field inputs + a submit Button,
    with every bound path seeded so ``readA2uiSurfaceState`` snapshots the filled
    values back on submit.

Field types map to the A2UI Basic catalog: ``date``→DateTimeInput,
``text``/``number``→TextField (number gets a numeric client regexp),
``select``→ChoicePicker, ``bool``→CheckBox. All widgets are UX only — the run
that handles the submit re-validates authoritatively.

Importing this module registers the mapping (side effect); the composition root
(``adk.agent``) imports it once at startup. The frontend needs NO changes — any
``placement:"chat"`` artifact renders via ``ChatPlacementForms``.
"""

from __future__ import annotations

import logging
from typing import Any

from adk.a2ui_ppa_render import _Tree
from adk.a2ui_result_render import WORKSPACE_SURFACE_ID, register

logger = logging.getLogger(__name__)

# Tool names whose elicitation-envelope output this generic transform renders.
# The registry gate keys on tool name, so EVERY tool that returns an elicitation
# envelope (and isn't the obligation tool, which keeps its own tuned transform)
# must be listed here — otherwise its card silently fails to render (the
# recurring A2UI-won't-render trap; see CLAUDE.md).
#   request_confirmation — an agent authoring a form from its own judgement (8.1).
#   transfer_to_agent    — ADK's native handoff tool; the before_tool_callback
#                          floor policy (v6.10.0) short-circuits a confirm/cwf-floor
#                          transfer and returns the elicitation envelope AS the
#                          tool result, so the render registry keys on this name.
REQUEST_CONFIRMATION_TOOL = "request_confirmation"
HANDOFF_TOOL = "transfer_to_agent"

# Client-side numeric guard (digits, thousands separators, decimal, sign). The
# backend re-validates LOUDLY — the regexp is UX, not the trust boundary.
_NUMERIC_REGEXP = "^[0-9.,-]*$"


def is_elicitation(result: Any) -> bool:
    """True when a tool result carries an elicitation envelope destined for chat.

    Honours the generic ``needs_input`` flag and the legacy obligation
    ``needs_assumptions`` flag (so the obligation tool can adopt this transform
    without changing its result shape)."""
    return (
        isinstance(result, dict)
        and isinstance(result.get("elicitation"), dict)
        and bool(result.get("needs_input") or result.get("needs_assumptions"))
    )


def _terse(text: str) -> str:
    """First sentence of a field's help — the form shows a one-line hint, not a
    paragraph (a wall of multi-line help is what made the old form unusable)."""
    if not text:
        return ""
    head = text.split(". ", 1)[0].strip().rstrip(".")
    return f"{head}." if head else ""


def _field_block(tree: _Tree, field: dict[str, Any], seed: dict[str, Any], *, mark_required: bool) -> str | None:
    """One field → a compact Column: a single labelled input (the label rides the
    widget — never a duplicate heading), then a terse help line and an optional
    "→ drives" disclosure. Seeds ``seed[name]`` so the bound path resolves against
    a live data model. Returns the Column id, or None for a malformed field."""
    name = str(field.get("name") or "").strip()
    if not name:
        return None
    ftype = str(field.get("type") or "text")
    label = str(field.get("label") or name)
    unit = str(field.get("unit") or "").strip()
    # Append the unit only when the label doesn't already end in a parenthetical.
    label_with_unit = label if (not unit or label.rstrip().endswith(")")) else f"{label} ({unit})"
    input_label = label_with_unit + (" *" if mark_required else "")
    default = field.get("default")
    path = f"/{name}"

    if ftype == "date":
        input_id = tree.datetime_input(path=path, label=input_label, prefix="ef-date")
        seed[name] = default if isinstance(default, str) else ""
    elif ftype == "select":
        options = field.get("options") or []
        input_id = tree.choice_picker(path=path, options=list(options), label=input_label, prefix="ef-sel")
        seed[name] = default if isinstance(default, str) else ""
    elif ftype == "bool":
        input_id = tree.checkbox(path=path, label=input_label, prefix="ef-bool")
        seed[name] = bool(default)
    elif ftype == "number":
        input_id = tree.text_field(path=path, label=input_label, validation_regexp=_NUMERIC_REGEXP, prefix="ef-num")
        seed[name] = "" if default is None else str(default)
    else:  # text (and any unknown type → safe text input)
        input_id = tree.text_field(path=path, label=input_label, prefix="ef-txt")
        seed[name] = "" if default is None else str(default)

    parts = [input_id]
    help_text = _terse(str(field.get("help") or "").strip())
    if help_text:
        parts.append(tree.text(help_text, variant="body", prefix="ef-help"))
    # A field's `resolves` disclosure carries COMPLIANCE-CRITICAL content for the
    # obligation form — the per-MW LD rates (EUR 150/200 /MW/day) that one input
    # derives, which `_terse` trims off the (long) help. It MUST stay disclosed
    # (schema: "surfaced as an assumption"). Keep it, but drop the dev-y "→ drives"
    # framing for a plainer "Used to derive:" so it reads as user disclosure, not
    # engine internals. Compactness is handled by the `.chat-a2ui-form` skin.
    resolves = [str(r).strip() for r in (field.get("resolves") or []) if str(r).strip()]
    if resolves:
        parts.append(tree.text("Used to derive: " + "; ".join(resolves), variant="body", prefix="ef-drv"))
    return tree.column(parts, prefix="ef-fld")


def elicitation_form_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    """Transform an elicitation envelope into an A2UI v0.9 chat form (Model B,
    out-of-model). Handles both ``confirm`` and ``confirm_with_fields``. Returns
    None for a non-elicitation result."""
    if not is_elicitation(result):
        return None
    envelope = result.get("elicitation") or {}
    kind = str(envelope.get("kind") or "confirm_with_fields")
    action = str(envelope.get("action") or "elicit_submit")
    context = envelope.get("context") if isinstance(envelope.get("context"), dict) else {}
    message = str(envelope.get("message") or "").strip()
    reason = str(envelope.get("reason") or "").strip()

    tree = _Tree()
    seed: dict[str, Any] = {}
    children: list[str] = []

    # The card now supplies a titled header (frontend chrome, from the artifact
    # title — "Confirm" / "Provide details"), so the A2UI body leads straight with
    # the description. For a plain confirm the message ("Hand this conversation to
    # X?") IS the ask; for a field form the message frames the inputs that follow.
    # No in-body h3 — it duplicated the card header (2026-07-15 removed the confirm
    # heading; 2026-07-16 removed the field-form "A bit more detail" heading once
    # the header bar landed). Fall back to a tiny heading only when there is
    # nothing else to show.
    if message:
        children.append(tree.text(message, variant="body", prefix="el-msg"))
    elif reason:
        children.append(tree.text(reason, variant="body", prefix="el-why"))
    elif kind == "confirm":
        children.append(tree.text("Please confirm", variant="h5", prefix="el-h"))

    if kind == "confirm_with_fields":
        fields = [f for f in (envelope.get("fields") or []) if isinstance(f, dict)]
        if not fields:
            return None
        required = [f for f in fields if f.get("required")]
        optional = [f for f in fields if not f.get("required")]
        req_blocks = [b for f in required if (b := _field_block(tree, f, seed, mark_required=True))]
        if req_blocks:
            children.append(tree.text("Required", variant="h5", prefix="el-reqh"))
            children.append(tree.card(tree.column(req_blocks, prefix="el-reqcol"), prefix="el-reqcard"))
        opt_blocks = [b for f in optional if (b := _field_block(tree, f, seed, mark_required=False))]
        if opt_blocks:
            children.append(tree.text("Optional", variant="h5", prefix="el-opth"))
            children.append(tree.card(tree.column(opt_blocks, prefix="el-optcol"), prefix="el-optcard"))
        submit_label = "Submit"
    else:
        submit_label = "Proceed"

    # NEVER-SILENT note + primary submit. The submit fires `action` carrying the
    # opaque `context` so the handling run knows what to do (re-run a tool, switch
    # skill, …). A `run:` prefix would force surface-action-RUN; we keep the raw
    # action name and let the handling endpoint drive the turn.
    children.append(
        tree.button(
            submit_label,
            {"event": {"name": action, "context": context}},
            variant="primary",
            prefix="el-go",
        )
    )
    tree.root(children)

    messages = tree.messages()
    if seed:
        # Seed every bound path so inputs resolve against a live data model;
        # _retarget_surface rewrites the surfaceId to the emit surface — the same
        # one readA2uiSurfaceState snapshots back on submit.
        messages.append({"version": "v0.9", "updateDataModel": {"surfaceId": WORKSPACE_SURFACE_ID, "value": seed}})
    return messages


def _elicit_key(result: Any) -> str:
    """A stable-ish id for the surface: entity/doc, else a handoff target, else
    the action name."""
    envelope = result.get("elicitation") or {} if isinstance(result, dict) else {}
    context = envelope.get("context") or {}
    return str(
        envelope.get("doc_id")
        or (context.get("target_skill_id") if isinstance(context, dict) else None)
        or envelope.get("action")
        or "elicit"
    )


def _elicitation_surface(result: Any) -> str:
    """A distinct per-emission surface (``elicit:{key}:{seq}``) so each ask is
    addressable + append-only and never collides with workbench tabs."""
    if not isinstance(result, dict):
        return WORKSPACE_SURFACE_ID
    seq = result.get("elicit_seq")
    base = f"elicit:{_elicit_key(result)}"
    return f"{base}:{seq}" if seq else base


def _elicitation_artifact(result: Any) -> dict[str, Any]:
    """``placement:"chat"`` routes this to the chat thread (ChatPlacementForms),
    not a workbench tab — the user acts where they are reading."""
    envelope = result.get("elicitation") or {} if isinstance(result, dict) else {}
    kind = str(envelope.get("kind") or "confirm_with_fields")
    title = "Confirm" if kind == "confirm" else "Provide details"
    return {
        "kind": "elicitation",
        # Sub-type so the frontend can render a plain confirm as a compact card
        # (tight width, auto-width right-aligned CTA) vs the taller field form.
        "elicitationKind": "confirm" if kind == "confirm" else "confirm_with_fields",
        "title": title,
        "description": str(envelope.get("reason") or envelope.get("message") or ""),
        "placement": "chat",
    }


# Register for the agent-initiated tool. Tools/skills that adopt the primitive
# (obligation, the 8.2 handoff) register this SAME transform for their own names.
register(
    elicitation_form_to_a2ui,
    tool_names=[REQUEST_CONFIRMATION_TOOL, HANDOFF_TOOL],
    result_matcher=is_elicitation,
    name="elicitation_form",
    surface=_elicitation_surface,
    artifact_meta=_elicitation_artifact,
)


def register_elicitation_for(tool_name: str) -> str:
    """Let a NORMAL tool raise this same chat form when it needs input (8.1 →
    v6.12.0 M5).

    The registry gates on tool name, so a tool that returns an elicitation
    envelope from its own signature (e.g. ``entsoe_day_ahead_prices`` when the
    zone/dates are missing) renders NOTHING until this transform is registered
    for its name — the recurring A2UI-won't-render trap. Call this from the
    tool's own render module BEFORE its success mapping: the first matching
    mapping wins, and the success transform declines an elicitation payload by
    returning None (which stops the search, it does not fall through).
    """
    return register(
        elicitation_form_to_a2ui,
        tool_names=[tool_name],
        result_matcher=is_elicitation,
        name=f"elicitation_form:{tool_name}",
        surface=_elicitation_surface,
        artifact_meta=_elicitation_artifact,
    )
