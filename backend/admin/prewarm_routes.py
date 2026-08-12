"""Admin route for L4 sentinel pre-warm.

POST /api/admin/documents/prewarm-from-blocks

Accepts pre-parsed AILANG Block ADT blocks (produced locally by
``docparse`` CLI), writes a parsed_documents record owned by
``PLATFORM_OWNER_UID``, keyed by ``sourceUrl``. The
``import-by-reference`` route's L4 cascade then clones from this
record on first user click, so every viewer pays ~500ms (Firestore
clone) instead of ~5s+ (AILANG API round-trip) or hits the AILANG
"no pages" bug on multi-page PDFs.

This endpoint deliberately accepts pre-parsed blocks instead of
triggering its own parse — it's the demo-readiness escape hatch when
AILANG can't handle a specific document. Once AILANG ships a fix, the
runtime path (``import-by-reference``'s L3 fresh parse) becomes the
default again; the L4 cache just makes things faster.

Auth: ``_assert_caller_is_service_account`` (same Google ID token +
SA-email allowlist pattern as seed-platform-skills).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from admin.auth import _assert_caller_is_service_account
from db.firestore import query_documents
from skills.platform import PLATFORM_OWNER_UID
from tools.documents.upload import _COLLECTION, _ParseResult, _store_document

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/documents", tags=["admin"])


class PrewarmRequest(BaseModel):
    bucket: str
    object: str
    skill_id: str = Field(default="", alias="skillId")
    blocks: list[dict[str, Any]]
    original_filename: str = Field(alias="originalFilename")
    source_format: str = Field(default="pdf", alias="sourceFormat")

    model_config = {"populate_by_name": True}


class PrewarmResponse(BaseModel):
    doc_id: str = Field(alias="docId")
    source_url: str = Field(alias="sourceUrl")
    block_count: int = Field(alias="blockCount")
    owner_uid: str = Field(alias="ownerUid")

    model_config = {"populate_by_name": True}


@router.post("/prewarm-from-blocks", response_model=PrewarmResponse)
def prewarm_from_blocks(req: PrewarmRequest, request: Request) -> PrewarmResponse:
    """Write a sentinel-owned parsed_documents record from pre-parsed blocks.

    Idempotent: if a PLATFORM_OWNER_UID record already exists for this
    sourceUrl we OVERWRITE it (so re-running the pre-warm picks up
    block-extraction fixes), but the docId is reused.
    """
    _assert_caller_is_service_account(request)

    gs_url = f"gs://{req.bucket}/{req.object}"

    existing = query_documents(
        _COLLECTION,
        filters=[("userId", "==", PLATFORM_OWNER_UID), ("sourceUrl", "==", gs_url)],
        limit=1,
    )
    doc_id = existing[0]["__id"] if existing else str(uuid.uuid4())
    if existing:
        log.info("prewarm: overwriting existing sentinel record %s for %s", doc_id, gs_url)
    else:
        log.info("prewarm: creating new sentinel record %s for %s", doc_id, gs_url)

    _store_document(
        doc_id,
        user_id=PLATFORM_OWNER_UID,
        skill_id=req.skill_id,
        gs_url=gs_url,
        storage_path=req.object,
        original_filename=req.original_filename,
        source_format=req.source_format,
        folder_id=None,
        parse_result=_ParseResult("parsed", req.blocks, None, None),
        now=datetime.now(UTC),
    )

    return PrewarmResponse(
        docId=doc_id,
        sourceUrl=gs_url,
        blockCount=len(req.blocks),
        ownerUid=PLATFORM_OWNER_UID,
    )


# Wider safety net: reject pre-warm payloads above ~1 MB (Firestore doc
# limit). Frontend / CLI should error visibly so the operator can
# truncate / split rather than getting a Firestore write rejection at the
# end of a long upload.
_MAX_PAYLOAD_BYTES = 900_000


@router.post("/prewarm-from-blocks/precheck")
def prewarm_precheck(req: PrewarmRequest, request: Request) -> dict[str, Any]:
    """Dry-run for the prewarm route — validates payload size without writing."""
    _assert_caller_is_service_account(request)
    import json

    payload_bytes = len(json.dumps(req.blocks).encode("utf-8"))
    if payload_bytes > _MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Block payload is {payload_bytes} bytes; Firestore limit is ~1 MB. "
                "Truncate blocks or split into multiple records before writing."
            ),
        )
    return {"ok": True, "bytes": payload_bytes, "limit": _MAX_PAYLOAD_BYTES}
