"""Per-task correlation records for resident-agent push notifications (M5a.1).

Firestore path: ``resident_tasks/{taskId}``

WHY THIS EXISTS AT ALL

The resident instance's ``notify()`` POSTs the whole A2A task and nothing else
(``ailang/docker/resident/lib/a2a.mjs``). A task carries an id, a contextId, a
state and an artifact — it does not carry a Firebase uid or an ADK session, and
it must not: the instance lives in another estate under another identity and
knowing whose conversation it is answering is not its business (design D11).
So the platform keeps the mapping on its own side and the webhook resolves it.

A RECORD MEANS "NOBODY IS WATCHING THIS TASK ANY MORE"

One owner at a time. While `ask_assistant` is polling, the poll owns the task
and no record exists; a record is written only when the poll gives up, at the
same moment the webhook is registered. Registering up front — which is what
this did first — meant the peer pushed the answer while the poll was ALSO
about to return it, and the user got told twice, the second time prefixed
"from earlier".

WHY IT IS ALSO THE CREDENTIAL STORE

There is no OIDC gate on the receiver. The instance sends the push
configuration's bearer token, not a Google ID token, and its callers are
per-user ``res-<hash>@…`` accounts in another project that grow with every
user — so "an exact service-account match" is both wrong mechanically and
wrong in principle (HANDOVER-2026-09-04 §9.3).

NO TOKEN IS STORED HERE — only ``tokenHash``, and it expires. Same rule as
``resident_agents.keyHash`` (design P7): a Firestore dump must not hand anyone
a working webhook credential, and an abandoned task must not leave a live one
lying about for ever.

THE ANSWER IS PERSISTED BEFORE IT IS DELIVERED. A session that has been
deleted, a session store having a bad minute, or an instance killed mid-write
would otherwise discard the peer's answer with nothing to show for it — and
nothing re-delivers, because ``notify()`` does not retry. Recording the text
and the reason turns a silent loss into a loss somebody can see and act on.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.firestore import get_client

logger = logging.getLogger(__name__)

COLLECTION = "resident_tasks"

# Last-observed A2A state. The peer's words (`a2a.mjs` TaskState), not a
# parallel vocabulary of our own — inventing one is how two sides stop agreeing
# about what happened.
STATE_PENDING = "pending"
TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})

# A task nobody claimed within a day is abandoned, and its token should stop
# working. Without this, a record written for a task whose send then failed
# leaves a usable webhook credential in Firestore indefinitely.
DEFAULT_TTL_HOURS = 24

# The peer caps its own artifact at 16,000 characters, but a token holder is
# not bound by the peer's code. An uncapped answer either fails the session
# write or lands and inflates every subsequent turn of that conversation.
MAX_ANSWER_CHARS = 16_000
MAX_STATE_CHARS = 64

# Firestore rejects `.`, `..`, `__*__` and ids over 1500 bytes with
# InvalidArgument — which is NOT a ValueError, so it would escape the gate as a
# 500 while the same input returned 403 locally. The id is chosen by the peer,
# so it is validated before it reaches any client at all.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

# One spelling per stored field, used by the model AND by every read and write
# below. Six hand-copied string literals is how a rename updates the model and
# leaves `mark_delivered` reading a key that no longer exists — which would
# make every terminal push claim, and the tests would stay green.
F_TASK_ID = "taskId"
F_USER_ID = "userId"
F_SESSION_ID = "sessionId"
F_APP_NAME = "appName"
F_TOKEN_HASH = "tokenHash"
F_STATE = "state"
F_CREATED_AT = "createdAt"
F_EXPIRES_AT = "expiresAt"
F_DELIVERED_AT = "deliveredAt"
F_ANSWER = "answer"
F_DELIVERY_ERROR = "deliveryError"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ResidentTask(BaseModel):
    """Who to answer, the one credential that may say so, and what was said."""

    task_id: str = Field(alias=F_TASK_ID)
    # The Firebase uid, NOT an email — `ask_assistant` reads
    # `invocation.user_id`, which `stream_skill` sets to `user.uid`. Appending
    # to an ADK session needs the same triple the session was created under.
    user_id: str = Field(alias=F_USER_ID)
    session_id: str = Field(alias=F_SESSION_ID)
    app_name: str = Field(alias=F_APP_NAME)
    # Handle, not the token. Enough to verify, useless if exfiltrated.
    token_hash: str = Field(alias=F_TOKEN_HASH)
    state: str = STATE_PENDING
    created_at: datetime | None = Field(default=None, alias=F_CREATED_AT)
    expires_at: datetime | None = Field(default=None, alias=F_EXPIRES_AT)
    # Set once, when the answer has been recorded. The instance notifies on
    # every transition, so this is what makes a duplicate `completed` a no-op
    # instead of a second answer.
    delivered_at: datetime | None = Field(default=None, alias=F_DELIVERED_AT)
    # The peer's words, kept so a delivery that fails is recoverable rather
    # than merely regrettable.
    answer: str | None = None
    delivery_error: str | None = Field(default=None, alias=F_DELIVERY_ERROR)

    model_config = ConfigDict(populate_by_name=True)


def _ref(task_id: str):
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
        raise ValueError(f"task_id is not a usable document id: {task_id!r}")
    return get_client().collection(COLLECTION).document(task_id)


def create(
    task_id: str,
    *,
    user_id: str,
    session_id: str,
    app_name: str,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> tuple[ResidentTask, str]:
    """Register a task and mint its webhook token. Returns (record, plaintext).

    The plaintext is returned ONCE. Callers hand it to the peer as the push
    configuration's token and keep no copy.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        F_TASK_ID: task_id,
        F_USER_ID: user_id,
        F_SESSION_ID: session_id,
        F_APP_NAME: app_name,
        F_TOKEN_HASH: hash_token(token),
        F_STATE: STATE_PENDING,
        F_CREATED_AT: now,
        F_EXPIRES_AT: now + timedelta(hours=ttl_hours),
        F_DELIVERED_AT: None,
        F_ANSWER: None,
        F_DELIVERY_ERROR: None,
    }
    _ref(task_id).set(data)
    return ResidentTask(**data), token


def get(task_id: str) -> ResidentTask | None:
    doc = _ref(task_id).get()
    if not getattr(doc, "exists", False):
        return None
    return ResidentTask(**(doc.to_dict() or {}))


def verify_token(task_id: str, presented: str) -> ResidentTask | None:
    """The record this token is good for, or None.

    ONE return value for every failure — unknown task, unusable id, wrong
    token, empty token, expired token, unreadable record — because the route
    turns all of them into one indistinguishable 403 and a caller must not be
    able to tell which it hit. That includes a Firestore outage: a 500 there
    would answer "this backend can reach its database" to anyone who asked.
    """
    if not presented:
        return None
    try:
        rec = get(task_id)
    except ValueError:
        return None
    except Exception as exc:  # Firestore unreachable, or a record that will not parse
        logger.warning("resident_tasks: lookup failed for %r: %s", task_id, exc)
        return None
    if rec is None:
        # Hash the presented token anyway. Returning early on a missing record
        # is what turns "does this task exist" into a timing oracle.
        hash_token(presented)
        return None
    if rec.expires_at is not None and rec.expires_at < datetime.now(UTC):
        logger.info("resident_tasks: %s presented an expired token", task_id)
        return None
    if not hmac.compare_digest(rec.token_hash, hash_token(presented)):
        return None
    return rec


def record_state(task_id: str, state: str) -> None:
    """Note the peer's latest state. A no-op for a task we do not know.

    A terminal state is never overwritten by a later non-terminal one. The
    peer's notifies are fire-and-forget and can arrive out of order, so a
    trailing `working` would otherwise leave a record reading
    ``state=working, deliveredAt=<set>`` — a contradiction for whatever reads
    it next.
    """
    state = (state or "")[:MAX_STATE_CHARS]
    ref = _ref(task_id)
    doc = ref.get()
    if not getattr(doc, "exists", False):
        return
    current = (doc.to_dict() or {}).get(F_STATE) or ""
    if current in TERMINAL_STATES and state not in TERMINAL_STATES:
        return
    ref.update({F_STATE: state})


def mark_delivered(task_id: str, *, answer: str | None = None) -> bool:
    """Claim the right to write this task's answer, recording the answer itself.

    True exactly once per task; False on a repeat or an unknown task. The text
    is stored in the SAME write as the claim, so a process killed immediately
    afterwards leaves a record that still knows what was supposed to be said.

    Read-then-write, not a transaction. Within one process the Firestore client
    is synchronous and blocks the loop between the read and the write, so the
    duplicate that matters — two notifies of the same terminal state — is
    handled. Across two Cloud Run instances of an autoscaled service both could
    still read an unclaimed record; the cost is one duplicated message, and the
    peer's `notify()` is fire-and-forget (a2a.mjs:141) rather than sequential,
    so this is a real if narrow race. A write precondition would close it and
    the in-memory client LOCAL_MODE runs on cannot express one.
    """
    ref = _ref(task_id)
    doc = ref.get()
    if not getattr(doc, "exists", False):
        return False
    if (doc.to_dict() or {}).get(F_DELIVERED_AT):
        return False
    update: dict[str, Any] = {F_DELIVERED_AT: datetime.now(UTC), F_DELIVERY_ERROR: None}
    if answer is not None:
        update[F_ANSWER] = answer[:MAX_ANSWER_CHARS]
    ref.update(update)
    return True


def release_delivery(task_id: str, *, error: str) -> None:
    """Give the claim back, and say why it was not honoured.

    Without this a write that raised would leave the task reading as delivered
    with nothing in the session — a spent claim standing in for a message
    nobody got. The answer stays on the record, so the loss is recoverable
    rather than merely recorded.
    """
    ref = _ref(task_id)
    if not getattr(ref.get(), "exists", False):
        return
    ref.update({F_DELIVERED_AT: None, F_DELIVERY_ERROR: error[:500]})
