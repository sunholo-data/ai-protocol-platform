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

    # The number is a DISTANCE, not a relevance (PRECUT-HARDENING M1a).
    #
    # rag.create_corpus() is called with no backend_config, so the corpus uses
    # Vertex's default RagManagedDb / COSINE_DISTANCE. Per the RagContexts.Context
    # proto, `score` is then "the distance between the query and the context. The
    # larger the distance, the less relevant … range [0, 2], 0 means the most
    # relevant". Labelling it "relevance" told the model the exact opposite: it
    # reads 0.37 as "37% — weak" when 0.37 actually means a CLOSE match, and would
    # read 1.80 as strong when it is nearly the worst possible.
    #
    # Also drop the word "relevant" from the header. Vertex returns nearest-K with
    # no distance filter configured, so on a non-empty corpus it ALWAYS returns
    # something; calling those hits "relevant" is a claim this tool cannot support
    # and is half of how a search for an absent contract came back looking like a
    # find. See the identity guard on the PPA tools for the half that actually
    # settles document identity — distance cannot, because different PPAs share
    # near-identical legal boilerplate and sit genuinely close in embedding space.
    lines: list[str] = [
        f"Found {len(results)} excerpt(s), nearest first. `distance` is a COSINE "
        f"distance in [0, 2] where LOWER is closer; it is NOT a relevance percentage. "
        f"These are the nearest chunks in the corpus, which is not the same as a match "
        f"— if none of them is actually the document or clause asked for, say so "
        f"rather than answering from the closest one.\n"
        f"SCOPE: this searches ONLY documents already parsed into the workspace. The "
        f"tenant bucket normally holds many more. A weak or wrong hit here therefore "
        f"means 'not in the parsed corpus', NOT 'does not exist' — call "
        f"list_bucket_documents and open it with gs_url= instead of settling for the "
        f"nearest excerpt.\n"
    ]
    for i, chunk in enumerate(results, 1):
        source = chunk.get("source_file") or "unknown source"
        text = (chunk.get("text") or "").strip()
        score = float(chunk.get("score") or 0)
        lines.append(f"[{i}] From: {source} (distance: {score:.2f})\n{text}\n")

    return "\n".join(lines)
