"""FastAPI routes for bucket + folder CRUD — /api/buckets endpoints.

Follows the same 404-on-deny pattern as skills/routes.py:
    - anon          → 401 (dependency on get_current_user)
    - not visible   → 404 (don't leak existence)
    - visible but not owner → 403 on PUT/DELETE (real forbidden)
    - owner / admin → 200 / 201 / 204

Folders enforce `effective_access` on every write — compute_effective_access
runs before persist so rules can read effectiveAccess directly without
recursion. Parent-access fan-out (bucket accessControl change re-writing
descendant folder effectiveAccess) is **deferred to v6.1** — see
resource-access-control.md §Open questions.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from auth import AccessContext, User, get_current_user
from buckets import bucket_config, folder_config
from db.clients import UnmappedTenantError, resolve_documents_bucket
from db.models import AccessControl, BucketConfig, BucketFolderConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/buckets", tags=["buckets"])


def _authorize_bucket_read(user: User, access: AccessContext, name: str) -> None:
    """Raise 403 unless the caller is authorized to read GCS bucket ``name``.

    The file-serving endpoints (`/{name}/list|preview|thumbnail`) stream bytes
    from an arbitrary bucket name under the runtime SA's credentials. Without this
    gate, any authenticated user could name a bucket the SA can read (e.g. a
    tenant's confidential ``…-llmops-bucket``) and exfiltrate it — the tenant
    isolation lives at the app layer, not the SA grant (v6.18.0). Allow iff:

      - the caller is a platform admin (operator diagnostics — explicit + logged), or
      - ``name`` is the caller's own tenant ``documents_bucket``, or
      - ``name`` is a registered bucket-config the caller ``can_access`` (v6.3.0 ACLs)
        **AND** the caller's email domain is in that config's ``allowed_domains``
        (v6.18.1, issue #37 — see ``_domain_allowed``).

    The bucket-config door carries BOTH checks because the config is an operator
    blessing, not a user assertion: registration is admin-only (``create_bucket``)
    and the domain binding is a second, independent lock. Before #37 the door
    opened on ``can_access`` alone, and ``can_access`` short-circuits on
    "owner always wins" — so a self-registered config named any bucket the runtime
    SA could read and returned True. Two locks now: an attacker would need an
    admin JWT *and* a matching login domain.

    Deny-by-default and fail-closed: an unmapped tenant under
    ``TENANT_FALLBACK_FAIL_CLOSED`` raises ``UnmappedTenantError`` → caught → 403,
    never a silent allow. The 403 reuses the structured ``{code, message}`` shape
    (mirrors upload-side ``TENANT_NOT_PROVISIONED``) so the UI renders a specific
    "why" (NEVER-SILENT #8).
    """
    if access.is_platform_admin:
        return
    try:
        if name == resolve_documents_bucket(user):
            return
    except UnmappedTenantError:
        pass  # fail-closed unmapped tenant → fall through to the config check / deny

    cfg = bucket_config.find_by_gcs_name(name)
    if cfg is not None and access.can_access(cfg) and _domain_allowed(user, cfg):
        return

    logger.warning("bucket-authz DENY uid=%s bucket=%s", user.uid, name)
    raise HTTPException(
        status_code=403,
        detail={
            "code": "BUCKET_NOT_AUTHORIZED",
            "message": "You don't have access to these documents.",
        },
    )


# === Request / Response models ===


class CreateBucketRequest(BaseModel):
    display_name: str = Field(alias="displayName")
    gcs_bucket: str = Field(alias="gcsBucket")
    region: str = "europe-west1"
    access_control: dict = Field(default_factory=lambda: {"type": "private"}, alias="accessControl")
    # Login domains allowed to read this bucket (issue #37). Required: a bucket
    # blessed for an installation is always blessed FOR SOMEONE. Empty → 400.
    allowed_domains: list[str] = Field(default_factory=list, alias="allowedDomains")
    tags: list[str] = []

    model_config = {"populate_by_name": True}


class UpdateBucketRequest(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")
    region: str | None = None
    access_control: dict | None = Field(default=None, alias="accessControl")
    allowed_domains: list[str] | None = Field(default=None, alias="allowedDomains")
    tags: list[str] | None = None

    model_config = {"populate_by_name": True}


class CreateFolderRequest(BaseModel):
    path: str
    display_name: str = Field(alias="displayName")
    access_control: dict | None = Field(default=None, alias="accessControl")
    tags: list[str] = []

    model_config = {"populate_by_name": True}


class UpdateFolderRequest(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")
    access_control: dict | None = Field(default=None, alias="accessControl")
    tags: list[str] | None = None

    model_config = {"populate_by_name": True}


class BucketResponse(BaseModel):
    bucket_id: str = Field(alias="bucketId")
    display_name: str = Field(alias="displayName")
    gcs_bucket: str = Field(alias="gcsBucket")
    region: str
    owner_id: str = Field(alias="ownerId")
    owner_email: str = Field(alias="ownerEmail")
    access_control: dict = Field(alias="accessControl")
    allowed_domains: list[str] = Field(default_factory=list, alias="allowedDomains")
    tags: list[str]
    created_at: float = Field(alias="createdAt")
    updated_at: float = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}

    @classmethod
    def from_config(cls, config: BucketConfig) -> BucketResponse:
        return cls.model_validate(config.model_dump(by_alias=True))


class FolderResponse(BaseModel):
    folder_id: str = Field(alias="folderId")
    bucket_id: str = Field(alias="bucketId")
    path: str
    display_name: str = Field(alias="displayName")
    owner_id: str = Field(alias="ownerId")
    access_control: dict | None = Field(alias="accessControl")
    effective_access: dict = Field(alias="effectiveAccess")
    tags: list[str]
    created_at: float = Field(alias="createdAt")
    updated_at: float = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}

    @classmethod
    def from_config(cls, config: BucketFolderConfig) -> FolderResponse:
        return cls.model_validate(config.model_dump(by_alias=True))


# === Helpers ===


def _domain_allowed(user: User, cfg: BucketConfig) -> bool:
    """True iff ``user``'s email domain is blessed for this bucket-config.

    EMPTY ``allowed_domains`` MEANS DENY (issue #37): a bucket blessed for an
    installation is always blessed for someone, so a config with no domains is
    half-configured and must not grant read. `create_bucket` rejects an empty
    list at the API boundary; this is the runtime backstop for a config written
    directly to Firestore or predating the field.
    """
    domains = cfg.allowed_domains
    if not domains:
        logger.warning(
            "bucket-authz DENY (no allowedDomains on config) bucket=%s bucketId=%s",
            cfg.gcs_bucket,
            cfg.bucket_id,
        )
        return False
    return bool(user.domain) and user.domain.strip().lower() in domains


def _require_platform_admin(request: Request, action: str) -> None:
    """Raise 403 unless the caller is a platform admin.

    Registering a GCS bucket into the platform is an OPERATOR act (issue #37):
    it names a real bucket the runtime SA can read, and the read gate trusts that
    naming. Self-service registration let any signed-in user mint themselves a
    config for someone else's bucket and walk through the bucket-config door
    ("owner always wins"). Ordinary users never need this — their own tenant
    bucket is reachable via the tenant-bucket door.
    """
    access = request.state.access
    if not access.is_platform_admin:
        logger.warning("bucket-admin DENY uid=%s action=%s", access.uid, action)
        raise HTTPException(
            status_code=403,
            detail={
                "code": "BUCKET_ADMIN_REQUIRED",
                "message": "Only a platform admin can register or change a bucket.",
            },
        )


def _validate_access_shape(ac: dict) -> None:
    """Surface AccessControl shape errors as 400, not 500."""
    try:
        AccessControl.model_validate(ac)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid accessControl: {exc.errors()[0]['msg']}") from exc


# === Bucket routes ===


@router.get("", response_model=list[BucketResponse])
def list_buckets(
    request: Request,
    owner_id: str | None = Query(None, alias="ownerId"),
    tag: str | None = None,
    access_type: str | None = Query(None, alias="accessType"),
    limit: int = Query(50, le=200),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """List buckets the caller can access."""
    access = request.state.access
    configs = bucket_config.list_buckets(owner_id=owner_id, tag=tag, access_type=access_type, limit=limit)
    visible = [c for c in configs if access.can_access(c)]
    return [BucketResponse.from_config(c) for c in visible]


@router.post("", status_code=201, response_model=BucketResponse)
def create_bucket(
    request: Request,
    req: CreateBucketRequest,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Register a GCS bucket with the platform. PLATFORM ADMIN ONLY (issue #37).

    `ownerId` is always set from the JWT — never client-supplied. `allowedDomains`
    is required: the read gate enforces it as a second lock beside `accessControl`.
    """
    _require_platform_admin(request, "create_bucket")
    _validate_access_shape(req.access_control)
    if not [d for d in req.allowed_domains if d and d.strip()]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ALLOWED_DOMAINS_REQUIRED",
                "message": "allowedDomains must name at least one login domain allowed to read this bucket.",
            },
        )
    config = bucket_config.create_bucket(
        display_name=req.display_name,
        gcs_bucket=req.gcs_bucket,
        owner_id=user.uid,
        owner_email=user.email,
        region=req.region,
        accessControl=req.access_control,
        allowedDomains=req.allowed_domains,
        tags=req.tags,
    )
    return BucketResponse.from_config(config)


@router.get("/{bucket_id}", response_model=BucketResponse)
def get_bucket(
    bucket_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Get a bucket by ID. 404 (not 403) if invisible."""
    config = bucket_config.get_bucket(bucket_id)
    if config is None or not request.state.access.can_access(config):
        raise HTTPException(status_code=404, detail="Bucket not found")
    return BucketResponse.from_config(config)


@router.put("/{bucket_id}", response_model=BucketResponse)
def update_bucket(
    bucket_id: str,
    req: UpdateBucketRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Update a bucket. PLATFORM ADMIN ONLY (issue #37); invisible buckets 404.

    Was owner-only. Since registration is now an operator act, so is mutation:
    `accessControl` / `allowedDomains` ARE the read gate's two locks, so anyone
    who can edit them can widen access.
    """
    _require_platform_admin(request, "update_bucket")
    updates = req.model_dump(by_alias=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "accessControl" in updates:
        _validate_access_shape(updates["accessControl"])

    # Everyone past _require_platform_admin is a platform admin, who sees every
    # bucket by definition — so this is an EXISTENCE check only. Re-running
    # can_access/is_owner here would 404 an admin on a private bucket they are
    # entitled to administer (and did, until #37 made the route admin-only).
    config = bucket_config.get_bucket(bucket_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Bucket not found")

    updated = bucket_config.update_bucket(bucket_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Bucket not found")
    return BucketResponse.from_config(updated)


@router.delete("/{bucket_id}", status_code=204)
def delete_bucket(
    bucket_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Delete a bucket. Owner or admin only; invisible buckets 404."""
    config = bucket_config.get_bucket(bucket_id)
    if config is None or not request.state.access.can_access(config):
        raise HTTPException(status_code=404, detail="Bucket not found")
    if not request.state.access.is_owner(config):
        raise HTTPException(status_code=403, detail="Only the bucket owner can delete")
    bucket_config.delete_bucket(bucket_id)


# === Folder routes ===


def _load_parent_or_404(bucket_id: str, request: Request) -> BucketConfig:
    parent = bucket_config.get_bucket(bucket_id)
    if parent is None or not request.state.access.can_access(parent):
        raise HTTPException(status_code=404, detail="Bucket not found")
    return parent


@router.get("/{bucket_id}/folders", response_model=list[FolderResponse])
def list_folders(
    bucket_id: str,
    request: Request,
    limit: int = Query(50, le=200),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """List folders under a bucket the caller can access."""
    _load_parent_or_404(bucket_id, request)
    access = request.state.access
    configs = folder_config.list_folders(bucket_id, limit=limit)
    visible = [c for c in configs if access.can_access_folder(c)]
    return [FolderResponse.from_config(c) for c in visible]


@router.post("/{bucket_id}/folders", status_code=201, response_model=FolderResponse)
def create_folder(
    bucket_id: str,
    req: CreateFolderRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Create a folder under `bucket_id`. Owner-only on the parent bucket."""
    parent = _load_parent_or_404(bucket_id, request)
    if not request.state.access.is_owner(parent):
        raise HTTPException(status_code=403, detail="Only the bucket owner can create folders")

    if req.access_control is not None:
        _validate_access_shape(req.access_control)

    try:
        config = folder_config.create_folder(
            bucket=parent,
            path=req.path,
            display_name=req.display_name,
            owner_id=user.uid,
            access_control=req.access_control,
            tags=req.tags,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc.errors()[0]["msg"])) from exc
    return FolderResponse.from_config(config)


@router.get("/{bucket_id}/folders/{folder_id}", response_model=FolderResponse)
def get_folder(
    bucket_id: str,
    folder_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Get a folder. 404 if invisible."""
    _load_parent_or_404(bucket_id, request)
    config = folder_config.get_folder(bucket_id, folder_id)
    if config is None or not request.state.access.can_access_folder(config):
        raise HTTPException(status_code=404, detail="Folder not found")
    return FolderResponse.from_config(config)


@router.put("/{bucket_id}/folders/{folder_id}", response_model=FolderResponse)
def update_folder(
    bucket_id: str,
    folder_id: str,
    req: UpdateFolderRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Update a folder. Bucket-owner or folder-owner only."""
    parent = _load_parent_or_404(bucket_id, request)
    config = folder_config.get_folder(bucket_id, folder_id)
    if config is None or not request.state.access.can_access_folder(config):
        raise HTTPException(status_code=404, detail="Folder not found")

    updates = req.model_dump(by_alias=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "accessControl" in updates:
        _validate_access_shape(updates["accessControl"])

    is_bucket_owner = request.state.access.is_owner(parent)
    is_folder_owner = user.uid == config.owner_id
    if not (is_bucket_owner or is_folder_owner):
        raise HTTPException(status_code=403, detail="Only the bucket or folder owner can update")

    updated = folder_config.update_folder(parent, folder_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return FolderResponse.from_config(updated)


@router.delete("/{bucket_id}/folders/{folder_id}", status_code=204)
def delete_folder(
    bucket_id: str,
    folder_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Delete a folder. Bucket-owner or folder-owner only."""
    parent = _load_parent_or_404(bucket_id, request)
    config = folder_config.get_folder(bucket_id, folder_id)
    if config is None or not request.state.access.can_access_folder(config):
        raise HTTPException(status_code=404, detail="Folder not found")

    is_bucket_owner = request.state.access.is_owner(parent)
    is_folder_owner = user.uid == config.owner_id
    if not (is_bucket_owner or is_folder_owner):
        raise HTTPException(status_code=403, detail="Only the bucket or folder owner can delete")

    folder_config.delete_folder(bucket_id, folder_id)


# === v6.4.0 4.5 SKILL-ONBOARDING M4: SA-proxied GCS list-objects endpoint ===
#
# The sidebar GCSFileBrowser (for skills that declare welcome.bucket_browser)
# calls /api/buckets/{name}/list to render the bucket contents. SA does the
# read; frontend never sees credentials. Authorization is at the APP layer:
# every file endpoint calls `_authorize_bucket_read` (platform admin / the
# caller's own tenant bucket / a bucket-config they can_access) BEFORE the SA
# read (v6.18.0). The SA's bucket whitelist is defence-in-depth behind that gate,
# NOT the primary boundary — the SA can read a tenant's confidential llmops
# bucket, so "the SA can read it" must never be the only check.

import re as _re  # noqa: E402

from google.api_core.exceptions import Forbidden as _GCSForbidden  # noqa: E402
from google.api_core.exceptions import NotFound as _GCSNotFound  # noqa: E402
from google.cloud import storage as _gcs_storage  # noqa: E402

_BUCKET_NAME_PATTERN = _re.compile(r"^[a-z0-9][a-z0-9_.-]{1,61}[a-z0-9]$")


class GCSBucketEntry(BaseModel):
    name: str
    size: int = 0
    content_type: str | None = None
    updated: float | None = None
    is_prefix: bool = Field(default=False, alias="isPrefix")

    model_config = {"populate_by_name": True}


class GCSBucketListResponse(BaseModel):
    entries: list[GCSBucketEntry]
    next_page_token: str | None = Field(default=None, alias="nextPageToken")
    prefix: str = ""

    model_config = {"populate_by_name": True}


@router.get("/{name}/list", response_model=GCSBucketListResponse)
def list_bucket_objects(
    name: str,
    request: Request,
    prefix: str = Query("", description="Object-name prefix to filter by"),
    limit: int = Query(100, le=500, description="Max entries per page"),
    page_token: str | None = Query(None, alias="pageToken"),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """List objects under a bucket prefix via SA-credentialed read.

    Returns blobs and sub-prefixes (collapsed directories) at the requested
    prefix level. Uses delimiter="/" for a single-level directory listing —
    the frontend lazy-expands one prefix at a time.
    """
    if not _BUCKET_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="Invalid bucket name format")
    _authorize_bucket_read(user, request.state.access, name)

    client = _gcs_storage.Client()
    try:
        bucket = client.bucket(name)
        iterator = client.list_blobs(
            bucket,
            prefix=prefix,
            delimiter="/",
            max_results=limit,
            page_token=page_token,
        )
        page = next(iterator.pages, None)
        blobs = list(page) if page is not None else []
        subprefixes = sorted(iterator.prefixes)
        next_token = iterator.next_page_token
    except _GCSNotFound as e:
        raise HTTPException(status_code=404, detail=f"Bucket not found: {name}") from e
    except _GCSForbidden as e:
        raise HTTPException(status_code=403, detail="Access denied to bucket") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bucket list failed: {e}") from e

    entries: list[GCSBucketEntry] = [GCSBucketEntry(name=sub, is_prefix=True) for sub in subprefixes]
    for blob in blobs:
        if blob.name == prefix:
            continue  # The prefix itself sometimes appears as a zero-size blob.
        entries.append(
            GCSBucketEntry(
                name=blob.name,
                size=blob.size or 0,
                content_type=blob.content_type,
                updated=blob.updated.timestamp() if blob.updated else None,
                is_prefix=False,
            )
        )

    return GCSBucketListResponse(
        entries=entries,
        next_page_token=next_token,
        prefix=prefix,
    )


# Inline-preview byte cap. PPAs are ~0.5-2MB; refuse anything huge so a preview
# request can't pull a multi-GB object through the app tier.
_PREVIEW_MAX_BYTES = 30 * 1024 * 1024

_CONTENT_TYPE_BY_EXT = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "csv": "text/csv",
}


@router.get("/{name}/preview")
def preview_bucket_object(
    name: str,
    request: Request,
    object_name: str = Query(..., alias="object", description="Full object name within the bucket"),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Stream a single bucket object's bytes for inline preview (auth-gated).

    Access surface: authenticated + ``_authorize_bucket_read`` (platform admin,
    the caller's own tenant bucket, or a bucket-config the caller can_access) —
    the SA's bucket whitelist is only defence-in-depth behind the app-layer gate.

    SECURITY: bytes are streamed through this authenticated route — NEVER a
    public / signed URL — so previews of private content (e.g. ONE's PPA
    contracts) stay behind the user's Firebase bearer. Do not add a public
    variant. See CLAUDE.md "Security Hard Rules".
    """
    from fastapi.responses import StreamingResponse

    if not _BUCKET_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="Invalid bucket name format")
    if not object_name or len(object_name) > 1024:
        raise HTTPException(status_code=400, detail="Invalid object name")
    _authorize_bucket_read(user, request.state.access, name)

    client = _gcs_storage.Client()
    try:
        blob = client.bucket(name).blob(object_name)
        blob.reload()  # populate size + content_type; raises NotFound if absent
    except _GCSNotFound as e:
        raise HTTPException(status_code=404, detail="Object not found") from e
    except _GCSForbidden as e:
        raise HTTPException(status_code=403, detail="Access denied to bucket") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e}") from e

    if (blob.size or 0) > _PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Object too large to preview inline")

    try:
        data = blob.download_as_bytes()
    except _GCSForbidden as e:
        raise HTTPException(status_code=403, detail="Access denied to bucket") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Could not fetch object bytes") from e

    ext = object_name.rsplit(".", 1)[-1].lower() if "." in object_name else ""
    content_type = blob.content_type or _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")
    filename = object_name.rsplit("/", 1)[-1]

    def _iter():
        yield data

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/{name}/thumbnail")
def thumbnail_bucket_object(
    name: str,
    request: Request,
    object_name: str = Query(..., alias="object", description="Full object name within the bucket"),
    width: int = Query(600, ge=64, le=1600, description="Thumbnail width in px"),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Render a bucket object (PDF first page or image) to a PNG for a clean preview.

    Same access surface + streaming-only guarantee as ``/{name}/preview``
    (``_authorize_bucket_read`` + auth; bytes never leave via a public URL). An
    ``<img>`` of this PNG avoids the browser PDF-viewer chrome that makes an
    inline ``<iframe>`` preview look tiny. PDFs + raster images only; other
    types get 415.
    """
    from fastapi.responses import Response

    from tools.documents.thumbnail import cache_get, cache_put, is_thumbnailable, render_thumbnail_png

    if not _BUCKET_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="Invalid bucket name format")
    if not object_name or len(object_name) > 1024:
        raise HTTPException(status_code=400, detail="Invalid object name")
    if not is_thumbnailable(object_name):
        raise HTTPException(status_code=415, detail="Thumbnails are only rendered for PDFs and images")
    _authorize_bucket_read(user, request.state.access, name)

    cache_key = f"bucket:{name}:{object_name}:{width}"
    cached = cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    client = _gcs_storage.Client()
    try:
        blob = client.bucket(name).blob(object_name)
        blob.reload()
    except _GCSNotFound as e:
        raise HTTPException(status_code=404, detail="Object not found") from e
    except _GCSForbidden as e:
        raise HTTPException(status_code=403, detail="Access denied to bucket") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Thumbnail failed: {e}") from e

    if (blob.size or 0) > _PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Object too large to thumbnail")

    try:
        data = blob.download_as_bytes()
    except _GCSForbidden as e:
        raise HTTPException(status_code=403, detail="Access denied to bucket") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Could not fetch object bytes") from e

    try:
        png = render_thumbnail_png(data, object_name, target_width=width)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Could not render thumbnail: {e}") from e

    cache_put(cache_key, png)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
