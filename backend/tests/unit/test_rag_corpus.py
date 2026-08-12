"""Unit tests for backend/rag/corpus.py.

All Vertex AI SDK calls and Firestore I/O are mocked — no GCP credentials needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rag_file(name: str = "projects/p/locations/l/ragCorpora/1/ragFiles/f") -> MagicMock:
    f = MagicMock()
    f.name = name
    f.display_name = "session-abc/doc.pdf"
    return f


def _make_corpus(name: str = "projects/p/locations/l/ragCorpora/1") -> MagicMock:
    c = MagicMock()
    c.name = name
    return c


def _make_context(text: str, source: str = "doc.pdf", score: float = 0.85) -> MagicMock:
    ctx = MagicMock()
    ctx.text = text
    ctx.source_display_name = source
    ctx.source_uri = ""
    ctx.score = score
    return ctx


def _make_retrieval_response(contexts: list) -> MagicMock:
    response = MagicMock()
    response.contexts.contexts = contexts
    return response


# ---------------------------------------------------------------------------
# get_or_create_user_corpus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_corpus():
    """When profile has ragCorpusName, return it without calling Vertex."""
    with (
        patch("db.firestore.get_document", return_value={"ragCorpusName": "projects/p/corpora/42"}),
        patch("db.firestore.set_document") as mock_update,
        patch("rag.corpus._create_corpus_sync") as mock_create,
    ):
        from rag.corpus import get_or_create_user_corpus

        result = await get_or_create_user_corpus("user-1")

    assert result == "projects/p/corpora/42"
    mock_create.assert_not_called()
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_creates_corpus_when_missing():
    """When profile has no corpus, create one and write the name to Firestore."""
    corpus_name = "projects/p/locations/l/ragCorpora/99"
    with (
        patch("db.firestore.get_document", return_value={}),
        patch("db.firestore.set_document") as mock_update,
        patch("rag.corpus._create_corpus_sync", return_value=corpus_name) as mock_create,
    ):
        from rag.corpus import get_or_create_user_corpus

        result = await get_or_create_user_corpus("user-2")

    assert result == corpus_name
    mock_create.assert_called_once_with("user-2")
    mock_update.assert_called_once_with("user_profiles", "user-2", {"ragCorpusName": corpus_name}, merge=True)


@pytest.mark.asyncio
async def test_get_or_create_handles_missing_profile():
    """None profile (first-time user) is treated the same as missing corpus."""
    corpus_name = "projects/p/locations/l/ragCorpora/7"
    with (
        patch("db.firestore.get_document", return_value=None),
        patch("db.firestore.update_document"),
        patch("rag.corpus._create_corpus_sync", return_value=corpus_name),
    ):
        from rag.corpus import get_or_create_user_corpus

        result = await get_or_create_user_corpus("user-new")

    assert result == corpus_name


# ---------------------------------------------------------------------------
# upload_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_document_returns_file_name():
    rag_file = _make_rag_file("projects/p/ragCorpora/1/ragFiles/f1")
    with patch("rag.corpus._upload_file_sync", return_value=rag_file):
        from rag.corpus import upload_document

        result = await upload_document("projects/p/ragCorpora/1", "gs://bucket/doc.pdf", "session-a/doc.pdf")

    assert result == "projects/p/ragCorpora/1/ragFiles/f1"


# ---------------------------------------------------------------------------
# search_corpus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_corpus_formats_results():
    contexts = [
        _make_context("Revenue was €2.8M", "Q1Report.pdf", 0.91),
        _make_context("EBITDA margin 12%", "Q1Report.pdf", 0.75),
    ]
    with patch(
        "rag.corpus._retrieval_query_sync",
        return_value=[
            {"text": ctx.text, "source_file": ctx.source_display_name, "score": float(ctx.score)} for ctx in contexts
        ],
    ):
        from rag.corpus import search_corpus

        results = await search_corpus("projects/p/ragCorpora/1", "revenue")

    assert len(results) == 2
    assert results[0]["text"] == "Revenue was €2.8M"
    assert results[0]["source_file"] == "Q1Report.pdf"
    assert results[0]["score"] == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_search_corpus_returns_empty_on_no_results():
    with patch("rag.corpus._retrieval_query_sync", return_value=[]):
        from rag.corpus import search_corpus

        results = await search_corpus("projects/p/ragCorpora/1", "xyzzy nothing matches")

    assert results == []


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_calls_delete_file():
    with patch("rag.corpus._delete_file_sync") as mock_del:
        from rag.corpus import delete_document

        await delete_document("projects/p/ragCorpora/1/ragFiles/f1")

    mock_del.assert_called_once_with("projects/p/ragCorpora/1/ragFiles/f1")


# ---------------------------------------------------------------------------
# list_user_documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_documents_returns_files():
    files = [_make_rag_file(f"projects/p/ragCorpora/1/ragFiles/f{i}") for i in range(3)]
    with patch("rag.corpus._list_files_sync", return_value=files):
        from rag.corpus import list_user_documents

        result = await list_user_documents("projects/p/ragCorpora/1")

    assert len(result) == 3
    assert result[0].name == "projects/p/ragCorpora/1/ragFiles/f0"


# ---------------------------------------------------------------------------
# _ensure_vertexai (isolation)
# ---------------------------------------------------------------------------


def test_ensure_vertexai_calls_init_once():
    """vertexai.init is called exactly once even with multiple _ensure_vertexai calls."""
    import rag.corpus as corpus_mod

    original = corpus_mod._vertexai_initialized
    corpus_mod._vertexai_initialized = False

    with (
        patch("vertexai.init") as mock_init,
        patch("config.gcp.resolve_gcp_project", return_value="test-project"),
    ):
        corpus_mod._ensure_vertexai()
        corpus_mod._ensure_vertexai()  # second call should not re-init

    mock_init.assert_called_once()
    corpus_mod._vertexai_initialized = original
