"""Unit tests for BucketConfig and BucketFolderConfig (RESOURCE-ACCESS M1).

These lock in the contract for `backend/db/models/buckets.py`:
  - aliased field round-trip (camelCase on the wire, snake_case in Python)
  - GCS bucket-name validator (allowed chars + length bounds)
  - folder path normalisation (trailing-slash semantics)
  - AccessControl shape passthrough (trusted — covered in test_access_models.py)
  - effectiveAccess is required on folders; no implicit inheritance at read time
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from db.models import AccessControl, BucketConfig, BucketFolderConfig

# ---------------------------------------------------------------------------
# BucketConfig
# ---------------------------------------------------------------------------


def test_bucket_config_minimal_happy_path() -> None:
    b = BucketConfig(
        bucketId="acme-reports-dev",
        displayName="Acme reports",
        ownerEmail="owner@yourcompany.com",
        ownerId="uid-owner",
        gcsBucket="acme-reports-dev",
    )
    assert b.bucket_id == "acme-reports-dev"
    assert b.display_name == "Acme reports"
    assert b.owner_email == "owner@yourcompany.com"
    assert b.owner_id == "uid-owner"
    assert b.gcs_bucket == "acme-reports-dev"
    assert b.region == "europe-west1"
    assert b.tags == []
    assert b.access_control.type == "private"


def test_bucket_config_alias_roundtrip() -> None:
    """The model accepts camelCase input and dumps camelCase when by_alias=True."""
    data = {
        "bucketId": "b1",
        "displayName": "B1",
        "ownerEmail": "o@x.com",
        "ownerId": "u",
        "gcsBucket": "bk1",
        "accessControl": {"type": "domain", "domain": "yourcompany.com"},
        "tags": ["finance"],
    }
    b = BucketConfig(**data)
    out = b.model_dump(by_alias=True)
    assert out["bucketId"] == "b1"
    assert out["ownerEmail"] == "o@x.com"
    assert out["accessControl"]["type"] == "domain"
    # snake_case keys must NOT leak when by_alias=True
    assert "bucket_id" not in out
    assert "owner_email" not in out


@pytest.mark.parametrize(
    "bucket_id",
    [
        "a",  # too short (GCS minimum is 3)
        "ab",  # too short
        "-leading-hyphen",
        "trailing-hyphen-",
        "UpperCase",  # GCS is lowercase
        "has spaces",
        "has!bang",
        "a" * 64,  # 64 chars — too long for the non-subdomain form (max 63)
        "goog-reserved",  # GCS reserves "goog" prefix
        "google-reserved",  # also starts with "goog"
        "a..b",  # adjacent dots are GCS-invalid
        "bucket..name",  # adjacent dots
    ],
)
def test_bucket_config_rejects_invalid_bucket_names(bucket_id: str) -> None:
    with pytest.raises(ValidationError):
        BucketConfig(
            bucketId=bucket_id,
            displayName="x",
            ownerEmail="o@x.com",
            ownerId="u",
            gcsBucket=bucket_id,
        )


@pytest.mark.parametrize(
    "bucket_id",
    [
        "abc",  # minimum length 3
        "acme-reports-dev",
        "with.dots.ok",
        "with_underscore_ok",
        "a1b2c3",
        "a" + "b" * 61 + "c",  # 63 chars, valid
    ],
)
def test_bucket_config_accepts_valid_bucket_names(bucket_id: str) -> None:
    b = BucketConfig(
        bucketId=bucket_id,
        displayName="x",
        ownerEmail="o@x.com",
        ownerId="u",
        gcsBucket=bucket_id,
    )
    assert b.bucket_id == bucket_id


def test_bucket_config_access_control_tagged_shape() -> None:
    b = BucketConfig(
        bucketId="b1",
        displayName="x",
        ownerEmail="o@x.com",
        ownerId="u",
        gcsBucket="bk1",
        accessControl={"type": "tagged", "tags": ["finance-team"]},
    )
    assert b.access_control.type == "tagged"
    assert b.access_control.tags == ["finance-team"]


# ---------------------------------------------------------------------------
# BucketFolderConfig
# ---------------------------------------------------------------------------


def test_folder_config_minimal_happy_path() -> None:
    f = BucketFolderConfig(
        folderId="f1",
        bucketId="b1",
        path="reports/2026/",
        displayName="2026 Reports",
        ownerId="uid-owner",
        effectiveAccess={"type": "private"},
    )
    assert f.folder_id == "f1"
    assert f.bucket_id == "b1"
    assert f.path == "reports/2026/"
    assert f.access_control is None  # inherited
    assert f.effective_access.type == "private"
    assert f.tags == []


def test_folder_config_effective_access_is_required() -> None:
    """A folder cannot be persisted without a materialised effectiveAccess —
    the API must compute it on every write so rules don't recurse at read time.
    """
    with pytest.raises(ValidationError):
        BucketFolderConfig(
            folderId="f1",
            bucketId="b1",
            path="reports/",
            displayName="x",
            ownerId="u",
        )


def test_folder_config_override_access_preserved() -> None:
    f = BucketFolderConfig(
        folderId="f1",
        bucketId="b1",
        path="reports/",
        displayName="x",
        ownerId="u",
        accessControl={"type": "domain", "domain": "yourcompany.com"},
        effectiveAccess={"type": "domain", "domain": "yourcompany.com"},
    )
    assert f.access_control is not None
    assert f.access_control.type == "domain"
    assert f.effective_access.type == "domain"


@pytest.mark.parametrize(
    "bad_path",
    [
        "",  # empty
        "/leading-slash/",
        "../escape/",
        "has//double-slash/",
    ],
)
def test_folder_config_rejects_bad_paths(bad_path: str) -> None:
    with pytest.raises(ValidationError):
        BucketFolderConfig(
            folderId="f1",
            bucketId="b1",
            path=bad_path,
            displayName="x",
            ownerId="u",
            effectiveAccess={"type": "private"},
        )


def test_folder_config_alias_roundtrip() -> None:
    data = {
        "folderId": "f1",
        "bucketId": "b1",
        "path": "reports/",
        "displayName": "Reports",
        "ownerId": "u",
        "effectiveAccess": {"type": "public"},
        "accessControl": {"type": "public"},
        "tags": ["finance"],
    }
    f = BucketFolderConfig(**data)
    out = f.model_dump(by_alias=True)
    assert out["folderId"] == "f1"
    assert out["effectiveAccess"]["type"] == "public"
    assert "folder_id" not in out
    assert "effective_access" not in out


# ---------------------------------------------------------------------------
# Shared AccessControl with Skills
# ---------------------------------------------------------------------------


def test_bucket_and_folder_use_same_access_control_class() -> None:
    """Both models import AccessControl from db.models.access — no duplication."""
    b = BucketConfig(
        bucketId="b1",
        displayName="x",
        ownerEmail="o@x.com",
        ownerId="u",
        gcsBucket="bk1",
    )
    f = BucketFolderConfig(
        folderId="f1",
        bucketId="b1",
        path="r/",
        displayName="x",
        ownerId="u",
        effectiveAccess={"type": "private"},
    )
    assert isinstance(b.access_control, AccessControl)
    assert isinstance(f.effective_access, AccessControl)
