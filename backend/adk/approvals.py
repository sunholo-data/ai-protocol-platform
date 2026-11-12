"""Human approvals: one decision about one exact stored action, from any surface.

Some tools prepare a write that only a person may release (a time entry, an
invoice). The tool keeps the exact payload in its own store and registers an
:class:`ApprovalProvider` here. Every surface then decides through
:func:`decide`: the web card, the ``/approve/<id>`` page, MCP elicitation and
the CLI. This module adds what the protocols leave open:

* **who** may decide — :func:`auth.approvals.can_approve`, one policy;
* **what** was approved — the decision carries the digest the person
  reviewed, and a stale digest is refused before the provider runs;
* **once** — a decided or expired request answers 410;
* an **audit** record of every decision (``approval_decisions``).

Providers stay authoritative for their payloads; this layer stores nothing
but the audit. Review text is written by the provider from the stored
payload, never by a model. Design: docs/design/v6.41.0/human-approvals.md.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from db.firestore import set_document

if TYPE_CHECKING:
    from auth.approvals import Channel, Credential
    from auth.firebase_auth import User

logger = logging.getLogger(__name__)

Risk = Literal["routine", "high"]
Decision = Literal["approve", "cancel"]
AUDIT_COLLECTION = "approval_decisions"


@dataclass(frozen=True)
class ApprovalRequest:
    """A pending decision about one exact stored payload. ``id`` is ``<kind>:<ref>``."""

    id: str
    owner_uid: str
    session_id: str
    risk: Risk
    title: str
    review: str
    digest: str
    expires_at: float
    state: str

    def view(self) -> dict[str, Any]:
        """What a client renders and echoes back. Never includes the owner."""
        return {
            "id": self.id,
            "title": self.title,
            "review": self.review,
            "risk": self.risk,
            "digest": self.digest,
            "expires_at": self.expires_at,
            "state": self.state,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class Approver:
    """Who decided and how; recorded on the receipt and in the audit."""

    uid: str
    channel: str
    credential: str
    fresh_auth_at: float | None = None


@dataclass(frozen=True)
class Receipt:
    state: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class ApprovalError(Exception):
    """A refusal with the HTTP status a REST caller should see; ``str()`` is safe to show."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ApprovalProvider(Protocol):
    kind: str

    def pending(self, uid: str, session_id: str | None) -> list[ApprovalRequest]: ...

    def get(self, ref: str, uid: str) -> ApprovalRequest | None: ...

    async def decide(self, ref: str, digest: str, decision: Decision, approver: Approver) -> Receipt: ...


_providers: dict[str, ApprovalProvider] = {}


def register_provider(provider: ApprovalProvider) -> None:
    _providers[provider.kind] = provider


def clear_registry() -> None:
    """Test hook only."""
    _providers.clear()


def _split(approval_id: str) -> tuple[ApprovalProvider, str]:
    kind, _, ref = approval_id.partition(":")
    provider = _providers.get(kind)
    if provider is None or not ref:
        raise ApprovalError(404, "No such approval.")
    return provider, ref


def pending_for(uid: str, session_id: str | None = None) -> list[ApprovalRequest]:
    """Every pending request this person owns, across providers. One broken store hides only itself."""
    found: list[ApprovalRequest] = []
    for provider in list(_providers.values()):
        try:
            found.extend(provider.pending(uid, session_id))
        except Exception:
            logger.exception("approvals: provider %s could not list pending requests", provider.kind)
    return found


def get(approval_id: str, uid: str) -> ApprovalRequest:
    """The request, if ``uid`` owns it; 404 otherwise (never reveal someone else's)."""
    provider, ref = _split(approval_id)
    request = provider.get(ref, uid)
    if request is None or request.owner_uid != uid:
        raise ApprovalError(404, "No such approval.")
    return request


async def decide(
    approval_id: str,
    *,
    digest: str,
    decision: Decision,
    user: User,
    channel: Channel,
    credential: Credential,
    now: float | None = None,
) -> Receipt:
    """Decide ``approval_id`` exactly as reviewed, once, if the policy allows it."""
    from auth.approvals import can_approve

    now = time.time() if now is None else now
    request = get(approval_id, user.uid)
    if request.state != "pending":
        raise ApprovalError(410, f"This approval is already {request.state}.")
    if request.expires_at <= now:
        raise ApprovalError(410, "This approval has expired without being submitted.")
    if digest != request.digest:
        raise ApprovalError(409, "This approval changed after you reviewed it. Review it again.")
    refusal = can_approve(user, request, channel=channel, credential=credential, now=now, decision=decision)
    if refusal:
        raise ApprovalError(403, refusal)
    provider, ref = _split(approval_id)
    approver = Approver(user.uid, channel, credential.id, user.auth_time)
    receipt = await provider.decide(ref, digest, decision, approver)
    _audit(request, decision, approver, receipt, now)
    if credential.kind == "token":
        # Out-of-band: a token holder can answer prompts, so tell the owner every time.
        from notifications import hooks

        hooks.notify_token_approval(user.uid, decision, credential.label or credential.id)
    return receipt


def _audit(request: ApprovalRequest, decision: str, approver: Approver, receipt: Receipt, at: float) -> None:
    """Record the decision. Never raises: the decision already happened."""
    record = {
        "approval_id": request.id,
        "owner_uid": request.owner_uid,
        "session_id": request.session_id,
        "risk": request.risk,
        "title": request.title,
        "digest": request.digest,
        "decision": decision,
        **{key: value for key, value in asdict(approver).items() if key != "uid"},
        "state": receipt.state,
        "decided_at": at,
    }
    logger.info(
        "approvals: %s %s by %s via %s -> %s", decision, request.id, approver.uid, approver.channel, receipt.state
    )
    try:
        set_document(AUDIT_COLLECTION, uuid.uuid4().hex, record)
    except Exception:
        logger.exception("approvals: audit write failed for %s", request.id)
