"""Tool tests for tools/rag_tool.py — search_documents FunctionTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Reference the CONSTANT, never the literal key: the scope prefix is part of
# the contract (issue #38) and a hardcoded literal silently stops matching
# the code under test when the scope is corrected.
from tools.rag_tool import _STATE_RAG_CORPUS_NAME


def _make_tool_context(state: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state
    return ctx


# ---------------------------------------------------------------------------
# search_documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_documents_returns_chunks_with_attribution():
    from tools.rag_tool import search_documents

    chunks = [
        {"text": "Revenue was €2.8M in Q1.", "source_file": "Q1Report.pdf", "score": 0.91},
        {"text": "EBITDA margin was 12%.", "source_file": "Q1Report.pdf", "score": 0.75},
    ]
    tool_context = _make_tool_context({_STATE_RAG_CORPUS_NAME: "projects/p/ragCorpora/1"})

    with patch("rag.corpus.search_corpus", return_value=chunks):
        result = await search_documents("revenue Q1", tool_context)

    assert "Revenue was €2.8M" in result
    assert "Q1Report.pdf" in result
    assert "0.91" in result
    assert "[1]" in result
    assert "[2]" in result


@pytest.mark.asyncio
async def test_search_documents_no_corpus_in_state():
    from tools.rag_tool import search_documents

    tool_context = _make_tool_context({})

    result = await search_documents("anything", tool_context)

    assert "No documents" in result
    assert "upload" in result.lower()


@pytest.mark.asyncio
async def test_search_documents_no_tool_context():
    from tools.rag_tool import search_documents

    result = await search_documents("anything", tool_context=None)

    assert "No documents" in result


@pytest.mark.asyncio
async def test_search_documents_empty_results():
    from tools.rag_tool import search_documents

    tool_context = _make_tool_context({_STATE_RAG_CORPUS_NAME: "projects/p/ragCorpora/1"})

    with patch("rag.corpus.search_corpus", return_value=[]):
        result = await search_documents("xyzzy nothing matches", tool_context)

    assert "No relevant content" in result


@pytest.mark.asyncio
async def test_search_documents_rag_error_returns_message():
    from tools.rag_tool import search_documents

    tool_context = _make_tool_context({_STATE_RAG_CORPUS_NAME: "projects/p/ragCorpora/1"})

    with patch("rag.corpus.search_corpus", side_effect=Exception("Vertex unavailable")):
        result = await search_documents("query", tool_context)

    assert "failed" in result.lower()
    assert "Vertex unavailable" in result
