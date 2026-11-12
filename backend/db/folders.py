"""User-facing document folder CRUD (Firestore).

These folders organise user document uploads within a client GCS bucket.
Distinct from the storage-ACL BucketFolderConfig in backend/buckets/.

Firestore path: `users/{uid}/folders/{folderId}`

A folder's DOCUMENTS are not stored under it. `ingest_document` writes every
document to the root `parsed_documents` collection, tagged with `userId` and
`folderId`, so a folder's contents are a QUERY, not a subcollection — see
:func:`list_folder_documents`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.firestore import get_client, query_documents

# Where documents actually live. Mirrors `tools.documents.ingest._COLLECTION`;
# duplicated as a literal rather than imported because ingest imports this
# module, and the reverse import would be a cycle.
_DOCS_COLLECTION = "parsed_documents"


class Folder(BaseModel):
    id: str
    name: str
    user_id: str = Field(alias="userId")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    doc_count: int = Field(default=0, alias="docCount")
    parsed_count: int = Field(default=0, alias="parsedCount")

    model_config = ConfigDict(populate_by_name=True)


def _folders_ref(user_id: str):
    return get_client().collection("users").document(user_id).collection("folders")


def create_folder(user_id: str, name: str, system: bool = False) -> dict[str, Any]:
    """Create a folder. `system=True` marks one the assistant owns (e.g. Created),
    which keeps it out of `ensure_default_folder`'s candidates and lets the UI
    badge it as generated rather than uploaded."""
    folder_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": folder_id,
        "name": name,
        "userId": user_id,
        "createdAt": now,
        "docCount": 0,
        "parsedCount": 0,
        "system": system,
    }
    _folders_ref(user_id).document(folder_id).set(data)
    return {
        "id": folder_id,
        "name": name,
        "userId": user_id,
        "docCount": 0,
        "parsedCount": 0,
        "system": system,
    }


def get_folder(user_id: str, folder_id: str) -> dict[str, Any] | None:
    doc = _folders_ref(user_id).document(folder_id).get()
    return doc.to_dict() if doc.exists else None


def get_owned_folder(user_id: str, folder_id: str) -> dict[str, Any] | None:
    """The caller's folder `folder_id`, or None when it doesn't exist or isn't theirs.

    Folders live under `users/{uid}/folders`, so a lookup is already scoped to the
    caller; the `userId` check is belt-and-braces for records copied between
    users. Legacy records without `userId` are accepted (same rule as rename).
    """
    folder_id = (folder_id or "").strip()
    if not folder_id:
        return None
    folder = get_folder(user_id, folder_id)
    if folder is None or folder.get("userId") not in (None, user_id):
        return None
    folder.setdefault("id", folder_id)
    return folder


def list_folders(user_id: str) -> list[dict[str, Any]]:
    results = []
    for doc in _folders_ref(user_id).stream():
        data = doc.to_dict()
        if data is not None:
            data.setdefault("id", doc.id)
            results.append(data)
    return results


# What a folder listing shows (useDocBrowser's mapApiDocument, the Add documents
# picker). Never `blocks` (up to ~700 KB each), `editedBlocks` or
# `sourceMarkdown`: one folder of 11 documents used to be a 2.2 MB response.
LISTING_FIELDS = [
    "originalFilename",
    "source",
    "sourceFormat",
    "parseStatus",
    "parseError",
    "status",
    "folderId",
    "userId",
    "blockCount",
    "a2uiComponents",
    "createdAt",
    "updatedAt",
    "projectIds",
]


def list_folder_documents(user_id: str, folder_id: str, *, fields: list[str] | None = None) -> list[dict[str, Any]]:
    """Documents in one of the user's folders, newest first.

    This used to stream `users/{uid}/folders/{fid}/documents` — a subcollection
    NOTHING has ever written. `ingest_document` puts every document in the root
    `parsed_documents` collection, so the listing returned `[]` for folders that
    genuinely held documents, while `update_folder_counts` kept incrementing
    `docCount` on the folder. A folder read "8 documents" and listed none, and
    an upload that reported `parsed (833 blocks)` was nowhere to be found.

    Two equality filters and no ordering is deliberate: Firestore serves an
    equality-only conjunction from single-field indexes, so this needs no
    composite index (the dedup query in `ingest.py` relies on the same shape).
    Sorting therefore happens here, not in the query.

    ``fields``: only these (plus ``createdAt``, for the order) — a caller that
    needs ids must not pull every document's parsed blocks.
    """
    docs = query_documents(
        _DOCS_COLLECTION,
        filters=[("userId", "==", user_id), ("folderId", "==", folder_id)],
        select=None if fields is None else sorted({*fields, "createdAt"}),
    )
    for data in docs:
        # `query_documents` returns the id as `__id`; callers and the API
        # response speak `id`.
        data.setdefault("id", data.get("__id", ""))
    docs.sort(key=lambda d: str(d.get("createdAt") or ""), reverse=True)
    return docs


def count_folder_documents(user_id: str, folder_id: str) -> int:
    """Documents actually in the folder (the `docCount` counter can drift)."""
    return len(
        query_documents(
            _DOCS_COLLECTION, filters=[("userId", "==", user_id), ("folderId", "==", folder_id)], select=["__name__"]
        )
    )


def find_folder_by_name(user_id: str, name: str) -> dict[str, Any] | None:
    """The user's folder called `name` (case-insensitive, trimmed), or None."""
    key = (name or "").strip().casefold()
    for folder in list_folders(user_id):
        if str(folder.get("name") or "").strip().casefold() == key:
            return folder
    return None


def rename_folder(user_id: str, folder_id: str, name: str) -> None:
    _folders_ref(user_id).document(folder_id).update({"name": name})


def subfolders(user_id: str, name: str) -> list[dict[str, Any]]:
    """The folders under `name`: those called `name/...` (case-insensitive).

    Folders are flat, so a folder upload's subfolder `Reports/2026` is just a
    folder whose name starts with `Reports/`.
    """
    prefix = (name or "").strip().casefold() + "/"
    return [f for f in list_folders(user_id) if str(f.get("name") or "").strip().casefold().startswith(prefix)]


def rename_folders(user_id: str, names: dict[str, str]) -> None:
    """Rename several folders (a folder and its subfolders) as one batch.

    One batch holds 500 writes; a rename larger than that is split, which is
    only reachable with folder trees far beyond what an upload creates.
    """
    items = list(names.items())
    for start in range(0, len(items), 400):
        batch = get_client().batch()
        for folder_id, name in items[start : start + 400]:
            batch.update(_folders_ref(user_id).document(folder_id), {"name": name})
        batch.commit()


def delete_folder(user_id: str, folder_id: str) -> None:
    """Delete the folder record. Callers check it is empty first."""
    _folders_ref(user_id).document(folder_id).delete()


def update_folder_counts(user_id: str, folder_id: str, doc_delta: int = 0, parsed_delta: int = 0) -> None:
    from google.cloud.firestore import Increment

    updates: dict[str, Any] = {}
    if doc_delta:
        updates["docCount"] = Increment(doc_delta)
    if parsed_delta:
        updates["parsedCount"] = Increment(parsed_delta)
    if updates:
        _folders_ref(user_id).document(folder_id).update(updates)


def _is_system(folder: dict[str, Any]) -> bool:
    return bool(folder.get("system"))


def _created_at_key(folder: dict[str, Any]) -> tuple[int, Any]:
    """Sort key: real timestamps first (oldest wins), undated records last.

    Legacy folders predate `createdAt`. They must stay selectable, so they sort
    after dated ones rather than crashing the comparison.
    """
    created = folder.get("createdAt")
    if isinstance(created, datetime):
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (0, created)
    if isinstance(created, str) and created:
        return (0, created)
    return (1, folder.get("id") or "")


def ensure_default_folder(user_id: str) -> str:
    """Return the user's own default folder, creating one if needed.

    Deterministic by construction. This used to return `folders[0]` from an
    unordered Firestore `stream()`, which was harmless only while every user had
    exactly one folder — with a second, system-created folder present, an upload
    carrying no explicit `folder_id` could silently land in **Created**, filing
    the user's own document under the assistant's output. Selection is now the
    oldest folder by `createdAt`, and **never a system folder**.
    """
    candidates = [f for f in list_folders(user_id) if not _is_system(f)]
    if candidates:
        return sorted(candidates, key=_created_at_key)[0]["id"]
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    result = create_folder(user_id, f"Uploads {today}")
    return result["id"]


def ensure_named_folder(user_id: str, name: str, system: bool = False) -> str:
    """Return the id of the user's folder called `name`, creating it on miss.

    Idempotent by name (case-insensitive, trimmed) so repeated generations share
    one folder rather than creating one per call. Used for the **Created** folder
    that generated documents land in.
    """
    wanted = (name or "").strip()
    if not wanted:
        raise ValueError("folder name must not be empty")

    key = wanted.casefold()
    for folder in list_folders(user_id):
        if str(folder.get("name") or "").strip().casefold() == key:
            return folder["id"]

    return create_folder(user_id, wanted, system=system)["id"]
