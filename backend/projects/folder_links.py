"""Which projects each library folder is in — for the documents sidebar.

A folder is "in" a project when any of its documents is tagged into it, and
the link says how it got there: a GitHub source writing into the folder (by
name), a folder source following it (by id, plus its subfolders by name), or
documents added by hand. The sidebar lists them in the folder's ⋯ menu, and
asks before a second "New project from folder" is made by accident.
"""

from __future__ import annotations

from typing import Any

import db.folders as folders_db
import db.projects as projects_db


def _under(name: str, prefix: str) -> bool:
    name, prefix = name.strip().casefold(), prefix.strip().strip("/").casefold()
    return bool(prefix) and (name == prefix or name.startswith(prefix + "/"))


def build_links(
    folders: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
    sources: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """{folderId: [{projectId, projectName, documents, via}]} — pure, for testing.

    ``counts`` is {projectId: {folderId: documents}}. ``via`` is "github",
    "folder" or "documents". A folder a source covers is listed even before
    its first sync has tagged anything into the project.
    """
    names = {str(p["id"]): str(p.get("name") or "") for p in projects}
    by_id = {str(f["id"]): str(f.get("name") or "") for f in folders}
    links: dict[str, dict[str, dict[str, Any]]] = {}

    def link(folder_id: str, project_id: str, via: str) -> None:
        if project_id not in names or folder_id not in by_id:
            return
        row = links.setdefault(folder_id, {}).setdefault(
            project_id,
            {"projectId": project_id, "projectName": names[project_id], "documents": 0, "via": via},
        )
        if row["via"] == "documents":
            row["via"] = via

    for src in sources:
        project_id = str(src.get("projectId") or "")
        if src.get("type") == "folder":
            followed = str(src.get("folderId") or "")
            followed_name = by_id.get(followed, "")
            subfolders = bool(src.get("includeSubfolders", True)) and bool(followed_name)
            for folder_id, name in by_id.items():
                # Same rule as folder_sync: subfolders by name prefix (case-sensitive).
                if folder_id == followed or (subfolders and name.startswith(followed_name + "/")):
                    link(folder_id, project_id, "folder")
        else:
            prefix = str(src.get("folderPrefix") or "")
            for folder_id, name in by_id.items():
                if _under(name, prefix):
                    link(folder_id, project_id, "github")

    for project_id, per_folder in counts.items():
        for folder_id, n in per_folder.items():
            link(folder_id, project_id, "documents")
            if folder_id in links and project_id in links[folder_id]:
                links[folder_id][project_id]["documents"] += n

    return {fid: sorted(rows.values(), key=lambda r: r["projectName"].casefold()) for fid, rows in links.items()}


def folder_links(user_id: str) -> dict[str, list[dict[str, Any]]]:
    from sources import store

    projects = projects_db.list_projects(user_id)
    counts = {str(p["id"]): projects_db.project_folder_counts(user_id, str(p["id"])) for p in projects}
    return build_links(folders_db.list_folders(user_id), projects, counts, store.list_user_sources(user_id))
