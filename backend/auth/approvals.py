"""Who may decide a human-approval request: one policy for every surface.

A tool that prepares a write only a human may release (``adk.approvals``)
gets its decision from the web card, an approval link page, an MCP
elicitation or the CLI. This module is the single rule for all of them, so no
surface has a weaker door than another. Design:
docs/design/v6.41.0/human-approvals.md ("Approver policy").

The rule judges the CREDENTIAL that authenticated this request, not just
``user.auth_mode``: a ``User`` rebuilt from a uid (channels, MCP tokens)
also says ``firebase`` or ``tailnet``, so only a request-authenticated human
session counts as one.

* ``session`` — the REST/web dependency verified a human sign-in on this
  request (Firebase ID token, or the tailnet login header). Valid on the
  ``web`` card and the ``link`` page.
* ``token`` — a personal token presented by an MCP client or the CLI. Valid
  only with the ``approve`` scope, only on ``mcp``/``cli``, and only for
  ``routine`` requests.

``high`` requests additionally need a recent sign-in: a Firebase ``auth_time``
within ``APPROVAL_FRESH_AUTH_SECONDS`` (default 600). A tailnet request is
itself a live human login, so it counts as fresh.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from adk.approvals import ApprovalRequest
    from auth.firebase_auth import User

Channel = Literal["web", "link", "mcp", "cli"]

HUMAN_AUTH_MODES = frozenset({"firebase", "tailnet"})
SESSION_CHANNELS = frozenset({"web", "link"})
TOKEN_CHANNELS = frozenset({"mcp", "cli"})
APPROVE_SCOPE = "approve"
DEFAULT_FRESH_AUTH_SECONDS = 600


@dataclass(frozen=True)
class Credential:
    """How this request authenticated: a human session, or a personal token."""

    kind: Literal["session", "token"]
    id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    # A person-readable name for the credential (a token's name), for receipts and pushes.
    label: str = ""


def fresh_auth_seconds() -> int:
    try:
        return int(os.environ.get("APPROVAL_FRESH_AUTH_SECONDS", DEFAULT_FRESH_AUTH_SECONDS))
    except ValueError:
        return DEFAULT_FRESH_AUTH_SECONDS


def fresh_auth_at(user: User, now: float) -> float | None:
    """When this request's human last proved they're present, or None."""
    if user.auth_mode == "tailnet":
        return now
    return user.auth_time


def can_approve(
    user: User,
    request: ApprovalRequest,
    *,
    channel: Channel,
    credential: Credential,
    now: float | None = None,
    decision: Literal["approve", "cancel"] = "approve",
) -> str | None:
    """None when ``user`` may make ``decision`` on ``request``; otherwise why not.

    The reason is safe to show the caller. Cancelling a pending request never
    writes anything, so it skips the fresh-sign-in requirement but nothing else.
    """
    now = time.time() if now is None else now
    if user.uid != request.owner_uid:
        return "This approval is not yours."
    if user.auth_mode not in HUMAN_AUTH_MODES:
        return "Only a signed-in person can decide approvals; machine and anonymous callers cannot."
    if credential.kind == "token":
        if APPROVE_SCOPE not in credential.scopes:
            return "This token has no approval rights. Mint one with approval rights, or approve in the web app."
        if channel not in TOKEN_CHANNELS:
            return "A token can approve only from an MCP client or the CLI."
        if request.risk != "routine":
            return "This action needs a fresh sign-in. Open the approval link to decide it."
        return None
    if channel not in SESSION_CHANNELS:
        return "A web session can decide only on the web card or the approval page."
    if request.risk == "high" and decision == "approve":
        signed_in = fresh_auth_at(user, now)
        if signed_in is None or now - signed_in > fresh_auth_seconds():
            return "This action needs a recent sign-in. Please sign in again, then approve."
    return None
