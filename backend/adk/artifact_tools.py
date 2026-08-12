"""ADK FunctionTools for artifact retrieval.

Complements _handle_large_output in callbacks.py: when large tool responses are
offloaded to artifacts, the agent can request specific sections via this tool
rather than loading the full content into context.
"""

from __future__ import annotations

import logging

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 10_000
_MAX_CHUNKS = 5


async def retrieve_artifact(
    artifact_id: str,
    section: str | None = None,
    tool_context: ToolContext = None,
) -> str:
    """Retrieve content from a previously saved artifact.

    Args:
        artifact_id: The artifact filename used when saving (e.g. 'search_response_abc123').
        section: Optional keyword to filter content. Returns up to 5 matching
            paragraph chunks (case-insensitive). Omit to get the first 10K chars.

    Returns:
        The requested content, or a not-found message if the artifact doesn't exist.
    """
    if tool_context is None:
        return "retrieve_artifact requires an active tool context."

    try:
        part = await tool_context.load_artifact(filename=artifact_id)
    except Exception as exc:
        logger.warning("load_artifact failed for %s: %s", artifact_id, exc)
        return f"Could not load artifact '{artifact_id}': {exc}"

    if part is None:
        return f"Artifact '{artifact_id}' not found. It may have expired or the ID is incorrect."

    # Extract text — artifacts saved by _handle_large_output are Part.from_text()
    content = getattr(part, "text", None)
    if content is None and hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
        content = part.inline_data.data.decode("utf-8", errors="replace")
    if not content:
        return f"Artifact '{artifact_id}' exists but contains no readable text."

    if section:
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        matches = [p for p in paragraphs if section.lower() in p.lower()]
        if matches:
            return "\n\n".join(matches[:_MAX_CHUNKS])
        return f"No content matching '{section}' found in artifact '{artifact_id}'."

    return content[:_PREVIEW_CHARS]
