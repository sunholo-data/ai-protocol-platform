"""User-facing document folder CRUD (Firestore).

These folders organise user document uploads within a client GCS bucket.
Distinct from the storage-ACL BucketFolderConfig in backend/buckets/.

Firestore path: `users/{uid}/folders/{folderId}`
Documents subcollection: `users/{uid}/folders/{folderId}/documents/{docId}`
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.firestore import get_client


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


def _docs_ref(user_id: str, folder_id: str):
    return _folders_ref(user_id).document(folder_id).collection("documents")


def create_folder(user_id: str, name: str) -> dict[str, Any]:
    folder_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": folder_id,
        "name": name,
        "userId": user_id,
        "createdAt": now,
        "docCount": 0,
        "parsedCount": 0,
    }
    _folders_ref(user_id).document(folder_id).set(data)
    return {
        "id": folder_id,
        "name": name,
        "userId": user_id,
        "docCount": 0,
        "parsedCount": 0,
    }


def get_folder(user_id: str, folder_id: str) -> dict[str, Any] | None:
    doc = _folders_ref(user_id).document(folder_id).get()
    return doc.to_dict() if doc.exists else None


def list_folders(user_id: str) -> list[dict[str, Any]]:
    results = []
    for doc in _folders_ref(user_id).stream():
        data = doc.to_dict()
        if data is not None:
            data.setdefault("id", doc.id)
            results.append(data)
    return results


def list_folder_documents(user_id: str, folder_id: str) -> list[dict[str, Any]]:
    results = []
    for doc in _docs_ref(user_id, folder_id).stream():
        data = doc.to_dict()
        if data is not None:
            data.setdefault("id", doc.id)
            results.append(data)
    return results


def update_folder_counts(user_id: str, folder_id: str, doc_delta: int = 0, parsed_delta: int = 0) -> None:
    from google.cloud.firestore import Increment

    updates: dict[str, Any] = {}
    if doc_delta:
        updates["docCount"] = Increment(doc_delta)
    if parsed_delta:
        updates["parsedCount"] = Increment(parsed_delta)
    if updates:
        _folders_ref(user_id).document(folder_id).update(updates)


def ensure_default_folder(user_id: str) -> str:
    """Return the first folder id for the user, creating a default one if needed."""
    folders = list_folders(user_id)
    if folders:
        return folders[0]["id"]
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    result = create_folder(user_id, f"Uploads {today}")
    return result["id"]
