"""Project sources in Firestore (github-sources.md §4).

``project_sources/{sourceId}`` holds one source: its owner (``uid``), project, the
GitHub location, the credential ref, and the last run's state and report. It is a
top-level collection so the scheduler can find due sources with one query.
Backend-only, like ``users/{uid}/projects``: no ``firestore.rules`` match, so
client access is denied by default.

``project_sources/{sourceId}/files/{key}`` is the source's **manifest**: one row
per file it owns — relative path, blob sha, the document it became, and that
document's parse status. Ownership is the manifest. A document a person uploaded
into the same folder is not in it, so a sync never updates or deletes it.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from google.cloud import firestore as _fs

from db.firestore import get_client

COLLECTION = "project_sources"
_FILES = "files"
MAX_SOURCES_PER_PROJECT = 10


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sources():
    return get_client().collection(COLLECTION)


def _files(source_id: str):
    return _sources().document(source_id).collection(_FILES)


def file_key(path: str) -> str:
    """A Firestore-safe document id for a relative path."""
    return hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()


def create_source(fields: dict[str, Any]) -> dict[str, Any]:
    source_id = str(uuid.uuid4())
    now = now_iso()
    doc = {**fields, "createdAt": now, "updatedAt": now, "status": "new", "syncedCommit": "", "etag": ""}
    _sources().document(source_id).set(doc)
    return {**doc, "id": source_id}


def get_source(source_id: str) -> dict[str, Any] | None:
    snap = _sources().document(source_id).get()
    if not snap.exists:
        return None
    return {**(snap.to_dict() or {}), "id": source_id}


def owned_source(uid: str, project_id: str, source_id: str) -> dict[str, Any] | None:
    """The source when it belongs to this owner AND project, else None (a 404)."""
    src = get_source(source_id)
    if src is None or src.get("uid") != uid or src.get("projectId") != project_id:
        return None
    return src


def all_sources() -> list[dict[str, Any]]:
    """Every source, oldest sync first — what the scheduler walks."""
    rows = [{**(s.to_dict() or {}), "id": s.id} for s in _sources().stream()]
    return sorted(rows, key=lambda r: str(r.get("syncedAt") or ""))


def list_project_sources(uid: str, project_id: str) -> list[dict[str, Any]]:
    query = (
        _sources()
        .where(filter=_fs.FieldFilter("uid", "==", uid))
        .where(filter=_fs.FieldFilter("projectId", "==", project_id))
    )
    rows = [{**(s.to_dict() or {}), "id": s.id} for s in query.stream()]
    return sorted(rows, key=lambda r: str(r.get("createdAt") or ""))


def list_user_sources(uid: str) -> list[dict[str, Any]]:
    """Every source the user owns, across their projects."""
    query = _sources().where(filter=_fs.FieldFilter("uid", "==", uid))
    return [{**(s.to_dict() or {}), "id": s.id} for s in query.stream()]


def update_source(source_id: str, fields: dict[str, Any]) -> None:
    _sources().document(source_id).update({**fields, "updatedAt": now_iso()})


def delete_source(source_id: str) -> int:
    """Delete the source and its manifest. Its documents stay in the library and
    the project as ordinary documents. Returns how many manifest rows went."""
    files = _files(source_id)
    keys = [snap.id for snap in files.stream()]
    for key in keys:
        files.document(key).delete()
    _sources().document(source_id).delete()
    return len(keys)


def delete_project_sources(uid: str, project_id: str) -> int:
    """Every source of a deleted project. Returns how many."""
    sources = list_project_sources(uid, project_id)
    for src in sources:
        delete_source(src["id"])
    return len(sources)


# --- manifest -----------------------------------------------------------------


def manifest(source_id: str) -> dict[str, dict[str, Any]]:
    """Relative path → manifest row, for every file the source owns."""
    out: dict[str, dict[str, Any]] = {}
    for snap in _files(source_id).stream():
        row = snap.to_dict() or {}
        if row.get("path"):
            out[str(row["path"])] = row
    return out


def put_file(
    source_id: str,
    path: str,
    *,
    blob_sha: str,
    doc_id: str,
    parse_status: str,
    parse_error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    row: dict[str, Any] = {
        "path": path,
        "blobSha": blob_sha,
        "docId": doc_id,
        "parseStatus": parse_status,
        "updatedAt": now_iso(),
    }
    if parse_error:
        row["parseError"] = parse_error[:500]
    row.update(extra or {})
    _files(source_id).document(file_key(path)).set(row)


def drop_file(source_id: str, path: str) -> None:
    _files(source_id).document(file_key(path)).delete()


# --- run lease ----------------------------------------------------------------


def acquire_lease(source_id: str, run_id: str, *, seconds: int) -> bool:
    """Mark the source as running for ``run_id``. False while another run holds
    an unexpired lease.

    Read-check-write, then read back: two instances racing for the same source
    within one round trip can both pass the check, and the read-back makes the
    loser step aside. The sync is idempotent per file, so the rare overlap that
    remains costs duplicate work, never a wrong result."""
    ref = _sources().document(source_id)
    snap = ref.get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    held = str(data.get("leaseUntil") or "")
    now = datetime.now(UTC)
    if data.get("runId") and held and held > now.isoformat():
        return False
    until = (now + timedelta(seconds=seconds)).isoformat()
    ref.update({"runId": run_id, "leaseUntil": until, "status": "running", "runStartedAt": now.isoformat()})
    return (ref.get().to_dict() or {}).get("runId") == run_id


def release_lease(source_id: str, run_id: str, fields: dict[str, Any]) -> None:
    """Write the run's outcome and drop the lease, if this run still holds it."""
    ref = _sources().document(source_id)
    snap = ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("runId") != run_id:
        return
    ref.update({**fields, "runId": "", "leaseUntil": "", "updatedAt": now_iso()})
