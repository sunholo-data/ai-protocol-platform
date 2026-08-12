"""POST /api/documents/import-by-reference — DOC-IMPORT-REF M2.

Closes the v6.4.0 4.5 SKILL-ONBOARDING picker/browser UX gap. When a user
clicks an example PPA or a bucket-listed file, the frontend POSTs here
instead of firing a synthetic chat message. We reuse the existing
``_run_parse`` + ``_store_document`` pipeline from upload.py — skipping
the file→GCS upload step since the file already lives in GCS.

Cache cascade (see docs/design/v6.4.0/document-import-by-reference.md):

- L2  self-dedup: parsed_documents WHERE (userId=self, sourceUrl=gs_url).
      Hit → return the existing per-user record (~200ms, no parse).
- L4  sentinel-dedup: parsed_documents WHERE (userId=PLATFORM_OWNER_UID,
      sourceUrl=gs_url). Hit → clone the parsed blocks into a fresh
      per-user record (~300ms, no parse). Per-user record preserves
      editedBlocks isolation.
- L3  fresh AILANG Parse → _store_document.

Returns 422 with parseError detail when AILANG Parse fails.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import User, get_current_user
from db.firestore import query_documents
from skills.platform import PLATFORM_OWNER_UID

from .upload import (
    _COLLECTION,
    ParsedDocumentResponse,
    _ParseResult,
    _run_parse,
    _store_document,
    _to_response,
)

_CurrentUser = Annotated[User, Depends(get_current_user)]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


class ImportByReferenceRequest(BaseModel):
    bucket: str
    object: str
    skill_id: str = Field(default="", alias="skillId")

    model_config = {"populate_by_name": True}


@router.post("/import-by-reference")
async def import_by_reference(
    req: ImportByReferenceRequest,
    user: _CurrentUser,
) -> ParsedDocumentResponse:
    """Parse a GCS-resident document by reference. Same pipeline as /upload,
    minus the file→GCS step since the file already lives at
    ``gs://{bucket}/{object}``.
    """
    gs_url = f"gs://{req.bucket}/{req.object}"

    # L2 — self-dedup. Same user has already imported this gs://, AND the
    # cached record is healthy ("parsed" status)? Stale records left from
    # prior broken AILANG runs ("pending_ai_extraction" with empty blocks,
    # "failed", etc.) MUST fall through to L4/L3 so the user can recover.
    # Without this guard a stale L2 record keeps serving the same broken
    # spinner forever — bug surfaced 2026-06-11 with 3 sentinel PPAs stuck
    # in pending_ai_extraction from 10:01 UTC (pre-PDF-fix records).
    self_hits = query_documents(
        _COLLECTION,
        filters=[("userId", "==", user.uid), ("sourceUrl", "==", gs_url)],
        limit=1,
    )
    if self_hits and self_hits[0].get("parseStatus") == "parsed":
        log.info("import_by_reference: l2_hit user=%s gs=%s", user.uid, gs_url)
        return _to_response(self_hits[0])
    if self_hits:
        log.info(
            "import_by_reference: l2_skip_stale user=%s gs=%s status=%s — falling through",
            user.uid,
            gs_url,
            self_hits[0].get("parseStatus"),
        )

    # L4 — sentinel-dedup. Platform-pre-parsed this gs://? Clone into the
    # existing per-user record if one exists (overwriting any stale state),
    # else create a fresh one. editedBlocks / folder semantics stay isolated.
    existing_user_doc_id = self_hits[0]["__id"] if self_hits else None
    sentinel_hit = query_documents(
        _COLLECTION,
        filters=[("userId", "==", PLATFORM_OWNER_UID), ("sourceUrl", "==", gs_url)],
        limit=1,
    )
    if sentinel_hit:
        log.info("import_by_reference: l4_hit user=%s gs=%s", user.uid, gs_url)
        return _clone_sentinel_to_user(
            sentinel_hit[0],
            user,
            req.skill_id,
            req.object,
            gs_url,
            doc_id_override=existing_user_doc_id,
        )

    # L3 — fresh AILANG Parse + persist. If there's a stale per-user record
    # already, reuse its docId so we overwrite the stale state instead of
    # creating a second record for the same gs_url.
    log.info("import_by_reference: l3_fresh user=%s gs=%s", user.uid, gs_url)
    parse_status, blocks, parsed_ms, parse_error = await _run_parse(gs_url)
    if parse_status == "failed":
        raise HTTPException(status_code=422, detail=parse_error or "Parse failed")

    doc_id = existing_user_doc_id or str(uuid.uuid4())
    filename = PurePosixPath(req.object).name
    source_format = PurePosixPath(req.object).suffix.lstrip(".") or "unknown"
    _store_document(
        doc_id,
        user_id=user.uid,
        skill_id=req.skill_id,
        gs_url=gs_url,
        storage_path=req.object,
        original_filename=filename,
        source_format=source_format,
        folder_id=None,  # imported-by-reference docs don't belong to a user folder
        parse_result=_ParseResult(parse_status, blocks, parse_error, parsed_ms),
        now=datetime.now(UTC),
    )
    return _to_response(
        {
            "__id": doc_id,
            "parseStatus": parse_status,
            "originalFilename": filename,
            "blockCount": len(blocks),
            "storagePath": req.object,
            "folderId": None,
        }
    )


def _clone_sentinel_to_user(
    sentinel: dict,
    user: User,
    skill_id: str,
    object_path: str,
    gs_url: str,
    doc_id_override: str | None = None,
) -> ParsedDocumentResponse:
    """Materialise a per-user parsed_documents record from a sentinel record.

    Copies the parsed blocks verbatim — they're already valid AILANG Parse
    output. Per-user record gets a fresh doc_id (or ``doc_id_override`` if
    one is supplied — used to OVERWRITE a stale prior record for the same
    gs_url so users don't accumulate duplicates), the caller's uid, and an
    empty ``editedBlocks`` map so user edits don't bleed across the
    PLATFORM_OWNER_UID-owned source.
    """
    doc_id = doc_id_override or str(uuid.uuid4())
    filename = sentinel.get("originalFilename") or PurePosixPath(object_path).name
    source_format = sentinel.get("sourceFormat") or PurePosixPath(object_path).suffix.lstrip(".")
    blocks = sentinel.get("blocks") or []
    parsed_ms = sentinel.get("parsedMs")
    _store_document(
        doc_id,
        user_id=user.uid,
        skill_id=skill_id,
        gs_url=gs_url,
        storage_path=object_path,
        original_filename=filename,
        source_format=source_format,
        folder_id=None,
        parse_result=_ParseResult("parsed", blocks, None, parsed_ms),
        now=datetime.now(UTC),
    )
    return _to_response(
        {
            "__id": doc_id,
            "parseStatus": "parsed",
            "originalFilename": filename,
            "blockCount": len(blocks),
            "storagePath": object_path,
            "folderId": None,
        }
    )
