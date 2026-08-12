"""RAG corpus management routes — /api/documents/corpus.

Provides list, delete, and ad-hoc search over the user's per-user
Vertex AI RAG corpus. Only meaningful when RAG_DOCUMENTS_ENABLED=true;
routes are registered unconditionally so the API surface is stable.

Endpoints:
    GET  /api/documents/corpus/files       — list all files in the user's corpus
    DELETE /api/documents/corpus/files     — delete a file by resource name
    POST /api/documents/corpus/search      — ad-hoc retrieval query (dev/debug)
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from adk.callbacks import _RAG_DOCUMENTS_ENABLED
from auth import User, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents/corpus", tags=["rag-corpus"])

_CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class _RagFileResponse(BaseModel):
    name: str
    display_name: str


class _FileListResponse(BaseModel):
    corpus_name: str
    files: list[_RagFileResponse]


class _SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class _SearchChunk(BaseModel):
    text: str
    source_file: str
    score: float


class _SearchResponse(BaseModel):
    query: str
    results: list[_SearchChunk]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/files", response_model=_FileListResponse)
async def list_corpus_files(user: _CurrentUser) -> _FileListResponse:
    """List all files in the user's RAG corpus."""
    if not _RAG_DOCUMENTS_ENABLED:
        raise HTTPException(status_code=404, detail="RAG_DOCUMENTS_ENABLED is not set")

    from rag.corpus import get_or_create_user_corpus, list_user_documents

    try:
        corpus_name = await get_or_create_user_corpus(user.uid)
        files = await list_user_documents(corpus_name)
    except Exception as exc:
        log.warning("list_corpus_files: failed for user %s: %s", user.uid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _FileListResponse(
        corpus_name=corpus_name,
        files=[
            _RagFileResponse(
                name=f.name,
                display_name=getattr(f, "display_name", "") or "",
            )
            for f in files
        ],
    )


@router.delete("/files")
async def delete_corpus_file(file_name: str, user: _CurrentUser) -> dict:
    """Delete a file from the user's RAG corpus by its resource name."""
    if not _RAG_DOCUMENTS_ENABLED:
        raise HTTPException(status_code=404, detail="RAG_DOCUMENTS_ENABLED is not set")

    from rag.corpus import delete_document, get_or_create_user_corpus

    corpus_name = await get_or_create_user_corpus(user.uid)
    if not file_name.startswith(corpus_name):
        raise HTTPException(
            status_code=403,
            detail="File does not belong to your corpus",
        )

    try:
        await delete_document(file_name)
    except Exception as exc:
        log.warning("delete_corpus_file: failed for %s: %s", file_name, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"deleted": file_name}


@router.post("/search", response_model=_SearchResponse)
async def search_corpus_files(body: _SearchRequest, user: _CurrentUser) -> _SearchResponse:
    """Ad-hoc retrieval query against the user's RAG corpus (dev/debug)."""
    if not _RAG_DOCUMENTS_ENABLED:
        raise HTTPException(status_code=404, detail="RAG_DOCUMENTS_ENABLED is not set")

    from rag.corpus import get_or_create_user_corpus, search_corpus

    try:
        corpus_name = await get_or_create_user_corpus(user.uid)
        chunks = await search_corpus(corpus_name, body.query, top_k=body.top_k)
    except Exception as exc:
        log.warning("search_corpus_files: failed for user %s: %s", user.uid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _SearchResponse(
        query=body.query,
        results=[
            _SearchChunk(
                text=c["text"],
                source_file=c["source_file"],
                score=c["score"],
            )
            for c in chunks
        ],
    )
