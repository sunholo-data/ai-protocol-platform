"""Document upload route — POST /api/documents/upload.

A thin HTTP wrapper over `ingest_document()` (see
[ingest.py](ingest.py)). Everything that makes an upload *correct* — per-client
bucket resolution, the fail-closed 403, dedup, write ordering, folder counters —
lives there, so the generation path gets it for free rather than
re-implementing it. See docs/design/v6.32.0/document-creation.md (M1).

The names re-exported below are kept importable from this module because
`admin/prewarm_routes.py` and `tools/documents/routes.py` already import them
from here.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile

from auth import User, get_current_user
from tools.documents.ingest import (
    _COLLECTION,
    ParsedDocumentResponse,
    _is_plain_text,
    _parse_plain_text,
    _ParseResult,
    _run_parse,
    _store_document,
    _to_response,
    ingest_document,
    require_owned_folder,
)

__all__ = [
    "_COLLECTION",
    "ParsedDocumentResponse",
    "_ParseResult",
    "_is_plain_text",
    "_parse_plain_text",
    "_run_parse",
    "_store_document",
    "_to_response",
    "router",
    "upload_document",
]

_CurrentUser = Annotated[User, Depends(get_current_user)]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    user: _CurrentUser,
    file: UploadFile,
    skill_id: Annotated[str, Form()] = "",
    folder_id: Annotated[str, Form()] = "",
    folder_path: Annotated[str, Form()] = "",
    skip_unchanged: Annotated[bool, Form()] = False,
) -> ParsedDocumentResponse:
    """Upload a document, parse it with AILANG Parse, store in Firestore.

    Uses the per-client GCS bucket resolved from the user's email domain.
    Documents are stored at users/{uid}/docs/{folderId}/{filename} within
    the client bucket — cross-user and cross-domain isolation at the path level.

    `folder_id` / `skill_id` are FORM fields, and must stay that way. A bare
    `str = ""` next to an `UploadFile` is a QUERY parameter to FastAPI, so the
    multipart field both real clients send — `UploadDropZone.tsx` does
    `formData.append("folder_id", …)`, the CLI passes `data={"folder_id": …}` —
    was silently dropped and every upload landed in the default folder. The
    picked folder was ignored with no error, which is how "I uploaded it to
    Contracts and it isn't there" happened. No caller sends them as query
    params (checked 2026-09-16), so nothing depended on the old shape.

    Folder upload sends ``folder_path`` (the file's directory relative to the
    chosen folder, e.g. ``Reports/2026``) instead of ``folder_id``, and
    ``skip_unchanged=true`` so a re-upload only processes changed files.
    """
    if folder_id.strip():
        # A caller-supplied id must name one of the caller's folders — an unknown
        # id used to be stored verbatim, orphaning the document (NEVER-SILENT #8).
        require_owned_folder(user.uid, folder_id.strip())
    return await ingest_document(
        user=user,
        file_bytes=await file.read(),
        filename=file.filename or "",
        content_type=file.content_type,
        folder_id=folder_id,
        skill_id=skill_id,
        source="uploaded",
        folder_path=folder_path,
        skip_unchanged=skip_unchanged,
    )
