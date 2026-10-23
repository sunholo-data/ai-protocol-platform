"""Project-aware memory recall and writes (projects.md §6.6, D8).

ADK's ``VertexAiMemoryBankService`` hard-codes the scope ``{app_name, user_id}``
in ``search_memory`` and ``add_events_to_memory``, and is never told which
session or project is asking. So a chat scoped to a project cannot use it as-is
without reading and writing PERSONAL memory.

This module keeps the ADK path for every unscoped chat — byte-for-byte the same
calls as before — and, when :func:`adk.memory_tools.memory_scope` carries a
``project_id``, talks to Memory Bank directly with the three-key scope:

* :data:`project_load_memory_tool` / :data:`project_preload_memory_tool` replace
  ADK's ``load_memory_tool`` / ``preload_memory_tool`` with the SAME names,
  declarations and injected text, so prompts and evals see no difference.
* :func:`ingest_project_turn` is what ``memory_autosave`` calls for a scoped turn,
  mirroring ADK's ``_add_events_to_memory_via_ingest`` (fire-and-forget).
* :func:`purge_project_memories` deletes a project's memories when the project is
  deleted — the one thing project deletion removes (§6.6).

Memory Bank matches scope exactly, so isolation is structural: a personal
search never returns a project memory, and a project search never returns a
personal one or another project's.

Without a Memory Bank (local in-memory service) project chats recall nothing and
save nothing — logged, never an error.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Any

from google.adk.memory.memory_entry import MemoryEntry
from google.adk.tools import _memory_entry_utils
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.load_memory_tool import LoadMemoryResponse, LoadMemoryTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from typing_extensions import override

from adk.memory_tools import _bank, memory_scope_async

if TYPE_CHECKING:
    from google.adk.models import LlmRequest

log = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()
_PURGE_PAGE = 100
_PURGE_MAX = 2000


async def search_scoped_memory(tool_context: Any, query: str) -> list[MemoryEntry]:
    """Memories for ``query`` in this chat's scope: personal via ADK's own path,
    a project's via Memory Bank with the project scope."""
    scope = await memory_scope_async(tool_context)
    if not scope:
        return []
    if "project_id" not in scope:
        response = await tool_context.search_memory(query)
        return list(response.memories)
    bank = _bank(tool_context)
    if bank is None:
        log.info("project memory: no Memory Bank behind this invocation; project recall is empty")
        return []
    client, engine = bank
    pager = await client.agent_engines.memories.retrieve(
        name=engine, scope=scope, similarity_search_params={"search_query": query}
    )
    entries: list[MemoryEntry] = []
    async for item in pager:
        memory = getattr(item, "memory", None)
        fact = getattr(memory, "fact", "") if memory is not None else ""
        if not fact:
            continue
        updated = getattr(memory, "update_time", None)
        entries.append(
            MemoryEntry(
                author="user",
                content=types.Content(parts=[types.Part(text=fact)], role="user"),
                timestamp=updated.isoformat() if updated is not None else None,
            )
        )
    return entries


async def load_memory(query: str, tool_context: ToolContext) -> LoadMemoryResponse:
    """Loads the memory for the current user.

    Args:
      query: The query to load the memory for.

    Returns:
      A list of memory results.
    """
    return LoadMemoryResponse(memories=await search_scoped_memory(tool_context, query))


class ProjectAwareLoadMemoryTool(LoadMemoryTool):
    """ADK's ``load_memory`` (same name, declaration and instruction), searching
    the chat's project memory when the chat is in a project."""

    def __init__(self) -> None:
        FunctionTool.__init__(self, load_memory)


class ProjectAwarePreloadMemoryTool(PreloadMemoryTool):
    """ADK's ``preload_memory`` (same name and injected block), reading the
    chat's project memory when the chat is in a project."""

    @override
    async def process_llm_request(self, *, tool_context: ToolContext, llm_request: LlmRequest) -> None:
        user_content = tool_context.user_content
        if not user_content or not user_content.parts or not user_content.parts[0].text:
            return
        user_query: str = user_content.parts[0].text
        try:
            memories = await search_scoped_memory(tool_context, user_query)
        except Exception:
            log.warning("Failed to preload memory for query: %s", user_query)
            return
        if not memories:
            return
        lines: list[str] = []
        for memory in memories:
            if memory.timestamp:
                lines.append(f"Time: {memory.timestamp}")
            if text := _memory_entry_utils.extract_text(memory):
                lines.append(f"{memory.author}: {text}" if memory.author else text)
        if not lines:
            return
        full_memory_text = "\n".join(lines)
        si = f"""The following content is from your previous conversations with the user.
They may be useful for answering the user's current query.
<PAST_CONVERSATIONS>
{full_memory_text}
</PAST_CONVERSATIONS>
"""
        llm_request.append_instructions([si])


project_load_memory_tool = ProjectAwareLoadMemoryTool()
project_preload_memory_tool = ProjectAwarePreloadMemoryTool()


def _log_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("project memory: background ingest failed: %s", exc)


async def ingest_project_turn(
    callback_context: Any, scope: dict[str, str], events: list[Any], *, stream_id: str, idle_seconds: int
) -> bool:
    """Send one scoped turn's text events to Memory Bank (fire-and-forget, like
    ADK). False when there is no Memory Bank to send to."""
    import vertexai

    bank = _bank(callback_context)
    if bank is None:
        log.info("project memory: no Memory Bank behind this invocation; the turn is not saved")
        return False
    client, engine = bank
    direct_events = []
    for event in events:
        if not getattr(event, "content", None):
            continue
        ts = getattr(event, "timestamp", None)
        direct_events.append(
            vertexai.types.IngestionDirectContentsSourceEvent(
                content=event.content,
                event_id=getattr(event, "id", None),
                event_time=datetime.datetime.fromtimestamp(ts, tz=datetime.UTC) if ts is not None else None,
            )
        )
    if not direct_events:
        return False
    task = asyncio.create_task(
        client.agent_engines.memories.ingest_events(
            name=engine,
            scope=scope,
            direct_contents_source=vertexai.types.IngestionDirectContentsSource(events=direct_events),
            stream_id=stream_id,
            generation_trigger_config={"generation_rule": {"idle_duration": f"{idle_seconds}s"}},
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_error)
    return True


async def purge_project_memories(user_id: str, project_id: str) -> int:
    """Delete every memory in the project's scope. Returns how many were deleted.

    Lists by EXACT scope (``retrieve`` with ``simple_retrieval_params``) and deletes
    each, re-checking the scope on every row, rather than a filter-expression
    purge: a filter typo there could delete personal memory. Best-effort — the
    caller logs and carries on.
    """
    from adk.agui import APP_NAME
    from adk.session import get_memory_service

    service = get_memory_service()
    engine_id = getattr(service, "_agent_engine_id", None)
    get_client = getattr(service, "_get_api_client", None)
    if not engine_id or not callable(get_client):
        return 0
    client = get_client()
    engine = f"reasoningEngines/{engine_id}"
    scope = {"app_name": APP_NAME, "user_id": user_id, "project_id": project_id}
    names: list[str] = []
    pager = await client.agent_engines.memories.retrieve(
        name=engine, scope=scope, simple_retrieval_params={"page_size": _PURGE_PAGE}
    )
    async for item in pager:
        memory = getattr(item, "memory", None)
        if memory is None or dict(getattr(memory, "scope", None) or {}) != scope:
            continue
        if getattr(memory, "name", ""):
            names.append(memory.name)
        if len(names) >= _PURGE_MAX:
            break
    deleted = 0
    for name in names:
        try:
            await client.agent_engines.memories.delete(name=name)
            deleted += 1
        except Exception as exc:
            log.warning("project memory: could not delete %s: %s", name, exc)
    return deleted


__all__ = [
    "ProjectAwareLoadMemoryTool",
    "ProjectAwarePreloadMemoryTool",
    "ingest_project_turn",
    "load_memory",
    "project_load_memory_tool",
    "project_preload_memory_tool",
    "purge_project_memories",
    "search_scoped_memory",
]
