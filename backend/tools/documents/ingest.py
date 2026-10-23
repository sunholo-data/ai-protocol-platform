"""Shared document ingest — the ONE way a document enters the workspace.

Extracted from `tools/documents/upload.py` (v6.32.0 document-creation M1). It was
welded inside the `POST /api/documents/upload` route handler, which meant nothing
that is not an HTTP request could put a document into the workspace.

Both entrances now call `ingest_document()`:

  * `POST /api/documents/upload`  — a user uploads a file
  * `generate_document(...)`      — a skill generates one

That matters because the correctness of an upload is not one step, it is six:
per-client bucket resolution with the fail-closed `TENANT_NOT_PROVISIONED` 403,
same-filename dedup within a folder, the pending-record-then-parse write ordering,
folder counter deltas, the no-delta-on-overwrite rule, and a `failed` record
written before a storage error propagates. A second path that re-implements five
of the six is how the two entrances drift — and the drift shows up as a generated
document that is subtly not a real document (wrong bucket on one tenant, no dedup
on regeneration, counters off by the number of iterations).

Flow:
  1. Resolve per-client GCS bucket from the user's email domain
  2. Write Firestore record with parseStatus: pending
  3. Upload bytes to GCS: users/{uid}/docs/{folderId}/{filename}
  4. AILANG Parse: blocks
  5. Update Firestore with parseStatus: parsed|failed + stats
  6. Return ParsedDocumentResponse
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import db.folders as folders_db
from auth import User
from db.clients import UnmappedTenantError, resolve_documents_bucket
from db.firestore import get_document, query_documents, set_document
from extensions import document_event

log = logging.getLogger(__name__)

_COLLECTION = "parsed_documents"

_ALLOWED_EXTENSIONS = {
    ".docx",
    ".pptx",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
    ".epub",
    ".eml",
    ".mbox",
    ".html",
    ".htm",
    ".md",
    ".csv",
    ".pdf",
    ".txt",
    # Images (and LaTeX) parse through AILANG Parse's AI extraction
    # (ailang_parse.PARSEABLE_EXTENSIONS). They were missing here, so the parser
    # supported them but the upload refused them with a 400 — a folder of
    # screenshots and scans uploaded nothing (2026-10-01).
    ".png",
    ".jpg",
    ".jpeg",
    ".tex",
}

# Canonical Content-Type by extension. Browsers usually set these correctly,
# but programmatic clients often send application/octet-stream — which makes
# downstream parsers (e.g. AILANG Parse) fall back to content sniffing and
# misroute Office files (docx → generic zip-office). Override at upload time.
_EXTENSION_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".epub": "application/epub+zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".eml": "message/rfc822",
    ".mbox": "application/mbox",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}


def _resolve_content_type(filename: str, client_content_type: str | None) -> str:
    ext = PurePosixPath(filename).suffix.lower()
    canonical = _EXTENSION_CONTENT_TYPES.get(ext)
    if canonical:
        return canonical
    return client_content_type or "application/octet-stream"


class ParsedDocumentResponse(BaseModel):
    doc_id: str = Field(alias="docId")
    status: str
    original_filename: str = Field(alias="originalFilename")
    blocks_count: int = Field(default=0, alias="blocksCount")
    storage_path: str = Field(alias="storagePath")
    folder_id: str | None = Field(default=None, alias="folderId")
    error: str | None = None

    # --- Template outcome (v6.40.0) -----------------------------------------
    # Optional, and absent on the plain upload path. `generate_document` (the
    # agent tool) has always reported these; the HTTP routes computed them and
    # threw them away, so the Formatting panel could only ever say "Updated" —
    # true whether the template had carried twelve parts or none. A restyle the
    # user cannot audit is indistinguishable from one that did not happen
    # (NEVER-SILENT #8).
    #
    # `template_used` is the FRIENDLY FILENAME (CLAUDE.md #9), never the doc-id.
    template_used: str = Field(default="", alias="templateUsed")
    reference_doc_applied: bool = Field(default=False, alias="referenceDocApplied")
    template_parts_carried: int = Field(default=0, alias="templatePartsCarried")

    # Folder upload: true when `skip_unchanged` found a byte-identical copy
    # already in that folder, so nothing was stored or re-parsed. The client
    # counts these separately ("12 unchanged") rather than as uploads.
    unchanged: bool = False

    model_config = {"populate_by_name": True}


def _upload_to_gcs(bucket_name: str, path: str, data: bytes, content_type: str, uid: str, filename: str) -> None:
    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(path)
    blob.metadata = {"originalName": filename, "uploadedBy": uid}
    blob.upload_from_string(data, content_type=content_type)


# Plain-text formats ailang-parse does NOT handle (it has no `.txt` path, so a
# .txt upload used to strand forever in `pending_ai_extraction` — nothing ever
# resolves that status, so the viewer span "Parsing document…" indefinitely).
# These need no parsing: read the bytes and emit paragraph blocks directly.
_PLAIN_TEXT_EXTENSIONS = {".txt", ".log", ".text"}


def _is_plain_text(gs_url: str) -> bool:
    return PurePosixPath(gs_url).suffix.lower() in _PLAIN_TEXT_EXTENSIONS


async def _parse_plain_text(gs_url: str) -> list | None:
    """Read a plain-text file from GCS and emit paragraph blocks (split on blank
    lines). Returns None on a read/decode failure. Block shape matches the
    permissive db.models.Block (type + text + a block_id for citations)."""
    import asyncio
    from urllib.parse import urlparse

    from google.cloud import storage as gcs

    def _read() -> bytes:
        parts = urlparse(gs_url)  # gs://bucket/path
        return gcs.Client().bucket(parts.netloc).blob(parts.path.lstrip("/")).download_as_bytes()

    try:
        data = await asyncio.to_thread(_read)
    except Exception as exc:
        log.error("plain-text read failed for %s: %s", gs_url, exc)
        return None

    text = data.decode("utf-8", errors="replace")
    blocks: list[dict] = [
        {"type": "paragraph", "text": para, "block_id": f"p{i}"}
        for i, para in enumerate(chunk.strip() for chunk in text.split("\n\n"))
        if para
    ]
    # A file with no blank-line breaks (or a single line) still gets one block.
    if not blocks and text.strip():
        blocks = [{"type": "paragraph", "text": text.strip(), "block_id": "p0"}]
    return blocks


async def _run_parse(gs_url: str) -> tuple[str, list, int, str | None]:
    """Run AILANG Parse. Returns (status, blocks, parsed_ms, error).

    We render documents from the BlockADT directly — see
    docs/design/v6.1.0/document-rendering-decision.md.
    """
    import time

    t0 = time.monotonic()

    # Plain text needs no parser — read it and emit blocks so it reaches
    # `parsed` instead of the never-resolving `pending_ai_extraction`.
    if _is_plain_text(gs_url):
        blocks = await _parse_plain_text(gs_url)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if blocks is None:
            return "failed", [], elapsed_ms, "Could not read text file"
        return "parsed", blocks, elapsed_ms, None

    from tools.documents.ailang_parse import parse_gcs_file

    outcome = await parse_gcs_file(gs_url, output_format="blocks")
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if outcome is None:
        log.info("AILANG Parse: extension not supported for %s, using AI extraction", gs_url)
        return "pending_ai_extraction", [], elapsed_ms, None
    if not outcome.ok:
        log.error("AILANG Parse failed for %s: [%s] %s", gs_url, outcome.error_code, outcome.error)
        return "failed", [], elapsed_ms, outcome.error

    return "parsed", outcome.blocks or [], elapsed_ms, None


class _ParseResult:
    __slots__ = ("blocks", "error", "parsed_ms", "status")

    def __init__(self, status: str, blocks: list, error: str | None, parsed_ms: int | None) -> None:
        self.status = status
        self.blocks = blocks
        self.error = error
        self.parsed_ms = parsed_ms


def _to_response(doc: dict) -> ParsedDocumentResponse:
    """Build a ParsedDocumentResponse from a parsed_documents-shaped dict.

    Accepts both shapes that flow through the document pipeline:
    - query_documents() results, which carry the Firestore doc id at "__id"
    - inline dicts assembled by routes that already know the doc_id locally
      and put it at "doc_id" / "docId"

    Lets /upload and /import-by-reference build identical responses without
    duplicating the field mapping.
    """
    return ParsedDocumentResponse(
        docId=doc.get("__id") or doc.get("doc_id") or doc.get("docId") or "",
        status=doc.get("parseStatus") or doc.get("status") or "parsed",
        originalFilename=doc.get("originalFilename") or "",
        blocksCount=doc.get("blockCount") or len(doc.get("blocks") or []),
        storagePath=doc.get("storagePath") or "",
        folderId=doc.get("folderId"),
        error=doc.get("parseError"),
        unchanged=bool(doc.get("unchanged")),
    )


_MAX_FOLDER_PATH = 200


def normalize_folder_path(folder_path: str) -> str:
    """A client folder path (``Reports/2026``) → the library folder name.

    Library folders are flat, so the cleaned relative path IS the name. Empty
    segments and surrounding slashes are dropped; ``..`` / ``.`` segments are
    refused rather than normalised away — a path naming a parent is a client
    bug, not something to guess at.
    """
    parts = [p.strip() for p in (folder_path or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="folder_path must not contain '.' or '..' segments.")
    name = "/".join(parts)
    if len(name) > _MAX_FOLDER_PATH:
        raise HTTPException(status_code=400, detail=f"folder_path is longer than {_MAX_FOLDER_PATH} characters.")
    return name


def require_owned_folder(user_id: str, folder_id: str) -> dict[str, Any]:
    """The caller's folder, or a visible 404 ``FOLDER_NOT_FOUND``.

    Every route that files a document under a CALLER-SUPPLIED folder id (upload,
    move) goes through this. Without it an unknown id was stored as-is and the
    document became an orphan: present in Firestore, invisible in My Documents,
    with a 200 telling the user it worked (found 2026-10-15 via
    `aiplatform docs upload --folder <name>`, which sent a name as the id).
    """
    folder = folders_db.get_owned_folder(user_id, folder_id)
    if folder is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FOLDER_NOT_FOUND",
                "message": (
                    f"No folder with id '{folder_id}' in your documents. "
                    "Pick an existing folder, or create it first (POST /api/folders)."
                ),
                "folderId": folder_id,
            },
        )
    return folder


def _store_document(
    doc_id: str,
    *,
    user_id: str,
    skill_id: str,
    gs_url: str,
    storage_path: str,
    original_filename: str,
    source_format: str,
    folder_id: str | None,
    parse_result: _ParseResult,
    now: datetime,
    preserve_blocks_when_pending: list | None = None,
    source: str = "uploaded",
    generated_from: dict[str, Any] | None = None,
    source_markdown: str = "",
    version: int | None = None,
    content_sha256: str = "",
) -> None:
    pr = parse_result
    blocks = pr.blocks
    doc: dict = {
        "skillId": skill_id,
        "userId": user_id,
        "sourceUrl": gs_url,
        "sourceFormat": source_format,
        "originalFilename": original_filename,
        "storagePath": storage_path,
        "folderId": folder_id,
        "parseStatus": pr.status,
        # Store parsed blocks so build_document_context / the AI pipeline can read them.
        # Firestore limit is 1 MiB per doc; typical documents are well under that.
        # A pending write used to blank `blocks`. For a NEW document that is
        # correct — there is nothing yet. For a RE-INGEST it wiped the content of
        # a document the user may be reading: the open panel re-renders from
        # Firestore live, sees an empty block list and shows "No preview content
        # for this document" until the parse lands a second later. Regenerating
        # made the document blink out, which reads as "nothing happened" — the
        # exact confusion reported on test 2026-09-01.
        "blocks": blocks
        if pr.status == "parsed"
        else (preserve_blocks_when_pending or [])
        if pr.status == "pending"
        else [],
        "blockCount": len(blocks) if pr.status == "parsed" else None,
        "tableCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "table") if blocks else None,
        "imageCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "image") if blocks else None,
        "changeCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "change") if blocks else None,
        "parsedMs": pr.parsed_ms if pr.status == "parsed" else None,
        "parseError": pr.error if pr.status == "failed" else None,
        "status": pr.status,
        "parsedAt": now.isoformat() if pr.status == "parsed" else None,
        "summary": {
            "totalBlocks": len(blocks),
            "headings": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "heading"),
            "tables": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "table"),
            "images": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "image"),
            "changes": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "change"),
        },
        "editedBlocks": {},
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        # --- Provenance (v6.32.0) ---
        # Optional by construction: existing records deserialize unchanged and
        # read as source="uploaded", so no migration runs. Provenance is cheap
        # now and unrecoverable later — a generated document becomes context for
        # a later generation, and at that point it matters whether the agent is
        # reading a customer's contract or its own earlier draft.
        "source": source,
    }
    if generated_from is not None:
        doc["generatedFrom"] = generated_from
    if source_markdown:
        # v6.34.0 P3. There is NO write path into a .docx — `ailang-parse`
        # exposes parse and convert and nothing else — so a revision cannot
        # surgically edit the binary. The markdown the model authored is the only
        # thing an edit can be applied to, and it used to be discarded the moment
        # the bytes were rendered. Keeping it is what makes "change the second
        # paragraph" possible at all.
        doc["sourceMarkdown"] = source_markdown
    if content_sha256:
        # What `skip_unchanged` compares against on the next upload of the same
        # filename into the same folder.
        doc["contentSha256"] = content_sha256
    if version is not None:
        # Latest ADK artifact version for this document (see versions.py).
        doc["currentVersion"] = version
    # This write REPLACES the record. Carry the document's project tags forward
    # (projects.md §5 trap): a new version, a regeneration or a reparse must not
    # silently take the document out of every project it was in.
    project_ids = _existing_project_ids(doc_id, user_id)
    if project_ids:
        doc["projectIds"] = project_ids
    set_document(_COLLECTION, doc_id, doc)
    log.info("Stored ParsedDocument %s (parseStatus=%s, blocks=%d, source=%s)", doc_id, pr.status, len(blocks), source)


def _existing_project_ids(doc_id: str, user_id: str) -> list[str]:
    """The project tags on an existing record of the caller's, or []. Best-effort:
    a failed read must not fail the ingest."""
    try:
        prior = get_document(_COLLECTION, doc_id)
    except Exception as exc:
        log.warning("Could not read %s to keep its project tags: %s", doc_id, exc)
        return []
    if not prior or prior.get("userId") != user_id:
        return []
    return [p for p in (prior.get("projectIds") or []) if p]


async def _save_version(
    *, bucket_name: str, user_id: str, doc_id: str, filename: str, data: bytes, content_type: str
) -> int | None:
    """Snapshot these bytes as a retrievable version. Best-effort.

    Versions live in the SAME per-client bucket as the document (decision
    2026-09-01) — see tools/documents/versions.py for why that is not the
    deployment-wide ADK artifact bucket.
    """
    from adk.agui import APP_NAME
    from tools.documents.versions import save_version

    return await save_version(
        bucket_name=bucket_name,
        app_name=APP_NAME,
        user_id=user_id,
        doc_id=doc_id,
        filename=filename,
        data=data,
        content_type=content_type,
    )


def _tenant_not_provisioned() -> HTTPException:
    """The fail-closed 403 for a domain with no mapped bucket.

    Structured detail so the frontend can render a clear, specific "why"
    (NEVER-SILENT #8) instead of a generic "Upload failed (403)". `code` lets
    the UI branch; `message` is user-facing copy.
    """
    return HTTPException(
        status_code=403,
        detail={
            "code": "TENANT_NOT_PROVISIONED",
            "message": (
                "Document upload isn't available for your account. This feature is only "
                "enabled for organizations that have been set up by an administrator — the "
                "account you're signed in with isn't mapped to one. Sign in with your work "
                "account, or ask an administrator to enable document storage for your domain."
            ),
        },
    )


async def ingest_document(
    *,
    user: User,
    file_bytes: bytes,
    filename: str,
    content_type: str | None = None,
    folder_id: str | None = None,
    skill_id: str = "",
    source: Literal["uploaded", "generated"] = "uploaded",
    generated_from: dict[str, Any] | None = None,
    source_markdown: str = "",
    folder_path: str = "",
    skip_unchanged: bool = False,
) -> ParsedDocumentResponse:
    """Put a document into the user's workspace. The only way in.

    ``folder_path`` (folder upload): when no ``folder_id`` is given, the file
    goes into the library folder named by that relative path, created on first
    use. ``skip_unchanged``: if the same filename already sits in that folder
    with byte-identical content and parsed cleanly, return it as-is with
    ``unchanged=True`` — re-uploading a folder only processes what changed.

    Callable with no HTTP request in scope — `POST /upload` is a thin route over
    this, and `generate_document` calls it directly, so an uploaded document and
    a generated one are indistinguishable to everything downstream (context
    building, RAG, comparison, the browser, artifacts).

    Raises:
        HTTPException: 400 on an unsupported extension, 403 when the user's
            domain maps to no bucket (fail-closed), 500 on a storage failure.
            Raised rather than returned so `/upload` keeps its exact contract;
            tool callers catch it and surface a visible error (NEVER-SILENT #8).
    """
    # Folder uploads: Chrome sends a directory-picker file's RELATIVE PATH as
    # its multipart filename (`llm/attention.pdf`). The folder arrives
    # separately (`folder_path`), so keep only the file's own name — the slash
    # used to become `_` and every document was named `llm_attention.pdf`.
    filename = PurePosixPath((filename or "").replace("\\", "/")).name
    if not filename:
        raise HTTPException(status_code=400, detail="File must have a name.")

    ext = PurePosixPath(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        log.warning("upload refused for %s: type %r is not supported", filename, ext)
        raise HTTPException(
            status_code=400,
            detail=f"File type {ext!r} is not supported. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    # Resolve destination folder (auto-create if not provided)
    folder_name = normalize_folder_path(folder_path) if folder_path and not (folder_id or "").strip() else ""
    effective_folder_id = (
        (folder_id or "").strip()
        or (folders_db.ensure_named_folder(user.uid, folder_name) if folder_name else "")
        or folders_db.ensure_default_folder(user.uid)
    )
    content_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # Resolve per-client GCS bucket. When the deployment fails closed and the
    # user's domain has no mapped bucket, refuse rather than write into the
    # shared deployment-wide bucket — surfaced as a visible 403 (NEVER-SILENT).
    try:
        bucket_name = resolve_documents_bucket(user)
    except UnmappedTenantError as exc:
        raise _tenant_not_provisioned() from exc

    safe_filename = filename.replace("/", "_").replace("\\", "_")

    # Deduplication: reuse existing doc_id if same filename already exists in
    # this folder. GCS overwrites the file at the same path; we update the
    # existing Firestore record in-place instead of creating a second entry.
    # This is also what makes regeneration update in place rather than pile up
    # a new draft per iteration.
    existing = query_documents(
        _COLLECTION,
        filters=[
            ("userId", "==", user.uid),
            ("folderId", "==", effective_folder_id),
            ("originalFilename", "==", safe_filename),
        ],
        limit=1,
    )
    is_overwrite = bool(existing)
    if (
        skip_unchanged
        and is_overwrite
        and existing[0].get("contentSha256") == content_sha256
        and (existing[0].get("parseStatus") or existing[0].get("status")) == "parsed"
    ):
        log.info("Unchanged re-upload of %s (doc %s) — skipped", safe_filename, existing[0]["__id"])
        return _to_response({**existing[0], "unchanged": True})
    doc_id = existing[0]["__id"] if is_overwrite else str(uuid.uuid4())
    if is_overwrite:
        log.info("Re-ingest detected for %s — reusing doc_id %s", safe_filename, doc_id)
    storage_path = f"users/{user.uid}/docs/{effective_folder_id}/{safe_filename}"
    gs_url = f"gs://{bucket_name}/{storage_path}"
    now = datetime.now(UTC)

    _store_kwargs: dict[str, Any] = {
        "user_id": user.uid,
        "skill_id": skill_id,
        "gs_url": gs_url,
        "storage_path": storage_path,
        "original_filename": safe_filename,
        "source_format": ext.lstrip("."),
        "folder_id": effective_folder_id,
        "now": now,
        "source": source,
        "generated_from": generated_from,
        "source_markdown": source_markdown,
        "content_sha256": content_sha256,
    }

    # Write pending record immediately so the frontend can show the file.
    # On a RE-INGEST, carry the existing blocks through so an open document does
    # not blank while the new bytes are parsed.
    existing_blocks = (existing[0].get("blocks") if is_overwrite and existing else None) or None
    _store_document(
        doc_id,
        parse_result=_ParseResult("pending", [], None, None),
        preserve_blocks_when_pending=existing_blocks,
        **_store_kwargs,
    )

    # Upload to GCS
    try:
        _upload_to_gcs(
            bucket_name,
            storage_path,
            file_bytes,
            _resolve_content_type(safe_filename, content_type),
            user.uid,
            safe_filename,
        )
        log.info("Ingested %s to gs://%s/%s", safe_filename, bucket_name, storage_path)
    except Exception as exc:
        log.error("GCS upload failed for %s: %s", safe_filename, exc)
        _store_document(doc_id, parse_result=_ParseResult("failed", [], str(exc), None), **_store_kwargs)
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {exc}") from exc

    # Version the bytes we just stored, in the SAME per-client bucket (v6.34.0
    # P3). Best-effort: a document that cannot be versioned is still a document.
    version = await _save_version(
        bucket_name=bucket_name,
        user_id=user.uid,
        doc_id=doc_id,
        filename=safe_filename,
        data=file_bytes,
        content_type=_resolve_content_type(safe_filename, content_type),
    )

    # Parse
    parse_status, blocks, parsed_ms, parse_error = await _run_parse(gs_url)

    _store_document(
        doc_id,
        parse_result=_ParseResult(parse_status, blocks, parse_error, parsed_ms),
        version=version,
        **_store_kwargs,
    )
    # Private extensions (e.g. a searchable folder re-indexes the document).
    # Observational and non-blocking — never fails the upload.
    document_event("upserted", {"uid": user.uid, "doc_id": doc_id, "folder_id": effective_folder_id})

    # Update folder counts — skip delta for overwrites (doc already counted)
    if not is_overwrite:
        if parse_status == "parsed":
            try:
                folders_db.update_folder_counts(user.uid, effective_folder_id, doc_delta=1, parsed_delta=1)
            except Exception as exc:
                log.warning("Failed to update folder counts for %s: %s", effective_folder_id, exc)
        elif parse_status not in ("failed",):
            try:
                folders_db.update_folder_counts(user.uid, effective_folder_id, doc_delta=1)
            except Exception as exc:
                log.warning("Failed to update folder counts for %s: %s", effective_folder_id, exc)

    return _to_response(
        {
            "__id": doc_id,
            "parseStatus": parse_status,
            "originalFilename": safe_filename,
            "blockCount": len(blocks),
            "storagePath": storage_path,
            "folderId": effective_folder_id,
        }
    )
