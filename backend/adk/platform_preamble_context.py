"""Prepend the admin-configured platform preamble to every agent (v6.14.0).

The platform preamble is shared identity / house-style / guardrails that should
lead EVERY skill's prompt (see ``config.platform_config``). Unlike the other
instruction wrappers (date, SVG, iframe/surface context), which APPEND their block
after the skill body, this one PREPENDS — the preamble reads as the platform
identity the skill builds on, and because it comes first the skill's own
instructions (which follow) take precedence for that skill's domain.

Composed as the FIRST wrapper in ``adk.agent.create_agent`` so it wraps the raw
skill instructions before every appending wrapper runs. Transparent no-op when the
config is disabled or the preamble is empty — exactly like the state-gated
wrappers. The config is read per request through a TTL cache
(``get_platform_config``), so an admin edit is picked up within ~a minute (or
immediately after the write path invalidates the cache) without a per-turn
Firestore read on the hot path.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from google.adk.agents.readonly_context import ReadonlyContext

from config.platform_config import get_platform_config

logger = logging.getLogger(__name__)

_BaseInstruction = str | Callable[[ReadonlyContext], Awaitable[str]]


def render_instruction_with_platform_preamble(base_str: str) -> str:
    """Prepend the platform preamble to ``base_str`` when enabled and non-empty.

    Returns ``base_str`` unchanged when the config is disabled, the preamble is
    blank, or the config read fails (fail-open — a config issue must not strip the
    skill's own instructions).
    """
    try:
        config = get_platform_config()
    except Exception as exc:  # defensive: loader already fails open, but never raise here
        logger.warning("platform_preamble: config read failed (%s) — skipping", type(exc).__name__)
        return base_str

    preamble = config.preamble.strip() if config.enabled else ""
    if not preamble:
        return base_str
    return f"{preamble}\n\n{base_str.lstrip()}"


def wrap_with_platform_preamble(base: _BaseInstruction) -> Callable[[ReadonlyContext], Awaitable[str]]:
    """Return an ``InstructionProvider`` that prepends the platform preamble."""

    async def _provider(ctx: ReadonlyContext) -> str:
        base_str = await base(ctx) if callable(base) else base
        return render_instruction_with_platform_preamble(base_str)

    return _provider


__all__ = ["render_instruction_with_platform_preamble", "wrap_with_platform_preamble"]
