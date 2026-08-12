"""Document upload handler — POST /api/documents/upload.

Flow:
  1. Resolve per-client GCS bucket from user's email domain
  2. Write Firestore record with parseStatus: pending
  3. Upload file to GCS: users/{uid}/docs/{folderId}/{filename}
  4. AILANG Parse: blocks + a2ui formats
  5. Update Firestore with parseStatus: parsed|failed + stats
  6. Return ParsedDocumentResponse
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field

import db.folders as folders_db
from auth import User, get_current_user
from db.clients import UnmappedTenantError, resolve_documents_bucket
from db.firestore import query_documents, set_document

_CurrentUser = Annotated[User, Depends(get_current_user)]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

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
    )


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
        "blocks": blocks if pr.status == "parsed" else [],
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
    }
    set_document(_COLLECTION, doc_id, doc)
    log.info("Stored ParsedDocument %s (parseStatus=%s, blocks=%d)", doc_id, pr.status, len(blocks))


@router.post("/upload")
async def upload_document(
    user: _CurrentUser,
    file: UploadFile,
    skill_id: str = "",
    folder_id: str = "",
) -> ParsedDocumentResponse:
    """Upload a document, parse it with AILANG Parse, store in Firestore.

    Uses the per-client GCS bucket resolved from the user's email domain.
    Documents are stored at users/{uid}/docs/{folderId}/{filename} within
    the client bucket — cross-user and cross-domain isolation at the path level.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="File must have a name.")

    ext = PurePosixPath(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {ext!r} is not supported. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    # Resolve destination folder (auto-create if not provided)
    effective_folder_id = folder_id.strip() or folders_db.ensure_default_folder(user.uid)

    # Resolve per-client GCS bucket. When the deployment fails closed and the
    # user's domain has no mapped bucket, refuse rather than write into the
    # shared deployment-wide bucket — surfaced as a visible 403 (NEVER-SILENT).
    try:
        bucket_name = resolve_documents_bucket(user)
    except UnmappedTenantError as exc:
        # Structured detail so the frontend can render a clear, specific "why"
        # (NEVER-SILENT #8) instead of a generic "Upload failed (403)". `code`
        # lets the UI branch; `message` is user-facing copy.
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TENANT_NOT_PROVISIONED",
                "message": (
                    "Document upload isn't available for your account. This feature is only "
                    "enabled for organizations that have been set up by an administrator — the "
                    "account you're signed in with isn't mapped to one. If your organization uses "
                    "Aitana, sign in with your work account, or ask an administrator to enable "
                    "document storage for your domain."
                ),
            },
        ) from exc

    safe_filename = file.filename.replace("/", "_").replace("\\", "_")

    # Deduplication: reuse existing doc_id if same filename already exists in
    # this folder. GCS overwrites the file at the same path; we update the
    # existing Firestore record in-place instead of creating a second entry.
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
    doc_id = existing[0]["__id"] if is_overwrite else str(uuid.uuid4())
    if is_overwrite:
        log.info("Re-upload detected for %s — reusing doc_id %s", safe_filename, doc_id)
    storage_path = f"users/{user.uid}/docs/{effective_folder_id}/{safe_filename}"
    gs_url = f"gs://{bucket_name}/{storage_path}"
    now = datetime.now(UTC)

    # Write pending record immediately so the frontend can show the file
    _store_document(
        doc_id,
        user_id=user.uid,
        skill_id=skill_id,
        gs_url=gs_url,
        storage_path=storage_path,
        original_filename=safe_filename,
        source_format=ext.lstrip("."),
        folder_id=effective_folder_id,
        parse_result=_ParseResult("pending", [], None, None),
        now=now,
    )

    # Upload to GCS
    file_bytes = await file.read()
    try:
        _upload_to_gcs(
            bucket_name,
            storage_path,
            file_bytes,
            _resolve_content_type(safe_filename, file.content_type),
            user.uid,
            safe_filename,
        )
        log.info("Uploaded %s to gs://%s/%s", safe_filename, bucket_name, storage_path)
    except Exception as exc:
        log.error("GCS upload failed for %s: %s", safe_filename, exc)
        _store_document(
            doc_id,
            user_id=user.uid,
            skill_id=skill_id,
            gs_url=gs_url,
            storage_path=storage_path,
            original_filename=safe_filename,
            source_format=ext.lstrip("."),
            folder_id=effective_folder_id,
            parse_result=_ParseResult("failed", [], str(exc), None),
            now=now,
        )
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {exc}") from exc

    # Parse
    parse_status, blocks, parsed_ms, parse_error = await _run_parse(gs_url)

    _store_document(
        doc_id,
        user_id=user.uid,
        skill_id=skill_id,
        gs_url=gs_url,
        storage_path=storage_path,
        original_filename=safe_filename,
        source_format=ext.lstrip("."),
        folder_id=effective_folder_id,
        parse_result=_ParseResult(parse_status, blocks, parse_error, parsed_ms),
        now=now,
    )

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
