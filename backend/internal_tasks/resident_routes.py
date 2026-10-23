"""`POST /internal/resident/notify` — the resident agent's push webhook (M5a.2).

WHY THIS ROUTE HAS ITS OWN GATE AND NOT `internal_tasks.auth`

M5's written criterion asked for the OIDC gate next door, matching one exact
service account. That would reject every real webhook. The instance sends the
A2A push configuration's bearer token — the protocol's own mechanism — not a
Google ID token; and there is no single SA to match, because its callers are
per-user `res-<hash>@…` accounts in ANOTHER project that grow with every user,
which this platform should not be enumerating (design D11).
See HANDOVER-2026-09-04 §9.3.

The per-task token is the better gate on its own terms: platform-minted,
scoped to the tenant task, and needing no cross-project identity knowledge. What it
DOES borrow from `internal_tasks.auth` is the posture — constant-time compare,
403 for everything, and never a hint about which check failed.

THIS ROUTE IS REACHABLE FROM THE INTERNET. The backend that runs
`ask_assistant` is the sidecar of the frontend service, deployed
`--allow-unauthenticated`, with no Next middleware and an `/api/proxy/**`
catch-all that forwards `Authorization` untouched. Nothing upstream of this
handler checks anything, which is also true of `/internal/tasks/recompact` and
is the reason both gates are written as if they were the only one — they are.

WHY THE BODY IS READ RAW

A Pydantic body model would have FastAPI answer 422 for a malformed body
BEFORE the gate ran, telling an unauthenticated caller that the route exists
and what shape it wants. Every fault here is one 403 instead.

STATUS CODES ARE NOT A RETRY PROTOCOL HERE, unlike the Cloud Tasks route.
`notify()` logs a warning on a non-2xx and moves on — nothing re-delivers. So a
push that cannot be honoured is recorded loudly on THIS side, where somebody
can act on it, rather than being handed back to a peer that will drop it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from google.adk.events.event import Event
from google.genai import types

from adk.session import get_session_service
from db import resident_tasks

logger = logging.getLogger(__name__)

_PREFIX = "/internal/resident"

# include_in_schema=False: /openapi.json is reachable through the same public
# proxy, and there is no reason to publish the receiver's existence and shape
# to everyone who can read it.
router = APIRouter(prefix=_PREFIX, tags=["internal-tasks"], include_in_schema=False)

# The one string both sides must agree on. `ask_assistant` builds the webhook
# URL from THIS constant rather than retyping the path, because the last time
# two repos held the same route as two literals it cost an afternoon: the
# platform POSTed `/start` while the coordinator served `/instances/start`, and
# all 23 tests stubbed the call that would have caught it (HANDOVER §3).
NOTIFY_PATH = f"{_PREFIX}/notify"

# The author on the appended event. Read by a human in the transcript and by
# the model on the next turn, so it is a name and not an address — never the
# sanitized agent name or the `res-<hash>` account behind it (CLAUDE.md #9).
RESIDENT_AUTHOR = "resident_assistant"

# The peer's own vocabulary. `input-required` is deliberately absent: it is a
# real outcome but not an ENDING, and `a2a_client.TERMINAL_STATES` includes it
# for the opposite reason — a poll must stop there, a receiver must not.
_TERMINAL = resident_tasks.TERMINAL_STATES

# A body larger than this is not a task update. Read the stream with a bound
# rather than letting an unauthenticated caller decide how much memory to take.
_MAX_BODY_BYTES = 256 * 1024


def _reject() -> HTTPException:
    """One rejection for every fault. Callers must not be able to tell whether
    a task id exists by the shape of the refusal."""
    return HTTPException(status_code=403, detail="Not authorized")


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "") or ""
    if not header.startswith("Bearer "):
        raise _reject()
    token = header[len("Bearer ") :].strip()
    if not token:
        raise _reject()
    return token


def _text_of(task: dict[str, Any]) -> str:
    """The peer's words for this task.

    `answer_of` reads the artifact, which the instance keeps current as deltas
    arrive. A failure often has no artifact at all and puts its reason in the
    status message, so that is the fallback — reporting "no answer" for a task
    that explained itself would be the worse of the two silences.
    """
    from protocols.a2a_client import answer_of

    try:
        text = answer_of(task)
    except Exception:
        text = ""
    if not text.strip():
        status = task.get("status")
        message = (status.get("message") if isinstance(status, dict) else None) or {}
        parts = message.get("parts") if isinstance(message, dict) else None
        text = " ".join(
            str(p.get("text") or "") for p in (parts or []) if isinstance(p, dict) and p.get("kind") == "text"
        )
    text = text.strip()
    # The peer caps its artifact at 16,000 characters; a token holder is not
    # bound by the peer's code, and an uncapped answer lands in every
    # subsequent turn's context for the life of the conversation.
    if len(text) > resident_tasks.MAX_ANSWER_CHARS:
        text = text[: resident_tasks.MAX_ANSWER_CHARS] + "\n\n[…truncated]"
    return text


def _compose(state: str, text: str) -> str:
    """What the user reads when this appears in a conversation they had left.

    It has to say WHY a message arrived unprompted, because from their side
    nothing was asked just now.
    """
    if state == "completed":
        body = text or "It finished, but returned nothing readable."
        return f"Your resident assistant finished the question from earlier:\n\n{body}"
    reason = text or "no reason was given"
    return f"Your resident assistant could not finish the question from earlier — {reason}"


@router.post("/notify")
async def resident_notify(request: Request) -> dict:
    """Accept one A2A task update from a resident instance.

    `working` / `submitted` are the common case by volume — the instance
    notifies on EVERY state change — and cost one Firestore write.
    `input-required` is recorded and deliberately NOT terminalised: replying to
    it is M5b, which is blocked on M9, and marking it delivered here would drop
    the answer that eventually arrives.
    """
    token = _bearer(request)

    try:
        raw = await request.body()
    except Exception:
        raise _reject() from None
    if len(raw) > _MAX_BODY_BYTES:
        raise _reject()
    try:
        task = json.loads(raw)
    except Exception:
        raise _reject() from None
    if not isinstance(task, dict):
        raise _reject()

    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise _reject()

    # verify_token answers None for EVERY fault, including an unusable document
    # id and a Firestore outage. Anything raising past it is a bug rather than
    # a caller's doing, but it must still not leave through this door as a 500.
    try:
        record = resident_tasks.verify_token(task_id, token)
    except Exception:
        logger.exception("resident_notify: gate raised for task=%r", task_id)
        raise _reject() from None
    if record is None:
        raise _reject()

    status = task.get("status")
    state = str((status.get("state") if isinstance(status, dict) else None) or "unknown")
    try:
        resident_tasks.record_state(task_id, state)
    except Exception:
        # Bookkeeping. Losing it must not cost the delivery underneath it.
        logger.warning("resident_notify: could not record state for task=%s", task_id)

    if state not in _TERMINAL:
        # Includes `input-required`: notified, not terminalised.
        logger.info("resident_notify: task=%s state=%s (not terminal)", task_id, state)
        return {"ok": True, "delivered": False, "state": state}

    # Claim BEFORE writing. At-most-once is the property worth having: a
    # duplicated answer is visible noise in someone's transcript, while a lost
    # one is recoverable by asking again. The claim is released if the write
    # fails, so a spent claim never stands in for a message nobody got.
    text = _text_of(task)

    # The claim and the ANSWER land in one write, so a process killed a
    # millisecond later leaves a record that still knows what was to be said.
    if not resident_tasks.mark_delivered(task_id, answer=text):
        logger.info("resident_notify: task=%s state=%s already delivered", task_id, state)
        return {"ok": True, "delivered": False, "state": state}

    try:
        delivered = await _append_to_session(record, state, text)
    except Exception:
        # Log FIRST. Releasing first meant that if the release also failed —
        # likely, since the same outage probably broke the append — the only
        # line naming this task never got written.
        logger.exception("resident_notify: task=%s could not be written to session", task_id)
        _release(task_id, "session write failed")
        raise HTTPException(status_code=500, detail="Delivery failed") from None

    if not delivered:
        _release(task_id, "session no longer exists")
        return {"ok": True, "delivered": False, "state": state, "reason": "session_unavailable"}

    _touch_session_index(record)

    logger.info(
        "resident_notify: task=%s state=%s delivered uid=%s session=%s",
        task_id,
        state,
        record.user_id,
        record.session_id,
    )
    return {"ok": True, "delivered": True, "state": state}


async def _append_to_session(record: resident_tasks.ResidentTask, state: str, text: str) -> bool:
    """Write the answer into the conversation that asked for it.

    No new read path is needed: `_events_to_messages`
    (protocols/sessions_route.py) renders any event with text and
    `author != "user"` as an assistant message, so this shows up wherever the
    transcript does.

    The session is addressed by the RECORDED triple, never by anything in the
    webhook body — a peer that could name the session could write into somebody
    else's conversation.

    A missing session is NOT auto-created. The a2ui action route does create
    one, because an action without a session is meaningless; here the session
    was made by a real chat turn, so its absence means it was deleted or an
    in-memory store was reset, and resurrecting a deleted conversation to put
    an answer in it is a worse outcome than a loud log.
    """
    service = get_session_service()
    session = await service.get_session(
        app_name=record.app_name,
        user_id=record.user_id,
        session_id=record.session_id,
    )
    if session is None:
        logger.warning(
            "resident_notify: session gone — answer NOT delivered task=%s uid=%s session=%s state=%s chars=%d",
            record.task_id,
            record.user_id,
            record.session_id,
            state,
            len(text),
        )
        return False

    event = Event(
        invocation_id=f"resident_notify_{record.task_id}",
        author=RESIDENT_AUTHOR,
        content=types.Content(role="model", parts=[types.Part(text=_compose(state, text))]),
        timestamp=time.time(),
    )
    await service.append_event(session=session, event=event)
    return True


def _release(task_id: str, reason: str) -> None:
    """Hand the claim back with the reason attached, and never raise doing it.

    The answer stays on the record either way, so an undelivered one is
    recoverable rather than merely regrettable — which matters because nothing
    retries: `notify()` logs its non-2xx and moves on (a2a.mjs:180).
    """
    try:
        resident_tasks.release_delivery(task_id, error=reason)
    except Exception:
        logger.exception("resident_notify: task=%s claim NOT released (%s)", task_id, reason)


def _touch_session_index(record: resident_tasks.ResidentTask) -> None:
    """Move the conversation to the top of the user's list.

    Without this the message is there and nothing points at it: the sidebar
    orders on `lastMessageAt`, and a user who never re-opens that particular
    conversation has no way to learn an answer landed in it. Best-effort —
    a message in the session beats a message nobody indexed.
    """
    try:
        from datetime import UTC, datetime

        from db import chat_sessions

        # ISO STRING, not a datetime. Every other write to these columns goes
        # through `_to_firestore`, which isoformats them; a native timestamp
        # here makes the column mixed-type, and Firestore orders by type before
        # value, so the sidebar's `order_by(lastMessageAt)` would silently
        # mis-sort (db/chat_sessions.py::clear_provisional says the same).
        chat_sessions.update_session_fields(record.session_id, {"lastMessageAt": datetime.now(UTC).isoformat()})
    except Exception:
        logger.warning("resident_notify: session index not touched for %s", record.session_id)
