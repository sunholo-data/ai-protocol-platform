"""Integration tests for the Vertex AI RAG corpus module.

Requires:
  - GCP credentials (GOOGLE_APPLICATION_CREDENTIALS or gcloud auth)
  - GOOGLE_CLOUD_PROJECT env var
  - RAG_DOCUMENTS_ENABLED=true
  - Vertex AI RAG API enabled in the project

Run with:
    RAG_DOCUMENTS_ENABLED=true pytest tests/integration/test_rag_corpus.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_ENABLED = os.environ.get("RAG_DOCUMENTS_ENABLED", "").lower() in ("1", "true")


@pytest.mark.skipif(not _ENABLED, reason="RAG_DOCUMENTS_ENABLED not set")
@pytest.mark.asyncio
async def test_get_or_create_user_corpus_creates_and_caches() -> None:
    """get_or_create_user_corpus creates a corpus on first call, returns same name on second."""
    from rag.corpus import get_or_create_user_corpus

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    corpus_name_1 = await get_or_create_user_corpus(user_id)
    assert corpus_name_1.startswith("projects/")

    corpus_name_2 = await get_or_create_user_corpus(user_id)
    assert corpus_name_1 == corpus_name_2, "Second call should return the cached corpus name"


@pytest.mark.skipif(not _ENABLED, reason="RAG_DOCUMENTS_ENABLED not set")
@pytest.mark.asyncio
async def test_list_user_documents_returns_list() -> None:
    """list_user_documents returns a list (possibly empty) for a valid corpus."""
    from rag.corpus import get_or_create_user_corpus, list_user_documents

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    corpus_name = await get_or_create_user_corpus(user_id)
    files = await list_user_documents(corpus_name)
    assert isinstance(files, list)


@pytest.mark.skipif(not _ENABLED, reason="RAG_DOCUMENTS_ENABLED not set")
@pytest.mark.asyncio
async def test_search_corpus_returns_list() -> None:
    """search_corpus returns a list of dicts with expected keys."""
    from rag.corpus import get_or_create_user_corpus, search_corpus

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    corpus_name = await get_or_create_user_corpus(user_id)
    results = await search_corpus(corpus_name, "test query", top_k=3)
    assert isinstance(results, list)
    for r in results:
        assert "text" in r
        assert "source_file" in r
        assert "score" in r
