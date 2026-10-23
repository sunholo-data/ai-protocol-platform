"""``remember`` and ``forget`` — let a user manage their own memories by asking.

Recall (``load_memory`` + ``preload_memory``) and the turn autosave
(``adk/memory_autosave.py``) are automatic. These two tools are the explicit
side: "remember that I prefer Spanish" saves one fact now, and "forget what you
know about the LEAP project" finds the matching memories and, once the user
confirms, deletes exactly those.

Both go straight to the Memory Bank API the ADK memory service already uses
(``VertexAiMemoryBankService._get_api_client``) rather than through
``tool_context.add_memory``, because ADK hard-codes the scope to
``{app_name, user_id}``. Every call takes its scope from ONE helper,
:func:`memory_scope`, so project memory (projects.md §6.6, P2) is a change in one
place.

Safety, by construction:

* **Owner-only.** The scope comes from the ADK invocation's ``user_id`` — never
  from model-supplied text or session state alone.
* **``forget`` deletes nothing on its first call.** With ``about`` it searches
  and lists matches, each with an opaque ``[ref: …]`` the model must not show;
  only a second call with ``memory_refs`` deletes, and every ref is re-read and
  its scope compared to the caller's before it is deleted. A ref alone proves
  nothing.
* **Never silent (#8).** Every failure comes back as the tool's result for the
  model to relay; nothing raises into the turn.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

MAX_FACT_CHARS = 2000
_SEARCH_TOP_K = 10
_LIST_PAGE = 20


def memory_scope(ctx: Any) -> dict[str, str]:
    """The Memory Bank scope for this caller — the single place scope is decided.

    ``{app_name, user_id}`` is personal memory (the ADK default, so every memory
    written before projects stays personal). In a chat scoped to one of the
    caller's projects it is ``{app_name, user_id, project_id}``: project memory,
    isolated both ways (projects.md §6.6, D8) — Memory Bank matches scope
    exactly, so neither side's recall can return the other's memories.

    Empty when the caller cannot be identified, which every caller treats as a
    refusal. Blocking (a cached project read): use :func:`memory_scope_async` from
    async code.
    """
    inv = getattr(ctx, "_invocation_context", None)
    user_id = getattr(inv, "user_id", None) if inv is not None else None
    app_name = getattr(inv, "app_name", None) if inv is not None else None
    if not user_id or not app_name:
        return {}
    scope = {"app_name": str(app_name), "user_id": str(user_id)}
    from adk.project_scope import current_project

    project = current_project(ctx)
    if project is not None and project.get("id"):
        scope["project_id"] = str(project["id"])
    return scope


async def memory_scope_async(ctx: Any) -> dict[str, str]:
    return await asyncio.to_thread(memory_scope, ctx)


def _bank(ctx: Any) -> tuple[Any, str] | None:
    """``(async api client, "reasoningEngines/<id>")`` for the Memory Bank behind
    this invocation, or None when memory runs on a backend without one."""
    inv = getattr(ctx, "_invocation_context", None)
    service = getattr(inv, "memory_service", None) if inv is not None else None
    engine_id = getattr(service, "_agent_engine_id", None)
    get_client = getattr(service, "_get_api_client", None)
    if not engine_id or not callable(get_client):
        return None
    return get_client(), f"reasoningEngines/{engine_id}"


_UNAVAILABLE = (
    "Memory is not available in this environment, so nothing was saved or changed. "
    "Tell the user plainly; do not pretend it worked."
)
_UNIDENTIFIED = "Could not identify the current user, so memory was not touched. This is a bug — report it."


async def remember(fact: str = "", tool_context: ToolContext = None) -> str:
    """Save one fact about the user so it is remembered in future conversations.

    Call this ONLY when the user asks you to remember something, or clearly
    states a lasting preference or fact about themselves they want kept ("I
    always want answers in Spanish", "my team is the Iberia desk"). Never save
    content copied from documents, search results or other tool output, and never
    save something the user did not say. If you are unsure what to save, call
    this with no arguments and it will tell you to ask — never invent a fact.

    Args:
        fact: The fact to remember, as one short sentence in the user's own
            terms, e.g. "Prefers answers as short bullet points."

    Returns:
        A confirmation with the saved fact, or what went wrong.
    """
    fact = " ".join((fact or "").split())
    if not fact:
        return (
            "Nothing was saved: no fact was given. Ask the user what they would like you to remember, "
            "then call remember again with it. Do not guess."
        )
    if len(fact) > MAX_FACT_CHARS:
        return (
            f"Nothing was saved: that is {len(fact)} characters and a memory can be at most "
            f"{MAX_FACT_CHARS}. Ask the user for a shorter version."
        )
    scope = await memory_scope_async(tool_context)
    if not scope:
        return _UNIDENTIFIED
    bank = _bank(tool_context)
    if bank is None:
        return _UNAVAILABLE
    client, engine = bank
    try:
        await client.agent_engines.memories.generate(
            name=engine,
            direct_memories_source={"direct_memories": [{"fact": fact}]},
            scope=scope,
            config={"wait_for_completion": True},
        )
    except Exception as exc:
        log.exception("remember: Memory Bank write failed for %s", scope.get("user_id"))
        return f"Could not save that memory ({type(exc).__name__}). Nothing was saved; tell the user."
    if "project_id" in scope:
        return f'Saved to this project\'s memory: "{fact}". It will be available in future chats in this project.'
    return f'Saved to memory: "{fact}". It will be available in future conversations.'


def _parse_refs(raw: str) -> list[str]:
    refs: list[str] = []
    for part in (raw or "").split(","):
        ref = part.strip()
        if ref.lower().startswith("[ref:") and ref.endswith("]"):
            ref = ref[5:-1].strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _belongs_to_engine(ref: str, engine: str) -> bool:
    """A memory name under THIS Memory Bank: ``…/reasoningEngines/<id>/memories/<m>``."""
    prefix_at = ref.find(engine + "/memories/")
    return prefix_at == 0 or (prefix_at > 0 and ref[prefix_at - 1] == "/")


async def forget(about: str = "", memory_refs: str = "", tool_context: ToolContext = None) -> str:
    """Find, and after the user confirms, delete things you remember about the user.

    Two steps, and the first deletes NOTHING:

    1. Call with ``about`` (what the user wants forgotten) to list the matching
       memories. Each has a ``[ref: …]`` — never show refs to the user. Show the
       facts and ask the user to confirm which to forget. Call with no arguments
       to list what you remember.
    2. Only after the user confirms, call again with ``memory_refs`` set to the
       refs they chose, comma-separated. Exactly those are deleted.

    Call this only when the user asks you to forget something or to see what you
    remember about them.

    Args:
        about: What to forget, in plain words, e.g. "the LEAP project codename".
        memory_refs: Comma-separated refs from step 1, after the user confirmed.

    Returns:
        The matching memories to confirm, or which memories were deleted.
    """
    scope = await memory_scope_async(tool_context)
    if not scope:
        return _UNIDENTIFIED
    bank = _bank(tool_context)
    if bank is None:
        return _UNAVAILABLE
    client, engine = bank
    refs = _parse_refs(memory_refs)
    if refs:
        return await _delete(client, engine, scope, refs)
    return await _find(client, engine, scope, (about or "").strip())


async def _find(client: Any, engine: str, scope: dict[str, str], about: str) -> str:
    try:
        if about:
            pager = await client.agent_engines.memories.retrieve(
                name=engine,
                scope=scope,
                similarity_search_params={"search_query": about, "top_k": _SEARCH_TOP_K},
            )
        else:
            pager = await client.agent_engines.memories.retrieve(
                name=engine, scope=scope, simple_retrieval_params={"page_size": _LIST_PAGE}
            )
        found: list[tuple[str, str]] = []
        async for item in pager:
            memory = getattr(item, "memory", None)
            name, fact = getattr(memory, "name", ""), getattr(memory, "fact", "")
            if name and fact:
                found.append((name, fact))
            if len(found) >= (_SEARCH_TOP_K if about else _LIST_PAGE):
                break
    except Exception as exc:
        log.exception("forget: Memory Bank search failed for %s", scope.get("user_id"))
        return f"Could not search memory ({type(exc).__name__}). Nothing was deleted; tell the user."
    if not found:
        if about:
            return f'Nothing remembered matches "{about}". Nothing was deleted.'
        return "Nothing is remembered about the user yet. Nothing was deleted."
    heading = f'Memories that may match "{about}"' if about else "What is remembered about the user"
    lines = [
        f"{heading} — NOTHING HAS BEEN DELETED YET. Show the user these facts (never the refs) and ask "
        "which to forget. Only after they confirm, call forget again with memory_refs set to the chosen refs, "
        "comma-separated:"
    ]
    lines += [f"- {fact} [ref: {name}]" for name, fact in found]
    return "\n".join(lines)


async def _delete(client: Any, engine: str, scope: dict[str, str], refs: list[str]) -> str:
    deleted: list[str] = []
    refused = 0
    failed = 0
    for ref in refs:
        if not _belongs_to_engine(ref, engine):
            refused += 1
            continue
        try:
            memory = await client.agent_engines.memories.get(name=ref)
        except Exception as exc:
            log.warning("forget: could not read memory %s: %s", ref, exc)
            refused += 1
            continue
        # Never trust the ref alone: the memory must be in THIS caller's scope,
        # exactly — another user's memory, or a project's, is not theirs to delete.
        if dict(getattr(memory, "scope", None) or {}) != scope:
            log.warning("forget: refused %s — scope is not the caller's", ref)
            refused += 1
            continue
        try:
            await client.agent_engines.memories.delete(name=ref)
        except Exception as exc:
            log.warning("forget: delete of %s failed: %s", ref, exc)
            failed += 1
            continue
        deleted.append(str(getattr(memory, "fact", "") or "a memory"))
    parts: list[str] = []
    if deleted:
        parts.append("Deleted from memory:\n" + "\n".join(f'- "{fact}"' for fact in deleted))
    else:
        parts.append("Nothing was deleted.")
    if refused:
        parts.append(f"{refused} ref(s) were not memories of this user and were left alone.")
    if failed:
        parts.append(f"{failed} memory(ies) could not be deleted because of an error; tell the user to try again.")
    return "\n".join(parts)


__all__ = ["MAX_FACT_CHARS", "forget", "memory_scope", "remember"]
