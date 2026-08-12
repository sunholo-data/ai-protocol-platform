"""Bucket + Folder Pydantic models (RESOURCE-ACCESS 1A.1b M1).

A `BucketConfig` is a Firestore document describing a logical namespace
backed by a GCS bucket. A `BucketFolderConfig` is a nested document under
`/buckets/{bucketId}/folders/{folderId}` scoping a prefix *inside* that
bucket.

Access is evaluated against the shared `AccessControl` schema from
`db.models.access` — no duplicated type definitions. See
`docs/design/v6.0.0/resource-access-control.md` for the full contract.

Folder invariant: `effective_access` is **required** on every persisted
folder. The API computes it on every write (folder.access_control or
parent-bucket.access_control). Rules then read `effectiveAccess` directly
without recursing to the parent, so access checks stay O(1).
"""

from __future__ import annotations

import re
import time

from pydantic import BaseModel, Field, field_validator

from db.models.access import AccessControl

# GCS bucket-name contract:
#   - 3-63 chars (non-domain form; dotted subdomain form up to 222 not supported here)
#   - lowercase letters, digits, hyphens, underscores, dots
#   - must start and end with a letter or digit
# Ref: https://cloud.google.com/storage/docs/buckets#naming
_BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")

# Folder paths live *inside* a bucket and must be relative (no leading slash,
# no traversal, no empty segments). Trailing slash is preserved so callers
# can treat the path as a prefix.


def _folder_path_is_invalid(v: str) -> bool:
    return v.startswith("/") or "//" in v or ".." in v


def _validate_bucket_name(v: str) -> str:
    if not isinstance(v, str) or not _BUCKET_NAME_PATTERN.match(v):
        raise ValueError(
            "bucket name must be 3-63 chars, lowercase alphanumeric with . _ -, "
            "starting and ending with a letter or digit"
        )
    if v.startswith("goog"):
        raise ValueError("bucket name cannot start with 'goog' (reserved by GCS)")
    if ".." in v:
        raise ValueError("bucket name cannot contain adjacent dots")
    return v


class BucketConfig(BaseModel):
    """Firestore document at `/buckets/{bucketId}`."""

    bucket_id: str = Field(alias="bucketId")
    display_name: str = Field(alias="displayName")
    owner_email: str = Field(alias="ownerEmail")
    owner_id: str = Field(alias="ownerId")
    gcs_bucket: str = Field(alias="gcsBucket")
    region: str = "europe-west1"
    access_control: AccessControl = Field(default_factory=AccessControl, alias="accessControl")
    # Login-domain binding (v6.18.1, issue #37). A bucket-config is an OPERATOR
    # blessing of a real GCS bucket for a specific installation ("this bucket is
    # ONE's"), so it also names the email domains allowed to read it. Enforced by
    # `_authorize_bucket_read` IN ADDITION to `access_control` — belt and braces:
    # even if an ACL is mis-scoped (or a config is somehow created by a non-admin),
    # a caller from an unrelated domain is still denied. EMPTY MEANS DENY: a config
    # without domains grants nothing through the bucket-config door, so a
    # half-configured bucket fails closed rather than open. Platform admins bypass
    # (diagnostics), as they do every other door.
    allowed_domains: list[str] = Field(default_factory=list, alias="allowedDomains")
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time, alias="createdAt")
    updated_at: float = Field(default_factory=time.time, alias="updatedAt")

    model_config = {"populate_by_name": True}

    @field_validator("allowed_domains")
    @classmethod
    def _normalise_domains(cls, v: list[str]) -> list[str]:
        """Lowercase + strip; drop empties and a leading '@' if someone pasted one."""
        return [d.strip().lstrip("@").lower() for d in (v or []) if d and d.strip()]

    @field_validator("gcs_bucket")
    @classmethod
    def _validate_gcs_bucket(cls, v: str) -> str:
        return _validate_bucket_name(v)

    @field_validator("bucket_id")
    @classmethod
    def _validate_bucket_id(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("bucketId must be 1-64 characters")
        return v


class BucketFolderConfig(BaseModel):
    """Firestore document at `/buckets/{bucketId}/folders/{folderId}`.

    `access_control` is optional — when omitted, the folder inherits from the
    parent bucket. `effective_access` is *always* required and must be
    computed on every write by the API (see `buckets/folder_config.py`
    `compute_effective_access`). Rules read `effectiveAccess` directly.
    """

    folder_id: str = Field(alias="folderId")
    bucket_id: str = Field(alias="bucketId")
    path: str
    display_name: str = Field(alias="displayName")
    owner_id: str = Field(alias="ownerId")
    access_control: AccessControl | None = Field(default=None, alias="accessControl")
    effective_access: AccessControl = Field(alias="effectiveAccess")
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time, alias="createdAt")
    updated_at: float = Field(default_factory=time.time, alias="updatedAt")

    model_config = {"populate_by_name": True}

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        if not v:
            raise ValueError("folder path must not be empty")
        if _folder_path_is_invalid(v):
            raise ValueError("folder path must be relative: no leading '/', no '..', no '//' segments")
        return v


__all__ = ["BucketConfig", "BucketFolderConfig"]
