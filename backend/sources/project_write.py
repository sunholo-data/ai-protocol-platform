"""The project's agent reads and writes files in its writable GitHub sources
(project-source-write.md §4-§5).

A path is the file's path as the project shows it: ``<folderPrefix>/<path in the
source>``. It goes to the writable source whose folder holds it (longest match),
and only there. Every call re-checks, against the project's owner as they are now:

* the source is ``writable``;
* a write credential (``access: write``) covers its repository for the owner;
* the writer App is installed on the repository (GitHub refuses otherwise).

A write is one commit (``git_write.commit_files``) that refuses to overwrite a
change made on GitHub since the project last synced. The written files are then
stored in the project straight away, with the new blob sha as their provenance,
so the next sync finds them unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from auth.access_context import build_access_context
from sources import credentials, git_write, store
from sources.engine import source_ref
from sources.github import GitHubError, TreeEntry
from sources.project_destination import ProjectDestination
from tools.documents.ingest import SOURCE_TEXT_EXTENSIONS

log = logging.getLogger(__name__)

# Text the agent can write whole. A PDF's parsed text is not its source, so
# binary and parsed formats are never written back (W8).
TEXT_EXTENSIONS = frozenset({".md", ".txt", ".csv", ".html", ".htm", ".tex"} | set(SOURCE_TEXT_EXTENSIONS))
MAX_FILES = 20
MAX_BYTES = 1_000_000
MAX_READ_BYTES = 200_000
COMMITS_PER_HOUR = 30


class WriteRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def writable_sources(uid: str, project_id: str) -> list[dict[str, Any]]:
    """The project's sources the AI may write. None on a deployment without a
    write credential, without reading anything."""
    if not any(c.can_write for c in credentials.load().values()):
        return []
    return [
        s
        for s in store.list_project_sources(uid, project_id)
        if s.get("type") == "github" and s.get("writable") and s.get("writeCredentialRef")
    ]


def folder_of(src: dict[str, Any]) -> str:
    return str(src.get("folderPrefix") or "").strip("/")


def _clean(path: str) -> str:
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p not in ("",)]
    if not parts or any(p in (".", "..") or p != p.strip() for p in parts) or path.startswith("/"):
        raise WriteRefused("SOURCE_WRITE_INVALID", f"{path!r} is not a usable file path.")
    return "/".join(parts)


def locate(sources: list[dict[str, Any]], path: str) -> tuple[dict[str, Any], str]:
    """(source, path within the source) for a project path. Longest folder wins."""
    clean = _clean(path)
    best: tuple[dict[str, Any], str] | None = None
    for src in sources:
        folder = folder_of(src)
        if folder and clean.casefold().startswith(folder.casefold() + "/"):
            if best is None or len(folder) > len(folder_of(best[0])):
                best = (src, clean[len(folder) + 1 :])
    if best is None:
        folders = ", ".join(f"{folder_of(s)}/" for s in sources) or "none"
        raise WriteRefused(
            "SOURCE_WRITE_OUTSIDE",
            f"{clean} is not in a folder this project can write. Writable folders: {folders}.",
        )
    return best


def repo_path(src: dict[str, Any], rel: str) -> str:
    return "/".join(p for p in (str(src.get("path") or "").strip("/"), rel) if p)


def _owner(uid: str) -> Any:
    from auth.firebase_auth import resolve_user_by_uid

    user = resolve_user_by_uid(uid)
    if user is None:
        raise WriteRefused("SOURCE_WRITE_NOT_ALLOWED", "Your access could not be checked. Try again.")
    return user


def _writer(src: dict[str, Any], owner: Any) -> credentials.Credential:
    cred = credentials.get(str(src.get("writeCredentialRef") or ""))
    repo = str(src.get("repo") or "")
    if cred is None or not cred.can_write:
        raise WriteRefused("SOURCE_WRITE_NOT_ALLOWED", f"Writing to {repo} is no longer set up on this deployment.")
    if not cred.usable_by(build_access_context(owner)) or not cred.covers(repo):
        raise WriteRefused("SOURCE_WRITE_NOT_ALLOWED", f"You may no longer write to {repo}.")
    return cred


def _recent_writes(src: dict[str, Any]) -> list[str]:
    cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return [t for t in src.get("writes") or [] if str(t) >= cutoff]


_IDENTITY = re.compile(r"^[^<>\n]+ <[^<>@\s]+@[^<>@\s]+>$")


def coauthor(owner: Any) -> str:
    """Who a commit is co-authored by: ``GITHUB_WRITE_COAUTHOR``.

    Unset or ``person``: the person in the chat. ``0``/``off``: nobody. A fixed
    ``Name <email>`` (e.g. the deployment's own name): that, for every commit, so
    no member's address reaches git. Anything else is ignored (logged) as unset."""
    setting = os.environ.get("GITHUB_WRITE_COAUTHOR", "").strip()
    if setting.lower() in ("0", "off", "none"):
        return ""
    if setting and setting.lower() != "person":
        if _IDENTITY.match(setting):
            return setting
        log.warning("GITHUB_WRITE_COAUTHOR is not 'Name <email>', 'person' or '0'; using the person")
    if not owner.email:
        return ""
    name = (owner.display_name or owner.email.split("@", 1)[0]).replace("<", "").replace(">", "")
    return f"{name} <{owner.email}>"


def _trailers(owner: Any, project_id: str, session_id: str) -> str:
    lines = []
    if who := coauthor(owner):
        lines.append(f"Co-authored-by: {who}")
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        lines.append(f"Platform-Project: {base}/projects/{project_id}")
    if session_id:
        lines.append(f"Platform-Session: {session_id}")
    return "\n".join(lines)


async def read_file(uid: str, project_id: str, path: str) -> dict[str, Any]:
    """The exact current text of a file in a writable source, from GitHub."""
    sources = await asyncio.to_thread(writable_sources, uid, project_id)
    if not sources:
        raise WriteRefused("SOURCE_WRITE_NONE", "This project has no folders the AI may write to.")
    src, rel = locate(sources, path)
    owner = await asyncio.to_thread(_owner, uid)
    cred = _writer(src, owner)
    repo, branch = str(src["repo"]), str(src["ref"])
    try:
        token = await credentials.write_token(cred, repo)
        found = await git_write.read_file(token, repo, branch, repo_path(src, rel))
    except GitHubError as exc:
        raise WriteRefused(exc.code, exc.message) from exc
    project_path = f"{folder_of(src)}/{rel}"
    if found is None:
        return {"status": "not_found", "path": project_path, "message": "No such file yet; writing it creates it."}
    data, sha = found
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise WriteRefused("SOURCE_WRITE_INVALID", f"{project_path} is not a text file.") from None
    truncated = len(data) > MAX_READ_BYTES
    return {
        "status": "ok",
        "path": project_path,
        "content": text[:MAX_READ_BYTES],
        "truncated": truncated,
        "version": sha,
        "url": f"https://github.com/{repo}/blob/{branch}/{repo_path(src, rel)}",
    }


async def write_files(
    uid: str, project_id: str, files: list[dict[str, Any]], message: str, *, session_id: str = ""
) -> dict[str, Any]:
    """Commit whole text files to one writable source and store them in the project."""
    message = (message or "").strip()
    if not message:
        raise WriteRefused("SOURCE_WRITE_INVALID", "Give a commit message saying what changed and why.")
    if not files:
        raise WriteRefused("SOURCE_WRITE_INVALID", "Give at least one file: {path, content}.")
    if len(files) > MAX_FILES:
        raise WriteRefused("SOURCE_WRITE_INVALID", f"At most {MAX_FILES} files in one commit.")
    sources = await asyncio.to_thread(writable_sources, uid, project_id)
    if not sources:
        raise WriteRefused("SOURCE_WRITE_NONE", "This project has no folders the AI may write to.")

    src: dict[str, Any] | None = None
    contents: dict[str, bytes] = {}
    versions: dict[str, str] = {}
    for item in files:
        where, rel = locate(sources, str(item.get("path") or ""))
        if src is not None and where["id"] != src["id"]:
            raise WriteRefused("SOURCE_WRITE_INVALID", "One commit writes to one folder. Split the files by folder.")
        src = where
        ext = PurePosixPath(rel).suffix.lower()
        if ext not in TEXT_EXTENSIONS:
            raise WriteRefused("SOURCE_WRITE_TYPE", f"I can only write text files; {rel} is {ext or 'without a type'}.")
        content = item.get("content")
        if not isinstance(content, str):
            raise WriteRefused("SOURCE_WRITE_INVALID", f"Give the whole new text of {rel} as content.")
        if rel in contents:
            raise WriteRefused(
                "SOURCE_WRITE_INVALID", f"{rel} is given twice. Give each file once, with its whole new content."
            )
        contents[rel] = content.encode("utf-8")
        version = str(item.get("version") or "").strip().lower()
        if version:
            if len(version) != 40 or any(c not in "0123456789abcdef" for c in version):
                raise WriteRefused("SOURCE_WRITE_INVALID", f"The version of {rel} is the one read_project_file gave.")
            versions[rel] = version
    assert src is not None
    if sum(len(b) for b in contents.values()) > MAX_BYTES:
        raise WriteRefused("SOURCE_WRITE_INVALID", f"At most {MAX_BYTES // 1000} KB in one commit.")
    if len(_recent_writes(src)) >= COMMITS_PER_HOUR:
        raise WriteRefused(
            "SOURCE_WRITE_LIMIT", f"{COMMITS_PER_HOUR} commits in the last hour to this folder. Wait a while."
        )

    owner = await asyncio.to_thread(_owner, uid)
    cred = _writer(src, owner)
    repo, branch = str(src["repo"]), str(src["ref"])
    manifest = await asyncio.to_thread(store.manifest, src["id"])
    by_repo_path = {repo_path(src, rel): data for rel, data in contents.items()}
    # What the agent's copy came from: the version it read, else what the project holds.
    expected = {
        repo_path(src, rel): versions.get(rel) or (manifest.get(rel) or {}).get("blobSha") or None for rel in contents
    }
    full_message = f"{message}\n\n{_trailers(owner, project_id, session_id)}".strip()
    started = time.monotonic()
    try:
        token = await credentials.write_token(cred, repo)
        done = await git_write.commit_files(token, repo, branch, by_repo_path, expected, full_message)
    except GitHubError as exc:
        if exc.code == "SOURCE_WRITE_STALE":
            # Bring the project up to date so a re-read sees what's on GitHub.
            _sync_soon(src, owner)
        raise WriteRefused(exc.code, exc.message) from exc
    log.info(
        "project %s: committed %d file(s) to %s@%s as %s in %.1fs",
        project_id,
        len(contents),
        repo,
        branch,
        done.commit[:8],
        time.monotonic() - started,
    )
    stored = await _store(src, owner, project_id, contents, done)
    writes = [*_recent_writes(src), store.now_iso()]
    fields: dict[str, Any] = {"writes": writes}
    if str(src.get("syncedCommit") or "") == done.parent:
        fields.update(syncedCommit=done.commit, syncedAt=store.now_iso())
    await asyncio.to_thread(store.update_source, src["id"], fields)
    return {
        "status": "committed",
        "commit": done.commit[:12],
        "commit_url": done.url,
        "repo": repo,
        "branch": branch,
        "files": stored,
    }


async def _store(
    src: dict[str, Any], owner: Any, project_id: str, contents: dict[str, bytes], done: git_write.Committed
) -> list[dict[str, Any]]:
    dest = ProjectDestination(owner, project_id, folder_of(src))
    out = []
    for rel, data in sorted(contents.items()):
        sha = done.blobs[repo_path(src, rel)]
        entry = TreeEntry(path=rel, sha=sha, size=len(data))
        result = await dest.put(rel, data, source_ref(src, done.commit, entry))
        if result.outcome != "failed":
            await asyncio.to_thread(
                store.put_file,
                src["id"],
                rel,
                blob_sha=sha,
                doc_id=result.doc_id,
                parse_status=result.parse_status,
                parse_error=result.error,
            )
        else:
            log.warning("project %s: committed %s but could not store it: %s", project_id, rel, result.error)
        out.append(
            {
                "path": f"{folder_of(src)}/{rel}",
                "outcome": "added" if result.outcome == "added" else result.outcome,
                **({"error": result.error} if result.outcome == "failed" else {}),
            }
        )
    return out


def _sync_soon(src: dict[str, Any], owner: Any) -> None:
    """Start a sync of ``src`` in the background (best effort; the schedule catches up anyway)."""

    async def run() -> None:
        from sources.engine import DEFAULT_BUDGET_SECONDS, run_sync

        try:
            cred = credentials.get(str(src.get("credentialRef") or ""))
            if cred is None:
                return
            from sources.github import GitHubClient

            client = GitHubClient(await credentials.token(cred, str(src["repo"])))
            await run_sync(
                src,
                client=client,
                destination=ProjectDestination(owner, str(src["projectId"]), folder_of(src)),
                budget_seconds=DEFAULT_BUDGET_SECONDS,
            )
        except Exception as exc:  # the schedule will sync it later
            log.warning("source %s: sync after a stale write failed: %s", src.get("id"), exc)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set[asyncio.Task[None]] = set()
