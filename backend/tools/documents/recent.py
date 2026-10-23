"""The documents a user has been working with, most recent first.

Powers ``GET /api/documents/recent`` — the "Pick up where you left off" list
the workbench picker shows instead of a skill's shared demo documents.

"Worked with" is two signals merged:

- **Used in a conversation.** ``chat_sessions.documentIds`` records every
  document ever attached to a session; the session's ``lastMessageAt`` is when
  the user last worked with it. This is the strong signal — it is what the user
  actually did, not just what arrived in their library.
- **Arrived in the library.** ``parsed_documents.createdAt`` for documents
  uploaded, imported or generated but not yet discussed — so a file dropped in
  a minute ago shows up before its first question.

Owner-scoped throughout: a session row can name a document the caller does not
own (a shared thread), so every candidate is re-read and dropped unless its
``userId`` is the caller.
"""

from __future__ import annotations

import logging
from typing import Any

from db.chat_sessions import most_recent_session_for_user
from db.firestore import get_document
from tools.documents.context import list_documents_for_user

log = logging.getLogger(__name__)

_PARSED_DOCS_COLLECTION = "parsed_documents"

# How many sessions to walk for document ids. A session usually carries 1-3
# documents, so this comfortably fills a list of 12 without a deep scan.
_SESSION_SCAN = 30

# Metadata the picker needs. Parsed blocks are deliberately absent — one
# listing of eleven documents was 2.2 MB with them.
_FIELDS = (
    "originalFilename",
    "sourceFormat",
    "parseStatus",
    "folderId",
    "source",
    "createdAt",
    "updatedAt",
    # Projects the document is tagged into (projects.md §5), for project chips.
    "projectIds",
)


def _iso(value: Any) -> str | None:
    """Timestamps arrive as ISO strings (parsed_documents) or datetimes (sessions)."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def recent_documents_for_user(user_id: str, limit: int = 12) -> list[dict[str, Any]]:
    """The caller's documents, ordered by when they last worked with them.

    Args:
        user_id: The caller's uid. Only documents they own are returned.
        limit: Maximum number of documents.

    Returns:
        Newest first. Each row has ``id``, the metadata in ``_FIELDS``,
        ``lastUsedAt``, and — when the document was used in a conversation —
        ``lastSessionId`` / ``lastSessionTitle`` for that most recent one.
    """
    # doc id → (lastUsedAt iso, session id, session title)
    used: dict[str, tuple[str, str | None, str | None]] = {}
    try:
        sessions = most_recent_session_for_user(user_id, limit=_SESSION_SCAN)
    except Exception as exc:  # pragma: no cover — Firestore outage
        log.warning("recent documents: session scan failed for %s: %s", user_id, exc)
        sessions = []
    for s in sessions:
        if getattr(s, "provisional", False):
            continue
        at = _iso(s.last_message_at) or ""
        for doc_id in s.document_ids:
            # Sessions arrive newest first, so the first sighting is the latest use.
            if doc_id and doc_id not in used:
                used[doc_id] = (at, s.session_id, s.title)

    rows: dict[str, dict[str, Any]] = {}
    for doc_id, (at, session_id, title) in used.items():
        if len(rows) >= limit * 2:
            break
        doc = get_document(_PARSED_DOCS_COLLECTION, doc_id)
        if not doc or doc.get("userId") != user_id or doc.get("parseStatus") == "failed":
            continue
        rows[doc_id] = _row(doc_id, doc, last_used=at, session_id=session_id, session_title=title)

    for doc in list_documents_for_user(user_id, limit=limit):
        doc_id = doc.get("__id") or doc.get("id")
        if not doc_id or doc_id in rows:
            continue
        rows[doc_id] = _row(doc_id, doc, last_used=_iso(doc.get("createdAt")) or "")

    ordered = sorted(rows.values(), key=lambda r: r["lastUsedAt"] or "", reverse=True)
    return ordered[:limit]


def _row(
    doc_id: str,
    doc: dict[str, Any],
    *,
    last_used: str,
    session_id: str | None = None,
    session_title: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": doc_id, **{k: doc.get(k) for k in _FIELDS}}
    row["lastUsedAt"] = last_used
    row["lastSessionId"] = session_id
    row["lastSessionTitle"] = session_title
    return row
