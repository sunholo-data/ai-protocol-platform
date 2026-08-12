"""Client domain → GCS bucket resolution.

Each client organisation maps to its own GCS bucket, keyed by email domain.
Firestore `clients/{domain}` stores the mapping. Falls back to the
DOCUMENTS_BUCKET env var for unmapped domains (dev, internal users) —
UNLESS the deployment is configured to fail closed
(``TENANT_FALLBACK_FAIL_CLOSED``), in which case an unmapped domain is denied
a bucket rather than sharing one deployment-wide bucket with every other
unmapped tenant. See ``_fail_closed`` / ``UnmappedTenantError``.

A durable two-tier cache (v6.9.0 M4) sits in front of ``get_client_sync`` for
the ``/api/skills`` hot path (``resolve_enabled_skills`` /
``resolve_default_skill`` / ``resolve_derived_group_tags`` each re-read
``clients/{domain}`` per authenticated request). ``get_client_cached`` serves an
in-process module tier (removes the >=2x re-read) backed by a Firestore
``client_config_cache`` durable tier (survives a cold start / new instance),
ported from ``tools/map_ppa_obligations.py``. BEST-EFFORT everywhere: any cache
error degrades to a live read, NEVER a 500. Admin mutations call
``invalidate_client_cache`` so an edit propagates immediately.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from pydantic import BaseModel, ConfigDict

from db.firestore import delete_document, get_document, set_document

log = logging.getLogger(__name__)

_COLLECTION = "clients"

_FAIL_CLOSED_TRUTHY = {"1", "true", "yes", "on"}


class UnmappedTenantError(Exception):
    """Raised when a user's email domain has no mapped document bucket and the
    deployment is configured to fail closed (``TENANT_FALLBACK_FAIL_CLOSED``).

    Without this, every unmapped domain shares the single deployment-wide
    ``DOCUMENTS_BUCKET`` — weaker than the bucket-per-tenant isolation model
    (app-level access is still uid/userId-scoped, so this is defence-in-depth,
    not an app-level read leak). Callers translate this to a user-visible 403 —
    never a silent shared-bucket fallback (NEVER-SILENT).
    """

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(
            f"No document bucket is mapped for domain {domain!r} and the "
            "deployment is configured to fail closed (TENANT_FALLBACK_FAIL_CLOSED)."
        )


def _fail_closed() -> bool:
    """Whether an unmapped domain must be denied a bucket rather than sharing
    the deployment-wide fallback.

    Off by default (backward-compatible). Flip ``TENANT_FALLBACK_FAIL_CLOSED=1``
    per-env only AFTER mapping current unmapped uploaders to their own tenant
    bucket — the one-release migration window.
    """
    return os.environ.get("TENANT_FALLBACK_FAIL_CLOSED", "").strip().lower() in _FAIL_CLOSED_TRUTHY


class ClientConfig(BaseModel):
    """Firestore document at `clients/{domain}`."""

    domain: str
    documents_bucket: str | None = None
    display_name: str = ""
    # v6.4.0 ONE-DEMO M1: per-tenant skill visibility filter. None = all skills
    # visible (existing default). Non-empty list = only these skill slugs are
    # surfaced to the user via /api/skills. Defence-in-depth even when the
    # deployment is single-tenant (admin domains accidentally landing here
    # don't see ONE-internal skills if any are marked that way later).
    enabled_skills: list[str] | None = None
    # Domain-derived group tags unioned into the JWT's `groupTags` claim at
    # request time. Lets a deployment grant the `ONE` tag to every
    # acme-energy.example user without an admin running `set_custom_user_claims`
    # per signup. Tagged-access skills (type=tagged) become reachable to the
    # whole domain. None/empty = no derived tags.
    derived_group_tags: list[str] | None = None
    # v6.5.0 AUTH-LANDING: the skill slug a signed-in user lands on when they
    # have no prior chat to resume. None = fall back to enabled_skills[0], then
    # to the marketplace. Per-client; routing behaviour is platform-wide.
    default_skill: str | None = None

    model_config = ConfigDict(populate_by_name=True)


def get_client_sync(domain: str) -> ClientConfig | None:
    """Return the ClientConfig for a domain, or None if not found.

    This is the UNCACHED primitive (one Firestore read). Hot-path callers use
    ``get_client_cached`` instead."""
    data = get_document(_COLLECTION, domain)
    if data is None:
        return None
    # Drop a stored `domain` field if the doc happens to carry one: `domain` is
    # the doc id and we pass it positionally, so leaving it in `data` raises
    # "multiple values for keyword argument 'domain'" — which would 500 every
    # caller (skill list, landing redirect, tag resolution). Defensive against
    # a doc written with the id duplicated as a field.
    data = {k: v for k, v in data.items() if k != "domain"}
    return ClientConfig(domain=domain, **data)


# ---------------------------------------------------------------------------
# Durable two-tier client-config cache (v6.9.0 M4)
# ---------------------------------------------------------------------------
# Ported from tools/map_ppa_obligations.py. The in-process module tier removes
# the >=2x clients/{domain} re-read per authenticated /api/skills request; the
# Firestore durable tier ("client_config_cache", id=domain) survives a cold
# start / new instance. Short TTLs + explicit invalidation on admin mutation
# keep an edit from lingering. BEST-EFFORT: any cache error -> live read.

_CACHE_COLLECTION = "client_config_cache"
_CACHE_MODULE_TTL = 60.0  # seconds; short so admin edits propagate fast
_CACHE_DURABLE_TTL = 300.0
_CACHE_LOCK = threading.Lock()
# Sentinel distinguishing "not cached" from "cached as None" (negative cache).
_MISS = object()
# domain -> (expires_at, ClientConfig | None)
_CLIENT_CACHE: dict[str, tuple[float, ClientConfig | None]] = {}


def _module_cache_get(domain: str):  # -> _MISS | ClientConfig | None
    with _CACHE_LOCK:
        entry = _CLIENT_CACHE.get(domain)
        if entry is None:
            return _MISS
        expires_at, value = entry
        if time.time() > expires_at:
            del _CLIENT_CACHE[domain]
            return _MISS
        return value


def _module_cache_set(domain: str, value: ClientConfig | None) -> None:
    with _CACHE_LOCK:
        _CLIENT_CACHE[domain] = (time.time() + _CACHE_MODULE_TTL, value)


def _durable_cache_get(domain: str):  # -> _MISS | ClientConfig | None
    """Durable read. Any Firestore error -> a MISS (never break a request over a
    cache lookup). Honours the durable TTL."""
    try:
        doc = get_document(_CACHE_COLLECTION, domain)
        if not doc:
            return _MISS
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, (int, float)) and time.time() > expires_at:
            return _MISS
        if doc.get("absent"):
            return None  # cached negative
        config_data = doc.get("config")
        if not isinstance(config_data, dict):
            return _MISS
        config_data = {k: v for k, v in config_data.items() if k != "domain"}
        return ClientConfig(domain=domain, **config_data)
    except Exception as exc:  # cache best-effort, never fatal
        log.debug("client_config_cache: durable read failed for %s: %s", domain, exc)
        return _MISS


def _durable_cache_set(domain: str, value: ClientConfig | None) -> None:
    """Durable write. Best-effort — a Firestore failure must not fail the read
    (the value is already resolved and returned to the caller)."""
    try:
        now = time.time()
        record: dict = {"domain": domain, "cached_at": now, "expires_at": now + _CACHE_DURABLE_TTL}
        if value is None:
            record["absent"] = True
        else:
            record["config"] = value.model_dump()
        set_document(_CACHE_COLLECTION, domain, record)
    except Exception as exc:  # cache best-effort, never fatal
        log.debug("client_config_cache: durable write failed for %s: %s", domain, exc)


def get_client_cached(domain: str) -> ClientConfig | None:
    """Cached read of ``get_client_sync``.

    Two-tier (module -> durable Firestore -> live). Negative results are cached
    too (short TTL) so an unmapped domain doesn't re-read every request. Any
    cache error degrades to the live read — NEVER a 500."""
    if not domain:
        return None
    hit = _module_cache_get(domain)
    if hit is not _MISS:
        return hit  # type: ignore[return-value]
    durable = _durable_cache_get(domain)
    if durable is not _MISS:
        _module_cache_set(domain, durable)  # type: ignore[arg-type]
        return durable  # type: ignore[return-value]
    config = get_client_sync(domain)
    _module_cache_set(domain, config)
    # Only persist real results / genuine negatives (never a test double).
    if config is None or isinstance(config, ClientConfig):
        _durable_cache_set(domain, config)
    return config


def invalidate_client_cache(domain: str) -> None:
    """Drop cached config for a domain (module + durable). Called on admin
    mutation so an edit propagates immediately. Best-effort."""
    if not domain:
        return
    with _CACHE_LOCK:
        _CLIENT_CACHE.pop(domain, None)
    try:
        delete_document(_CACHE_COLLECTION, domain)
    except Exception as exc:  # best-effort
        log.debug("client_config_cache: durable invalidate failed for %s: %s", domain, exc)


def _reset_client_cache() -> None:
    """Test helper — clear the in-process client-config cache so a cached value
    never leaks across tests (the durable tier is stubbed empty per test)."""
    with _CACHE_LOCK:
        _CLIENT_CACHE.clear()


def _user_domain(user) -> str:  # type: ignore[no-untyped-def]
    """Extract the email domain from a User object, falling back to email parse."""
    domain = getattr(user, "domain", None)
    if domain:
        return domain
    email = getattr(user, "email", "") or ""
    return email.split("@")[1] if "@" in email else ""


def resolve_documents_bucket(user) -> str:  # type: ignore[no-untyped-def]
    """Return the GCS bucket name for the user's email domain.

    Looks up `clients/{domain}` in Firestore. When no mapping exists (or the
    mapping has no documents_bucket set): falls back to the DOCUMENTS_BUCKET
    env var — UNLESS the deployment fails closed (``_fail_closed``), in which
    case it raises ``UnmappedTenantError`` so unmapped domains never share one
    deployment-wide bucket. A mapped tenant with its own bucket is unaffected
    by the flag.

    Deliberately uses the UNCACHED ``get_client_sync`` — the upload path is not
    the /api/skills hot path, and the fail-closed security decision must always
    read the live mapping.
    """
    domain = _user_domain(user)
    client = get_client_sync(domain) if domain else None
    if client and client.documents_bucket:
        return client.documents_bucket
    if _fail_closed():
        raise UnmappedTenantError(domain or "(no domain)")
    return os.environ.get("DOCUMENTS_BUCKET", "aitana-documents-bucket")


def documents_bucket_for_domain(domain: str) -> str | None:
    """Return `clients/{domain}.documents_bucket`, or None if unmapped/unset.

    Viewer-INDEPENDENT resolution for a resource that belongs to a specific
    tenant regardless of who is looking at it — e.g. a skill's curated example
    library (ONE's PPAs), which lives in ONE's per-env bucket whether the viewer
    is a ONE user or a platform admin. Unlike ``resolve_documents_bucket`` there
    is NO env fallback: the caller leaves the value empty when there's no mapping
    rather than borrowing the wrong (deployment-default) bucket."""
    if not domain:
        return None
    client = get_client_sync(domain)
    return client.documents_bucket if client and client.documents_bucket else None


def resolve_enabled_skills(user) -> list[str] | None:  # type: ignore[no-untyped-def]
    """Return the tenant's enabled-skills filter, or None for "all skills".

    Looks up `clients/{domain}.enabled_skills`. None = unfiltered (existing
    behaviour for unmapped domains and tenants without the field set).
    Used by `/api/skills` to filter the response server-side (cached).
    """
    domain = _user_domain(user)
    if not domain:
        return None
    client = get_client_cached(domain)
    if client is None:
        return None
    return client.enabled_skills


def resolve_default_skill(user) -> str | None:  # type: ignore[no-untyped-def]
    """Return the skill slug a signed-in user should land on with no prior
    chat (v6.5.0 AUTH-LANDING), or None to fall back to the marketplace.

    Resolution: `clients/{domain}.default_skill`, else the first entry of
    `enabled_skills`, else None. Routing is platform-wide; this value is the
    per-client knob that focuses it.
    """
    domain = _user_domain(user)
    if not domain:
        return None
    client = get_client_cached(domain)
    if client is None:
        return None
    if client.default_skill:
        return client.default_skill
    if client.enabled_skills:
        return client.enabled_skills[0]
    return None


def resolve_derived_group_tags(domain: str) -> frozenset[str]:
    """Return tags the deployment grants to every user from this email domain.

    Read from `clients/{domain}.derived_group_tags`. Empty frozenset when no
    mapping or the field is unset. Called once per authenticated request from
    `get_current_user` and unioned with the JWT's `groupTags` claim (cached).
    """
    if not domain:
        return frozenset()
    client = get_client_cached(domain)
    if client is None or not client.derived_group_tags:
        return frozenset()
    return frozenset(client.derived_group_tags)


def resolve_channel_bucket() -> str:
    """Return the GCS bucket for files arriving via channel webhooks.

    Channel attachments don't carry the user's email domain in a way
    the upload path can rely on (a Discord user might have no email at
    all), so we use a single shared bucket per deployment. Defaults
    to the same value as `resolve_documents_bucket`'s fallback so the
    "user library" view shows channel uploads alongside web uploads.

    Forks that want per-channel buckets (e.g., one for Discord, one
    for email) set CHANNEL_DOCUMENTS_BUCKET to override.
    """
    return os.environ.get("CHANNEL_DOCUMENTS_BUCKET") or os.environ.get("DOCUMENTS_BUCKET", "aitana-documents-bucket")
