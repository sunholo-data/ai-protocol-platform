"""FastAPI routes for projects — /api/projects and /api/me/instructions.

Design: docs/design/private/projects.md §7. Every route is owner-only behind
``get_current_user``. A project id that is not under the caller's
``users/{uid}/projects`` is a 404, never a 403, so ids cannot be probed. Errors
use the existing ``{code, message}`` detail shape.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

import db.folders as folders_db
import db.projects as projects_db
from auth import User, get_current_user
from db.chat_sessions import get_session_index, list_sessions_for_project
from db.firestore import get_document

log = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])

_CurrentUser = Annotated[User, Depends(get_current_user)]
_MAX_DOCS_PER_REQUEST = 100


def _forget_cached(user_id: str, project_id: str) -> None:
    """A scoped chat reads the project (name, documents, instructions) through
    short caches; drop them on this instance as soon as the project changes."""
    from adk.project_context import clear_cache as clear_block_cache
    from adk.project_scope import clear_cache as clear_scope_cache

    clear_scope_cache(user_id, project_id)
    clear_block_cache(user_id)


def _purge_memories(user_id: str, project_id: str) -> int:
    """Delete the project's memories (projects.md §6.6). Best-effort: a failure is
    logged and the project delete still succeeds."""
    try:
        from adk.project_memory import purge_project_memories

        return asyncio.run(purge_project_memories(user_id, project_id))
    except Exception as exc:
        log.warning("project %s: could not purge its memories: %s", project_id, exc)
        return -1


def _http(exc: projects_db.ProjectError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found."})


def _require_project(user: User, project_id: str) -> dict[str, Any]:
    project = projects_db.get_project(user.uid, project_id)
    if project is None:
        raise _not_found()
    return project


class ProjectResponse(BaseModel):
    id: str
    name: str
    instructions: str = ""
    defaultSkillId: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    lastOpenedAt: str | None = None
    docCount: int | None = None
    chatCount: int | None = None
    # Meaning-based search readiness (projects.md §11 D4): {indexed, failed,
    # total, status: ready|indexing|unavailable}. Absent in the public template.
    search: dict[str, Any] | None = None


def _response(project: dict[str, Any], *, counts: bool = True, user_id: str = "") -> ProjectResponse:
    out = ProjectResponse(
        id=project["id"],
        name=str(project.get("name") or ""),
        instructions=str(project.get("instructions") or ""),
        defaultSkillId=project.get("defaultSkillId"),
        createdAt=project.get("createdAt"),
        updatedAt=project.get("updatedAt"),
        lastOpenedAt=project.get("lastOpenedAt"),
    )
    if counts and user_id:
        out.docCount = projects_db.count_project_documents(user_id, project["id"])
        out.chatCount = projects_db.count_project_sessions(user_id, project["id"])
    return out


class ProjectsListResponse(BaseModel):
    projects: list[ProjectResponse]


class TagResult(BaseModel):
    added: list[str] = Field(default_factory=list)
    already: list[str] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)


class CreatedProjectResponse(ProjectResponse):
    # Present when the create also tagged documents (fromFolderId/docIds/fromSessionId).
    documents: TagResult | None = None
    chatAdded: bool = False


class CreateProjectRequest(BaseModel):
    name: str
    instructions: str | None = None
    fromFolderId: str | None = None
    docIds: list[str] | None = None
    fromSessionId: str | None = None
    # E3/E4/E5 pre-fill a name; on a clash they append " (2)" rather than fail.
    uniqueName: bool = False


class PatchProjectRequest(BaseModel):
    name: str | None = None
    instructions: str | None = None
    defaultSkillId: str | None = None


class AddDocumentsRequest(BaseModel):
    doc_ids: list[str]


class DocumentsPage(BaseModel):
    documents: list[dict[str, Any]]
    next_cursor: str | None = None


@router.get("/api/projects")
def list_projects(user: _CurrentUser, limit: int = Query(default=50, ge=1, le=100)) -> ProjectsListResponse:
    rows = projects_db.list_projects(user.uid, limit=limit)
    return ProjectsListResponse(projects=[_response(p, user_id=user.uid) for p in rows])


@router.post("/api/projects", status_code=201)
def create_project(body: CreateProjectRequest, user: _CurrentUser) -> CreatedProjectResponse:
    to_tag: list[str] = list(body.docIds or [])
    session = None
    if body.fromFolderId:
        folder = folders_db.get_owned_folder(user.uid, body.fromFolderId)
        if folder is None:
            raise HTTPException(status_code=404, detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found."})
        to_tag += [d["id"] for d in folders_db.list_folder_documents(user.uid, folder["id"]) if d.get("id")]
    if body.fromSessionId:
        session = get_session_index(body.fromSessionId)
        if session is None or session.owner_uid != user.uid:
            raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Chat not found."})
        to_tag += list(session.document_ids)
    try:
        name = projects_db.unique_name(user.uid, body.name) if body.uniqueName else body.name
        project = projects_db.create_project(user.uid, name, instructions=body.instructions or "")
    except projects_db.ProjectError as exc:
        raise _http(exc) from exc
    tagged = projects_db.tag_documents(user.uid, project["id"], to_tag) if to_tag else None
    if session is not None:
        projects_db.set_session_project(session.session_id, project["id"])
    out = CreatedProjectResponse(**_response(project, user_id=user.uid).model_dump())
    out.documents = TagResult(**tagged) if tagged is not None else None
    out.chatAdded = session is not None
    return out


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, user: _CurrentUser) -> ProjectResponse:
    project = _require_project(user, project_id)
    projects_db.touch_project(user.uid, project_id)
    out = _response(project, user_id=user.uid)
    # Let the search index catch up on this project in the background (D4), and
    # say how far it has got. Both are no-ops without the private tier.
    from extensions import document_event, project_search_status

    document_event("project_opened", {"uid": user.uid, "project_id": project_id})
    out.search = project_search_status(user.uid, project_id)
    return out


@router.patch("/api/projects/{project_id}")
def patch_project(project_id: str, body: PatchProjectRequest, user: _CurrentUser) -> ProjectResponse:
    kwargs: dict[str, Any] = {"name": body.name, "instructions": body.instructions}
    if "defaultSkillId" in body.model_fields_set:
        kwargs["default_skill_id"] = body.defaultSkillId
    try:
        project = projects_db.update_project(user.uid, project_id, **kwargs)
    except projects_db.ProjectError as exc:
        raise _http(exc) from exc
    _forget_cached(user.uid, project_id)
    return _response(project, user_id=user.uid)


@router.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str, user: _CurrentUser) -> Response:
    """Untag every document and chat, then delete the project. Documents and chats
    are kept. The project's MEMORIES are deleted (projects.md §6.6) — the one thing
    a project delete removes; the count is in ``X-Project-Memories-Deleted``
    (-1 when the purge failed, logged; the project is deleted either way)."""
    try:
        projects_db.delete_project(user.uid, project_id)
    except projects_db.ProjectError as exc:
        raise _http(exc) from exc
    _forget_cached(user.uid, project_id)
    purged = _purge_memories(user.uid, project_id)
    return Response(status_code=204, headers={"X-Project-Memories-Deleted": str(purged)})


@router.post("/api/projects/{project_id}/documents")
def add_documents(project_id: str, body: AddDocumentsRequest, user: _CurrentUser) -> TagResult:
    _require_project(user, project_id)
    if len(body.doc_ids) > _MAX_DOCS_PER_REQUEST:
        raise HTTPException(
            status_code=422,
            detail={"code": "TOO_MANY_DOCUMENTS", "message": f"Add at most {_MAX_DOCS_PER_REQUEST} documents at once."},
        )
    result = TagResult(**projects_db.tag_documents(user.uid, project_id, body.doc_ids))
    _forget_cached(user.uid, project_id)
    return result


@router.delete("/api/projects/{project_id}/documents/{doc_id}", status_code=204)
def remove_document(project_id: str, doc_id: str, user: _CurrentUser) -> Response:
    _require_project(user, project_id)
    if not projects_db.untag_document(user.uid, project_id, doc_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "DOCUMENT_NOT_IN_PROJECT", "message": "That document is not in this project."},
        )
    _forget_cached(user.uid, project_id)
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/documents")
def list_documents(
    project_id: str,
    user: _CurrentUser,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=100),
) -> DocumentsPage:
    _require_project(user, project_id)
    docs, next_cursor = projects_db.list_project_documents(user.uid, project_id, cursor=cursor, page_size=page_size)
    return DocumentsPage(documents=docs, next_cursor=next_cursor)


@router.get("/api/projects/{project_id}/sessions")
def list_sessions(
    project_id: str,
    user: _CurrentUser,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    """Explicit plus derived chats, newest first. Each row is the usual session
    summary plus ``membership`` and, for a derived row, ``linkedDocument``
    ``{id, filename}`` — the document that put it in the project."""
    from protocols.sessions_route import _to_summary, resolve_skill_labels

    _require_project(user, project_id)
    doc_ids = set(projects_db.project_document_ids(user.uid, project_id))
    rows, next_cursor = list_sessions_for_project(user.uid, project_id, doc_ids, page_size=page_size, cursor=cursor)
    labels = resolve_skill_labels([r["session"].skill_id for r in rows])
    filenames: dict[str, str] = {}
    sessions: list[dict[str, Any]] = []
    for row in rows:
        idx = row["session"]
        summary = _to_summary(idx, user.uid, labels.get(idx.skill_id), viewer_email=user.email).model_dump()
        summary["membership"] = row["membership"]
        linked = row["linkedDocumentId"]
        if linked:
            if linked not in filenames:
                doc = get_document("parsed_documents", linked) or {}
                filenames[linked] = str(doc.get("originalFilename") or "") if doc.get("userId") == user.uid else ""
            summary["linkedDocument"] = {"id": linked, "filename": filenames[linked]}
        else:
            summary["linkedDocument"] = None
        sessions.append(summary)
    return {"sessions": sessions, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# "Your instructions" (§6.5)
# ---------------------------------------------------------------------------


class InstructionsBody(BaseModel):
    instructions: str = ""


@router.get("/api/me/instructions")
def get_my_instructions(user: _CurrentUser) -> InstructionsBody:
    return InstructionsBody(instructions=projects_db.get_user_instructions(user.uid))


@router.put("/api/me/instructions")
def put_my_instructions(body: InstructionsBody, user: _CurrentUser) -> InstructionsBody:
    from adk.project_context import clear_cache

    try:
        saved = projects_db.set_user_instructions(user.uid, body.instructions)
    except projects_db.ProjectError as exc:
        raise _http(exc) from exc
    clear_cache(user.uid)
    return InstructionsBody(instructions=saved)
