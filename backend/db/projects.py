"""Projects: a named tag on the user's documents, with a home (Firestore).

Design: docs/design/private/projects.md (§5 data model).

Firestore path: ``users/{uid}/projects/{projectId}``. Backend-only — there is
deliberately no ``firestore.rules`` match for it, so client access is denied by
default.

Membership lives on the things that are IN a project, not on the project:

* ``parsed_documents.projectIds`` — the projects a document is tagged into.
* ``chat_sessions.projectId`` — EXPLICIT membership (started in, made into, or
  moved into a project).
* ``chat_sessions.projectIds`` — DERIVED membership, denormalised: the union of
  the projectIds of the documents the chat used. Three writers keep it true, all
  here so there is one implementation:

  1. tagging a document backfills every one of the owner's chats that used it
     (:func:`tag_documents`);
  2. attaching a document to a chat unions its projects onto the chat
     (:func:`add_document_projects_to_session`, called by
     ``db.chat_sessions.add_session_documents``);
  3. untagging or deleting a document recomputes each affected chat from its
     ``documentIds`` (:func:`recompute_session_projects`).

  A failure in any writer never fails the user's action: it is logged, and the
  project's chat listing re-checks derived rows against the session's current
  documents, so a stale row is dropped rather than shown.

Counts are never stored. Document and chat counts are computed at read time, so a
deleted document or an archived chat cannot leave a count wrong.

Owner-scoping is structural: a project id is only ever looked up under the
caller's own ``users/{uid}/projects``, documents are tagged only when their
``userId`` is the caller, and derived membership only ever touches sessions whose
``ownerUid`` is the caller — a document in someone else's shared thread never
pulls their chat into your project.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore as _fs

from db.firestore import get_client, get_document

log = logging.getLogger(__name__)

_DOCS = "parsed_documents"
_SESSIONS = "chat_sessions"

MAX_PROJECTS = 100
MAX_NAME_CHARS = 80
MAX_INSTRUCTIONS_CHARS = 8000
MAX_PROJECTS_PER_DOCUMENT = 50
# Firestore caps a write batch at 500; stay well under it.
_BATCH = 400

# Document metadata a project listing returns — the same fields as
# GET /api/documents/recent. Parsed blocks are deliberately absent.
DOC_FIELDS = (
    "originalFilename",
    "sourceFormat",
    "parseStatus",
    "folderId",
    "source",
    "createdAt",
    "updatedAt",
    "projectIds",
)


class ProjectError(Exception):
    """A refusal the routes turn into ``{code, message}`` with ``status``."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _projects_ref(user_id: str):
    return get_client().collection("users").document(user_id).collection("projects")


def name_key(name: str) -> str:
    return (name or "").strip().casefold()


def clean_name(name: str) -> str:
    """Trimmed project name, or :class:`ProjectError` (422) when unusable."""
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > MAX_NAME_CHARS:
        raise ProjectError("PROJECT_NAME_INVALID", f"A project name must be 1-{MAX_NAME_CHARS} characters.", 422)
    return cleaned


def clean_instructions(text: str | None) -> str:
    text = (text or "").strip()
    if len(text) > MAX_INSTRUCTIONS_CHARS:
        raise ProjectError(
            "PROJECT_INSTRUCTIONS_TOO_LONG",
            f"Project instructions can be at most {MAX_INSTRUCTIONS_CHARS:,} characters.",
            422,
        )
    return text


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def list_projects(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """The caller's projects, most recently opened first."""
    rows: list[dict[str, Any]] = []
    for snap in _projects_ref(user_id).stream():
        data = snap.to_dict()
        if data is None:
            continue
        data["id"] = snap.id
        rows.append(data)
    rows.sort(key=lambda p: str(p.get("lastOpenedAt") or p.get("createdAt") or ""), reverse=True)
    return rows[:limit]


def get_project(user_id: str, project_id: str) -> dict[str, Any] | None:
    """The caller's project, or None. A project id that is not under the caller's
    own subcollection is simply not found — ids cannot be probed."""
    project_id = (project_id or "").strip()
    if not user_id or not project_id or "/" in project_id:
        return None
    snap = _projects_ref(user_id).document(project_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["id"] = project_id
    return data


def find_project_by_name(user_id: str, name: str) -> dict[str, Any] | None:
    key = name_key(name)
    if not key:
        return None
    for project in list_projects(user_id, limit=MAX_PROJECTS):
        if project.get("nameKey") == key or name_key(str(project.get("name") or "")) == key:
            return project
    return None


def resolve_project(user_id: str, query: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Friendly name (or the opaque ref) → the caller's project (CLAUDE.md #9).

    Returns ``(project, candidates)``: the project when the query names exactly
    one, else None plus the names that could be meant (all of them when nothing
    matched), so a caller can offer a choice instead of an empty answer.
    """
    query = (query or "").strip()
    if query.lower().startswith("[ref:") and query.endswith("]"):
        query = query[5:-1].strip()
    projects = list_projects(user_id, limit=MAX_PROJECTS)
    if not query:
        return None, [str(p.get("name") or "") for p in projects]
    for project in projects:
        if project["id"] == query:
            return project, []
    key = name_key(query)
    exact = [p for p in projects if name_key(str(p.get("name") or "")) == key]
    if len(exact) == 1:
        return exact[0], []
    partial = [p for p in projects if key in name_key(str(p.get("name") or ""))]
    if len(partial) == 1:
        return partial[0], []
    pool = partial or projects
    return None, [str(p.get("name") or "") for p in pool]


def unique_name(user_id: str, base: str) -> str:
    """``base``, or ``base (2)``, ``base (3)``… — for names the app pre-fills
    (from a folder, a chat or a document) where a clash should not fail."""
    base = clean_name(base)
    taken = {name_key(str(p.get("name") or "")) for p in list_projects(user_id, limit=MAX_PROJECTS)}
    if name_key(base) not in taken:
        return base
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = f"{base[: MAX_NAME_CHARS - len(suffix)].rstrip()}{suffix}"
        if name_key(candidate) not in taken:
            return candidate
        n += 1


def create_project(
    user_id: str,
    name: str,
    *,
    instructions: str = "",
    default_skill_id: str | None = None,
) -> dict[str, Any]:
    """Create a project. Raises :class:`ProjectError` on a taken name or the limit."""
    name = clean_name(name)
    instructions = clean_instructions(instructions)
    existing = list_projects(user_id, limit=MAX_PROJECTS + 1)
    if any(name_key(str(p.get("name") or "")) == name_key(name) for p in existing):
        raise ProjectError("PROJECT_EXISTS", f"You already have a project called '{name}'.")
    if len(existing) >= MAX_PROJECTS:
        raise ProjectError("PROJECT_LIMIT", f"You can have at most {MAX_PROJECTS} projects. Delete one first.")
    project_id = str(uuid.uuid4())
    now = _now()
    data: dict[str, Any] = {
        "name": name,
        "nameKey": name_key(name),
        "instructions": instructions,
        "defaultSkillId": default_skill_id or None,
        "userId": user_id,
        "createdAt": now,
        "updatedAt": now,
        "lastOpenedAt": now,
    }
    _projects_ref(user_id).document(project_id).set(data)
    return {**data, "id": project_id}


_UNSET: Any = object()


def update_project(
    user_id: str,
    project_id: str,
    *,
    name: str | None = None,
    instructions: str | None = None,
    default_skill_id: Any = _UNSET,
) -> dict[str, Any]:
    """Rename / re-instruct / re-default. Raises :class:`ProjectError` (404 when
    the project is not the caller's, 409 on a taken name, 422 on bad input)."""
    project = get_project(user_id, project_id)
    if project is None:
        raise ProjectError("PROJECT_NOT_FOUND", "Project not found.", 404)
    fields: dict[str, Any] = {}
    if name is not None:
        cleaned = clean_name(name)
        other = find_project_by_name(user_id, cleaned)
        if other is not None and other["id"] != project_id:
            raise ProjectError("PROJECT_EXISTS", f"You already have a project called '{cleaned}'.")
        fields["name"] = cleaned
        fields["nameKey"] = name_key(cleaned)
    if instructions is not None:
        fields["instructions"] = clean_instructions(instructions)
    if default_skill_id is not _UNSET:
        fields["defaultSkillId"] = default_skill_id or None
    if fields:
        fields["updatedAt"] = fields["lastOpenedAt"] = _now()
        _projects_ref(user_id).document(project_id).update(fields)
    return {**project, **fields}


def touch_project(user_id: str, project_id: str) -> None:
    """Stamp ``lastOpenedAt`` — the Projects list is ordered by it. Best-effort."""
    try:
        _projects_ref(user_id).document(project_id).update({"lastOpenedAt": _now()})
    except Exception as exc:
        log.warning("project %s: could not stamp lastOpenedAt: %s", project_id, exc)


def delete_project(user_id: str, project_id: str) -> dict[str, int]:
    """Untag every document and chat, then delete the project. Never deletes a
    document or a chat. Returns how many of each were untagged."""
    if get_project(user_id, project_id) is None:
        raise ProjectError("PROJECT_NOT_FOUND", "Project not found.", 404)
    updates: list[tuple[str, str, dict[str, Any]]] = []
    doc_ids = project_document_ids(user_id, project_id)
    for doc_id in doc_ids:
        updates.append((_DOCS, doc_id, {"projectIds": _fs.ArrayRemove([project_id])}))
    session_ids: set[str] = set()
    for snap in _owner_sessions_where(user_id, "projectIds", "array_contains", project_id):
        session_ids.add(snap.id)
        updates.append((_SESSIONS, snap.id, {"projectIds": _fs.ArrayRemove([project_id])}))
    for snap in _owner_sessions_where(user_id, "projectId", "==", project_id):
        session_ids.add(snap.id)
        updates.append((_SESSIONS, snap.id, {"projectId": None}))
    _write_updates(updates)
    _projects_ref(user_id).document(project_id).delete()
    _announce_projects_changed(user_id, doc_ids)
    return {"documents": len(doc_ids), "chats": len(session_ids)}


# ---------------------------------------------------------------------------
# Documents in a project
# ---------------------------------------------------------------------------


def _project_docs_query(user_id: str, project_id: str):
    return (
        get_client()
        .collection(_DOCS)
        .where(filter=_fs.FieldFilter("userId", "==", user_id))
        .where(filter=_fs.FieldFilter("projectIds", "array_contains", project_id))
    )


def project_document_ids(user_id: str, project_id: str) -> list[str]:
    return [snap.id for snap in _project_docs_query(user_id, project_id).stream() if snap.exists]


def doc_row(doc_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Listing metadata for one document — never its blocks."""
    row: dict[str, Any] = {"id": doc_id, **{k: doc.get(k) for k in DOC_FIELDS}}
    row["projectIds"] = list(doc.get("projectIds") or [])
    return row


def list_project_documents(
    user_id: str, project_id: str, *, cursor: str | None = None, page_size: int = 50
) -> tuple[list[dict[str, Any]], str | None]:
    """The project's documents, newest first. ``cursor`` is the ``createdAt`` of
    the previous page's last row (opaque to clients)."""
    query = _project_docs_query(user_id, project_id)
    if cursor:
        query = query.where(filter=_fs.FieldFilter("createdAt", "<", cursor))
    query = query.order_by("createdAt", direction=_fs.Query.DESCENDING).limit(page_size + 1)
    rows: list[dict[str, Any]] = []
    for snap in query.stream():
        data = snap.to_dict() if snap.exists else None
        if data is None:
            continue
        rows.append(doc_row(snap.id, data))
    more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = str(rows[-1].get("createdAt") or "") if more and rows else None
    return rows, next_cursor or None


def count_project_documents(user_id: str, project_id: str) -> int:
    return _count(_project_docs_query(user_id, project_id))


def count_project_sessions(user_id: str, project_id: str) -> int:
    """Explicit plus derived chats, not archived, not provisional."""
    ids: set[str] = set()
    for field, op in (("projectId", "=="), ("projectIds", "array_contains")):
        for snap in _owner_sessions_where(user_id, field, op, project_id):
            data = snap.to_dict() or {}
            if data.get("archivedAt") is None and not data.get("provisional"):
                ids.add(snap.id)
    return len(ids)


def tag_documents(user_id: str, project_id: str, doc_ids: list[str]) -> dict[str, list[Any]]:
    """Tag the caller's documents into the project (writer 1).

    Returns ``{added: [ids], already: [ids], failed: [{id, filename, reason}]}``.
    A document that is not the caller's is reported exactly like a missing one,
    with no filename — saying whose it is would itself be a disclosure.
    """
    added: list[str] = []
    already: list[str] = []
    failed: list[dict[str, str]] = []
    seen: set[str] = set()
    for doc_id in doc_ids:
        doc_id = (doc_id or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        doc = get_document(_DOCS, doc_id)
        if not doc or doc.get("userId") != user_id:
            failed.append({"id": doc_id, "filename": "", "reason": "Not found in your documents."})
            continue
        filename = str(doc.get("originalFilename") or "")
        current = list(doc.get("projectIds") or [])
        if project_id in current:
            already.append(doc_id)
            continue
        if len(current) >= MAX_PROJECTS_PER_DOCUMENT:
            failed.append(
                {
                    "id": doc_id,
                    "filename": filename,
                    "reason": f"Already in {MAX_PROJECTS_PER_DOCUMENT} projects, the most one document can be in.",
                }
            )
            continue
        try:
            get_client().collection(_DOCS).document(doc_id).update({"projectIds": _fs.ArrayUnion([project_id])})
        except Exception as exc:
            log.warning("project %s: could not tag %s: %s", project_id, doc_id, exc)
            failed.append({"id": doc_id, "filename": filename, "reason": "Could not be added. Try again."})
            continue
        added.append(doc_id)
        _backfill_sessions(user_id, project_id, doc_id)
    if added:
        touch_project(user_id, project_id)
        _announce_projects_changed(user_id, added)
    return {"added": added, "already": already, "failed": failed}


def untag_document(user_id: str, project_id: str, doc_id: str) -> bool:
    """Remove one of the caller's documents from the project. False when the
    document is not the caller's or was not in the project."""
    doc = get_document(_DOCS, doc_id)
    if not doc or doc.get("userId") != user_id or project_id not in (doc.get("projectIds") or []):
        return False
    get_client().collection(_DOCS).document(doc_id).update({"projectIds": _fs.ArrayRemove([project_id])})
    for snap in _owner_sessions_where(user_id, "documentIds", "array_contains", doc_id):
        recompute_session_projects(snap.id, snap.to_dict())
    _announce_projects_changed(user_id, [doc_id])
    return True


def on_document_deleted(user_id: str, doc_id: str, project_ids: list[str] | None) -> None:
    """Writer 3 for a deleted document: its chats lose the projects it brought.
    Call AFTER the document record is gone. Best-effort."""
    if not project_ids:
        return
    try:
        for snap in _owner_sessions_where(user_id, "documentIds", "array_contains", doc_id):
            recompute_session_projects(snap.id, snap.to_dict())
    except Exception as exc:
        log.warning("projects: recompute after deleting %s failed: %s", doc_id, exc)


# ---------------------------------------------------------------------------
# Derived chat membership
# ---------------------------------------------------------------------------


def _owner_sessions_where(user_id: str, field: str, op: str, value: Any):
    """The caller's own chat rows matching one extra clause. Equality +
    array-contains only, no ordering, so Firestore serves it by merging
    single-field indexes — no composite index needed for these writers."""
    query = (
        get_client()
        .collection(_SESSIONS)
        .where(filter=_fs.FieldFilter("ownerUid", "==", user_id))
        .where(filter=_fs.FieldFilter(field, op, value))
    )
    return [snap for snap in query.stream() if snap.exists]


def _backfill_sessions(user_id: str, project_id: str, doc_id: str) -> None:
    """Writer 1: every one of the owner's chats that used ``doc_id`` joins the project."""
    try:
        sessions = _owner_sessions_where(user_id, "documentIds", "array_contains", doc_id)
        _write_updates([(_SESSIONS, s.id, {"projectIds": _fs.ArrayUnion([project_id])}) for s in sessions])
    except Exception as exc:
        log.warning("project %s: chats that used %s were not backfilled: %s", project_id, doc_id, exc)


def _owned_projects_of(doc_ids: list[str], owner_uid: str) -> set[str]:
    out: set[str] = set()
    for doc_id in dict.fromkeys(doc_ids):
        doc = get_document(_DOCS, doc_id) if doc_id else None
        if doc and doc.get("userId") == owner_uid:
            out.update(p for p in (doc.get("projectIds") or []) if p)
    return out


def add_document_projects_to_session(session_id: str, doc_ids: list[str]) -> None:
    """Writer 2: a chat that uses a document joins that document's projects.

    Only documents the chat's OWNER owns count — a shared-library document is
    owned by someone else, and their projects are none of this chat's business.
    Best-effort: never raises.
    """
    try:
        session = get_document(_SESSIONS, session_id)
        owner = (session or {}).get("ownerUid")
        if not owner:
            return
        project_ids = _owned_projects_of(doc_ids, owner)
        if project_ids:
            get_client().collection(_SESSIONS).document(session_id).update(
                {"projectIds": _fs.ArrayUnion(sorted(project_ids))}
            )
    except Exception as exc:
        log.warning("projects: could not add document projects to session %s: %s", session_id, exc)


def recompute_session_projects(session_id: str, data: dict[str, Any] | None = None) -> list[str]:
    """Writer 3: set a chat's derived ``projectIds`` from its current documents."""
    data = data if data is not None else (get_document(_SESSIONS, session_id) or {})
    owner = data.get("ownerUid") or ""
    project_ids = sorted(_owned_projects_of(list(data.get("documentIds") or []), owner)) if owner else []
    try:
        get_client().collection(_SESSIONS).document(session_id).update({"projectIds": project_ids})
    except Exception as exc:
        log.warning("projects: could not recompute session %s: %s", session_id, exc)
    return project_ids


def set_session_project(session_id: str, project_id: str | None) -> None:
    """Explicit membership. The caller has already checked the session and the
    project are both theirs."""
    get_client().collection(_SESSIONS).document(session_id).update({"projectId": project_id or None})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(query: Any) -> int:
    """Firestore ``count()`` aggregation, falling back to streaming where the
    client has none (the LOCAL_MODE in-memory client)."""
    counter = getattr(query, "count", None)
    if callable(counter):
        try:
            value = counter(alias="n").get()[0][0].value
            if isinstance(value, int):
                return value
        except Exception as exc:
            log.debug("count() aggregation unavailable, streaming instead: %s", exc)
    return sum(1 for snap in query.stream() if getattr(snap, "exists", True))


def _write_updates(updates: list[tuple[str, str, dict[str, Any]]]) -> None:
    """Apply field updates in Firestore batches of :data:`_BATCH`."""
    if not updates:
        return
    client = get_client()
    batch_factory = getattr(client, "batch", None)
    if not callable(batch_factory):
        for collection, doc_id, data in updates:
            client.collection(collection).document(doc_id).update(data)
        return
    for start in range(0, len(updates), _BATCH):
        batch = batch_factory()
        for collection, doc_id, data in updates[start : start + _BATCH]:
            batch.update(client.collection(collection).document(doc_id), data)
        batch.commit()


def _announce_projects_changed(user_id: str, doc_ids: list[str]) -> None:
    """Tell private extensions (the knowledge index, P2) which documents' project
    tags changed. Observational; never raises."""
    if not doc_ids:
        return
    from extensions import document_event

    for doc_id in doc_ids:
        document_event("projects_changed", {"uid": user_id, "doc_id": doc_id})


# ---------------------------------------------------------------------------
# "Your instructions" — the default project's instructions (§6.5)
# ---------------------------------------------------------------------------

MAX_USER_INSTRUCTIONS_CHARS = 4000


def get_user_instructions(user_id: str) -> str:
    """``users/{uid}.instructions``, or "" when unset."""
    if not user_id:
        return ""
    snap = get_client().collection("users").document(user_id).get()
    if not snap.exists:
        return ""
    return str((snap.to_dict() or {}).get("instructions") or "")


def set_user_instructions(user_id: str, text: str) -> str:
    """Save (or, with "", clear) the user's own instructions. Returns the saved text."""
    text = (text or "").strip()
    if len(text) > MAX_USER_INSTRUCTIONS_CHARS:
        raise ProjectError(
            "INSTRUCTIONS_TOO_LONG",
            f"Your instructions can be at most {MAX_USER_INSTRUCTIONS_CHARS:,} characters.",
            422,
        )
    get_client().collection("users").document(user_id).set(
        {"instructions": text, "instructionsUpdatedAt": _now()}, merge=True
    )
    return text
