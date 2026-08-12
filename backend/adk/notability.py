"""Notability tiers for activity events (v6.11.0 workbench-home-and-curated-activity).

The workbench curates: an event's tier decides *where* it renders —

  - ``internal`` : plumbing the user should never see (``transfer_to_agent`` and
                   other ADK-native control verbs, sub-agent hops). Activity feed
                   only, and collapsed there.
  - ``notable``  : a useful result worth curating (a tool whose result maps to a
                   plain A2UI card, a delegation, a sources list). Home digest +
                   Activity.
  - ``artifact`` : a full structured surface that earns its own Result tab (clause
                   cards, a comparison, the obligation WASM analysis). Home index
                   + Activity.

Tier is decided backend-side (Axiom #10 THIN CLIENT / #7 API FIRST): the frontend
filters by tier, the CLI and every channel get the same curation. Classification
is derived from the existing result→A2UI registry
([a2ui_result_render](a2ui_result_render.py)) so there is a single source of
truth for "does this tool produce user-facing UI" — no second hand-maintained list.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

INTERNAL = "internal"
NOTABLE = "notable"
ARTIFACT = "artifact"

# ADK-native orchestration verbs + retired handoff tools: pure plumbing. These
# never carry a result→A2UI mapping, but we name them explicitly so the default
# can stay "internal" without misclassifying a genuinely useful unmapped tool
# (which is surfaced via its own digest emission, not via tool tier).
_INTERNAL_TOOLS = frozenset(
    {
        "transfer_to_agent",  # ADK-native handoff (v6.10 unification)
        "request_handoff",  # legacy confirm/confirm-with-fields tool (retired)
    }
)


def tool_tier(tool_name: str) -> str:
    """Classify a tool call by user-notability. Never raises (fail to ``internal``).

    - explicit control verbs → ``internal``
    - a registered result→A2UI mapping that produces artifact metadata → ``artifact``
    - a registered result→A2UI mapping without artifact metadata → ``notable``
    - anything else (unmapped plumbing) → ``internal``
    """
    if not tool_name or tool_name in _INTERNAL_TOOLS:
        return INTERNAL
    try:
        from adk import a2ui_result_render as rr

        if rr.tool_produces_artifact(tool_name):
            return ARTIFACT
        if rr.is_render_payload_tool(tool_name):
            return NOTABLE
    except Exception as exc:  # classification must never break the activity path
        logger.warning("tool_tier(%r) failed (suppressed): %s", tool_name, exc)
    return INTERNAL
