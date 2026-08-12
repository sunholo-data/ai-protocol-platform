"""Bucket configuration — Firestore CRUD for the /buckets collection.

Mirrors skills/skill_config.py but without the in-memory cache (buckets
are low-traffic config docs, not hot request-path reads — revisit if the
list endpoint becomes slow).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from db import firestore as fs
from db.models import BucketConfig

COLLECTION = "buckets"


def _to_firestore(config: BucketConfig) -> dict[str, Any]:
    return config.model_dump(by_alias=True)


def _from_firestore(data: dict[str, Any]) -> BucketConfig:
    data.pop("__id", None)
    return BucketConfig.model_validate(data)


def create_bucket(
    display_name: str,
    gcs_bucket: str,
    owner_id: str,
    owner_email: str,
    **kwargs: Any,
) -> BucketConfig:
    """Create a new bucket and persist to Firestore.

    Caller must supply `owner_id` from the verified JWT — route handler
    MUST NOT accept it from the request body.
    """
    bucket_id = str(uuid.uuid4())
    now = time.time()
    config = BucketConfig(
        bucketId=bucket_id,
        displayName=display_name,
        gcsBucket=gcs_bucket,
        ownerId=owner_id,
        ownerEmail=owner_email,
        createdAt=now,
        updatedAt=now,
        **kwargs,
    )
    fs.set_document(COLLECTION, bucket_id, _to_firestore(config))
    return config


def get_bucket(bucket_id: str) -> BucketConfig | None:
    data = fs.get_document(COLLECTION, bucket_id)
    if data is None:
        return None
    return _from_firestore(data)


def find_by_gcs_name(gcs_bucket: str) -> BucketConfig | None:
    """Return the registered bucket-config whose ``gcsBucket`` matches, or None.

    The file-serving endpoints (`/{name}/list|preview|thumbnail`) address by GCS
    bucket **name**, not by config-id, so per-tenant authorization needs a
    name→config lookup. Raw buckets that were never registered as a config (e.g. a
    per-env llmops bucket) return None; the caller then falls back to the
    tenant-bucket check in `_authorize_bucket_read`. First match wins — `gcsBucket`
    is expected unique per config; a duplicate would be a config error, and both
    copies still carry `accessControl`, so it is not a security hole.
    """
    if not gcs_bucket:
        return None
    docs = fs.query_documents(
        COLLECTION,
        filters=[("gcsBucket", "==", gcs_bucket)],
        limit=1,
    )
    return _from_firestore(docs[0]) if docs else None


def update_bucket(bucket_id: str, updates: dict[str, Any]) -> BucketConfig | None:
    existing = get_bucket(bucket_id)
    if existing is None:
        return None
    updates["updatedAt"] = time.time()
    fs.update_document(COLLECTION, bucket_id, updates)
    return get_bucket(bucket_id)


def delete_bucket(bucket_id: str) -> bool:
    if get_bucket(bucket_id) is None:
        return False
    fs.delete_document(COLLECTION, bucket_id)
    return True


def list_buckets(
    owner_id: str | None = None,
    tag: str | None = None,
    access_type: str | None = None,
    limit: int = 50,
) -> list[BucketConfig]:
    """List buckets with optional filters.

    Caller must still run each result through `AccessContext.can_access` to
    drop buckets the user cannot see — see routes.list_buckets.
    """
    filters: list[tuple[str, str, Any]] = []
    if owner_id:
        filters.append(("ownerId", "==", owner_id))
    if tag:
        filters.append(("tags", "array_contains", tag))
    if access_type:
        filters.append(("accessControl.type", "==", access_type))

    docs = fs.query_documents(
        COLLECTION,
        filters=filters if filters else None,
        order_by="updatedAt",
        order_direction="DESCENDING",
        limit=limit,
    )
    return [_from_firestore(doc) for doc in docs]
