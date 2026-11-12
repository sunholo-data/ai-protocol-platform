"""Read and write files in the project's writable GitHub folders
(project-source-write.md §5).

Only in a chat scoped to a project the caller owns, and only for folders whose
source the owner made writable. Everything else (which folder, which repository,
whether the person may still write, the staleness check) is decided by
``sources.project_write``; these wrappers turn its refusals into results the
model can act on, never exceptions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from adk.project_scope import current_project
from tools.documents.context import resolve_caller_uid

log = logging.getLogger(__name__)

WRITE_TOOL_NAMES = ("read_project_file", "write_project_files", "delete_project_files")

_NEXT_STEP = {
    "SOURCE_WRITE_STALE": "Call read_project_file for each file named, redo your change on the new text, and write again "
    "with the new version.",
    "SOURCE_WRITE_OUTSIDE": "Write only inside the writable folders named.",
    "SOURCE_WRITE_NONE": "Tell the person this project's folders are read-only; the owner can turn on writing for a "
    "GitHub source on the project page.",
}


async def _scope(tool_context: Any) -> tuple[str, str] | dict[str, Any]:
    uid = resolve_caller_uid(tool_context)
    project = await asyncio.to_thread(current_project, tool_context) if uid else None
    if not uid or project is None:
        return {"status": "error", "message": "This only works in a chat inside one of your projects."}
    return uid, str(project["id"])


def _refused(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", "SOURCE_WRITE_FAILED")
    out = {"status": "refused", "code": code, "message": getattr(exc, "message", str(exc))}
    if code in _NEXT_STEP:
        out["next_step"] = _NEXT_STEP[code]
    return out


async def read_project_file(path: str, tool_context: ToolContext = None) -> dict:
    """Read the exact current text of a file in one of the project's writable folders, from GitHub.

    Use this before changing a file with write_project_files: it returns the
    text as committed (not the parsed version search returns) and its
    `version`, which you pass back when writing so nobody's change is lost.

    Args:
        path: The file's path as the project shows it, starting with the folder, e.g. "notes/plan.md".
    """
    from sources.project_write import WriteRefused, read_file

    scope = await _scope(tool_context)
    if isinstance(scope, dict):
        return scope
    try:
        return await read_file(*scope, path)
    except WriteRefused as exc:
        return _refused(exc)
    except Exception:
        log.exception("read_project_file failed")
        return {"status": "error", "message": "Reading the file from GitHub failed. Try again."}


async def write_project_files(files: list[dict[str, str]], message: str, tool_context: ToolContext = None) -> dict:
    """Commit one or more whole text files to one of the project's writable folders on GitHub.

    Each call is one commit on the folder's branch that people and other tools
    see. Use it to keep the project's notes, plans, status and drafts in the
    repository when the person asks, or when the project's instructions say to.
    Put EVERY file one request changes into ONE call, so the request is one
    commit; don't commit file by file.
    Write the WHOLE new content of each file. To change an existing file, read
    it first with read_project_file and pass its `version`. A file someone else
    changed meanwhile is refused, never overwritten. Only text files (Markdown,
    text, CSV, JSON, YAML, code); at most 20 files and 1 MB per commit; all in
    one folder.

    Args:
        files: The files, each {"path": "<folder>/<file>", "content": "<whole new text>", "version": "<from read_project_file, for an existing file>"}.
        message: The commit message: one line saying what changed and why.
    """
    from sources.project_write import WriteRefused, write_files

    scope = await _scope(tool_context)
    if isinstance(scope, dict):
        return scope
    invocation = getattr(tool_context, "_invocation_context", None)
    session_id = str(getattr(getattr(invocation, "session", None), "id", "") or "")
    try:
        return await write_files(*scope, list(files or []), message, session_id=session_id)
    except WriteRefused as exc:
        return _refused(exc)
    except Exception:
        log.exception("write_project_files failed")
        return {"status": "error", "message": "The commit failed. Nothing was written. Try again."}


async def delete_project_files(paths: list[str], message: str, tool_context: ToolContext = None) -> dict:
    """Ask to delete files from one of the project's writable folders on GitHub.

    Nothing is deleted by this call: it prepares the deletion and returns an
    approve_url. Give the person that link and say that the files are deleted,
    in one commit, only when they approve it there. Use only when the person
    asked to delete or move files away; never to tidy up on your own.

    Args:
        paths: The files as the project shows them, e.g. ["notes/old-plan.md"]. At most 20, all in one folder.
        message: The commit message: one line saying what is deleted and why.
    """
    from sources.delete_approvals import prepare
    from sources.project_write import WriteRefused

    scope = await _scope(tool_context)
    if isinstance(scope, dict):
        return scope
    invocation = getattr(tool_context, "_invocation_context", None)
    session_id = str(getattr(getattr(invocation, "session", None), "id", "") or "")
    try:
        return await prepare(*scope, [str(p) for p in paths or []], message, session_id=session_id)
    except WriteRefused as exc:
        return _refused(exc)
    except Exception:
        log.exception("delete_project_files failed")
        return {"status": "error", "message": "The deletion could not be prepared. Nothing was deleted."}


# What each entry of write_project_files' `files` is. The function takes plain
# dicts, but the model must see the fields: typed `dict[str, str]`, the schema
# reached it as a bare object with nothing to fill in, and on Sunholo
# (2026-11-09) Haiku once sent the same file twice, the second time with an
# 11-character stub. Inline rather than a pydantic model, whose `$defs`/`$ref`
# not every provider resolves.
FILE_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "The file as the project shows it, e.g. notes/plan.md."},
        "content": {"type": "string", "description": "The WHOLE new text of the file."},
        "version": {
            "type": "string",
            "description": "For a file that exists: the version read_project_file returned. Omit for a new file.",
        },
    },
    "required": ["path", "content"],
}


class WriteFilesTool(FunctionTool):
    """``write_project_files`` with each file's fields spelled out for the model."""

    def __init__(self) -> None:
        super().__init__(write_project_files)

    def _get_declaration(self) -> Any:
        decl = super()._get_declaration()
        if decl is None:
            return decl
        schema = getattr(decl, "parameters_json_schema", None)
        if isinstance(schema, dict) and isinstance(schema.get("properties", {}).get("files"), dict):
            schema["properties"]["files"]["items"] = dict(FILE_ENTRY_SCHEMA)
            schema["properties"]["files"]["description"] = "Every file this request changes; each path once."
        elif getattr(decl, "parameters", None) is not None and decl.parameters.properties:
            files = decl.parameters.properties.get("files")
            if files is not None:
                files.items = types.Schema.model_validate(FILE_ENTRY_SCHEMA)
        return decl


__all__ = [
    "FILE_ENTRY_SCHEMA",
    "WRITE_TOOL_NAMES",
    "WriteFilesTool",
    "delete_project_files",
    "read_project_file",
    "write_project_files",
]
