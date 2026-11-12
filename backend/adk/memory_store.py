"""Memory Bank reads and deletes within a single owner's scope.

Shared by the ``forget`` tool, the "Your memory" routes and project deletion, so
there is one deletion path (design: docs/design/private/memory-screen.md §4).

Every function takes the scope from its caller and never widens it:

* **Listing is by exact scope** (``retrieve`` with ``simple_retrieval_params``), and
  rows whose scope is not exactly the caller's are dropped anyway — Memory Bank
  matches scope exactly today, and this does not depend on it.
* **A ref alone proves nothing.** Before a delete, the memory is re-read and its
  scope compared to the caller's; another user's memory, or a project's, is left
  alone and counted as refused.
* **No filter-expression purge.** "Delete all" lists, re-checks and deletes one by
  one, so a filter typo can never reach personal memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from adk.memory_client import memories_api

log = logging.getLogger(__name__)

LIST_MAX = 500
_PAGE = 100
_DELETE_ALL_ROUNDS = 10


@dataclass
class Memory:
    ref: str
    fact: str
    updated_at: str | None


@dataclass
class DeleteResult:
    deleted: list[str] = field(default_factory=list)
    refused: int = 0
    failed: int = 0


def bank_of(service: Any) -> tuple[Any, str] | None:
    """``(async api client, "reasoningEngines/<id>")`` behind a memory service, or
    None when it has no Memory Bank (local / in-memory)."""
    engine_id = getattr(service, "_agent_engine_id", None)
    get_client = getattr(service, "_get_api_client", None)
    if not engine_id or not callable(get_client):
        return None
    return get_client(), f"reasoningEngines/{engine_id}"


def bank() -> tuple[Any, str] | None:
    """The deployment's Memory Bank, for callers outside an agent invocation."""
    from adk.session import get_memory_service

    return bank_of(get_memory_service())


def scope_for(user_id: str, project_id: str | None = None) -> dict[str, str]:
    """Personal memory, or one project's: the same scopes the chat path writes."""
    from adk.agui import APP_NAME

    scope = {"app_name": APP_NAME, "user_id": user_id}
    if project_id:
        scope["project_id"] = project_id
    return scope


def belongs_to_engine(ref: str, engine: str) -> bool:
    """A memory name under THIS Memory Bank: ``…/reasoningEngines/<id>/memories/<m>``."""
    prefix_at = ref.find(engine + "/memories/")
    return prefix_at == 0 or (prefix_at > 0 and ref[prefix_at - 1] == "/")


def _when(memory: Any) -> str | None:
    value = getattr(memory, "update_time", None) or getattr(memory, "create_time", None)
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


async def list_memories(client: Any, engine: str, scope: dict[str, str]) -> tuple[list[Memory], bool]:
    """Every memory in exactly ``scope``, newest first, and whether the list was cut
    at LIST_MAX."""
    pager = await memories_api(client).retrieve(name=engine, scope=scope, simple_retrieval_params={"page_size": _PAGE})
    found: list[Memory] = []
    truncated = False
    async for item in pager:
        memory = getattr(item, "memory", None)
        if memory is None or dict(getattr(memory, "scope", None) or {}) != scope:
            continue
        name, fact = getattr(memory, "name", ""), getattr(memory, "fact", "")
        if not name or not fact:
            continue
        if len(found) >= LIST_MAX:
            truncated = True
            break
        found.append(Memory(ref=name, fact=fact, updated_at=_when(memory)))
    found.sort(key=lambda m: m.updated_at or "", reverse=True)
    return found, truncated


async def delete_refs(client: Any, engine: str, scope: dict[str, str], refs: list[str]) -> DeleteResult:
    """Delete exactly ``refs``, each only after re-reading it and finding it in ``scope``."""
    result = DeleteResult()
    for ref in refs:
        if not belongs_to_engine(ref, engine):
            result.refused += 1
            continue
        try:
            memory = await memories_api(client).get(name=ref)
        except Exception as exc:
            log.warning("memory: could not read %s: %s", ref, exc)
            result.refused += 1
            continue
        if dict(getattr(memory, "scope", None) or {}) != scope:
            log.warning("memory: refused %s — scope is not the caller's", ref)
            result.refused += 1
            continue
        try:
            await memories_api(client).delete(name=ref)
        except Exception as exc:
            log.warning("memory: delete of %s failed: %s", ref, exc)
            result.failed += 1
            continue
        result.deleted.append(str(getattr(memory, "fact", "") or "a memory"))
    return result


async def delete_all(client: Any, engine: str, scope: dict[str, str]) -> DeleteResult:
    """Delete every memory in exactly ``scope`` (listed by scope, each re-checked).
    Repeats while a listing was cut at LIST_MAX, up to a bound."""
    total = DeleteResult()
    for _ in range(_DELETE_ALL_ROUNDS):
        memories, truncated = await list_memories(client, engine, scope)
        result = await delete_refs(client, engine, scope, [m.ref for m in memories])
        total.deleted += result.deleted
        total.refused += result.refused
        total.failed += result.failed
        if not truncated or not result.deleted:
            break
    return total


__all__ = [
    "LIST_MAX",
    "DeleteResult",
    "Memory",
    "bank",
    "bank_of",
    "belongs_to_engine",
    "delete_all",
    "delete_refs",
    "list_memories",
    "scope_for",
]
