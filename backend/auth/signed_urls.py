"""GCS signed-URL issuance for agent-driven read access to buckets/folders.

Design (RESOURCE-ACCESS M3, see
docs/design/v6.0.0/resource-access-control.md §Signed URLs):

    1. The Cloud Run service account does NOT carry storage.objects.get
       permission directly. At signing time we impersonate a dedicated
       signing SA (env `SIGNED_URL_SA_EMAIL`, fallback to ADC principal)
       via IAM Credentials API's `SignBlob`. This is the standard
       service-account impersonation pattern on Cloud Run.

    2. Access is enforced *before* we sign. `ctx.can_access_folder(folder)`
       / `ctx.can_access(bucket)` — both backed by the 5-type evaluator —
       are called first, raising `AccessDenied` on refusal. No URL ever
       leaves the function for a user who can't see the resource.

    3. TTL is capped at 3600 s (one hour). Env var `SIGNED_URL_TTL_SECONDS`
       overrides the default (900 s) but is clamped to the cap. Per-call
       `ttl_seconds` arg takes precedence over env.

    4. The agent-factory pre-run hook (`build_signed_urls_for_folders`)
       stashes `{folder_id: [url, ...]}` under `tool_context.state['signed_urls']`
       so downstream tools never re-read Firestore. Folders the user can't
       access are silently skipped (they shouldn't have been offered in
       the first place — defense in depth).

    5. Fallback: if IAM signer construction fails (DefaultCredentialsError,
       SignBlob HTTP 403, etc.), we log a warning, set
       `state['signed_urls_unavailable']=True`, and return. The run
       continues — the LLM simply won't have signed URLs to work with.
"""

from __future__ import annotations

import datetime
import logging
import os
from collections.abc import Iterable
from typing import Any

import google.auth as google_auth
from google.auth import impersonated_credentials
from google.cloud import storage

from auth.access_context import AccessContext

logger = logging.getLogger(__name__)


# --- Constants ---

DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
_SIGNING_SCOPES = ("https://www.googleapis.com/auth/devstorage.read_only",)


# --- Exceptions ---


class AccessDenied(Exception):
    """Raised when the caller is not permitted to read the target resource."""

    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id
        super().__init__(f"access denied to resource {resource_id!r}")


# --- TTL helpers ---


def _clamped_ttl(ttl_seconds: int | None) -> int:
    """Resolve the effective TTL honoring env + safety cap.

    Precedence:
        1. explicit `ttl_seconds` arg (if not None) — still clamped to cap
        2. env `SIGNED_URL_TTL_SECONDS` — clamped to cap
        3. `DEFAULT_TTL_SECONDS` (900)
    """
    if ttl_seconds is None:
        env = os.getenv("SIGNED_URL_TTL_SECONDS")
        if env:
            try:
                ttl_seconds = int(env)
            except ValueError:
                logger.warning("invalid SIGNED_URL_TTL_SECONDS=%r; using default", env)
                ttl_seconds = DEFAULT_TTL_SECONDS
        else:
            ttl_seconds = DEFAULT_TTL_SECONDS
    return max(1, min(ttl_seconds, MAX_TTL_SECONDS))


# --- Signing infrastructure ---


def _build_signer_credentials() -> Any:
    """Return impersonated credentials capable of signing GCS URLs.

    Uses ADC as the source principal and impersonates the SA named by
    `SIGNED_URL_SA_EMAIL`. If that env var is unset, we fall back to the
    source principal's own service-account email if available (works on
    Cloud Run where ADC *is* a service account).

    Raises:
        Any credential/authorization error bubbles up — callers are
        expected to catch and fall back.
    """
    source, _project = google_auth.default()
    target_sa = os.getenv("SIGNED_URL_SA_EMAIL") or getattr(source, "service_account_email", None)
    if not target_sa:
        raise RuntimeError("no signing SA email available — set SIGNED_URL_SA_EMAIL or run with a service-account ADC")
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=target_sa,
        target_scopes=list(_SIGNING_SCOPES),
        lifetime=MAX_TTL_SECONDS,
    )


def _get_storage_client() -> storage.Client:
    """Return a module-level GCS client. Overridable via monkeypatch in tests."""
    return storage.Client()


def _sign_blobs(
    gcs_bucket: str,
    prefix: str,
    ttl_seconds: int,
    signer: Any,
) -> list[str]:
    """List blobs under `prefix` inside `gcs_bucket` and return signed GET URLs.

    The URL list preserves the order returned by GCS. An empty prefix lists
    the entire bucket root.
    """
    client = _get_storage_client()
    expiration = datetime.timedelta(seconds=ttl_seconds)
    sa_email = getattr(signer, "service_account_email", None)

    urls: list[str] = []
    for blob in client.list_blobs(gcs_bucket, prefix=prefix or None):
        url = blob.generate_signed_url(
            expiration=expiration,
            method="GET",
            version="v4",
            credentials=signer,
            service_account_email=sa_email,
            access_token=getattr(signer, "token", None),
        )
        urls.append(url)
    return urls


# --- Public API: folder + bucket issuance ---


def issue_folder_read_urls(
    folder: Any,
    ctx: AccessContext,
    ttl_seconds: int | None = None,
) -> list[str]:
    """Issue signed GET URLs for every object under the folder's prefix.

    Args:
        folder: A `BucketFolderConfig`-shaped object with `bucket_id`,
            `path`, `folder_id`, and `effective_access`.
        ctx: Request-scoped access context.
        ttl_seconds: Per-call TTL override. None → env / default.

    Returns:
        List of signed URLs, one per object.

    Raises:
        AccessDenied: if `ctx.can_access_folder(folder)` is False.
    """
    if not ctx.can_access_folder(folder):
        raise AccessDenied(getattr(folder, "folder_id", "<folder>"))

    ttl = _clamped_ttl(ttl_seconds)
    signer = _build_signer_credentials()

    # The folder belongs to a bucket-config that maps bucket_id to the
    # underlying GCS bucket name. For M3 we treat folder.bucket_id as the
    # GCS bucket directly; M5 will resolve via the BucketConfig lookup if
    # the two diverge.
    gcs_bucket = getattr(folder, "gcs_bucket", None) or folder.bucket_id
    return _sign_blobs(gcs_bucket, folder.path, ttl, signer)


def issue_bucket_read_urls(
    bucket: Any,
    ctx: AccessContext,
    ttl_seconds: int | None = None,
) -> list[str]:
    """Issue signed GET URLs for every object at the bucket root.

    Args:
        bucket: A `BucketConfig`-shaped object with `gcs_bucket`,
            `bucket_id`, `owner_id`, and `access_control`.
        ctx: Request-scoped access context.
        ttl_seconds: Per-call TTL override. None → env / default.

    Raises:
        AccessDenied: if `ctx.can_access(bucket)` is False.
    """
    if not ctx.can_access(bucket):
        raise AccessDenied(getattr(bucket, "bucket_id", "<bucket>"))

    ttl = _clamped_ttl(ttl_seconds)
    signer = _build_signer_credentials()
    gcs_bucket = getattr(bucket, "gcs_bucket", None) or bucket.bucket_id
    return _sign_blobs(gcs_bucket, "", ttl, signer)


# --- Agent-factory integration ---


def build_signed_urls_for_folders(
    folders: Iterable[Any],
    ctx: AccessContext,
    state: dict[str, Any],
    ttl_seconds: int | None = None,
) -> None:
    """Populate `state['signed_urls']` with {folder_id: [url, ...]}.

    Intended to run in the agent's before-run callback (see
    `adk/agent.py` `make_signed_urls_callback`). Never raises — if the
    signer cannot be built, sets `state['signed_urls_unavailable']=True`
    and returns. Individual folders the user can't access are silently
    skipped.
    """
    # Pre-flight: can we even build a signer? Do it once up front so a
    # missing IAM signer fails fast without running the access loop.
    try:
        _ = _build_signer_credentials()
    except Exception as exc:  # broad by design — any failure = fallback
        logger.warning("signed-URL signer unavailable; falling back: %s", exc)
        state["signed_urls_unavailable"] = True
        state.setdefault("signed_urls", {})
        return

    out: dict[str, list[str]] = {}
    for folder in folders:
        try:
            urls = issue_folder_read_urls(folder, ctx, ttl_seconds=ttl_seconds)
        except AccessDenied:
            # Defense in depth — skip silently; logged at DEBUG for triage.
            logger.debug("skipping folder %s: access denied", getattr(folder, "folder_id", "?"))
            continue
        except Exception as exc:
            logger.warning("failed to sign URLs for folder %s: %s", getattr(folder, "folder_id", "?"), exc)
            continue
        out[folder.folder_id] = urls

    state["signed_urls"] = out


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "AccessDenied",
    "build_signed_urls_for_folders",
    "issue_bucket_read_urls",
    "issue_folder_read_urls",
]
