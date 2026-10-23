"""Tenant brand assets — logos, covers and staff photos held in the tenant's
own documents bucket rather than in the repo.

Why the bucket and not `backend/assets/`
----------------------------------------
Brand *furniture* (a logo, a cover) is small, changes rarely and has no privacy
dimension, so it lives in the image and a generation never depends on a network
fetch to look right. Staff **photographs** are different: they are personal data
for identifiable people, and anything committed to git is in history for good —
which does not sit well with an erasure request. Those live here instead, where
replacing or deleting one is a bucket operation with no deploy and no history.

Addressing
----------
    gs://{documents_bucket_for_domain(domain)}/brand/{domain}/{category}/{name}

Keyed on the email domain, which is what `clients/{domain}` is already keyed on,
so no codename mapping table has to be kept in sync. `documents_bucket_for_domain`
is the viewer-INDEPENDENT resolver on purpose: a tenant's brand is theirs
whoever is generating the document, and it has no env fallback, so an env where
the tenant is unmapped yields nothing rather than quietly reading some other
deployment's bucket.

Reads use the backend's own service account — the same reasoning as
`generate._download_to_tempfile`: handing a converter a `gcsRef` would mean
granting a third-party SA read on a customer bucket, and that cross-org grant is
the pattern that has already bitten this deployment.

Never silent (CLAUDE.md #8)
---------------------------
Every function reports *why* it came back empty instead of returning a bare
`[]`/`None` that a caller cannot tell apart from "the tenant has no photos".
A caller that cannot render a photo must be able to say so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Reserved top-level prefix. Sits beside user content in the same bucket and is
# visible in the bucket browser as a `brand/` folder — deliberately not filtered
# out of the listing route, because a hidden prefix is worse than a legible one.
BRAND_PREFIX = "brand"

# Categories a tenant may hold. An allowlist, so a caller cannot walk the bucket
# by passing a crafted category.
#
# `templates` is the tenant's reference .docx files (a letterhead). It is what
# makes a house style available to EVERY user in the tenant: `_find_template`
# searches the caller's own workspace first and falls back here, so nobody has
# to upload the letterhead into their own documents to use it.
CATEGORIES = frozenset({"photos", "logos", "covers", "templates"})


@dataclass
class BrandAssets:
    """What a tenant has, plus why it is empty when it is."""

    domain: str = ""
    bucket: str = ""
    category: str = ""
    names: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def brand_prefix_for(domain: str, category: str) -> str:
    """The object-name prefix for one tenant's assets of one category."""
    return f"{BRAND_PREFIX}/{domain}/{category}/"


def gs_url_for(bucket: str, domain: str, category: str, name: str) -> str:
    return f"gs://{bucket}/{brand_prefix_for(domain, category)}{name}"


def _resolve_bucket(domain: str) -> tuple[str, str | None]:
    """(bucket, error). Never raises — an unmapped tenant is a normal state."""
    if not domain:
        return "", "No tenant domain was given, so no brand bucket could be resolved."
    try:
        from db.clients import documents_bucket_for_domain

        bucket = documents_bucket_for_domain(domain) or ""
    except Exception as exc:  # Firestore unavailable — say so, do not pretend empty
        log.error("brand assets: client lookup failed for %s: %s", domain, exc)
        return "", f"The tenant configuration for {domain} could not be read ({exc})."
    if not bucket:
        return "", (
            f"No documents bucket is mapped for {domain} in this environment, so its "
            f"brand assets cannot be located. Map one on clients/{domain}.documents_bucket."
        )
    return bucket, None


def list_brand_assets(domain: str, category: str = "photos") -> BrandAssets:
    """List a tenant's brand assets of one category.

    Returns object *names* (the filename, not the full path) so callers speak
    friendly names rather than gs:// paths — CLAUDE.md #9.
    """
    if category not in CATEGORIES:
        return BrandAssets(
            domain=domain,
            category=category,
            error=f"Unknown brand category {category!r}. Known: {sorted(CATEGORIES)}.",
        )
    bucket, err = _resolve_bucket(domain)
    if err:
        return BrandAssets(domain=domain, category=category, error=err)

    prefix = brand_prefix_for(domain, category)
    try:
        from google.cloud import storage as gcs

        blobs = gcs.Client().list_blobs(bucket, prefix=prefix)
        names = sorted(b.name[len(prefix) :] for b in blobs if b.name != prefix and not b.name.endswith("/"))
    except Exception as exc:
        log.error("brand assets: list failed for gs://%s/%s: %s", bucket, prefix, exc)
        return BrandAssets(
            domain=domain,
            bucket=bucket,
            category=category,
            error=f"The brand assets for {domain} could not be listed ({exc}).",
        )
    return BrandAssets(domain=domain, bucket=bucket, category=category, names=names)


def fetch_brand_asset(domain: str, name: str, category: str = "photos") -> tuple[bytes | None, str | None]:
    """Fetch one asset's bytes. Returns (bytes, error) — exactly one is set."""
    if category not in CATEGORIES:
        return None, f"Unknown brand category {category!r}. Known: {sorted(CATEGORIES)}."
    if not name or "/" in name or name.startswith("."):
        # No traversal, and no reaching outside the tenant's own prefix.
        return None, f"Invalid asset name {name!r}."
    bucket, err = _resolve_bucket(domain)
    if err:
        return None, err

    path = f"{brand_prefix_for(domain, category)}{name}"
    try:
        from google.cloud import storage as gcs

        blob = gcs.Client().bucket(bucket).blob(path)
        if not blob.exists():
            return None, f"{name!r} is not among {domain}'s brand {category}."
        return blob.download_as_bytes(), None
    except Exception as exc:
        log.error("brand assets: fetch failed for gs://%s/%s: %s", bucket, path, exc)
        return None, f"{name!r} could not be read from storage ({exc})."


def brand_asset_manifest(domain: str) -> dict[str, Any]:
    """Everything a document generator needs, in one call, with errors preserved."""
    out: dict[str, Any] = {"domain": domain, "categories": {}}
    for category in sorted(CATEGORIES):
        assets = list_brand_assets(domain, category)
        out["categories"][category] = (
            {"names": assets.names, "bucket": assets.bucket} if assets.ok else {"names": [], "error": assets.error}
        )
    return out
