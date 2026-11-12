"""Your memory: see and delete what the assistant remembers about you.

Design: docs/design/private/memory-screen.md. Personal memory, or one of the
caller's projects' memory (``?project_id=``). The scope always comes from the
verified token and an ownership check, never from the request; every delete goes
through ``adk.memory_store``, which re-reads each memory and refuses any outside
that scope.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import db.projects as projects_db
from adk import memory_store
from auth import User, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])

_CurrentUser = Annotated[User, Depends(get_current_user)]
_CONFIRM_ALL = "forget"


class MemoryItem(BaseModel):
    ref: str
    fact: str
    updated_at: str | None = None


class MemoryList(BaseModel):
    available: bool
    memories: list[MemoryItem] = []
    truncated: bool = False


class DeleteOutcome(BaseModel):
    deleted: int
    refused: int = 0
    failed: int = 0


async def _scope(user: User, project_id: str) -> dict[str, str]:
    """The caller's personal scope, or their project's after an ownership check.
    Someone else's project id is a 404, exactly like a project that doesn't exist."""
    project_id = project_id.strip()
    if project_id and await asyncio.to_thread(projects_db.get_project, user.uid, project_id) is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found."})
    return memory_store.scope_for(user.uid, project_id or None)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "MEMORY_UNAVAILABLE", "message": "Memory isn't switched on in this workspace."},
    )


def _bank_error(exc: Exception, action: str) -> HTTPException:
    log.warning("memory: %s failed: %s", action, exc)
    return HTTPException(
        status_code=502,
        detail={"code": "MEMORY_ERROR", "message": f"Couldn't {action} your memory right now. Try again."},
    )


def _log_delete(user: User, scope: dict[str, str], result: memory_store.DeleteResult) -> None:
    # Counts only: never the memory text (design §5).
    log.info(
        "memory_delete uid=%s scope=%s deleted=%d refused=%d failed=%d",
        user.uid,
        "project" if "project_id" in scope else "personal",
        len(result.deleted),
        result.refused,
        result.failed,
    )


@router.get("/api/me/memories")
async def list_my_memories(user: _CurrentUser, project_id: str = Query("")) -> MemoryList:
    scope = await _scope(user, project_id)
    bank = memory_store.bank()
    if bank is None:
        return MemoryList(available=False)
    try:
        memories, truncated = await memory_store.list_memories(*bank, scope)
    except Exception as exc:
        raise _bank_error(exc, "load") from exc
    return MemoryList(
        available=True,
        memories=[MemoryItem(ref=m.ref, fact=m.fact, updated_at=m.updated_at) for m in memories],
        truncated=truncated,
    )


# The ref (a Memory Bank resource name, full of slashes) rides in the query, not
# the path: proxies decode %2F in paths inconsistently.
@router.delete("/api/me/memories/item")
async def delete_my_memory(user: _CurrentUser, ref: str = Query(...), project_id: str = Query("")) -> DeleteOutcome:
    scope = await _scope(user, project_id)
    bank = memory_store.bank()
    if bank is None:
        raise _unavailable()
    try:
        result = await memory_store.delete_refs(*bank, scope, [ref])
    except Exception as exc:
        raise _bank_error(exc, "change") from exc
    _log_delete(user, scope, result)
    if result.refused:
        # Not theirs, or already gone: indistinguishable on purpose.
        raise HTTPException(
            status_code=404, detail={"code": "MEMORY_NOT_FOUND", "message": "That memory wasn't found."}
        )
    if result.failed:
        raise HTTPException(
            status_code=502, detail={"code": "MEMORY_ERROR", "message": "Couldn't delete that memory. Try again."}
        )
    return DeleteOutcome(deleted=len(result.deleted))


@router.delete("/api/me/memories")
async def delete_all_my_memories(
    user: _CurrentUser, project_id: str = Query(""), confirm: str = Query("")
) -> DeleteOutcome:
    if confirm != _CONFIRM_ALL:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFIRMATION_REQUIRED", "message": f"Pass confirm={_CONFIRM_ALL} to delete everything."},
        )
    scope = await _scope(user, project_id)
    bank = memory_store.bank()
    if bank is None:
        raise _unavailable()
    try:
        result = await memory_store.delete_all(*bank, scope)
    except Exception as exc:
        raise _bank_error(exc, "change") from exc
    _log_delete(user, scope, result)
    return DeleteOutcome(deleted=len(result.deleted), refused=result.refused, failed=result.failed)
