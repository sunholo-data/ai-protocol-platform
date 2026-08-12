"""Reusable elicitation-in-chat primitive (v6.8.0 8.1).

Generalises the obligation-analysis "build-from-assumptions" A2UI form (7.8 M1)
into a domain-agnostic contract any tool, skill, OR the delegation/handoff path
can use to ask the user — in the CHAT AREA — for either:

  * a bare OK/cancel CONFIRM (``kind="confirm"``), or
  * a CONFIRM-WITH-FIELDS form whose fields the agent authors and the backend
    validates (``kind="confirm_with_fields"``).

The envelope a tool/agent returns is rendered by the generic transform in
:mod:`adk.a2ui_elicitation_render` (registered against the result→A2UI
registry), pushed to a ``placement:"chat"`` surface (``ChatPlacementForms`` — no
frontend change), and the submitted values are read back AUTHORITATIVELY from
the surface data model via :func:`read_submitted_values` (no LLM transcription
of trust-critical values — the same closed loop the obligation form proved).

Two entry points converge on the same envelope → transform → chat render →
surface read-back:
  * a TOOL returns an elicitation result (e.g. a refusal that needs input), or
  * an AGENT calls :func:`request_confirmation` from its own judgement.

Design: docs/design/v6.8.0/elicitation-in-chat-primitive.md
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Field vocabulary. ``date`` → DateTimeInput, ``text``/``number`` → TextField
# (number gets a numeric client regexp), ``select`` → ChoicePicker, ``bool`` →
# CheckBox. Backend re-validates LOUDLY on submit — the client widgets are UX,
# never the trust boundary.
ElicitationFieldType = Literal["date", "text", "number", "select", "bool"]

# AI-friendly type normalization (friendly-names rule for field TYPES). The model
# authoring a form doesn't know our exact enum — live 2026-07-16, Gemini authored
# `type:"dropdown"`, which failed the Literal validation and RUN_ERROR'd the whole
# turn. Accept the model's natural vocabulary and normalize; an unknown type
# degrades to "text" so a form NEVER crashes the run over a type name.
_TYPE_ALIASES = {
    "dropdown": "select",
    "choice": "select",
    "picker": "select",
    "enum": "select",
    "option": "select",
    "options": "select",
    "combobox": "select",
    "radio": "select",
    "checkbox": "bool",
    "boolean": "bool",
    "toggle": "bool",
    "switch": "bool",
    "yesno": "bool",
    "yes_no": "bool",
    "flag": "bool",
    "string": "text",
    "str": "text",
    "textarea": "text",
    "textfield": "text",
    "email": "text",
    "url": "text",
    "uri": "text",
    "phone": "text",
    "password": "text",
    "integer": "number",
    "int": "number",
    "float": "number",
    "decimal": "number",
    "numeric": "number",
    "currency": "number",
    "datetime": "date",
    "date-time": "date",
    "time": "date",
    "day": "date",
}
ElicitationKind = Literal["confirm", "confirm_with_fields"]

# Reserved generic submit action. A tool/skill may override with its own action
# name (e.g. obligation's ``start_obligation_analysis``); the handoff path uses
# ``confirm_delegation`` (8.2). The submit Button fires ``action`` carrying
# ``context`` so the run that handles it knows what to do.
ELICIT_SUBMIT_ACTION = "elicit_submit"


class ElicitationField(BaseModel):
    """One typed input the user supplies, keyed by ``name``.

    ``name`` is the closed loop: it is BOTH the A2UI data-model path
    (``/{name}``) the widget binds to AND the key the handling code reads back
    from the submitted surface state — so a value never round-trips through the
    model's prose.
    """

    name: str = Field(min_length=1, description="dataModel path + read-back key (snake_case). The closed loop.")
    type: ElicitationFieldType = Field(default="text", description="Widget/validation type.")
    label: str = Field(min_length=1, description="Human field label (rides on the widget).")
    help: str = Field(default="", description="One-line hint (source/why).")
    default: str | int | float | bool | None = Field(default=None, description="Prefilled value, or null.")
    options: list[str] | None = Field(default=None, description="Choices for type='select' (required there).")
    resolves: list[str] = Field(
        default_factory=list, description="Optional: downstream knob(s) this input drives (earned-trust disclosure)."
    )
    required: bool = Field(default=False, description="True = the form cannot be completed without it.")
    unit: str = Field(default="", description="Display unit, e.g. 'MW', 'EUR', 'days'.")

    model_config = ConfigDict(extra="forbid")

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: object) -> object:
        """Map the model's natural type vocabulary → the canonical enum; an
        unrecognised type degrades to 'text' so a request_confirmation call never
        RUN_ERRORs the whole turn over a type name (live-verified 2026-07-16)."""
        if isinstance(v, str):
            key = v.strip().lower()
            if key in ("date", "text", "number", "select", "bool"):
                return key
            return _TYPE_ALIASES.get(key, "text")
        return v

    @model_validator(mode="after")
    def _check_select_options(self) -> ElicitationField:
        # A select with no options can't render a picker. Rather than crash the
        # turn, degrade to a free-text input (still usable) — the never-silent /
        # never-crash principle for an AI-authored form.
        if self.type == "select" and not self.options:
            self.type = "text"
        return self


class ElicitationEnvelope(BaseModel):
    """The structured "ask the user in chat" contract.

    ``kind="confirm"`` renders a message + a primary Button (+ optional cancel),
    no field inputs. ``kind="confirm_with_fields"`` renders the fields
    (required-first) + a submit Button. In both cases the Button fires ``action``
    carrying ``context`` (the opaque payload the handling run echoes back — e.g.
    ``{"doc": ...}`` for obligation, ``{"target_skill_id": ...}`` for a handoff).
    """

    kind: ElicitationKind = Field(default="confirm_with_fields", description="Confirm vs confirm-with-fields.")
    action: str = Field(default=ELICIT_SUBMIT_ACTION, min_length=1, description="Surface action the submit fires.")
    message: str = Field(default="", description="Copy shown above the form / the confirm prompt.")
    reason: str = Field(default="", description="Why the input is needed (headline).")
    fields: list[ElicitationField] = Field(default_factory=list)
    doc_id: str | None = Field(default=None, description="Optional entity identity (surface id + back-compat).")
    context: dict[str, Any] = Field(default_factory=dict, description="Opaque payload echoed back on submit.")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_kind(self) -> ElicitationEnvelope:
        if self.kind == "confirm_with_fields" and not self.fields:
            raise ValueError("kind='confirm_with_fields' requires at least one field")
        if self.kind == "confirm" and not (self.message or self.reason):
            raise ValueError("kind='confirm' requires a message (or reason)")
        return self


# The generic flag the render matcher keys off (obligation's legacy
# ``needs_assumptions`` is also honoured by the matcher for back-compat).
NEEDS_INPUT_KEY = "needs_input"


def next_elicit_seq(tool_context: Any, *, key: str = "_elicit_seq") -> int:
    """Monotonic per-session counter so each elicitation EMISSION gets a unique
    surface — a re-ask APPENDS a fresh form (append-only history; the prior form
    stays frozen) instead of replacing the one the user just submitted.

    Best-effort: with no usable state we return 1 (single-form fallback). An
    EMPTY state dict is still usable — only a truly absent state falls back.
    """
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is None:
        return 1
    try:
        seq = int(state.get(key, 0) or 0) + 1
    except (TypeError, ValueError):
        seq = 1
    state[key] = seq
    return seq


def _pause_turn_after_elicitation(tool_context: Any) -> None:
    """Set ``skip_summarization`` so ADK ends the turn on the elicitation card
    instead of re-invoking the model with the envelope as a tool result.

    An elicitation means "pause and wait for the user" — the turn is DONE once
    the card is on the wire. This mirrors ADK's own ``get_user_choice`` tool,
    which sets the same flag. Without it, the function-response is NOT
    ``is_final_response`` (it carries a function response and no skip flag), so
    the flow's outer loop keeps calling the model — and a well-behaved model
    reads the envelope as an incomplete tool call and RE-ISSUES it. A lite
    front-door re-calling ``transfer_to_agent`` this way produced a runaway of
    "Confirm" cards / delegation markers (2026-07-22 report). See
    ``base_llm_flow.run_async`` (breaks on ``is_final_response``) and
    ``Event.is_final_response`` (true when ``skip_summarization`` is set).

    Best-effort: unit/preview paths pass no tool context (or a bare stub); a
    missing/read-only ``actions`` must never crash the turn.
    """
    actions = getattr(tool_context, "actions", None) if tool_context is not None else None
    if actions is None:
        return
    try:
        actions.skip_summarization = True
    except Exception:  # exotic/read-only actions object — never let this kill the turn
        logger.debug("could not set skip_summarization on tool_context.actions", exc_info=True)


def make_elicitation_result(envelope: ElicitationEnvelope, *, tool_context: Any = None) -> dict[str, Any]:
    """Wrap an envelope as a tool RESULT the result→A2UI registry renders as a
    chat form. Any tool can return this; :func:`request_confirmation` is the
    agent-facing shortcut.

    Also PAUSES the turn (``skip_summarization``) so the model is not re-invoked
    after the card — an elicitation is a wait-for-the-user boundary, not a step
    the model should continue past. See :func:`_pause_turn_after_elicitation`."""
    _pause_turn_after_elicitation(tool_context)
    return {
        NEEDS_INPUT_KEY: True,
        "elicitation": envelope.model_dump(),
        "placement": "chat",
        "elicit_seq": next_elicit_seq(tool_context),
    }


def request_confirmation(
    message: str,
    kind: str = "confirm",
    fields: list[dict[str, Any]] | None = None,
    action: str = ELICIT_SUBMIT_ACTION,
    context: dict[str, Any] | None = None,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Ask the user, IN THE CHAT AREA, to confirm — or to confirm AND supply
    fields — before you proceed. Renders an A2UI card/form in chat; the user's
    answer is read back authoritatively from the surface, never transcribed by you.

    Use ``kind="confirm"`` for a simple OK/cancel. Use
    ``kind="confirm_with_fields"`` and author ``fields`` when you need structured
    input. Do NOT act as if the user already answered — wait for the submit run.

    Args:
        message: The prompt shown above the card/form.
        kind: "confirm" (OK/cancel) or "confirm_with_fields" (with inputs).
        fields: For confirm_with_fields — a list of field dicts. Each: name
            (snake_case), label, and type — one of "text", "number", "date",
            "select" (also give options=[...]), or "bool". Optional: help,
            default, required, unit. (Common synonyms like "dropdown"/"checkbox"
            are accepted and normalized.)
        action: Surface action the submit fires (defaults to the generic submit).
        context: Opaque payload echoed back to the handling run on submit.

    Returns:
        A pending-input record — the chat form. Relay nothing further until the
        user submits.
    """
    # Parse resiliently: a single malformed field must never RUN_ERROR the turn.
    # Type synonyms are already normalized by ElicitationField; anything still
    # unparseable (e.g. missing name/label) is skipped rather than raised.
    parsed_fields: list[ElicitationField] = []
    for f in fields or []:
        try:
            parsed_fields.append(ElicitationField.model_validate(f))
        except Exception as exc:  # never let one malformed field kill the whole form
            logger.warning("request_confirmation: skipping malformed field %r: %s", f, exc)
    # If the model asked for fields but none survived, fall back to a bare confirm
    # so the user still gets a visible card (never a silent no-op).
    effective_kind = kind
    if kind == "confirm_with_fields" and not parsed_fields:
        effective_kind = "confirm"
    envelope = ElicitationEnvelope(
        kind=effective_kind,  # type: ignore[arg-type]
        action=action,
        message=message,
        fields=parsed_fields,
        context=context or {},
    )
    return make_elicitation_result(envelope, tool_context=tool_context)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def read_submitted_values(tool_context: Any, *, expected_fields: list[str] | None = None) -> dict[str, Any] | None:
    """Read the submitted form's data model straight from session state — the
    AUTHORITATIVE, no-LLM-transcription source.

    The ``surface-action-run`` endpoint seeds ``a2ui_surface_state`` (the
    frontend's per-turn data-model snapshot) and ``a2ui_action_trigger`` (which
    surface was clicked). We prefer the triggering surface's data model, then
    fall back to scanning active surfaces. When ``expected_fields`` is given we
    anchor on it (so an unrelated surface is never mistaken for the form); with
    no hint we accept the triggering surface's non-empty data model.
    """
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if state is None:
        return None
    surface_state = state.get("a2ui_surface_state")
    if not isinstance(surface_state, dict):
        return None

    ordered_ids: list[str] = []
    trigger = state.get("a2ui_action_trigger")
    if isinstance(trigger, dict) and isinstance(trigger.get("surfaceId"), str):
        ordered_ids.append(trigger["surfaceId"])
    ordered_ids.extend(sid for sid in surface_state if isinstance(sid, str) and sid not in ordered_ids)

    def looks_like(data_model: dict[str, Any]) -> bool:
        if expected_fields:
            return any(not _is_blank(data_model.get(name)) for name in expected_fields)
        return bool(data_model)

    for sid in ordered_ids:
        entry = surface_state.get(sid)
        if not isinstance(entry, dict):
            continue
        data_model = entry.get("dataModel")
        if isinstance(data_model, dict) and looks_like(data_model):
            return data_model
    return None
