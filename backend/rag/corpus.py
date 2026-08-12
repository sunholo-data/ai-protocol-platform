"""Per-user Vertex AI RAG Engine corpus management.

Each user gets one persistent RAG corpus. The corpus resource name is stored
in Firestore at ``user_profiles/{user_id}`` under ``ragCorpusName``.

All public functions are async. Synchronous vertexai.rag SDK calls are
dispatched via ``asyncio.to_thread`` so the FastAPI event loop is not blocked.

Only active when ``RAG_DOCUMENTS_ENABLED=true`` (default false). The corpus
module itself has no guard — the caller (callbacks.py, rag_tool.py) is
responsible for checking the flag before calling these functions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_USER_PROFILES_COLLECTION = "user_profiles"
_CORPUS_NAME_FIELD = "ragCorpusName"
_DISPLAY_NAME_PREFIX = "aitana-user-"

_vertexai_initialized = False


def _ensure_vertexai() -> None:
    global _vertexai_initialized
    if _vertexai_initialized:
        return
    import vertexai

    from config.gcp import resolve_gcp_project

    project = resolve_gcp_project() or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west1")
    vertexai.init(project=project, location=location)
    _vertexai_initialized = True


# ---------------------------------------------------------------------------
# Corpus lifecycle
# ---------------------------------------------------------------------------


async def get_or_create_user_corpus(user_id: str) -> str:
    """Return the user's RAG corpus resource name, creating it if absent.

    Reads ``ragCorpusName`` from ``user_profiles/{user_id}`` in Firestore.
    On a miss, creates a new corpus and writes the name back before returning.
    """
    from db.firestore import get_document, set_document

    profile = get_document(_USER_PROFILES_COLLECTION, user_id) or {}
    corpus_name: str | None = profile.get(_CORPUS_NAME_FIELD)
    if corpus_name:
        return corpus_name

    corpus_name = await asyncio.to_thread(_create_corpus_sync, user_id)
    # merge=True: safe whether or not the profile doc already exists
    set_document(_USER_PROFILES_COLLECTION, user_id, {_CORPUS_NAME_FIELD: corpus_name}, merge=True)
    logger.info("rag: created corpus for user %s → %s", user_id, corpus_name)
    return corpus_name


def _create_corpus_sync(user_id: str) -> str:
    from vertexai import rag

    _ensure_vertexai()
    corpus = rag.create_corpus(display_name=f"{_DISPLAY_NAME_PREFIX}{user_id}")
    return corpus.name


# ---------------------------------------------------------------------------
# Document operations
# ---------------------------------------------------------------------------


async def upload_document(corpus_name: str, path: str, display_name: str) -> str:
    """Upload a document to the corpus; return the RagFile resource name.

    ``path`` may be a GCS URI (``gs://…``) or a local filesystem path.
    """
    rag_file = await asyncio.to_thread(_upload_file_sync, corpus_name, path, display_name)
    return rag_file.name


def _upload_file_sync(corpus_name: str, path: str, display_name: str) -> Any:
    from vertexai import rag

    _ensure_vertexai()
    return rag.upload_file(corpus_name=corpus_name, path=path, display_name=display_name)


async def search_corpus(corpus_name: str, query: str, top_k: int = 5) -> list[dict]:
    """Retrieve top-K chunks relevant to ``query``.

    Returns a list of dicts with keys ``text``, ``source_file``, ``score``.
    Empty list if the corpus has no relevant content.
    """
    return await asyncio.to_thread(_retrieval_query_sync, corpus_name, query, top_k)


def _retrieval_query_sync(corpus_name: str, query: str, top_k: int) -> list[dict]:
    from vertexai import rag

    _ensure_vertexai()
    response = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
    )
    results: list[dict] = []
    for ctx in response.contexts.contexts:
        results.append(
            {
                "text": ctx.text,
                "source_file": ctx.source_display_name or ctx.source_uri or "",
                "score": float(ctx.score),
            }
        )
    return results


async def import_document_from_gcs(corpus_name: str, gcs_uri: str) -> None:
    """Import a GCS document into the corpus via the batch import path.

    Use this (not ``upload_document``) when the source file already lives in GCS.
    ``import_files`` is the Vertex-native path for GCS URIs and avoids a
    download-then-re-upload round-trip.
    """
    await asyncio.to_thread(_import_files_sync, corpus_name, gcs_uri)


def _import_files_sync(corpus_name: str, gcs_uri: str) -> None:
    from vertexai import rag

    _ensure_vertexai()
    rag.import_files(corpus_name=corpus_name, paths=[gcs_uri])


async def delete_document(file_name: str) -> None:
    """Delete a file from the corpus by its full resource name."""
    await asyncio.to_thread(_delete_file_sync, file_name)


def _delete_file_sync(file_name: str) -> None:
    from vertexai import rag

    _ensure_vertexai()
    rag.delete_file(name=file_name)


async def list_user_documents(corpus_name: str) -> list[Any]:
    """Return all RagFile objects in the user's corpus."""
    return await asyncio.to_thread(_list_files_sync, corpus_name)


def _list_files_sync(corpus_name: str) -> list[Any]:
    from vertexai import rag

    _ensure_vertexai()
    return list(rag.list_files(corpus_name=corpus_name))
