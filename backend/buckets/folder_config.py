"""Folder configuration — Firestore CRUD for /buckets/{bucketId}/folders.

The folder's `effective_access` is computed on every write (create and
update) so Firestore rules can read it directly without recursing to the
parent bucket — keeps access checks O(1).

Parent-access-change fan-out (re-computing effective_access for all
descendant folders when a bucket's accessControl changes) is deferred to
v6.1 — see resource-access-control.md §Open questions.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from db import firestore as fs
from db.models import AccessControl, BucketConfig, BucketFolderConfig

_FOLDERS_SUBCOLLECTION = "folders"


def _folder_path(bucket_id: str, folder_id: str | None = None) -> str:
    base = f"buckets/{bucket_id}/{_FOLDERS_SUBCOLLECTION}"
    return f"{base}/{folder_id}" if folder_id else base


def _to_firestore(config: BucketFolderConfig) -> dict[str, Any]:
    return config.model_dump(by_alias=True)


def _from_firestore(data: dict[str, Any]) -> BucketFolderConfig:
    data.pop("__id", None)
    return BucketFolderConfig.model_validate(data)


def compute_effective_access(
    folder_access: AccessControl | dict[str, Any] | None,
    parent: BucketConfig,
) -> AccessControl:
    """Resolve effective access at write time.

    Rule: folder.accessControl wins if set (override); otherwise inherit
    from parent bucket. Rules then read effectiveAccess directly — no
    recursion to the parent at read time.
    """
    if folder_access is None:
        return parent.access_control
    if isinstance(folder_access, AccessControl):
        return folder_access
    return AccessControl.model_validate(folder_access)


def create_folder(
    bucket: BucketConfig,
    path: str,
    display_name: str,
    owner_id: str,
    access_control: AccessControl | dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> BucketFolderConfig:
    """Create a new folder under `bucket`.

    `bucket` is required (not just bucket_id) so we can compute
    effective_access without a second Firestore read.
    """
    folder_id = str(uuid.uuid4())
    now = time.time()
    effective = compute_effective_access(access_control, bucket)
    config = BucketFolderConfig(
        folderId=folder_id,
        bucketId=bucket.bucket_id,
        path=path,
        displayName=display_name,
        ownerId=owner_id,
        accessControl=access_control
        if access_control is None or isinstance(access_control, AccessControl)
        else AccessControl.model_validate(access_control),
        effectiveAccess=effective,
        tags=tags or [],
        createdAt=now,
        updatedAt=now,
    )
    fs.get_client().document(_folder_path(bucket.bucket_id, folder_id)).set(_to_firestore(config))
    return config


def get_folder(bucket_id: str, folder_id: str) -> BucketFolderConfig | None:
    doc = fs.get_client().document(_folder_path(bucket_id, folder_id)).get()
    if not doc.exists:
        return None
    return _from_firestore(doc.to_dict() or {})


def update_folder(
    bucket: BucketConfig,
    folder_id: str,
    updates: dict[str, Any],
) -> BucketFolderConfig | None:
    """Update a folder. Recomputes effective_access if accessControl changed."""
    existing = get_folder(bucket.bucket_id, folder_id)
    if existing is None:
        return None

    if "accessControl" in updates:
        effective = compute_effective_access(updates["accessControl"], bucket)
        updates["effectiveAccess"] = effective.model_dump()
    updates["updatedAt"] = time.time()
    fs.get_client().document(_folder_path(bucket.bucket_id, folder_id)).update(updates)
    return get_folder(bucket.bucket_id, folder_id)


def delete_folder(bucket_id: str, folder_id: str) -> bool:
    if get_folder(bucket_id, folder_id) is None:
        return False
    fs.get_client().document(_folder_path(bucket_id, folder_id)).delete()
    return True


def list_folders(bucket_id: str, limit: int = 50) -> list[BucketFolderConfig]:
    """List folders under a bucket."""
    coll = fs.get_client().collection(_folder_path(bucket_id))
    query = coll.order_by("updatedAt", direction="DESCENDING").limit(limit)
    results: list[BucketFolderConfig] = []
    for doc in query.stream():
        data = doc.to_dict()
        if data:
            results.append(_from_firestore(data))
    return results
