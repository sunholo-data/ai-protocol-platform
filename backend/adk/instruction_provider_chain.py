"""Compose multiple ADK InstructionProviders into a single chain.

ADK's ``LlmAgent.instruction`` accepts either a static string or a
callable ``(ReadonlyContext) -> Awaitable[str]``. Wrappers like
``wrap_with_iframe_context`` and ``wrap_with_a2ui_surface_context``
take a base (str or provider) and return a new provider that appends
their block. Composing them by hand looks like::

    instruction = wrap_with_a2ui_surface_context(
        wrap_with_iframe_context(skill_config.instructions),
    )

That's correct but the order is structural — the outermost wrapper's
block is appended LAST, so the rendered prompt has iframe-context
before a2ui-surface-context. Adding a third wrapper means knowing the
chain order, and the nesting reads inside-out.

``compose_instruction_providers`` makes the order LEFT-TO-RIGHT and
explicit::

    instruction = compose_instruction_providers(
        skill_config.instructions,
        wrap_with_iframe_context,
        wrap_with_a2ui_surface_context,
    )

Identical behaviour, less Yoda. Each wrapper accepts the previous
result (str or callable) and returns a provider; the helper applies
``functools.reduce`` over the list.

Sprint 2.10 follow-up. No behaviour change vs the hand-written nest.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import reduce

from google.adk.agents.readonly_context import ReadonlyContext

# An InstructionProvider in ADK terms: an async callable that resolves
# the runtime instruction from the agent's current context.
InstructionProvider = Callable[[ReadonlyContext], Awaitable[str]]

# A wrapper takes either a static string OR an upstream provider and
# returns a new provider that appends its own behaviour. The base of
# the chain is always a string; intermediates are providers.
InstructionProviderOrStr = str | InstructionProvider
InstructionProviderWrapper = Callable[[InstructionProviderOrStr], InstructionProvider]


def compose_instruction_providers(
    base: str,
    *wrappers: InstructionProviderWrapper,
) -> InstructionProvider:
    """Chain ``wrappers`` left-to-right over ``base``.

    Args:
        base: The skill's static instruction string. Every wrapper
            sees this (or the previous wrapper's output) and returns
            a new provider.
        *wrappers: Zero or more InstructionProvider factories, each
            accepting an upstream base/provider and returning a new
            provider. The leftmost wrapper sees ``base`` as a str;
            subsequent wrappers see the previous wrapper's
            provider.

    Returns:
        The final ``InstructionProvider`` ready to assign to
        ``LlmAgent.instruction``. If no wrappers are supplied,
        returns a provider that yields ``base`` verbatim.

    Examples:
        >>> chain = compose_instruction_providers(
        ...     "You are a helpful agent.",
        ...     wrap_with_iframe_context,
        ...     wrap_with_a2ui_surface_context,
        ... )
        >>> # Equivalent to:
        >>> wrap_with_a2ui_surface_context(
        ...     wrap_with_iframe_context("You are a helpful agent."),
        ... )
    """
    if not wrappers:
        # Render the base string as a provider for type uniformity.
        async def _base_only_provider(_ctx: ReadonlyContext) -> str:
            return base

        return _base_only_provider

    # Fold the wrappers left-to-right. The first wrapper receives the
    # base string; each subsequent wrapper receives the previous
    # wrapper's provider (a callable).
    return reduce(
        lambda acc, wrapper: wrapper(acc),
        wrappers,
        base,
    )


__all__ = [
    "InstructionProvider",
    "InstructionProviderOrStr",
    "InstructionProviderWrapper",
    "compose_instruction_providers",
]
