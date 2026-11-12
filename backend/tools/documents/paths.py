"""Library paths: a document's ``folder/filename``, and opening one by path.

A synced repository, a folder upload, a Markdown wiki or an Open Knowledge
Format bundle keeps its meaning in its paths: a file's path is its identity and
documents link to each other by path (``[customers](/tables/customers.md)``).
The library stores a document as a folder (whose name IS the relative folder
path, see ``sources/project_destination.py``) plus a filename, so the path is
``<folder name>/<filename>``. Without it the agent saw ``index.md`` once per
folder with nothing to tell them apart, and had no way to follow a link.

Resolution is forgiving on purpose, because a link is written relative to
something the agent cannot see (the bundle's root, the linking file's folder):
an exact path wins, then a unique path that ends with the link's path, then
the same with ``.md`` added. Several matches are listed, never guessed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_MAX_MATCHES_SHOWN = 10
_lock = threading.Lock()
_folder_cache: dict[str, tuple[float, dict[str, str]]] = {}
_project_cache: dict[tuple[str, str], tuple[float, list[tuple[str, str]]]] = {}


def folder_names(user_id: str) -> dict[str, str]:
    """``{folder id: folder name}`` for the user's own (non-system) folders."""
    now = time.monotonic()
    with _lock:
        hit = _folder_cache.get(user_id)
        if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    import db.folders as folders_db

    names = {
        str(f["id"]): str(f.get("name") or "").strip("/")
        for f in folders_db.list_folders(user_id)
        if f.get("id") and not f.get("system") and str(f.get("name") or "").strip("/")
    }
    with _lock:
        _folder_cache[user_id] = (now, names)
    return names


def join_path(folder_name: str, filename: str) -> str:
    return f"{folder_name}/{filename}" if folder_name else filename


def path_of(doc: dict[str, Any], names: dict[str, str]) -> str:
    """The document's library path; its bare filename when it sits in no named folder."""
    filename = str(doc.get("originalFilename") or doc.get("id") or doc.get("__id") or "")
    return join_path(names.get(str(doc.get("folderId") or ""), ""), filename)


def document_path(raw: dict[str, Any]) -> str:
    """Best-effort path for one stored document record ("" on failure)."""
    uid = str(raw.get("userId") or "")
    if not uid or not raw.get("folderId"):
        return ""
    try:
        return path_of(raw, folder_names(uid))
    except Exception as exc:
        log.warning("document_path: could not read folders for %s: %s", uid, exc)
        return ""


def project_paths(user_id: str, project_id: str) -> list[tuple[str, str]]:
    """``[(doc id, path)]`` for every document in the project (cached briefly)."""
    key = (user_id, project_id)
    now = time.monotonic()
    with _lock:
        hit = _project_cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    import db.projects as projects_db

    names = folder_names(user_id)
    rows = [
        (doc_id, join_path(names.get(folder_id, ""), filename))
        for doc_id, folder_id, filename in projects_db.project_document_locations(user_id, project_id)
    ]
    with _lock:
        _project_cache[key] = (now, rows)
    return rows


def normalize(path: str) -> str:
    """A link or path as the library spells it: no leading ``/``, ``./``,
    ``../``, ``#anchor`` or ``?query``, forward slashes only."""
    text = (path or "").strip().replace("\\", "/")
    for sep in ("#", "?"):
        text = text.split(sep, 1)[0]
    return "/".join(p for p in text.split("/") if p not in ("", ".", ".."))


@dataclass
class Resolved:
    doc_id: str = ""
    path: str = ""
    matches: list[str] = field(default_factory=list)


def match(path: str, candidates: list[tuple[str, str]]) -> Resolved:
    """Pick the document ``path`` names among ``[(doc id, path)]``."""
    want = normalize(path).casefold()
    if not want:
        return Resolved()
    folded = [(doc_id, p, p.casefold()) for doc_id, p in candidates]
    tries = [want] if PurePosixPath(want).suffix else [want, f"{want}.md"]
    for target in tries:
        exact = [(d, p) for d, p, f in folded if f == target]
        if exact:
            return Resolved(exact[0][0], exact[0][1])
        ends = [(d, p) for d, p, f in folded if f.endswith("/" + target)]
        if len(ends) == 1:
            return Resolved(ends[0][0], ends[0][1])
        if ends:
            return Resolved(matches=sorted(p for _, p in ends))
    return Resolved()


def library_candidates(user_id: str, path: str) -> list[tuple[str, str]]:
    """Outside a project: the user's documents whose folder matches the path's
    folder part exactly or as a suffix. Needs a folder: a bare filename would
    mean scanning the whole library."""
    from db.firestore import query_documents

    want = normalize(path)
    folder_part = str(PurePosixPath(want).parent) if "/" in want else ""
    if not folder_part:
        return []
    filename = PurePosixPath(want).name
    wanted = folder_part.casefold()
    names = folder_names(user_id)
    folders = [
        (fid, name)
        for fid, name in names.items()
        if name.casefold() == wanted or name.casefold().endswith("/" + wanted)
    ][:_MAX_MATCHES_SHOWN]
    out: list[tuple[str, str]] = []
    for fid, name in folders:
        for fname in [filename] if PurePosixPath(filename).suffix else [filename, f"{filename}.md"]:
            rows = query_documents(
                "parsed_documents",
                filters=[("userId", "==", user_id), ("folderId", "==", fid), ("originalFilename", "==", fname)],
                limit=1,
            )
            out.extend((str(r["__id"]), join_path(name, fname)) for r in rows)
    return out


def resolve(user_id: str, path: str, project_id: str = "") -> Resolved:
    """Find the caller's document at ``path``: within the project when the chat
    is in one (the project rule), else in the user's library folders."""
    if project_id:
        return match(path, project_paths(user_id, project_id))
    return match(path, library_candidates(user_id, path))


def not_found_message(path: str, found: Resolved, *, in_project: bool) -> str:
    if found.matches:
        shown = found.matches[:_MAX_MATCHES_SHOWN]
        more = len(found.matches) - len(shown)
        listing = "\n".join(f"- {p}" for p in shown) + (f"\n- … and {more} more" if more > 0 else "")
        return (
            f"Several documents match the path '{path}':\n{listing}\n"
            "Call get_document_content again with the full path of the one you mean. For a link inside a "
            "document, resolve it against that document's own Path first."
        )
    where = "this project" if in_project else "the user's library folders"
    return (
        f"No document at the path '{path}' in {where}. Paths are '<folder>/<file>' as list_documents "
        "shows them; a link inside a document is relative to that document's Path. Check the path, or "
        "use list_documents or search to find it."
    )


def wiki_maps(paths: list[str], limit: int = 8) -> list[str]:
    """The top ``index.md`` files: each one maps its folder (Open Knowledge
    Format, LLM wikis, many docs sites). An index under a folder that already
    has one is a sub-map, reachable from it, so it is left out."""
    indexes = sorted(
        (p for p in paths if PurePosixPath(p).name.casefold() == "index.md"),
        key=lambda p: (p.count("/"), p.casefold()),
    )
    top: list[str] = []
    for p in indexes:
        folder = str(PurePosixPath(p).parent).casefold()
        covered = any(
            folder == (parent := str(PurePosixPath(t).parent).casefold()) or folder.startswith(parent + "/")
            for t in top
            if str(PurePosixPath(t).parent) != "."
        )
        if not covered:
            top.append(p)
    return sorted(top, key=str.casefold)[:limit]


def _reset_caches() -> None:
    """Tests only."""
    with _lock:
        _folder_cache.clear()
        _project_cache.clear()
