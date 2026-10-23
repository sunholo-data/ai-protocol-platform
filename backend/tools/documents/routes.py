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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

import db.folders as folders_db
from auth import User, get_current_user
from db.clients import resolve_documents_bucket
from db.firestore import delete_document as _delete_firestore_doc
from db.firestore import get_document as _get_firestore_doc
from db.firestore import set_document as _set_firestore_doc
from extensions import document_event
from tools.documents.ingest import require_owned_folder
from tools.documents.recent import recent_documents_for_user

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
    # The UI badges system folders (e.g. Created) and sorts by creation. Clients
    # that list folders through this route (no Firebase session in the browser:
    # tailnet / local deployments) need them as much as the Firestore listener.
    system: bool = False
    createdAt: str | None = None
    # Set when the folder is searchable (a knowledge base is fed by it). Only
    # deployments with the knowledge-bases extension ever write it.
    knowledgeBaseId: str | None = None

    @field_validator("createdAt", mode="before")
    @classmethod
    def _iso(cls, v: object) -> str | None:
        if v is None:
            return None
        return v.isoformat() if hasattr(v, "isoformat") else str(v)


class _FoldersListResponse(BaseModel):
    folders: list[_FolderResponse]


class _DocumentsListResponse(BaseModel):
    documents: list[dict]


@router.post("/api/folders", status_code=201)
def create_folder(body: _CreateFolderRequest, user: _CurrentUser) -> _FolderResponse:
    result = folders_db.create_folder(user_id=user.uid, name=body.name)
    return _FolderResponse(**result)


class _RenameFolderRequest(BaseModel):
    name: str


@router.patch("/api/folders/{folder_id}")
def rename_folder(folder_id: str, body: _RenameFolderRequest, user: _CurrentUser) -> _FolderResponse:
    folder = folders_db.get_folder(user_id=user.uid, folder_id=folder_id)
    if folder is None or folder.get("userId") not in (None, user.uid):
        raise HTTPException(status_code=404, detail="Folder not found")
    name = (body.name or "").strip()
    if not name or len(name) > 200:
        raise HTTPException(status_code=400, detail="A folder name must be 1-200 characters.")
    other = folders_db.find_folder_by_name(user.uid, name)
    if other is not None and other.get("id") != folder_id:
        raise HTTPException(
            status_code=409, detail={"code": "FOLDER_EXISTS", "message": f"You already have a folder called '{name}'."}
        )
    folders_db.rename_folder(user.uid, folder_id, name)
    return _FolderResponse(**{**folder, "id": folder_id, "name": name, "userId": user.uid})


@router.delete("/api/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: str, user: _CurrentUser) -> None:
    """Delete an EMPTY folder (Mark, 2026-10-02: empty folders only — a folder
    with documents is refused, never cascaded)."""
    folder = folders_db.get_folder(user_id=user.uid, folder_id=folder_id)
    if folder is None or folder.get("userId") not in (None, user.uid):
        raise HTTPException(status_code=404, detail="Folder not found")
    count = folders_db.count_folder_documents(user.uid, folder_id)
    if count:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FOLDER_NOT_EMPTY",
                "message": f"'{folder.get('name')}' still has {count} document{'s' if count != 1 else ''}. "
                "Move or delete them first.",
                "count": count,
            },
        )
    folders_db.delete_folder(user.uid, folder_id)
    document_event("folder_deleted", {"uid": user.uid, "folder_id": folder_id})
    log.info("Deleted empty folder %s for %s", folder_id, user.uid)


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
    # A listing needs metadata, not content: parsed blocks made one folder of 11
    # documents a 2.2 MB response. GET /api/documents/{id} still returns them.
    for doc in docs:
        doc.pop("blocks", None)
    return _DocumentsListResponse(documents=docs)


_PARSED_DOCS_COLLECTION = "parsed_documents"


# NB: registered BEFORE `/api/documents/{doc_id}` so "recent" isn't captured as
# a document id.
@router.get("/api/documents/recent")
def list_recent_documents(user: _CurrentUser, limit: int = Query(default=12, ge=1, le=50)) -> _DocumentsListResponse:
    """The caller's own documents, most recently worked with first.

    Replaces a skill's shared demo documents in the workbench picker: what this
    user was last using, ranked by their latest conversation with it. Owner-only.
    """
    return _DocumentsListResponse(documents=recent_documents_for_user(user.uid, limit=limit))


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


# Content-Type by recorded source format. PDFs render inline in every major
# browser; the rest are downloads either way.
_CONTENT_TYPE_BY_FORMAT = {
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


def _fetch_owned_document_bytes(doc_id: str, user) -> tuple[bytes, str, str]:  # type: ignore[no-untyped-def]
    """Return ``(bytes, content_type, filename)`` for a document the caller owns.

    The shared half of ``/preview`` and ``/download`` — they differ only in
    Content-Disposition, so the ownership gate, the GCS fetch and the
    content-type resolution live here rather than in two places that can drift.

    Bytes are fetched server-side with the backend SA. **Never a signed or
    public URL**: a generated offer on ``storage.googleapis.com`` is exactly the
    leak CLAUDE.md's security rule exists to prevent (see also the per-client
    bucket isolation these documents rely on).

    Raises:
        HTTPException: 404 unknown document / no source, 403 not the owner,
            500 malformed record, 502 the object could not be read.
    """
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

    from google.cloud import storage

    try:
        data = storage.Client().bucket(bucket_name).blob(blob_name).download_as_bytes()
    except Exception as exc:
        log.error("document bytes: GCS download failed for %s: %s", source_url, exc)
        raise HTTPException(status_code=502, detail="Could not fetch document bytes") from exc

    source_format = (doc.get("sourceFormat") or "").lower().lstrip(".")
    content_type = _CONTENT_TYPE_BY_FORMAT.get(source_format, "application/octet-stream")
    filename = doc.get("originalFilename") or f"document.{source_format or 'bin'}"
    return data, content_type, filename


async def _load_version_bytes(doc_id: str, user, doc: dict, version: int) -> bytes:  # type: ignore[no-untyped-def]
    """Bytes of one stored version, from the caller's own per-client bucket.

    Versions are ADK artifacts living beside the document (see
    tools/documents/versions.py) — never a second store, and never a shared one.
    """
    from adk.agui import APP_NAME
    from db.clients import resolve_documents_bucket
    from tools.documents.versions import load_version

    try:
        bucket_name = resolve_documents_bucket(user)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="No version store for this account") from exc

    data = await load_version(
        bucket_name=bucket_name,
        app_name=APP_NAME,
        user_id=user.uid,
        doc_id=doc_id,
        filename=str(doc.get("originalFilename") or ""),
        version=version,
    )
    if data is None:
        raise HTTPException(status_code=404, detail=f"Version {version} is not available for this document")
    return data


@router.get("/api/documents/{doc_id}/download")
async def download_document(doc_id: str, user: _CurrentUser, version: int | None = None):
    """Stream a document as a file download (DOC-WORKBENCH P2).

    Same gate as ``/preview`` — the caller must own the record. The only
    difference is ``attachment`` rather than ``inline``, so the browser saves
    the file instead of rendering it. Users create documents to use offline;
    this is that path.

    The filename goes through ``_content_disposition`` (RFC 6266), so a
    generated document called ``Angebot - Groesse.docx`` with real umlauts does
    not 500 on the latin-1 header encoding.
    """
    from fastapi.responses import StreamingResponse

    data, content_type, filename = _fetch_owned_document_bytes(doc_id, user)

    if version is not None:
        # An earlier rendering. The ownership gate above has already run, so the
        # version fetch inherits it rather than re-deriving access.
        doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id) or {}
        data = await _load_version_bytes(doc_id, user, doc, version)
        filename = f"v{version} - {filename}"

    def _iter():
        yield data

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={
            "Content-Disposition": _content_disposition("attachment", filename),
            # A download is a deliberate act; never serve a stale copy.
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/api/documents/{doc_id}/preview")
def preview_document(doc_id: str, user: _CurrentUser, v: str = ""):
    """Stream the original document bytes for inline browser preview.

    Returns the binary content with the correct Content-Type so an ``<iframe>``
    can render PDFs natively. Shares its ownership gate and GCS fetch with
    ``/download`` (see ``_fetch_owned_document_bytes``) — the two differ only in
    Content-Disposition.

    Access: caller must own the record (``userId == request.auth.uid``), 403
    otherwise.

    Why a proxy instead of signed URLs: signed URLs require the backend SA to
    have ``iam.serviceAccountTokenCreator`` on itself, which adds infra surface —
    and a signed URL for customer-confidential content is the thing CLAUDE.md's
    security rule refuses.

    ``v`` is a caller-supplied freshness token (the document's ``parsedAt``),
    unused by the handler and present only to make the URL change when the bytes
    change. It is what earns the shared 5-minute cache below.

    Why that matters (bug found 2026-09-04): a document is re-ingested IN PLACE
    on every restyle — same doc-id, same URL — so with an unconditional
    ``max-age=300`` the browser answered the post-restyle refetch out of its own
    cache and redrew the OLD bytes. The restyle really had worked: theme,
    header, footer and page setup were all carried, and none of it reached the
    screen for five minutes. A feature that works and looks broken is a bug
    (NEVER-SILENT #8), so an un-versioned request now revalidates every time and
    only a versioned one is allowed to be cached.
    """
    from fastapi.responses import StreamingResponse

    data, content_type, filename = _fetch_owned_document_bytes(doc_id, user)

    def _iter():
        yield data

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={
            # Inline so the browser renders rather than downloads. RFC 6266
            # encoding — a raw non-latin-1 filename here 500s the response.
            "Content-Disposition": _content_disposition("inline", filename),
            # Versioned URL → safe to cache; bare URL → never serve a stale copy.
            "Cache-Control": ("private, max-age=300" if v.strip() else "private, no-cache, must-revalidate"),
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
    document_event("upserted", {"uid": user.uid, "doc_id": doc_id, "folder_id": doc.get("folderId")})
    log.info("Reparse complete for doc %s: status=%s blocks=%d", doc_id, status, len(blocks))
    return {"docId": doc_id, "parseStatus": status, "blockCount": len(blocks), "parseError": error}


def _owned_doc(doc_id: str, user: User) -> dict:
    doc = _get_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail=_ACCESS_DENIED)
    return doc


def _count_deltas(doc: dict, sign: int) -> tuple[int, int]:
    parsed = (doc.get("parseStatus") or doc.get("status")) == "parsed"
    return sign, sign if parsed else 0


def _adjust_counts(user: User, folder_id: str | None, doc: dict, sign: int) -> None:
    """Keep the folder's docCount/parsedCount in step. Best-effort: the
    counters are display hints; listings count the real documents."""
    if not folder_id:
        return
    doc_delta, parsed_delta = _count_deltas(doc, sign)
    try:
        folders_db.update_folder_counts(user.uid, folder_id, doc_delta=doc_delta, parsed_delta=parsed_delta)
    except Exception as exc:
        log.warning("folder count update failed for %s: %s", folder_id, exc)


def _delete_owned_document(doc_id: str, doc: dict, user: User) -> None:
    # Delete the stored file only when it is the caller's OWN copy: every file
    # the app writes for a user lives under users/{uid}/ (upload, generate,
    # channel attachment). An imported-by-reference record's storagePath is the
    # SOURCE object (a shared library file, possibly in this same bucket), and
    # removing the record from My Documents must never delete the original.
    # Best-effort: a missing object doesn't fail the request.
    storage_path: str | None = doc.get("storagePath")
    if storage_path and not storage_path.startswith(f"users/{user.uid}/"):
        log.info("Kept stored object %s: not the caller's own copy (a reference)", storage_path)
        storage_path = None
    if storage_path:
        try:
            from google.cloud import storage as gcs

            bucket_name = resolve_documents_bucket(user)
            gcs.Client().bucket(bucket_name).blob(storage_path).delete()
            log.info("Deleted GCS object gs://%s/%s", bucket_name, storage_path)
        except Exception as exc:
            log.warning("GCS delete failed for %s (continuing): %s", storage_path, exc)

    _delete_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id)
    # The counters used to be left as they were, so an emptied folder still
    # read "1 document" (seen live 2026-10-02).
    _adjust_counts(user, doc.get("folderId"), doc, -1)
    # Projects writer 3: chats that used this document lose the projects it
    # brought them (projects.md §5). Best-effort; never fails the delete.
    from db.projects import on_document_deleted

    on_document_deleted(user.uid, doc_id, list(doc.get("projectIds") or []))
    document_event("deleted", {"uid": user.uid, "doc_id": doc_id, "folder_id": doc.get("folderId")})
    log.info("Deleted document %s", doc_id)


@router.delete("/api/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user: _CurrentUser) -> None:
    """Delete a document: removes the Firestore record and the GCS file."""
    _delete_owned_document(doc_id, _owned_doc(doc_id, user), user)


class _UpdateDocumentRequest(BaseModel):
    """``PATCH /api/documents/{id}`` body: a move (``folder_id``) OR a rename
    (``filename``), one change per request."""

    folder_id: str | None = None
    # A document with the same filename already in the target: 409 unless the
    # caller confirms replacing it. Never "keep both" — versions are keyed on
    # (doc id, filename), so renaming on the way in would orphan them.
    replace: bool = False
    filename: str | None = None


# Same ceiling as a folder name (rename_folder).
_MAX_FILENAME_LEN = 200


def _clean_new_filename(raw: str, current: str) -> str:
    """Validate a user-typed display name and keep the document's extension.

    The extension is part of what the document IS (its format, its version-store
    key in ``versions.artifact_filename``, how it downloads), so a rename never
    changes it: when the typed name doesn't end in the current extension, that
    extension is appended (``Q3 summary`` -> ``Q3 summary.pdf``; ``notes.txt`` on
    a PDF -> ``notes.txt.pdf``). Friendlier than refusing a name the user simply
    typed without its ending.

    Raises:
        HTTPException: 422 with a message the UI shows as-is.
    """
    from pathlib import PurePosixPath

    name = (raw or "").strip()
    if not name.strip("."):
        raise HTTPException(status_code=422, detail="A document name can't be empty.")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=422, detail="A document name can't contain / or \\.")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise HTTPException(status_code=422, detail="A document name can't contain control characters.")
    ext = PurePosixPath(current or "").suffix
    if ext and PurePosixPath(name).suffix.lower() != ext.lower():
        name = f"{name}{ext}"
    if len(name) > _MAX_FILENAME_LEN:
        raise HTTPException(status_code=422, detail=f"A document name must be at most {_MAX_FILENAME_LEN} characters.")
    return name


def _rename_document(doc_id: str, doc: dict, raw_name: str, user: User) -> dict:
    """Change a document's display name (``originalFilename``).

    The bytes are never rewritten. The stored OBJECT is renamed (same-bucket
    copy + delete, as a move does) only when it sits at the folder-derived path
    an upload writes to (``users/{uid}/docs/{folder}/{filename}``): left there,
    a later upload of a file with the OLD name into the same folder finds no
    record of that name, mints a new document, and overwrites this one's bytes
    at the shared path. Objects anywhere else (imported by reference, channel
    attachments) are never written by an upload, so only the record changes.
    """
    current = str(doc.get("originalFilename") or "")
    name = _clean_new_filename(raw_name, current)
    if name == current:
        return {**doc, "id": doc_id}

    folder_id = doc.get("folderId")
    if folder_id:
        clash = [
            d
            for d in folders_db.list_folder_documents(user_id=user.uid, folder_id=folder_id)
            if d.get("originalFilename") == name and d.get("id") != doc_id
        ]
        if clash:
            folder = folders_db.get_folder(user_id=user.uid, folder_id=folder_id) or {}
            where = f"'{folder['name']}'" if folder.get("name") else "This folder"
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "FILENAME_EXISTS",
                    "message": f"{where} already has a document called '{name}'. Choose another name.",
                    "existingDocId": clash[0].get("id"),
                },
            )

    updates: dict = {"originalFilename": name, "updatedAt": datetime.now(UTC).isoformat()}
    old_path = doc.get("storagePath") or ""
    if folder_id and old_path == f"users/{user.uid}/docs/{folder_id}/{current.replace('/', '_')}":
        new_path = f"users/{user.uid}/docs/{folder_id}/{name}"
        bucket_name = resolve_documents_bucket(user)
        try:
            from google.cloud import storage as gcs

            bucket = gcs.Client().bucket(bucket_name)
            blob = bucket.blob(old_path)
            bucket.copy_blob(blob, bucket, new_path)
            blob.delete()
        except Exception as exc:
            log.error("rename of %s to %s failed: %s", old_path, new_path, exc)
            raise HTTPException(status_code=500, detail=f"Could not rename the stored file: {exc}") from exc
        updates["storagePath"] = new_path
        updates["sourceUrl"] = f"gs://{bucket_name}/{new_path}"

    from db.firestore import update_document as _update_firestore_doc

    _update_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id, updates)
    # A searchable folder re-indexes it, so search results carry the new name.
    document_event("upserted", {"uid": user.uid, "doc_id": doc_id, "folder_id": folder_id})
    log.info("Renamed document %s", doc_id)
    return {**doc, **updates, "id": doc_id}


@router.patch("/api/documents/{doc_id}")
async def update_document(doc_id: str, body: _UpdateDocumentRequest, user: _CurrentUser) -> dict:
    """Move a document to another folder (``folder_id``) or rename it
    (``filename``). Owner only: 404 unknown document, 403 someone else's."""
    if (body.filename is None) == (body.folder_id is None):
        raise HTTPException(status_code=422, detail="Send either folder_id (move) or filename (rename).")
    doc = _owned_doc(doc_id, user)
    if body.filename is not None:
        return _rename_document(doc_id, doc, body.filename, user)
    return _move_document(doc_id, doc, str(body.folder_id), body.replace, user)


def _move_document(doc_id: str, doc: dict, folder_id: str, replace: bool, user: User) -> dict:
    """Move a document to another of the caller's folders.

    The stored object moves too: its path embeds the folder, so leaving it
    behind would let a later same-name upload into the OLD folder overwrite the
    moved document's bytes.
    """
    target = require_owned_folder(user.uid, folder_id)
    source_folder = doc.get("folderId")
    if source_folder == folder_id:
        doc.setdefault("id", doc_id)
        return doc

    filename = doc.get("originalFilename") or ""
    clash = [
        d
        for d in folders_db.list_folder_documents(user_id=user.uid, folder_id=folder_id)
        if d.get("originalFilename") == filename and d.get("id") != doc_id
    ]
    if clash and not replace:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILENAME_EXISTS",
                "message": f"'{target.get('name')}' already has a document called '{filename}'. Replace it?",
                "existingDocId": clash[0].get("id"),
            },
        )
    for other in clash:
        _delete_owned_document(other["id"], other, user)

    old_path = doc.get("storagePath") or ""
    new_path = f"users/{user.uid}/docs/{folder_id}/{filename.replace('/', '_')}"
    bucket_name = resolve_documents_bucket(user)
    if old_path and old_path != new_path:
        try:
            from google.cloud import storage as gcs

            bucket = gcs.Client().bucket(bucket_name)
            blob = bucket.blob(old_path)
            bucket.copy_blob(blob, bucket, new_path)
            blob.delete()
        except Exception as exc:
            log.error("move of %s to %s failed: %s", old_path, new_path, exc)
            raise HTTPException(status_code=500, detail=f"Could not move the stored file: {exc}") from exc

    now = datetime.now(UTC).isoformat()
    updates = {
        "folderId": folder_id,
        "storagePath": new_path,
        "sourceUrl": f"gs://{bucket_name}/{new_path}",
        "updatedAt": now,
    }
    from db.firestore import update_document as _update_firestore_doc

    _update_firestore_doc(_PARSED_DOCS_COLLECTION, doc_id, updates)
    _adjust_counts(user, source_folder, doc, -1)
    _adjust_counts(user, folder_id, doc, +1)
    document_event(
        "moved", {"uid": user.uid, "doc_id": doc_id, "from_folder_id": source_folder, "folder_id": folder_id}
    )
    log.info("Moved document %s from folder %s to %s", doc_id, source_folder, folder_id)
    return {**doc, **updates, "id": doc_id}
