"""Document version history — v6.34.0 DOC-WORKBENCH P3.

Regenerating a document under the same filename updates it **in place** (the
dedup path in `ingest.py` reuses the doc-id, which is what makes "change the
second paragraph and redo it" work). Until now that silently destroyed the
previous draft: the whole point of the iterate loop is to try something, look at
it, and be able to go back.

## Why ADK artifacts and not GCS object versioning

The documents bucket has GCS object versioning enabled, and it is *not* the same
thing. Noncurrent generations are a storage-level recovery feature — the
application cannot address them, so "show me v2, revert to it" cannot be built
on them. ADK's artifact service gives real, addressable versions:

    save_artifact(...) -> int          # the version number
    list_versions(...) -> list[int]
    load_artifact(..., version=N)      # any specific version

## Where the bytes live (decision, 2026-09-01)

**In the per-client bucket**, not the deployment-wide `ADK_ARTIFACT_BUCKET`.
`GcsArtifactService` takes an explicit `bucket_name`, so we get the versioning
API without moving customer documents into a store shared across tenants. Every
other document in this system is isolated by per-client bucket; versions of those
documents must be too. Objects land under ADK's own
`{app}/{uid}/user/{filename}/{version}` prefix, beside `users/{uid}/docs/…` —
same bucket, same isolation boundary.

The artifact filename is `user:`-prefixed, which is ADK's marker for the
**user namespace**: a document belongs to a person, not to the chat session that
happened to create it, and must still be there next week from a different
session.

## The trap

`save_artifact` and `load_artifact` are **coroutines**. An un-awaited save
returns a coroutine object, writes nothing, and the later read 404s — this has
already cost this codebase a document-fetch bug, and no mock catches it. Only a
real save→load round-trip does; there is one in the tests.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

# One GcsArtifactService per bucket. Construction opens a storage client, and
# the per-client bucket is stable for the life of the process.
_services: dict[str, Any] = {}


def _artifact_service(bucket_name: str) -> Any | None:
    """A version store bound to `bucket_name`, or None if unavailable."""
    if not bucket_name:
        return None
    if bucket_name not in _services:
        try:
            from google.adk.artifacts import GcsArtifactService

            _services[bucket_name] = GcsArtifactService(bucket_name=bucket_name)
        except Exception as exc:  # pragma: no cover - construction is env-dependent
            log.warning("document versions: could not open version store in %s: %s", bucket_name, exc)
            return None
    return _services[bucket_name]


def reset_services() -> None:
    """Drop cached services (tests)."""
    _services.clear()


def artifact_filename(doc_id: str, filename: str) -> str:
    """Stable per-document artifact key.

    Keyed on the **doc-id**, not the display filename: two folders may hold
    documents of the same name, and a rename must not orphan the history. The
    extension is kept so the stored object is still recognisable in the bucket.

    The `user:` prefix is ADK's user-namespace marker — a document outlives the
    session that made it (see module docstring).
    """
    ext = PurePosixPath(filename or "").suffix
    return f"user:documents/{doc_id}{ext}"


async def save_version(
    *,
    bucket_name: str,
    app_name: str,
    user_id: str,
    doc_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> int | None:
    """Store a rendering and return its version number, or None on failure.

    Best-effort by design: a document that cannot be *versioned* is still a
    document. Losing history is bad; refusing to save the user's document
    because history failed is worse.
    """
    service = _artifact_service(bucket_name)
    if service is None or not data:
        return None

    try:
        from google.genai import types

        part = types.Part.from_bytes(data=data, mime_type=content_type or "application/octet-stream")
        # MUST be awaited — see the module docstring.
        version = await service.save_artifact(
            app_name=app_name,
            user_id=user_id,
            filename=artifact_filename(doc_id, filename),
            artifact=part,
        )
        log.info("document versions: saved %s v%s", doc_id, version)
        return int(version)
    except Exception as exc:
        log.warning("document versions: save failed for %s: %s", doc_id, exc)
        return None


async def list_versions(*, bucket_name: str, app_name: str, user_id: str, doc_id: str, filename: str) -> list[int]:
    """Version numbers for a document, oldest first. Empty when there is no history."""
    service = _artifact_service(bucket_name)
    if service is None:
        return []
    try:
        versions = await service.list_versions(
            app_name=app_name,
            user_id=user_id,
            filename=artifact_filename(doc_id, filename),
        )
        return sorted(int(v) for v in versions or [])
    except Exception as exc:
        log.warning("document versions: list failed for %s: %s", doc_id, exc)
        return []


async def load_version(
    *, bucket_name: str, app_name: str, user_id: str, doc_id: str, filename: str, version: int
) -> bytes | None:
    """Bytes of one version, or None if it is gone."""
    service = _artifact_service(bucket_name)
    if service is None:
        return None
    try:
        part = await service.load_artifact(
            app_name=app_name,
            user_id=user_id,
            filename=artifact_filename(doc_id, filename),
            version=version,
        )
    except Exception as exc:
        log.warning("document versions: load failed for %s v%s: %s", doc_id, version, exc)
        return None

    if part is None:
        return None
    inline = getattr(part, "inline_data", None)
    return getattr(inline, "data", None) if inline is not None else None
