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


def _chunks(content: str) -> list[str]:
    """Split into searchable units, tolerating payloads with no blank lines.

    The paragraph heuristic was written against markdown, where ``blocks_to_markdown``
    joins on ``"\\n\\n"``. It degenerates on the OTHER shape this tool is routinely
    pointed at: ``get_document_content(mode="blocks")`` returns ``json.dumps`` with
    no ``indent=``, so the payload has no newlines at all and splitting yields ONE
    chunk containing everything. Every section query then "matches", and the whole
    payload comes back — which is how a 63K result became a self-referential
    offload pointer on 2026-08-24 (see the loop note in the tests).

    Falling back to single-newline lines makes the section filter do something
    useful on that shape instead of returning the entire artifact.
    """
    paragraphs = [p for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    lines = [ln for ln in content.split("\n") if ln.strip()]
    return lines if len(lines) > 1 else paragraphs


async def retrieve_artifact(
    artifact_id: str,
    section: str | None = None,
    tool_context: ToolContext = None,
) -> str:
    """Retrieve content from a previously saved artifact.

    Args:
        artifact_id: The artifact filename used when saving (e.g. 'search_response_abc123').
        section: Optional keyword to filter content. Returns up to 5 matching
            chunks (case-insensitive), truncated to 10K chars. Omit to get the
            first 10K chars.

    Returns:
        A slice of the artifact, always at most 10K chars — never another
        "[large response saved as artifact …]" pointer. If you get a pointer
        back from this tool, that is a bug; do not call it again with a
        different section, because the result will be the same. Use
        load_artifacts instead.

        Returns a not-found message if the artifact doesn't exist, and an
        explicit "No content matching …" message if the section matches nothing
        — that means the section is absent, not that it is empty.
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

    # The model does not always send a string: on 2026-09-14 a prod turn passed
    # ``section=1`` (meaning "section 1"), and ``int.lower`` killed the whole
    # run with a RUN_ERROR — and the orphaned tool_call then poisoned every
    # later turn on that session ("Missing tool results"). A tool argument is
    # untrusted input; coerce rather than crash.
    if section is not None and not isinstance(section, str):
        section = str(section)
    if section:
        matches = [p for p in _chunks(content) if section.lower() in p.lower()]
        if matches:
            # Cap CHARS, not just chunks. _MAX_CHUNKS bounds the chunk count and
            # says nothing about their size, so without this the section path can
            # return more than it was given a pointer to — see the loop note above.
            return "\n\n".join(matches[:_MAX_CHUNKS])[:_PREVIEW_CHARS]
        return f"No content matching '{section}' found in artifact '{artifact_id}'."

    return content[:_PREVIEW_CHARS]
