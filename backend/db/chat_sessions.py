"""Firestore repository for ChatSessionIndex.

All I/O goes through `db.firestore` helpers — no raw SDK calls here.
`list_sessions_for_document` performs server-side filtering where Firestore
supports it (ownerUid, archivedAt, documentIds array_contains) and
post-filters tagged sessions in Python, since Firestore's ARRAY_CONTAINS_ANY
is unpredictable when the viewer's tag list is empty.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from google.cloud import firestore as _fs

from auth.access_context import AccessContext, can_access
from db.firestore import get_client, get_document, set_document, update_document
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex

logger = logging.getLogger(__name__)

_COLLECTION = "chat_sessions"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def owner_domain_of(email: str | None) -> str:
    """Email → lower-cased domain for the ``ownerDomain`` index field.

    Blank for a missing/malformed address: a blank domain is treated as
    out-of-scope by tenant-scoped analytics, so guessing here would be worse
    than admitting we don't know.
    """
    email = (email or "").strip().lower()
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def create_session_index(
    *,
    session_id: str,
    skill_id: str,
    owner_uid: str,
    access_control: AccessControl,
    document_ids: list[str] | None = None,
    first_message_at: datetime | None = None,
    owner_domain: str = "",
    provisional: bool = False,
) -> ChatSessionIndex:
    """Persist a new ChatSessionIndex row. Idempotent: overwrites if exists.

    ``owner_domain`` is passed by callers that have the authenticated ``User``
    (the synchronous create in ``skill_processor`` and the bootstrap route). The
    ADK callback path only has a uid and leaves it blank; that path is an
    idempotent fallback which normally observes the row already created
    synchronously, and the backfill script covers anything it does write.
    """
    now = first_message_at or _utcnow()
    idx = ChatSessionIndex(
        sessionId=session_id,
        documentIds=list(document_ids) if document_ids else [],
        skillId=skill_id,
        ownerUid=owner_uid,
        ownerDomain=(owner_domain or "").strip().lower(),
        accessControl=access_control,
        firstMessageAt=now,
        lastMessageAt=now,
        provisional=provisional,
    )
    set_document(_COLLECTION, session_id, _to_firestore(idx))
    return idx


def clear_provisional(session_id: str, not_after: datetime | None = None) -> None:
    """Promote a bootstrap-provisional row to a real session (issue #38 follow-up).

    Called on the FIRST real turn. Also re-stamps ``firstMessageAt``: the
    provisional row was stamped when the page mounted, which can be minutes (or
    a whole abandoned sitting) before the user actually said anything, and that
    skews every "how long was this conversation" reading.

    ``not_after`` clamps the new stamp — pass the row's current
    ``last_message_at`` (the turn's own timestamp). Without it the stamp is
    simply "now", which is whenever this call happens to run within the turn;
    verified live on test, that landed 44s AFTER ``lastMessageAt``, i.e. a
    session whose first message came after its last one.
    """
    # ISO STRING, not a datetime: every other write goes through `_to_firestore`,
    # which `.isoformat()`s these fields. Passing a datetime straight to
    # `update_document` makes Firestore store a native timestampValue, so the
    # column ends up mixed-type across rows — Firestore orders by type before
    # value, so a mixed column silently mis-sorts any future order_by.
    stamp = _utcnow()
    if not_after is not None and not_after < stamp:
        stamp = not_after
    update_document(_COLLECTION, session_id, {"provisional": False, "firstMessageAt": stamp.isoformat()})


def save_session_index(idx: ChatSessionIndex) -> None:
    """Overwrite the full index row (used after bumping counters / title)."""
    set_document(_COLLECTION, idx.session_id, _to_firestore(idx))


def update_session_fields(session_id: str, fields: dict) -> None:
    """Partial update — only send changed fields to Firestore."""
    update_document(_COLLECTION, session_id, fields)


def set_session_skill(session_id: str, new_skill_id: str, prev_skill_id: str | None = None) -> None:
    """Point the session index at the skill of its most recent turn (v6.10.0
    unified-adk-handoff / confirm→switch).

    The switch re-issues a turn on the specialist over the SAME thread; the row
    was created on the door, so without this the door's ``skillId`` sticks and
    the surface-action binding gate (URL skill == session skill) 403s the
    specialist's own form. ``skillHistory`` keeps an audit trail of the chain
    (ArrayUnion is idempotent — re-entering a skill doesn't duplicate)."""
    history = [s for s in (prev_skill_id, new_skill_id) if s]
    update_document(
        _COLLECTION,
        session_id,
        {
            "skillId": new_skill_id,
            "skillHistory": _fs.ArrayUnion(history),
        },
    )


def add_session_documents(session_id: str, doc_ids: list[str]) -> None:
    """Atomically add doc ids to the session's ``documentIds`` array.

    Uses Firestore ``ArrayUnion`` so concurrent turns don't clobber each
    other and re-adding an existing id is a no-op. Called by
    ``make_document_loader`` whenever new tabs are loaded mid-session.
    """
    if not doc_ids:
        return
    update_document(
        _COLLECTION,
        session_id,
        {
            "documentIds": _fs.ArrayUnion(list(doc_ids)),
        },
    )


def soft_delete_session(session_id: str) -> None:
    """Soft-delete: set archivedAt to now. Owner-only gate is in the route."""
    update_document(_COLLECTION, session_id, {"archivedAt": _utcnow().isoformat()})


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_session_index(session_id: str) -> ChatSessionIndex | None:
    """Return the index row or None if it doesn't exist."""
    data = get_document(_COLLECTION, session_id)
    if data is None:
        return None
    return _from_firestore(data, session_id)


SessionFilter = Literal["mine", "team", "all"]


def list_sessions_for_document(
    doc_id: str,
    viewer_ctx: AccessContext,
    filter: SessionFilter = "all",
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[ChatSessionIndex], str | None]:
    """List non-archived sessions for a document visible to the viewer.

    Returns (sessions, next_cursor). next_cursor is the last sessionId in the
    page, or None when the page is the last one. Cursor-based pagination is
    implemented by re-querying and skipping to the cursor document — Firestore
    start_after() requires a DocumentSnapshot, so we fetch a small page-sized
    window after the cursor document.

    Access semantics:
    - filter=mine: only sessions owned by viewer (fast; single equality query)
    - filter=team: sessions where can_access() passes AND not owned by viewer
    - filter=all:  union of mine + team (post-filtered by can_access())

    Post-filter is needed because Firestore's ARRAY_CONTAINS_ANY returns no
    results when the provided array is empty (viewer has no tags), which would
    incorrectly hide public/domain/specific sessions the viewer can see.
    """
    client = get_client()
    col = client.collection(_COLLECTION)

    # Base query: non-archived sessions whose documentIds list contains this
    # doc, newest first. ``array_contains`` is the canonical way to find a
    # value inside an array field — sessions with multiple docs match each
    # of their docs' panels.
    query = (
        col.where(filter=_fs.FieldFilter("documentIds", "array_contains", doc_id))
        .where(filter=_fs.FieldFilter("archivedAt", "==", None))
        .order_by("lastMessageAt", direction=_fs.Query.DESCENDING)
        .limit(page_size * 4)  # over-fetch to absorb post-filter losses
    )

    # Apply cursor: start after the cursor document
    if cursor:
        cursor_doc = col.document(cursor).get()
        if cursor_doc.exists:
            query = query.start_after(cursor_doc)

    # Pull raw documents and post-filter by access. Stream errors typically
    # mean the composite index hasn't been deployed — Firestore's error
    # carries the create-index URL, so re-raise after logging so the dev
    # sees both the intent ("missing index") and the actionable link.
    try:
        snaps = list(query.stream())
    except Exception as exc:
        logger.error(
            "list_sessions_for_document failed for doc=%s — likely missing "
            "Firestore index for chat_sessions[documentIds array_contains, "
            "archivedAt ==, lastMessageAt desc]. Underlying: %s",
            doc_id,
            exc,
        )
        raise

    results: list[ChatSessionIndex] = []
    last_id: str | None = None
    for snap in snaps:
        if snap.id is None or not snap.exists:
            continue
        data = snap.to_dict()
        if data is None:
            continue
        try:
            idx = _from_firestore(data, snap.id)
        except Exception as exc:
            logger.warning("malformed chat_sessions/%s: %s", snap.id, exc)
            continue

        if not can_access(idx.access_control, viewer_ctx, idx.owner_uid):
            continue

        if filter == "mine" and idx.owner_uid != viewer_ctx.uid:
            continue
        if filter == "team" and idx.owner_uid == viewer_ctx.uid:
            continue

        # Hide bootstrap-provisional rows: a chat the user opened but never sent
        # a message in is not history. Post-filtered rather than a Firestore
        # `where(provisional == False)`, which would silently drop every legacy
        # row written before the field existed.
        if getattr(idx, "provisional", False):
            last_id = snap.id
            continue
        results.append(idx)
        last_id = snap.id
        if len(results) >= page_size:
            break

    next_cursor = last_id if len(results) == page_size else None
    return results, next_cursor


def list_sessions_for_skill(
    skill_id: str | None,
    owner_uid: str,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[ChatSessionIndex], str | None]:
    """List non-archived sessions owned by owner_uid, newest first.

    ``skill_id=None`` lists across ALL skills. That matters because a session
    belongs to exactly one skill, and switching agent via the top bar starts a
    NEW session on the new skill (``skillHref`` carries no ``?session=``) — so a
    sitting that moved between agents is split into one session per skill, each
    visible only under that skill. Scoping history per-skill therefore hides
    most of a user's own conversations from wherever they happen to be standing;
    2026-08-05 a 7-turn sitting across two agents read as "it didn't record my
    session". The cross-skill listing is the history the user actually had.

    Returns (sessions, next_cursor). Only the owner's sessions are returned —
    no access-control fan-out needed because this endpoint is owner-only.

    Both shapes are served by the same composite index
    (ownerUid ==, archivedAt ==, lastMessageAt desc) that
    ``most_recent_session_for_user`` already relies on; the skill-scoped variant
    adds skillId ==, which is the pre-existing index.
    """
    client = get_client()
    col = client.collection(_COLLECTION)

    query = col
    if skill_id is not None:
        query = query.where(filter=_fs.FieldFilter("skillId", "==", skill_id))
    query = (
        query.where(filter=_fs.FieldFilter("ownerUid", "==", owner_uid))
        .where(filter=_fs.FieldFilter("archivedAt", "==", None))
        .order_by("lastMessageAt", direction=_fs.Query.DESCENDING)
        .limit(page_size + 1)  # fetch one extra to detect next page
    )

    if cursor:
        cursor_doc = col.document(cursor).get()
        if cursor_doc.exists:
            query = query.start_after(cursor_doc)

    results: list[ChatSessionIndex] = []
    last_id: str | None = None
    for snap in query.stream():
        if snap.id is None or not snap.exists:
            continue
        data = snap.to_dict()
        if data is None:
            continue
        try:
            idx = _from_firestore(data, snap.id)
        except Exception as exc:
            logger.warning("malformed chat_sessions/%s: %s", snap.id, exc)
            continue
        # Hide bootstrap-provisional rows: a chat the user opened but never sent
        # a message in is not history. Post-filtered rather than a Firestore
        # `where(provisional == False)`, which would silently drop every legacy
        # row written before the field existed.
        if getattr(idx, "provisional", False):
            last_id = snap.id
            continue
        results.append(idx)
        last_id = snap.id
        if len(results) >= page_size:
            break

    next_cursor = last_id if len(results) == page_size else None
    return results, next_cursor


def most_recent_session_for_user(owner_uid: str, limit: int = 10) -> list[ChatSessionIndex]:
    """Return the user's most-recent non-archived sessions across ALL skills,
    newest first (up to `limit`).

    v6.5.0 AUTH-LANDING: powers `GET /api/sessions/recent`, which lands a
    signed-in user back in their last chat. Returns a small batch (not just
    one) so the caller can skip sessions whose skill is no longer visible or
    enabled and fall through to the next candidate. Owner-scoped — only
    `owner_uid`'s own sessions.
    """
    client = get_client()
    col = client.collection(_COLLECTION)
    query = (
        col.where(filter=_fs.FieldFilter("ownerUid", "==", owner_uid))
        .where(filter=_fs.FieldFilter("archivedAt", "==", None))
        .order_by("lastMessageAt", direction=_fs.Query.DESCENDING)
        .limit(limit)
    )
    results: list[ChatSessionIndex] = []
    for snap in query.stream():
        if snap.id is None or not snap.exists:
            continue
        data = snap.to_dict()
        if data is None:
            continue
        try:
            results.append(_from_firestore(data, snap.id))
        except Exception as exc:
            logger.warning("malformed chat_sessions/%s: %s", snap.id, exc)
            continue
    return results


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _to_firestore(idx: ChatSessionIndex) -> dict:
    """Convert to a flat dict suitable for Firestore set()."""
    d = idx.model_dump(by_alias=True, exclude_none=False)
    # Convert datetimes to ISO strings for consistent storage
    for key in ("firstMessageAt", "lastMessageAt", "archivedAt"):
        val = d.get(key)
        if isinstance(val, datetime):
            d[key] = val.isoformat()
    # Nested AccessControl → dict
    if "accessControl" in d and hasattr(d["accessControl"], "model_dump"):
        d["accessControl"] = d["accessControl"].model_dump(exclude_none=True)
    return d


def _from_firestore(data: dict, doc_id: str) -> ChatSessionIndex:
    """Hydrate a ChatSessionIndex from a Firestore document dict."""
    # Ensure sessionId is populated from doc ID if not stored explicitly
    if "sessionId" not in data:
        data = {**data, "sessionId": doc_id}
    return ChatSessionIndex.model_validate(data)
