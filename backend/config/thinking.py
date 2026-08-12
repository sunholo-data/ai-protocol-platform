"""Per-workload thinking depth — the single seam for Gemini thinking config.

Thinking is a per-WORKLOAD property, not a per-model one. Extracting four
fields from a sentence and reasoning across interacting PPA clauses want very
different budgets, and before this module the codebase had no way to say so:
skill agents got a hardcoded ``thinking_budget=-1`` in ``adk.agent._planner_for``
and every other caller (search sub-agents, the code agent, the PPA pipeline,
``structured_extraction``, ``title_generator``) sent NOTHING and inherited the
API default. So the mechanical paths thought hardest by accident.

Measured on gemini-2.5-flash-lite (2026-07-21, N=4), thinking OFF vs the
dynamic default:

    mechanical (field extraction)   0.36s vs 2.19s   6.1x — and 4/4 correct BOTH
    grounded summarisation          0.66s vs 2.31s   3.5x
    analytical (clause reasoning)   0.76s vs 8.58s  11.2x, 1886 thinking tokens

The extraction row is the case for this module: identical correctness, 6.1x the
latency, 374 thinking tokens to copy four fields. The analytical row is the case
for keeping DYNAMIC where reasoning is the point — do NOT read this module as
"thinking is waste".

WHY A SEAM AND NOT A LITERAL PER CALL SITE: the two Gemini families take
DIFFERENT parameters, and getting it wrong is a hard 400, not a silent
degrade. Verified against Vertex 2026-07-21:

    param              2.5 family        3.x family
    thinking_budget=0  ok (0 thoughts)   ok (0 thoughts)
    thinking_budget=-1 ok (dynamic)      ok (dynamic)
    thinking_budget=N  ok, min 512       ok
    thinking_level=*   REJECTED 400      ok

``thinking_level`` is 3.x-only — sending it to a 2.5 model fails with
"Unable to submit request because thinking_level is not supported by...".
Callers declare INTENT (a ThinkDepth) and this module picks the parameter.
"""

from __future__ import annotations

from enum import StrEnum

from google.genai.types import ThinkingConfig, ThinkingLevel

from config.models import api_name_for

# 2.5 rejects any positive budget below this with a 400 (128 verified rejected,
# 512 accepted -> 292 thoughts). Used as the LOW rung for the 2.5 family.
_MIN_2_5_BUDGET = 512

# Dynamic thinking: the model decides how much to spend. Accepted by BOTH
# families, so DYNAMIC needs no family branch and is a true no-op change for
# callers that previously hardcoded it.
_DYNAMIC_BUDGET = -1


class ThinkDepth(StrEnum):
    """How much a workload should think.

    OFF     — mechanical work with a single right answer: field extraction,
              title generation, format conversion. Thinking buys nothing
              measurable here and costs multiples of the latency.
    LOW     — grounded synthesis where the facts are already supplied and the
              job is to phrase them: summarising search results a tool
              returned. Some reasoning, bounded.
    DYNAMIC — the model decides. Genuine multi-step reasoning: clause
              analysis, code generation, open-ended chat. The default, and the
              back-compat value for anything previously unset.
    """

    OFF = "off"
    LOW = "low"
    DYNAMIC = "dynamic"


def _is_3x(api_name: str) -> bool:
    """True for the Gemini 3.x family (thinking_level-capable)."""
    return api_name.startswith("gemini-3")


def thinking_config_for(depth: ThinkDepth, model_ref: str) -> ThinkingConfig | None:
    """The ThinkingConfig for a workload depth on a given model.

    Args:
        depth: The workload's thinking intent.
        model_ref: A tier name (``"lite"``), registry id, or raw api name —
            resolved through ``config.models.api_name_for`` so a tier that
            resolves differently per residency policy still gets the right
            family's parameter.

    Returns:
        A ThinkingConfig, or None for non-Gemini models (Claude and OpenAI
        carry their own reasoning_effort wiring in ``adk.agent.resolve_model``
        — passing a Gemini ThinkingConfig there would be meaningless).
    """
    api_name = api_name_for(model_ref)
    if not api_name.startswith("gemini-"):
        return None

    if depth is ThinkDepth.OFF:
        # Universal across both families — no branch needed.
        return ThinkingConfig(thinking_budget=0)

    if depth is ThinkDepth.LOW:
        if _is_3x(api_name):
            return ThinkingConfig(include_thoughts=True, thinking_level=ThinkingLevel.LOW)
        return ThinkingConfig(include_thoughts=True, thinking_budget=_MIN_2_5_BUDGET)

    # DYNAMIC. include_thoughts=True is load-bearing (MODEL-RELIABILITY M4):
    # without it Gemini thinks silently, the REASONING -> ThinkingPanel
    # pipeline stays dark, and a long thinking phase reads to the user as a
    # hang. See CLAUDE.md #8 (never silent).
    return ThinkingConfig(include_thoughts=True, thinking_budget=_DYNAMIC_BUDGET)


def thinking_config_dict_for(depth: ThinkDepth, model_ref: str) -> dict | None:
    """``thinking_config_for`` as a plain dict, for the raw-genai callers.

    ``tools.resilient_genai.generate_content_resilient`` takes a config DICT
    that it forwards to ``generate_content``, so those callers cannot pass a
    ThinkingConfig object directly.

    Returns None when there is nothing to set, so a caller can splat it
    conditionally without special-casing non-Gemini models.
    """
    cfg = thinking_config_for(depth, model_ref)
    if cfg is None:
        return None
    return cfg.model_dump(exclude_none=True)


__all__ = ["ThinkDepth", "thinking_config_dict_for", "thinking_config_for"]
