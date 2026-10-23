"""Project tools — reach, create and fill projects by asking (projects.md §6.7).

``list_projects`` and ``list_documents(project=…)`` let any skill answer "what's in
my Iberia project?" without the user opening it. ``create_project``,
``add_to_project`` and ``remove_from_project`` let a user who works in chat never
need the sidebar.

Friendly names in, friendly names out (CLAUDE.md #9): documents are named by
filename (or part of one) and projects by name; ids travel only as opaque
``[ref: …]`` values the model is told never to show. An ambiguous document name
changes NOTHING and returns the candidates to choose from.

The three write tools return a structured result that ``a2ui_project_render``
turns into a card in the chat (#7, #8): the outcome, what was added, anything not
found, any error, and an "Open project" button. There is deliberately no delete
tool — deleting a project also deletes its memory (§6.6), so it stays a
deliberate click in the UI.

Owner-only: the caller is resolved from the ADK invocation
(``resolve_caller_uid``), never from state alone, and every write goes through
``db.projects``, which only ever touches the caller's own documents and chats.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

import db.projects as projects_db
from db.firestore import get_document, query_documents

log = logging.getLogger(__name__)

PROJECT_TOOL_NAMES = ("list_projects", "create_project", "add_to_project", "remove_from_project")
WRITE_TOOL_NAMES = ("create_project", "add_to_project", "remove_from_project")

_DOCS = "parsed_documents"
_SESSIONS = "chat_sessions"
# How far back to look when resolving a filename. A user's library is usually a
# few dozen documents; this comfortably covers it without an unbounded read.
_LIBRARY_SCAN = 300
_MAX_CANDIDATES = 5
_STOPWORDS = frozenset({"the", "a", "an", "my", "of", "and", "doc", "document", "file"})


def _caller(tool_context: Any) -> str:
    from tools.documents.context import resolve_caller_uid

    return resolve_caller_uid(tool_context)


def _project_url(project_id: str) -> str:
    return f"/projects/{project_id}"


# ---------------------------------------------------------------------------
# Friendly document resolution
# ---------------------------------------------------------------------------


def _split_names(raw: str) -> list[str]:
    names = [n.strip() for n in re.split(r"[,;\n]", raw or "")]
    return [n for n in dict.fromkeys(names) if n]


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[0-9a-z]+", text.casefold()) if t not in _STOPWORDS]


def _library(user_id: str) -> list[dict[str, Any]]:
    """The caller's documents, newest first (parsed ones — the same set
    ``list_documents`` shows)."""
    docs = query_documents(
        _DOCS,
        filters=[("userId", "==", user_id), ("status", "==", "parsed")],
        order_by="createdAt",
        order_direction="DESCENDING",
        limit=_LIBRARY_SCAN,
    )
    for doc in docs:
        doc.setdefault("id", doc.get("__id", ""))
    return [d for d in docs if d.get("id")]


def resolve_documents(user_id: str, raw: str) -> dict[str, Any]:
    """Filenames (or parts of them, or refs) → the caller's documents.

    Returns ``{resolved: [(id, filename)], not_found: [name],
    ambiguous: [{name, candidates: [filename]}]}``. Matching, most specific
    first: a ref/id, the exact filename, the filename without its extension,
    then every word of the name appearing in the filename. Only a single match
    resolves; two or more is ambiguous.
    """
    library = _library(user_id)
    by_id = {d["id"]: d for d in library}
    resolved: list[tuple[str, str]] = []
    not_found: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    for name in _split_names(raw):
        ref = name[5:-1].strip() if name.lower().startswith("[ref:") and name.endswith("]") else name
        if ref in by_id:
            resolved.append((ref, str(by_id[ref].get("originalFilename") or ref)))
            continue
        key = name.casefold()

        def _fname(d: dict[str, Any]) -> str:
            return str(d.get("originalFilename") or "")

        exact = [d for d in library if _fname(d).casefold() == key]
        if not exact:
            exact = [d for d in library if _fname(d).rsplit(".", 1)[0].casefold() == key]
        if exact:
            # Same filename twice: the newest (the library is newest first).
            resolved.append((exact[0]["id"], _fname(exact[0])))
            continue
        words = _tokens(name)
        hits = [d for d in library if words and all(w in _fname(d).casefold() for w in words)]
        if len(hits) == 1:
            resolved.append((hits[0]["id"], _fname(hits[0])))
        elif hits:
            ambiguous.append({"name": name, "candidates": [_fname(d) for d in hits[:_MAX_CANDIDATES]]})
        else:
            not_found.append(name)
    unique: dict[str, str] = {}
    for doc_id, filename in resolved:
        unique.setdefault(doc_id, filename)
    return {"resolved": list(unique.items()), "not_found": not_found, "ambiguous": ambiguous}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def _result(action: str, status: str, **fields: Any) -> dict[str, Any]:
    """The structured result the chat card renders. ``cardId`` makes each call's
    card its own surface, so a later change never rewrites an earlier card."""
    out: dict[str, Any] = {
        "kind": "project_change",
        "action": action,
        "status": status,
        "cardId": uuid.uuid4().hex[:10],
        "added": [],
        "already": [],
        "removed": [],
        "notFound": [],
        "ambiguous": [],
        "failed": [],
    }
    out.update(fields)
    return out


def _with_project(out: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    out["project"] = {"name": project.get("name") or "", "ref": project["id"]}
    out["url"] = _project_url(project["id"])
    return out


def _needs_choice(action: str, resolution: dict[str, Any]) -> dict[str, Any]:
    names = "; ".join(f"“{a['name']}” could be {', '.join(a['candidates'])}" for a in resolution["ambiguous"])
    return _result(
        action,
        "needs_choice",
        ambiguous=resolution["ambiguous"],
        notFound=resolution["not_found"],
        message=(
            f"Nothing was changed: {names}. Reply to the user now: list these files and ask which one they "
            "mean. Do not pick one yourself, do not add them all, and do not call another tool first. When "
            "they answer, call again with the exact filename."
        ),
    )


def _filenames(ids: list[str], names: dict[str, str]) -> list[str]:
    return [names.get(i, i) for i in ids]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def list_projects(tool_context: ToolContext = None) -> str:
    """List the user's projects: each project's name, how many documents and chats
    it has, and when it was last opened.

    Call it with no arguments whenever the user asks about their projects, or
    names a project you need to find. Refer to projects by name; never show the
    [ref] values to the user.

    Returns:
        The user's projects, most recently opened first.
    """
    uid = _caller(tool_context)
    if not uid:
        return "Could not identify the current user, so no projects can be listed. This is a bug — report it."
    try:
        rows = await asyncio.to_thread(projects_db.list_projects, uid)
        if not rows:
            return (
                "The user has no projects yet. A project gathers documents from anywhere, plus the chats "
                "that used them. They can ask you to create one (create_project) or use "
                "“+ New project” in the sidebar."
            )
        lines = [f"The user has {len(rows)} project(s). Refer to them by name; never show the [ref] values:"]
        for project in rows:
            docs = await asyncio.to_thread(projects_db.count_project_documents, uid, project["id"])
            chats = await asyncio.to_thread(projects_db.count_project_sessions, uid, project["id"])
            opened = str(project.get("lastOpenedAt") or "")[:10]
            detail = f"{docs} document(s), {chats} chat(s)" + (f", last opened {opened}" if opened else "")
            lines.append(f"- {project.get('name')} — {detail} [ref: {project['id']}]")
        return "\n".join(lines)
    except Exception as exc:
        log.warning("list_projects failed for %s: %s", uid, exc)
        return f"Could not list projects ({type(exc).__name__}). Tell the user and suggest trying again."


async def create_project(
    name: str = "",
    instructions: str = "",
    documents: str = "",
    from_this_chat: bool = False,
    tool_context: ToolContext = None,
) -> dict:
    """Create a project for the user, optionally with documents in it.

    Call this ONLY when the user asks to create a project or to turn this chat
    into one — never on your own initiative. A project gathers documents (which
    stay in their folders) and the chats that used them.

    Args:
        name: The project's name, as the user said it. Optional only with
            from_this_chat, where it defaults to the chat's title.
        instructions: Optional standing instructions for chats in the project,
            only if the user gave some.
        documents: Optional comma-separated filenames (or parts of filenames) of
            the user's documents to put in it, e.g. "EDP PPA, LEAP contract".
            Pass them exactly as the user said them. Do not look documents up
            first, and never choose between similar files or turn one name into
            several yourself: this tool resolves names and tells you when to
            ask the user.
        from_this_chat: True when the user asks to turn THIS chat into a
            project: the documents this chat used are added and the chat
            becomes part of the project.

    Returns:
        What happened. A card showing the result is displayed to the user
        automatically, with a button to open the project; summarise it in one
        line. Never show refs or links.
    """
    uid = _caller(tool_context)
    if not uid:
        return _result("create", "error", error="Could not identify the current user. Nothing was created.")
    name = (name or "").strip()
    if not name and not from_this_chat:
        return _result(
            "create",
            "error",
            error="No project name was given. Nothing was created.",
            message="Ask the user what to call the project, then call create_project again. Do not invent a name.",
        )
    try:
        return await asyncio.to_thread(_create_sync, uid, name, instructions, documents, from_this_chat, tool_context)
    except Exception as exc:
        log.exception("create_project failed for %s", uid)
        return _result("create", "error", error=f"The project could not be created ({type(exc).__name__}).")


def _session_of(tool_context: Any) -> tuple[str, dict[str, Any] | None]:
    invocation = getattr(tool_context, "_invocation_context", None)
    session_id = getattr(getattr(invocation, "session", None), "id", None) or ""
    return session_id, (get_document(_SESSIONS, session_id) if session_id else None)


def _create_sync(
    uid: str, name: str, instructions: str, documents: str, from_this_chat: bool, tool_context: Any
) -> dict[str, Any]:
    resolution = resolve_documents(uid, documents) if documents.strip() else None
    if resolution and resolution["ambiguous"]:
        return _needs_choice("create", resolution)

    session_id, session = _session_of(tool_context) if from_this_chat else ("", None)
    if from_this_chat and session is not None and session.get("ownerUid") != uid:
        session = None  # a shared thread the caller does not own: never link it
    try:
        if name:
            project = projects_db.create_project(uid, name, instructions=instructions)
        else:
            title = str((session or {}).get("title") or "").strip() or "New project"
            project = projects_db.create_project(
                uid, projects_db.unique_name(uid, title[: projects_db.MAX_NAME_CHARS]), instructions=instructions
            )
    except projects_db.ProjectError as exc:
        return _result("create", "error", error=exc.message, code=exc.code)

    names: dict[str, str] = {}
    to_tag: list[str] = []
    if resolution:
        for doc_id, filename in resolution["resolved"]:
            names[doc_id] = filename
            to_tag.append(doc_id)
    chat_added = False
    if from_this_chat and session is not None:
        for doc_id in session.get("documentIds") or []:
            if doc_id not in names:
                doc = get_document(_DOCS, doc_id)
                if doc and doc.get("userId") == uid:
                    names[doc_id] = str(doc.get("originalFilename") or doc_id)
                    to_tag.append(doc_id)
        projects_db.set_session_project(session_id, project["id"])
        chat_added = True
    tagged = projects_db.tag_documents(uid, project["id"], to_tag) if to_tag else {"added": [], "already": []}
    out = _result(
        "create",
        "created",
        added=_filenames(tagged["added"], names),
        notFound=(resolution or {}).get("not_found", []),
        failed=[{"filename": names.get(f["id"], ""), "reason": f["reason"]} for f in tagged.get("failed", [])],
        chatAdded=chat_added,
        chatNotLinked=bool(from_this_chat and not chat_added),
    )
    return _with_project(out, project)


async def add_to_project(project: str = "", documents: str = "", tool_context: ToolContext = None) -> dict:
    """Add the user's documents to one of their projects. The documents stay in
    their folders; a document can be in several projects.

    Call this ONLY when the user asks to add documents to a project.

    Args:
        project: The project's name (or part of it).
        documents: Comma-separated filenames (or parts of filenames), exactly
            as the user said them. Do not look documents up first or choose
            between similar files yourself; this tool tells you when to ask.

    Returns:
        What happened. A card showing the result is displayed to the user
        automatically; summarise it in one line. Never show refs or links.
    """
    return await _change("add", project, documents, tool_context)


async def remove_from_project(project: str = "", documents: str = "", tool_context: ToolContext = None) -> dict:
    """Take documents out of one of the user's projects. The documents themselves
    are NOT deleted — they stay in the user's library and folders.

    Call this ONLY when the user asks to remove documents from a project.

    Args:
        project: The project's name (or part of it).
        documents: Comma-separated filenames (or parts of filenames), exactly
            as the user said them. Do not look documents up first or choose
            between similar files yourself; this tool tells you when to ask.

    Returns:
        What happened. A card showing the result is displayed to the user
        automatically; summarise it in one line. Never show refs or links.
    """
    return await _change("remove", project, documents, tool_context)


async def _change(action: str, project: str, documents: str, tool_context: Any) -> dict[str, Any]:
    uid = _caller(tool_context)
    if not uid:
        return _result(action, "error", error="Could not identify the current user. Nothing was changed.")
    if not (project or "").strip() or not (documents or "").strip():
        missing = "project" if not (project or "").strip() else "documents"
        return _result(
            action,
            "error",
            error=f"No {missing} given. Nothing was changed.",
            message=f"Ask the user which {missing} they mean, then call again. Do not guess.",
        )
    try:
        return await asyncio.to_thread(_change_sync, action, uid, project, documents)
    except Exception as exc:
        log.exception("%s_project failed for %s", action, uid)
        return _result(action, "error", error=f"The project could not be changed ({type(exc).__name__}).")


def _change_sync(action: str, uid: str, project_query: str, documents: str) -> dict[str, Any]:
    project, candidates = projects_db.resolve_project(uid, project_query)
    if project is None:
        if candidates:
            listing = ", ".join(candidates[:10])
            error = f"No single project matches “{project_query}”. Your projects: {listing}."
        else:
            error = "You have no projects yet. Nothing was changed."
        return _result(action, "error", error=error, projectCandidates=candidates[:10])
    resolution = resolve_documents(uid, documents)
    if resolution["ambiguous"]:
        return _with_project(_needs_choice(action, resolution), project)
    names = dict(resolution["resolved"])
    if action == "add":
        tagged = projects_db.tag_documents(uid, project["id"], list(names))
        out = _result(
            action,
            "updated",
            added=_filenames(tagged["added"], names),
            already=_filenames(tagged["already"], names),
            failed=[{"filename": names.get(f["id"], ""), "reason": f["reason"]} for f in tagged["failed"]],
            notFound=resolution["not_found"],
        )
    else:
        removed: list[str] = []
        not_in: list[str] = []
        for doc_id, filename in names.items():
            (removed if projects_db.untag_document(uid, project["id"], doc_id) else not_in).append(filename)
        out = _result(action, "updated", removed=removed, notInProject=not_in, notFound=resolution["not_found"])
    # A chat scoped to this project lists its documents from a short cache; make
    # the change visible to the very next model call.
    from adk.project_context import clear_cache

    clear_cache(uid)
    return _with_project(out, project)


def project_function_tools() -> list[FunctionTool]:
    """The project tools every skill gets by default (``tool_configs.defaults.projects``)."""
    from adk.project_text_search import grep_project_documents

    return [
        FunctionTool(list_projects),
        FunctionTool(create_project),
        FunctionTool(add_to_project),
        FunctionTool(remove_from_project),
        FunctionTool(grep_project_documents),
    ]


__all__ = [
    "PROJECT_TOOL_NAMES",
    "add_to_project",
    "create_project",
    "list_projects",
    "project_function_tools",
    "remove_from_project",
    "resolve_documents",
]
