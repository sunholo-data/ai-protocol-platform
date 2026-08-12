"""Pluggable artefact-render content review hook (backend mirror).

Sprint 2.13 (v6.2.0) M3. Mirrors the TypeScript Protocol at
``frontend/src/components/protocols/ArtefactReviewer.ts`` exactly —
forks may implement the same policy in either layer and switch
deployment-side without rewriting. Naming convention:

  - Python: snake_case (tool_name, server_id, resource_uri, ...)
  - TypeScript: camelCase (toolName, serverId, resourceUri, ...)

JSON wire shape decides which converts when a server-side 403 reaches
the frontend.

The hook is ABOVE the iframe sandbox + CSP — defence-in-depth, not
replacement. A reviewer that crashes or is bypassed leaves the
sandbox boundary intact.

Design contract: ``docs/design/v6.2.0/artefact-render-hook.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# ─── Wire shape ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArtefactReview:
    """Input the reviewer sees before an artefact is rendered.

    Field set MUST match the TypeScript ``ArtefactReview`` interface —
    adding or removing a field requires a parallel change in
    ``frontend/src/components/protocols/ArtefactReviewer.ts``.
    """

    tool_name: str
    server_id: str
    resource_uri: str
    html: str
    csp: str | None
    structured_content: object
    invocation_id: str


@dataclass(frozen=True)
class ArtefactDecision:
    """The reviewer's answer.

    For Python the discriminated-union is encoded with a Literal
    action + nullable supporting fields. The TS mirror uses a true
    discriminated union; both shapes serialize identically on the
    wire.
    """

    action: Literal["approve", "warn", "block"]
    message: str | None
    reason_code: str | None
    appeal_url: str | None


# ─── Protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class ArtefactReviewer(Protocol):
    """Consulted before a ``resources/read`` artefact is forwarded.

    Forks implement this with their own backend (htmlparser2-style
    static analysis, header inspection, headless render preview, etc).
    Duck-typed — no inheritance needed.
    """

    async def review(self, request: ArtefactReview) -> ArtefactDecision:
        """Return approve / warn / block for the artefact."""
        ...


# ─── Exception ───────────────────────────────────────────────────────────────


class BlockedArtefactError(Exception):
    """Raised internally when a server-side reviewer blocks an artefact.

    Carries the decision so the proxy can serialise it into a typed
    403 body without re-consulting.
    """

    def __init__(self, decision: ArtefactDecision) -> None:
        self.decision = decision
        message = decision.message or "Artefact blocked."
        super().__init__(message)


# ─── Registry ────────────────────────────────────────────────────────────────


_registered: ArtefactReviewer | None = None


def register_artefact_reviewer(impl: ArtefactReviewer) -> None:
    """Register the process-wide reviewer.

    Forks call this once at startup. Calling twice replaces the
    previous registration (no warning — late registration is a valid
    pattern for test fixtures).

    Raises ``TypeError`` if ``impl`` doesn't satisfy the Protocol —
    fork misconfiguration should fail loud at startup rather than
    silently approve everything.
    """
    import asyncio

    if not isinstance(impl, ArtefactReviewer) or not asyncio.iscoroutinefunction(getattr(impl, "review", None)):
        raise TypeError(
            "register_artefact_reviewer requires an ArtefactReviewer "
            "with an async review() method; got "
            f"{type(impl).__name__}"
        )
    global _registered
    _registered = impl


def get_registered_artefact_reviewer() -> ArtefactReviewer | None:
    """Return the registered reviewer, or ``None`` if forks haven't plugged one.

    The proxy's interception short-circuits to pass-through when this
    returns ``None`` — back-compat with the dumb-forwarder semantics.
    """
    return _registered


def clear_registered_artefact_reviewer() -> None:
    """Drop the registered reviewer. Used by tests; not for production."""
    global _registered
    _registered = None
