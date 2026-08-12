"""ADK before/after model callback pair for budget enforcement.

Sprint 2.12 (v6.2.0) M2. The pair is the platform's hot-path gate:

- ``_before(ctx, llm_request)`` consults the registered enforcer with
  a worst-case projected cost (input tokens + ``max_output_tokens`` x
  per-model rate). Block → raise ``BudgetExceededError``. Warn → stash
  ``state['budget:warn_message']`` for the after_agent prefix
  injector. Allow → fall through.

- ``_after(ctx, llm_response)`` reads the realised token usage off
  ``llm_response.usage_metadata`` and calls ``enforcer.record(...)``
  to reconcile the held projection with the actual cost.

The pair shares a closure-scoped ``pending`` dict keyed by
``invocation_id`` so the before/after can hand off the consultation
without round-tripping through session state.
"""

from __future__ import annotations

import logging
from typing import Any

from adk.budget_config import BudgetConfig
from auth.firebase_auth import User
from budget.enforcer import (
    BudgetConsultation,
    BudgetEnforcer,
    BudgetExceededError,
)
from observability.llm_metrics import estimate_cost

logger = logging.getLogger("budget")


_DEFAULT_MAX_OUTPUT_TOKENS = 4096
"""Fallback when ``llm_request.config.max_output_tokens`` isn't set —
worst-case projection should still over-estimate, not under."""

_CHARS_PER_TOKEN = 4
"""Crude token-count estimate for pre-call projection. Tokenisers vary
per-model; this is a rough planning number that over-estimates more
often than not."""


def make_budget_callbacks(
    enforcer: BudgetEnforcer | None,
    *,
    user: User,
    skill_id: str,
    budget_config: BudgetConfig | None,
) -> tuple[Any, Any]:
    """Build the (before_model, after_model) callback pair for one agent.

    Returns no-op pair when:
      - no enforcer is registered (``enforcer is None``)
      - the skill has no ``tool_configs.budget`` (``budget_config is None``)
      - the skill is marked exempt (``budget_config.exempt is True``)

    Otherwise returns a pair that consults the enforcer pre-call and
    records the realised cost post-call.
    """
    if enforcer is None or budget_config is None or budget_config.exempt:
        return _no_op_callbacks()

    identity_value = _extract_identity(user, budget_config.identity_key)
    if not identity_value:
        logger.warning(
            "budget.identity_unresolved",
            extra={
                "skill_id": skill_id,
                "identity_key": budget_config.identity_key,
                "user_uid": user.uid,
            },
        )
        return _no_op_callbacks()

    # Closure-shared lookup so the after callback can find the
    # consultation the before callback stashed. Keyed by invocation_id.
    pending: dict[str, BudgetConsultation] = {}

    async def _before(callback_context: Any, llm_request: Any) -> None:
        projected = _estimate_projected_cost(llm_request) * budget_config.cost_multiplier
        consultation = BudgetConsultation(
            identity_value=identity_value,
            skill_id=skill_id,
            model_id=_extract_model_id(llm_request),
            projected_cost_usd=projected,
            invocation_id=callback_context.invocation_id,
        )
        decision = await enforcer.consult(consultation)
        pending[callback_context.invocation_id] = consultation

        if decision.action == "block":
            # Drain pending — record will never fire on block.
            pending.pop(callback_context.invocation_id, None)
            raise BudgetExceededError(decision)
        if decision.action == "warn":
            callback_context.state["budget:warn_message"] = decision.message
        return None

    async def _after(callback_context: Any, llm_response: Any) -> None:
        consultation = pending.pop(callback_context.invocation_id, None)
        if consultation is None:
            return
        actual_cost = _extract_actual_cost(llm_response, consultation.model_id)
        if actual_cost is None:
            return
        await enforcer.record(consultation, actual_cost_usd=actual_cost)

    return _before, _after


# ─── No-op fallback ──────────────────────────────────────────────────────────


def _no_op_callbacks() -> tuple[Any, Any]:
    async def _noop_before(callback_context: Any, llm_request: Any) -> None:
        return None

    async def _noop_after(callback_context: Any, llm_response: Any) -> None:
        return None

    return _noop_before, _noop_after


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _extract_identity(user: User, identity_key: str) -> str:
    """Read ``user.<identity_key>`` and return it as a non-empty string,
    or empty string if absent. Used to decide whether to skip the gate.

    Falls open: an unresolved identity short-circuits to no-op + WARN
    log rather than block. The design's reasoning (see howto): forks
    that misconfigure the identity_key shouldn't have the platform
    silently deny everyone.
    """
    value = getattr(user, identity_key, None)
    if value is None:
        return ""
    s = str(value).strip()
    return s


def _extract_model_id(llm_request: Any) -> str:
    """Pull the model id off the request. ADK passes either a string or
    a model object whose ``__str__`` is the id."""
    model = getattr(llm_request, "model", None)
    return str(model) if model is not None else "unknown"


def _estimate_projected_cost(llm_request: Any) -> float:
    """Worst-case projection in USD: input-token estimate x input rate +
    ``max_output_tokens`` x output rate. Reuses
    ``observability.llm_metrics.estimate_cost`` (the same pricing table
    the post-call meter uses, so projection and realised cost stay on
    one source of truth)."""
    model_id = _extract_model_id(llm_request)
    input_tokens = _approx_input_tokens(llm_request)
    max_out = _read_max_output_tokens(llm_request)
    return estimate_cost(model_id, input_tokens, max_out)


def _approx_input_tokens(llm_request: Any) -> int:
    """Sum text-part char counts and divide by 4. Rough but consistent.

    A real tokeniser would be model-specific; this is a planning number
    that's over-conservative for English (cheap to over-charge by 10-20%
    in projection — record() reconciles to the actual on the way out).
    """
    contents = getattr(llm_request, "contents", None) or []
    chars = 0
    for content in contents:
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                chars += len(text)
    return max(1, chars // _CHARS_PER_TOKEN)


def _read_max_output_tokens(llm_request: Any) -> int:
    """Pull ``max_output_tokens`` off the request's config; default to
    a conservative 4096 if unset."""
    config = getattr(llm_request, "config", None)
    if config is None:
        return _DEFAULT_MAX_OUTPUT_TOKENS
    value = getattr(config, "max_output_tokens", None)
    if value is None:
        return _DEFAULT_MAX_OUTPUT_TOKENS
    try:
        return int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_OUTPUT_TOKENS


def _extract_actual_cost(llm_response: Any, model_id: str) -> float | None:
    """Pull realised token usage off ``usage_metadata`` and price it.

    ADK fills ``usage_metadata.prompt_token_count`` +
    ``candidates_token_count`` after a successful call. If the
    metadata is missing (some stream paths don't populate it), return
    ``None`` and skip the record() — the held projection stays.
    """
    metadata = getattr(llm_response, "usage_metadata", None)
    if metadata is None:
        return None
    prompt_tokens = getattr(metadata, "prompt_token_count", None)
    candidate_tokens = getattr(metadata, "candidates_token_count", None)
    if prompt_tokens is None or candidate_tokens is None:
        return None
    return estimate_cost(model_id, int(prompt_tokens), int(candidate_tokens))
