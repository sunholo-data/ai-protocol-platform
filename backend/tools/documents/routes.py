"""FastAPI routes for user document folders — /api/folders and /api/sessions context.

User-facing document organization layer. Distinct from the storage-ACL
bucket/folder system in backend/buckets/ which manages GCS namespace config.

These folders group user uploads within their per-client GCS bucket, keyed
by the user's email domain (see db/clients.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db.folders as folders_db
from auth import User, get_current_user
from db.clients import resolve_documents_bucket
from db.firestore import delete_document as _delete_firestore_doc
from db.firestore import get_document as _get_firestore_doc
from db.firestore import set_document as _set_firestore_doc

log = logging.getLogger(__name__)

router = APIRouter(tags=["doc-folders"])

_CurrentUser = Annotated[User, Depends(get_current_user)]
_ACCESS_DENIED = "Access denied"


def _content_disposition(disposition: str, filename: str) -> str:
    """Build an RFC 6266 ``Content-Disposition`` value that is safe for ANY
    filename.

    HTTP header values are serialised as latin-1 by Starlette/uvicorn, so a raw
    ``filename="..."`` containing a non-latin-1 character (an en/em dash
    U+2013/U+2014, a curly quote, accented or CJK text) raises
    ``UnicodeEncodeError`` when the response is sent, surfacing as an opaque
    **500**. (This bit a real upload whose name held an en-dash between the trip
    dates; the preview route 500'd on it.)

    We emit an ASCII-only ``filename=`` fallback plus an RFC 5987
    ``filename*=UTF-8''`` param carrying the true name percent-encoded, so modern
    browsers show the correct name and the header always encodes.
    """
    from urllib.parse import quote

    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii").strip()
    # Neutralise quoted-string delimiters in the fallback; default to a name.
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_") or "document"
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


class _CreateFolderRequest(BaseModel):
    name: str


class _FolderResponse(BaseModel):
    id: str
    name: str
    userId: str
    docCount: int = 0
    parsedCount: int = 0


class _FoldersListResponse(BaseModel):
    folders: list[_FolderResponse]


class _DocumentsListResponse(BaseModel):
    documents: list[dict]


@router.post("/api/folders", status_code=201)
def create_folder(body: _CreateFolderRequest, user: _CurrentUser) -> _FolderResponse:
    result = folders_db.create_folder(user_id=user.uid, name=body.name)
    return _FolderResponse(**result)


@router.get("/api/folders")
def list_folders(user: _CurrentUser) -> _FoldersListResponse:
    items = folders_db.list_folders(user_id=user.uid)
    return _FoldersListResponse(folders=[_FolderResponse(**f) for f in items])


@router.get("/api/folders/{folder_id}/documents")
def list_folder_documents(folder_id: str, user: _CurrentUser) -> _DocumentsListResponse:
    folder = folders_db.get_folder(user_id=user.uid, folder_id=folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)
    docs = folders_db.list_folder_documents(user_id=user.uid, folder_id=folder_id)
    return _DocumentsListResponse(documents=docs)


_PARSED_DOCS_COLLECTION = "parsed_documents"


@router.get("/api/documents/{doc_id}")
def get_document(doc_id: str, user: _CurrentUser) -> dict:
    """Fetch a single parsed document by ID.

    Returns the full document record including blocks for frontend rendering.
    """
    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)
    doc.setdefault("id", doc_id)
    return doc


@router.get("/api/documents/{doc_id}/preview")
def preview_document(doc_id: str, user: _CurrentUser):
    """Stream the original document bytes for inline browser preview.

    Returns the binary content with the correct Content-Type so an
    ``<iframe>`` can render PDFs natively (and browsers can download or
    side-render any other supported format). The doc's source GCS URL is
    fetched server-side using the backend SA — the frontend never sees
    bucket credentials.

    Access: same as ``GET /api/documents/{doc_id}`` — caller must own the
    record (``userId == request.auth.uid``). Returns 403 otherwise.

    Why a proxy instead of signed URLs: signed URLs require the backend SA
    to have ``iam.serviceAccountTokenCreator`` on itself, which adds infra
    surface. For demo-scale traffic on the 5 ONE PPAs this proxy is fine;
    a signed-URL variant can ship later for high-throughput cases.
    """
    from fastapi.responses import StreamingResponse

    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)

    source_url = doc.get("sourceUrl") or ""
    if not source_url.startswith("gs://"):
        raise HTTPException(status_code=404, detail="No GCS source URL on this document")

    bucket_name, _, blob_name = source_url[len("gs://") :].partition("/")
    if not bucket_name or not blob_name:
        raise HTTPException(status_code=500, detail="Malformed sourceUrl on document")

    # Lazy import — keeps tests fast and avoids loading google.cloud.storage
    # at module load time.
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    try:
        data = blob.download_as_bytes()
    except Exception as exc:
        log.error("preview_document: GCS download failed for %s: %s", source_url, exc)
        raise HTTPException(status_code=502, detail="Could not fetch document bytes") from exc

    # Resolve Content-Type from the doc's recorded format (best signal) and
    # fall back to a generic application/octet-stream. PDFs render inline in
    # all major browsers when Content-Disposition is inline.
    source_format = (doc.get("sourceFormat") or "").lower().lstrip(".")
    content_type_map = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "html": "text/html",
        "htm": "text/html",
        "md": "text/markdown",
        "csv": "text/csv",
        "txt": "text/plain",
    }
    content_type = content_type_map.get(source_format, "application/octet-stream")

    filename = doc.get("originalFilename") or f"document.{source_format or 'bin'}"

    def _iter():
        yield data

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={
            # Inline so the browser renders rather than downloads. RFC 6266
            # encoding — a raw non-latin-1 filename here 500s the response.
            "Content-Disposition": _content_disposition("inline", filename),
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/api/documents/{doc_id}/thumbnail")
def thumbnail_document(doc_id: str, user: _CurrentUser, width: int = 600):
    """Render an imported document (PDF first page or image) to a PNG thumbnail.

    Standardised preview image for imported docs — the same renderer + cache the
    bucket thumbnail route uses, so document lists / hover previews look
    identical whether the doc is a bucket reference or an owned upload.

    Access: same as ``GET /api/documents/{doc_id}`` — caller must own the record
    (``userId == uid``), 403 otherwise. Bytes are rendered server-side and the
    PNG streamed through this authenticated route — never a public URL, so
    private-content thumbnails stay behind the user's Firebase bearer.
    """
    from fastapi.responses import Response

    from tools.documents.thumbnail import cache_get, cache_put, is_thumbnailable, render_thumbnail_png

    width = max(64, min(1600, int(width)))

    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)

    source_url = doc.get("sourceUrl") or ""
    source_format = doc.get("sourceFormat") or doc.get("originalFilename") or source_url
    if not source_url.startswith("gs://"):
        raise HTTPException(status_code=404, detail="No GCS source URL on this document")
    if not is_thumbnailable(source_format):
        raise HTTPException(status_code=415, detail="Thumbnails are only rendered for PDFs and images")

    cache_key = f"doc:{doc_id}:{width}"
    cached = cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    bucket_name, _, blob_name = source_url[len("gs://") :].partition("/")
    if not bucket_name or not blob_name:
        raise HTTPException(status_code=500, detail="Malformed sourceUrl on document")

    from google.cloud import storage

    try:
        data = storage.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    except Exception as exc:
        log.error("thumbnail_document: GCS download failed for %s: %s", source_url, exc)
        raise HTTPException(status_code=502, detail="Could not fetch document bytes") from exc

    try:
        png = render_thumbnail_png(data, source_format, target_width=width)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Could not render thumbnail: {e}") from e

    cache_put(cache_key, png)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/documents/{doc_id}/reparse")
async def reparse_document(doc_id: str, user: _CurrentUser) -> dict:
    """Re-run AILANG Parse on an existing document using its stored GCS URL.

    Use this to populate blocks for documents uploaded before the AI pipeline
    was wired, or to retry a failed parse after a transient AILANG error.
    Returns the updated parseStatus and blockCount.
    """
    from tools.documents.upload import _run_parse

    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)

    gs_url: str | None = doc.get("sourceUrl")
    if not gs_url:
        raise HTTPException(status_code=422, detail="Document has no GCS source URL — cannot reparse")

    log.info("Reparsing doc %s from %s", doc_id, gs_url)
    status, blocks, elapsed_ms, error = await _run_parse(gs_url)

    now = datetime.now(UTC)
    update: dict = {
        "parseStatus": status,
        "status": status,
        "blocks": blocks if status == "parsed" else [],
        "blockCount": len(blocks) if status == "parsed" else None,
        "tableCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "table") if blocks else None,
        "imageCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "image") if blocks else None,
        "changeCount": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "change") if blocks else None,
        "parsedMs": elapsed_ms if status == "parsed" else None,
        "parseError": error if status == "failed" else None,
        "parsedAt": now.isoformat() if status == "parsed" else None,
        "updatedAt": now.isoformat(),
    }
    _set_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id, update, merge=True)
    log.info("Reparse complete for doc %s: status=%s blocks=%d", doc_id, status, len(blocks))
    return {"docId": doc_id, "parseStatus": status, "blockCount": len(blocks), "parseError": error}


@router.delete("/api/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user: _CurrentUser) -> None:
    """Delete a document: removes the Firestore record and the GCS file."""
    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)

    # Delete GCS file — best-effort, don't fail the whole request if it's missing
    storage_path: str | None = doc.get("storagePath")
    if storage_path:
        try:
            from google.cloud import storage as gcs

            bucket_name = resolve_documents_bucket(user)
            gcs.Client().bucket(bucket_name).blob(storage_path).delete()
            log.info("Deleted GCS object gs://%s/%s", bucket_name, storage_path)
        except Exception as exc:
            log.warning("GCS delete failed for %s (continuing): %s", storage_path, exc)

    _delete_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    log.info("Deleted document %s", doc_id)
