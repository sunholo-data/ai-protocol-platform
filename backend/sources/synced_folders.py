"""Library folders a GitHub source writes into (github-sources.md §4).

A GitHub source places each file by folder NAME: its folder prefix plus the
file's directory (``docs/design/v1``). Renaming such a folder breaks that
link: the next sync recreates the old name and adds files there again, beside
the renamed copy. So a synced folder, anything under it, and any folder above
it can't be renamed; the folder rename route asks :func:`covering_source`.
"""

from __future__ import annotations

from typing import Any

import db.projects as projects_db
from sources import store


def synced_prefixes(uid: str) -> list[dict[str, Any]]:
    """The folder prefixes the user's GitHub sources write into, with their project."""
    out: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for src in store.list_user_sources(uid):
        if src.get("type", "github") != "github":
            continue
        prefix = str(src.get("folderPrefix") or "").strip().strip("/")
        if not prefix:
            continue
        project_id = str(src.get("projectId") or "")
        if project_id not in names:
            project = projects_db.get_project(uid, project_id) if project_id else None
            names[project_id] = str((project or {}).get("name") or "a project")
        out.append(
            {
                "prefix": prefix,
                "projectId": project_id,
                "projectName": names[project_id],
                "repo": str(src.get("repo") or ""),
                "sourceId": str(src.get("id") or ""),
            }
        )
    return out


def covering_source(prefixes: list[dict[str, Any]], folder_name: str) -> dict[str, Any] | None:
    """The source whose folders `folder_name` is, is inside, or contains — else None."""
    name = (folder_name or "").strip().casefold()
    if not name:
        return None
    for entry in prefixes:
        prefix = str(entry["prefix"]).casefold()
        if name == prefix or name.startswith(prefix + "/") or prefix.startswith(name + "/"):
            return entry
    return None
