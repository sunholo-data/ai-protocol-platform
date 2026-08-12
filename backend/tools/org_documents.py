"""ADK FunctionTools for org-scoped document discovery and load (G46 M3).

Two tools the root agent can call to surface documents that live in the
deploy's bound GCS bucket (configured via the
`A2A_AGENT_DOCUMENTS_BUCKET` env var):

- `list_org_documents(prefix)` — returns name/size/mimeType/timeCreated
  per object. Empty list when no bucket bound; never 500s.

- `read_org_document(name)` — fetches an object by name, saves it as a
  `doc:{id}.json` session artifact, appends the minted document_id to
  `state["document_ids"]` so the existing doc-loader picks it up
  alongside any A2A-uploaded files.

Module layout
-------------
This module bundles BOTH the tool functions (model-facing) and the
underlying GCS bucket-access helpers (`get_bound_bucket`,
`list_documents_in_bucket`, `read_document_from_bucket`). The
gde-ap-agent reference split these across two files
(`tools/org_documents.py` + `protocols/a2a_org_bucket.py`); we keep them
together here because the bucket helpers have no callers outside the
tools and the cohesion is high — one new module to grep when debugging
"why doesn't the agent see my bucket?".

ADK FunctionTool detection
--------------------------
ADK auto-wraps any plain async function passed in an agent's `tools=[]`
list. We expose the two functions directly. ToolContext arrives as the
last argument (per ADK's tool-binding convention) and gives us access
to session state and the artifact_service via the tool's runner.

Binding storage — v1 design
---------------------------
ONE bucket per deploy, configured via `A2A_AGENT_DOCUMENTS_BUCKET`
(e.g. `gs://my-org-invoices/`). When the env var is unset,
`get_bound_bucket()` returns `None` and the tools degrade gracefully
to an empty list — never 500.

Security note
-------------
The Cloud Run service account needs `roles/storage.objectViewer` on
the bound bucket. No wildcard access; the SA is granted explicitly
per-bucket. If the env var is set but the SA can't read, list returns
`[]` with a logged warning rather than 500-ing.

Size limits
-----------
List is capped at `A2A_ORG_BUCKET_LIST_LIMIT` (default 100) so a peer
asking for documents from a 10k-object bucket doesn't blow up the
model's context. The orchestrator should use the `prefix` arg for
narrower listings.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_LIST_LIMIT = 100


# ---------------------------------------------------------------------------
# Bucket binding + GCS access helpers (kept module-private but exposed at
# module level so tests can monkeypatch `_gcs_client` without spelunking.)
# ---------------------------------------------------------------------------


def get_bound_bucket() -> str | None:
    """Return the bound bucket URI for this deploy, or None.

    Reads `A2A_AGENT_DOCUMENTS_BUCKET` env var. Validates the prefix
    (`gs://`) and returns the URI normalised with a single trailing
    slash so downstream `f"{bucket}{name}"` concatenation produces clean
    object paths. Returns None on unset/malformed values rather than
    raising — the tools fall back to a graceful "no documents available"
    response.
    """
    raw = os.environ.get("A2A_AGENT_DOCUMENTS_BUCKET", "").strip()
    if not raw:
        return None
    if not raw.startswith("gs://"):
        logger.warning(
            "A2A_AGENT_DOCUMENTS_BUCKET=%r does not start with gs://; ignoring",
            raw,
        )
        return None
    return raw.rstrip("/") + "/"  # Normalise trailing slash


def _list_limit() -> int:
    raw = os.environ.get("A2A_ORG_BUCKET_LIST_LIMIT", "")
    if not raw:
        return _DEFAULT_LIST_LIMIT
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_LIST_LIMIT


def _parse_bucket_uri(bucket_uri: str) -> tuple[str, str]:
    """Split a gs://bucket/prefix URI into (bucket_name, prefix).

    Allows the env var to point at a sub-prefix within a bucket so a
    single shared bucket can serve multiple deploys via path isolation.
    """
    parsed = urlparse(bucket_uri)
    if parsed.scheme != "gs":
        msg = f"Expected gs:// URI, got {bucket_uri!r}"
        raise ValueError(msg)
    return parsed.netloc, parsed.path.lstrip("/")


@lru_cache(maxsize=1)
def _gcs_client() -> Any:
    """Lazy singleton — keeps the GCS SDK out of cold-start path for
    deploys that don't use this feature. Cached at module level so
    repeated tool calls don't re-init the client.
    """
    from google.cloud import storage

    return storage.Client()


async def list_documents_in_bucket(bucket_uri: str, *, prefix: str = "") -> list[dict[str, Any]]:
    """LIST objects in the bound bucket; return per-object metadata.

    Returns `[]` on:
      - Unbound deploy (caller already short-circuits but defence in depth)
      - SA can't read (logged WARNING)
      - Bucket doesn't exist (logged WARNING)

    Each returned object has: name, size, mimeType, timeCreated. The
    list is capped at A2A_ORG_BUCKET_LIST_LIMIT (default 100). Prefix
    is concatenated with any prefix encoded in the bucket_uri itself.
    """
    try:
        bucket_name, base_prefix = _parse_bucket_uri(bucket_uri)
    except ValueError as exc:
        logger.warning("list_documents_in_bucket: bad bucket URI: %s", exc)
        return []

    full_prefix = f"{base_prefix}{prefix}".lstrip("/")

    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        # max_results applied client-side via iteration so we never page
        # past the limit even if the bucket has many more objects.
        limit = _list_limit()
        items: list[dict[str, Any]] = []
        for blob in client.list_blobs(bucket, prefix=full_prefix, max_results=limit):
            items.append(
                {
                    "name": blob.name,
                    "size": blob.size,
                    "mimeType": blob.content_type,
                    "timeCreated": blob.time_created.isoformat() if blob.time_created else None,
                }
            )
        logger.info(
            "list_documents_in_bucket: bucket=%s prefix=%r returned %d object(s)",
            bucket_name,
            full_prefix,
            len(items),
        )
        return items
    except Exception:
        logger.exception(
            "list_documents_in_bucket: failed for bucket=%s prefix=%r — returning []",
            bucket_uri,
            full_prefix,
        )
        return []


async def read_document_from_bucket(
    bucket_uri: str,
    name: str,
    *,
    runner: Any,
    app_name: str,
    user_id: str,
    session_id: str,
) -> str | None:
    """Fetch an object from the bound bucket; save as `doc:{id}.json` artifact.

    Returns the minted `document_id` on success, None on failure. The
    artifact format mirrors what the AG-UI doc-loader writes: a JSON
    blob carrying a single block with the inline bytes (base64) plus
    metadata. The orchestrator's doc-loader callback picks it up via
    the standard `state["document_ids"]` path.
    """
    try:
        bucket_name, base_prefix = _parse_bucket_uri(bucket_uri)
    except ValueError as exc:
        logger.warning("read_document_from_bucket: bad bucket URI: %s", exc)
        return None

    object_name = f"{base_prefix}{name}".lstrip("/")

    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        data = blob.download_as_bytes()
        content_type = blob.content_type or "application/octet-stream"
        display_name = name.rsplit("/", 1)[-1] or name
    except Exception:
        logger.exception(
            "read_document_from_bucket: download failed for gs://%s/%s",
            bucket_name,
            object_name,
        )
        return None

    doc_id = str(uuid.uuid4())
    block = {
        "kind": "a2a-org-bucket-file",
        "displayName": display_name,
        "mimeType": content_type,
        "bytesBase64": base64.b64encode(data).decode("ascii"),
        "sourceUri": f"gs://{bucket_name}/{object_name}",
    }

    from google.genai.types import Blob, Part

    artifact = Part(
        inline_data=Blob(
            data=json.dumps([block]).encode("utf-8"),
            mime_type="application/json",
        )
    )

    try:
        await runner.artifact_service.save_artifact(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=f"doc:{doc_id}.json",
            artifact=artifact,
        )
        logger.info(
            "read_document_from_bucket: saved gs://%s/%s as doc:%s.json (%d bytes)",
            bucket_name,
            object_name,
            doc_id,
            len(data),
        )
        return doc_id
    except Exception:
        logger.exception(
            "read_document_from_bucket: artifact save failed for gs://%s/%s",
            bucket_name,
            object_name,
        )
        return None


# ---------------------------------------------------------------------------
# Model-facing tool functions (ADK FunctionTool auto-wrapped at agent build)
# ---------------------------------------------------------------------------


async def list_org_documents(prefix: str = "", tool_context: Any = None) -> list[dict[str, Any]]:
    """List documents in this agent's bound GCS bucket.

    Use this when the user asks about existing organisational documents
    (invoices already uploaded, vendor master records, contracts,
    policies) before deciding to extract from any peer-supplied
    attachments. Returns an empty list if no bucket is bound to this
    deploy — answer from text context only in that case.

    Args:
        prefix: Optional path prefix to filter within the bucket
            (e.g. "vendor-master/" or "2026-Q1/"). Empty by default
            returns top-level objects.
        tool_context: Injected by ADK; do not pass explicitly.

    Returns:
        List of dicts. Each has keys: name (object path within bucket),
        size (bytes), mimeType (content-type), timeCreated (ISO
        timestamp). Empty list = no documents available; the model
        should answer from context only.
    """
    _ = tool_context  # ADK injects it; we don't need it for list.
    bucket = get_bound_bucket()
    if bucket is None:
        logger.info("list_org_documents: no bound bucket — returning []")
        return []

    return await list_documents_in_bucket(bucket, prefix=prefix or "")


async def read_org_document(name: str, tool_context: Any = None) -> dict[str, Any]:
    """Load an org document into the current session for the agent to use.

    Call this AFTER `list_org_documents` once you've identified the
    specific document needed. The fetched bytes land as a session
    artifact and a document_id is appended to session state; the
    existing document-loader callback picks it up on the next agent
    turn so the orchestrator can reason about its content.

    Args:
        name: Object name within the bucket, as returned by
            `list_org_documents` in the `name` field.
        tool_context: Injected by ADK; do not pass explicitly.

    Returns:
        A dict with keys:
            ok (bool): True if loaded, False otherwise
            doc_id (str | None): Minted document_id on success
            message (str): Human-readable status for the model
    """
    bucket = get_bound_bucket()
    if bucket is None:
        return {
            "ok": False,
            "doc_id": None,
            "message": "No organisational bucket is bound to this deploy.",
        }

    # tool_context exposes the runner + session triple through ADK's
    # public API. We need (app_name, user_id, session_id) to write the
    # artifact correctly.
    runner = getattr(tool_context, "runner", None) if tool_context is not None else None
    if runner is None and tool_context is not None:
        # In ADK FunctionTool execution, tool_context exposes the runner
        # via private attribute fallback; try both shapes.
        runner = getattr(tool_context, "_runner", None)
    if runner is None:
        logger.warning("read_org_document: tool_context has no runner; cannot save artifact")
        return {
            "ok": False,
            "doc_id": None,
            "message": "Tool context missing runner reference.",
        }

    invocation_context = getattr(tool_context, "_invocation_context", None) or getattr(
        tool_context, "invocation_context", None
    )
    if invocation_context is None:
        logger.warning("read_org_document: tool_context has no invocation context")
        return {
            "ok": False,
            "doc_id": None,
            "message": "Tool context missing invocation context.",
        }

    app_name = invocation_context.app_name
    session = invocation_context.session
    user_id = session.user_id
    session_id = session.id

    doc_id = await read_document_from_bucket(
        bucket,
        name,
        runner=runner,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if doc_id is None:
        return {
            "ok": False,
            "doc_id": None,
            "message": f"Failed to load gs://{bucket.replace('gs://', '')}{name} — check object exists and SA has read access.",
        }

    # Append to state["document_ids"] so the doc-loader sees it.
    state = getattr(tool_context, "state", None)
    if state is not None:
        existing = list(state.get("document_ids") or [])
        if doc_id not in existing:
            state["document_ids"] = [*existing, doc_id]

    return {
        "ok": True,
        "doc_id": doc_id,
        "message": f"Loaded {name} into session as doc:{doc_id}.json. Use it in your next response.",
    }
