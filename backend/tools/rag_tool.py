"""search_documents FunctionTool — semantic search over the user's RAG corpus.

Only active when ``RAG_DOCUMENTS_ENABLED=true``. The corpus name is read from
``user:rag_corpus_name`` in session state, populated by the RAG document loader
callback on every turn that has documents attached.
"""

from __future__ import annotations

import logging

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# `user:` — the corpus is per-user (issue #38). Under `app:` this key was
# shared by every session of the app, so a user with no uploads inherited
# another user's corpus and searched THEIR private documents.
_STATE_RAG_CORPUS_NAME = "user:rag_corpus_name"


async def search_documents(query: str, tool_context: ToolContext = None) -> str:
    """Search across documents the user has uploaded in this conversation.

    Call this whenever the user asks about content from their uploaded files.
    Returns relevant excerpts with source attribution (file name).

    Args:
        query: What to search for in the uploaded documents.

    Returns:
        Relevant excerpts from the documents with source file attribution,
        or a message explaining why no results were found.
    """
    corpus_name: str | None = None
    if tool_context is not None:
        corpus_name = tool_context.state.get(_STATE_RAG_CORPUS_NAME)

    if not corpus_name:
        logger.info("search_documents: no corpus in state — documents not yet loaded")
        return "No documents have been uploaded to this conversation yet. Please upload a document first."

    from rag.corpus import search_corpus

    try:
        results = await search_corpus(corpus_name, query)
    except Exception as exc:
        logger.warning("search_documents: RAG query failed for corpus %s: %s", corpus_name, exc)
        return f"Document search failed: {exc}"

    if not results:
        return "No relevant content found in your documents for that query."

    lines: list[str] = [f"Found {len(results)} relevant excerpt(s) from your documents:\n"]
    for i, chunk in enumerate(results, 1):
        source = chunk.get("source_file") or "unknown source"
        text = (chunk.get("text") or "").strip()
        score = float(chunk.get("score") or 0)
        lines.append(f"[{i}] From: {source} (relevance: {score:.2f})\n{text}\n")

    return "\n".join(lines)
