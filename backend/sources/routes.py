"""/api/projects/{id}/sources — keep a project in step with a GitHub folder
(github-sources.md §4, §7, §8).

Owner-only, like every project route: another user's project or source id is a
404, never a 403, and errors use the ``{code, message}`` shape.

A sync runs inside its request, within a time budget (§5). That is what works on
Cloud Run, where CPU stops once a response is sent: a background task would
crawl. A run that reaches its budget reports ``partial`` with the files still to
go, and the next call carries on from the manifest.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

import db.folders as folders_db
import db.projects as projects_db
from auth import User, get_current_user
from auth.access_context import build_access_context
from extensions import project_search_status
from sources import credentials, store
from sources.engine import DEFAULT_BUDGET_SECONDS, run_sync
from sources.folder_sync import sync_folder
from sources.github import GitHubClient, GitHubError
from sources.project_destination import ProjectDestination

log = logging.getLogger(__name__)

router = APIRouter(tags=["project-sources"])

_CurrentUser = Annotated[User, Depends(get_current_user)]

_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_MAX_PATTERNS = 20
_MAX_PATH = 200

# What a source shows its owner. The owner's uid and email, the lease and the
# ETag are bookkeeping.
_VISIBLE = (
    "projectId",
    "type",
    "repo",
    "ref",
    "path",
    "include",
    "exclude",
    "folderPrefix",
    "credentialRef",
    "status",
    "syncedCommit",
    "syncedAt",
    "lastReport",
    "createdAt",
    "updatedAt",
    "folderId",
    "folderName",
    "includeSubfolders",
    "progress",
    "runStartedAt",
    "writable",
)


class CreateSourceRequest(BaseModel):
    # "github": a folder of a repository. "folder": one of your library folders
    # (§10) — then only folder_id and include_subfolders apply.
    type: str = "github"
    repo: str = ""
    folder_id: str = ""
    include_subfolders: bool = True
    ref: str = "main"
    path: str = ""
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    folder_prefix: str = ""
    credential_ref: str = ""


class UpdateSourceRequest(BaseModel):
    # Let the project's agent commit to this source (project-source-write.md §4).
    writable: bool | None = None


class SyncRequest(BaseModel):
    dry_run: bool = False
    confirm_removals: bool = False
    force: bool = False


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _not_found() -> HTTPException:
    return _error(404, "SOURCE_NOT_FOUND", "Source not found.")


def _require_project(user: User, project_id: str) -> dict[str, Any]:
    project = projects_db.get_project(user.uid, project_id)
    if project is None:
        raise _error(404, "PROJECT_NOT_FOUND", "Project not found.")
    return project


def _require_source(user: User, project_id: str, source_id: str) -> dict[str, Any]:
    _require_project(user, project_id)
    src = store.owned_source(user.uid, project_id, source_id)
    if src is None:
        raise _not_found()
    return src


def _view(src: dict[str, Any]) -> dict[str, Any]:
    out = {"id": src["id"], **{k: src[k] for k in _VISIBLE if k in src}}
    # A run that died (instance stopped mid-sync) leaves "running" behind; once
    # its lease has expired it isn't running, and the next sync carries on.
    if out.get("status") == "running" and str(src.get("leaseUntil") or "") < store.now_iso():
        out["status"] = "partial"
        out.pop("progress", None)
    return out


def clean_path(raw: str) -> str:
    parts = [p.strip() for p in (raw or "").replace("\\", "/").split("/") if p.strip()]
    if any(p in (".", "..") for p in parts):
        raise _error(422, "SOURCE_PATH_INVALID", "A source path must not contain '.' or '..'.")
    path = "/".join(parts)
    if len(path) > _MAX_PATH:
        raise _error(422, "SOURCE_PATH_INVALID", f"A source path must be at most {_MAX_PATH} characters.")
    return path


def _clean_patterns(patterns: list[str], name: str) -> list[str]:
    cleaned = [p.strip() for p in patterns if p and p.strip()]
    if len(cleaned) > _MAX_PATTERNS or any(len(p) > _MAX_PATH for p in cleaned):
        raise _error(
            422, "SOURCE_PATTERNS_INVALID", f"At most {_MAX_PATTERNS} {name} patterns, each under 200 characters."
        )
    return cleaned


def _pick_credential(user: User, requested: str, repo: str) -> credentials.Credential:
    """The credential this person may use to read ``repo``: the one they named, or
    the only one of theirs that covers it (§16). Nobody can borrow a credential
    they aren't allowed, or read a repository it doesn't cover."""
    mine = credentials.offered(build_access_context(user))
    if not mine:
        raise _error(
            403,
            "NO_SOURCE_CREDENTIALS",
            "You don't have GitHub access on this deployment. An admin grants it per repository.",
        )
    covering = [c for c in mine if c.covers(repo)]
    wanted = (requested or "").strip().lower()
    if wanted:
        chosen = next((c for c in mine if c.ref == wanted), None)
        if chosen is None:
            raise _error(403, "SOURCE_CREDENTIAL_NOT_ALLOWED", f"You can't use the GitHub access {wanted!r}.")
        if not chosen.covers(repo):
            raise _error(403, "SOURCE_REPO_NOT_COVERED", f"The {wanted!r} access doesn't include {repo}.")
        return chosen
    if not covering:
        reach = ", ".join(sorted({r for c in mine for r in c.repos}))
        raise _error(
            403, "SOURCE_REPO_NOT_COVERED", f"Your GitHub access doesn't include {repo}. You can sync: {reach}."
        )
    return covering[0]


async def client_for(src: dict[str, Any], owner: User) -> GitHubClient:
    """A reader for an existing source, re-checking that its owner may still use
    its credential on its repository — access can be withdrawn after a source
    was added, and the next sync must notice."""
    cred = credentials.get(str(src.get("credentialRef") or ""))
    repo = str(src.get("repo") or "")
    if cred is None:
        raise _error(
            409,
            "SOURCE_CREDENTIAL_MISSING",
            f"The GitHub access {src.get('credentialRef')!r} is no longer configured on this deployment.",
        )
    if not cred.usable_by(build_access_context(owner)) or not cred.covers(repo):
        raise _error(
            403,
            "SOURCE_CREDENTIAL_NOT_ALLOWED",
            f"This source's owner may no longer read {repo} through {cred.ref!r}.",
        )
    try:
        return GitHubClient(await credentials.token(cred, repo))
    except GitHubError as exc:
        raise _error(exc.status, exc.code, exc.message) from exc


@router.get("/api/sources/synced-folders")
def synced_folders(user: _CurrentUser) -> dict[str, Any]:
    """The library folder prefixes the caller's GitHub sources write into.

    The documents sidebar marks these folders and offers no rename for them.
    """
    from sources.synced_folders import synced_prefixes

    return {"synced": synced_prefixes(user.uid)}


async def write_access(user: User, src: dict[str, Any]) -> dict[str, Any] | None:
    """Whether the AI could be allowed to write ``src``, for its owner's page.

    None when no write credential covers the repository for this person (nothing
    to offer). Otherwise ``canWrite``, and when the writer App isn't installed on
    the repository yet, ``installUrl``: where an owner of the GitHub account
    installs it or adds the repository (project-source-write.md §7)."""
    if src.get("type") != "github":
        return None
    repo = str(src.get("repo") or "")
    cred = credentials.writer_for(build_access_context(user), repo)
    if cred is None:
        return None
    from sources import github_app

    try:
        installed = await github_app.installed_on(cred, repo)
        url = "" if installed else await github_app.install_url(cred)
    except GitHubError as exc:
        log.warning("source %s: could not check the writer App on %s: %s", src.get("id"), repo, exc.message)
        return {"canWrite": False, "problem": exc.message}
    return {"canWrite": installed, **({"installUrl": url} if url else {})}


@router.get("/api/projects/{project_id}/sources")
async def list_sources(project_id: str, user: _CurrentUser) -> dict[str, Any]:
    await asyncio.to_thread(_require_project, user, project_id)
    rows = await asyncio.to_thread(store.list_project_sources, user.uid, project_id)
    views = []
    for src in rows:
        view = _view(src)
        if (write := await write_access(user, src)) is not None:
            view["write"] = write
        views.append(view)
    return {
        "sources": views,
        # Only what this person may use, with the repositories each one reaches.
        "credentials": [c.describe() for c in credentials.offered(build_access_context(user))],
        # Where this person could let the AI write (the writer App still decides per repository).
        "writers": [c.describe() for c in credentials.writers(build_access_context(user))],
    }


@router.post("/api/projects/{project_id}/sources", status_code=201)
async def create_source(project_id: str, body: CreateSourceRequest, user: _CurrentUser) -> dict[str, Any]:
    _require_project(user, project_id)
    existing = store.list_project_sources(user.uid, project_id)
    if len(existing) >= store.MAX_SOURCES_PER_PROJECT:
        raise _error(409, "TOO_MANY_SOURCES", f"A project can have at most {store.MAX_SOURCES_PER_PROJECT} sources.")
    if body.type == "folder":
        return _create_folder_source(user, project_id, body, existing)
    if body.type != "github":
        raise _error(422, "SOURCE_TYPE_INVALID", "A source is a GitHub folder or one of your library folders.")
    repo = body.repo.strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if not _REPO.match(repo):
        raise _error(422, "SOURCE_REPO_INVALID", "Give the repository as owner/name, e.g. example-org/docs.")
    ref = body.ref.strip()
    if not _REF.match(ref) or ".." in ref:
        raise _error(422, "SOURCE_REF_INVALID", "Give a branch name, e.g. main.")
    path = clean_path(body.path)
    prefix = clean_path(body.folder_prefix) or (path.rsplit("/", 1)[-1] if path else repo.split("/", 1)[1])
    include = _clean_patterns(body.include, "include")
    exclude = _clean_patterns(body.exclude, "exclude")
    cred = _pick_credential(user, body.credential_ref, repo)

    # Two sources writing one folder would each try to own the other's files.
    taken = {str(s.get("folderPrefix") or "").casefold() for s in existing if s.get("type") == "github"}
    if prefix.casefold() in taken:
        raise _error(409, "SOURCE_FOLDER_TAKEN", f"Another source of this project already writes to {prefix!r}.")

    # Prove the credential can read the folder now, rather than on the first sync.
    try:
        client = GitHubClient(await credentials.token(cred, repo))
        state = await client.resolve_ref(repo, ref)
        assert state is not None
        await client.tree(repo, state.commit, path)
    except GitHubError as exc:
        raise _error(422 if exc.status < 500 else exc.status, exc.code, exc.message) from exc

    src = store.create_source(
        {
            "uid": user.uid,
            "ownerEmail": user.email,
            "ownerDomain": user.domain,
            "projectId": project_id,
            "type": "github",
            "repo": repo,
            "ref": ref,
            "path": path,
            "include": include,
            "exclude": exclude,
            "folderPrefix": prefix,
            "credentialRef": cred.ref,
        }
    )
    log.info("project %s: added source %s (%s@%s:%s)", project_id, src["id"], repo, ref, path)
    return _view(src)


def _create_folder_source(
    user: User, project_id: str, body: CreateSourceRequest, existing: list[dict[str, Any]]
) -> dict[str, Any]:
    folder = folders_db.get_owned_folder(user.uid, body.folder_id.strip())
    if folder is None:
        raise _error(404, "FOLDER_NOT_FOUND", "Folder not found.")
    if any(s.get("folderId") == folder["id"] for s in existing):
        raise _error(409, "SOURCE_FOLDER_TAKEN", "This project already follows that folder.")
    src = store.create_source(
        {
            "uid": user.uid,
            "ownerEmail": user.email,
            "ownerDomain": user.domain,
            "projectId": project_id,
            "type": "folder",
            "folderId": folder["id"],
            "folderName": str(folder.get("name") or ""),
            "includeSubfolders": body.include_subfolders,
        }
    )
    log.info("project %s: added folder source %s (%s)", project_id, src["id"], folder["id"])
    return _view(src)


@router.get("/api/projects/{project_id}/sources/{source_id}")
def get_source(project_id: str, source_id: str, user: _CurrentUser) -> dict[str, Any]:
    return _view(_require_source(user, project_id, source_id))


@router.patch("/api/projects/{project_id}/sources/{source_id}")
async def update_source(
    project_id: str, source_id: str, body: UpdateSourceRequest, user: _CurrentUser
) -> dict[str, Any]:
    """Turn the AI's write access to this source on or off."""
    src = await asyncio.to_thread(_require_source, user, project_id, source_id)
    if body.writable is None:
        return _view(src)
    if not body.writable:
        store.update_source(source_id, {"writable": False, "writeCredentialRef": ""})
    else:
        if src.get("type") != "github":
            raise _error(422, "SOURCE_WRITE_NOT_ALLOWED", "Only a GitHub source can be written.")
        repo = str(src.get("repo") or "")
        cred = credentials.writer_for(build_access_context(user), repo)
        if cred is None:
            raise _error(403, "SOURCE_WRITE_NOT_ALLOWED", f"Writing to {repo} isn't set up for you.")
        access = await write_access(user, src)
        if not (access or {}).get("canWrite"):
            raise _error(
                409,
                "SOURCE_WRITE_NOT_INSTALLED",
                (access or {}).get("problem") or f"The writer GitHub App isn't installed on {repo} yet.",
            )
        store.update_source(source_id, {"writable": True, "writeCredentialRef": cred.ref})
    log.info("project %s: source %s writable=%s", project_id, source_id, body.writable)
    from adk.project_context import clear_cache

    clear_cache(user.uid)  # the project block names the writable folders
    fresh = store.get_source(source_id) or src
    view = _view(fresh)
    if (write := await write_access(user, fresh)) is not None:
        view["write"] = write
    return view


@router.delete("/api/projects/{project_id}/sources/{source_id}", status_code=204)
def delete_source(project_id: str, source_id: str, user: _CurrentUser) -> Response:
    """Stop syncing. The documents stay in the library and the project."""
    _require_source(user, project_id, source_id)
    store.delete_source(source_id)
    return Response(status_code=204)


@router.post("/api/projects/{project_id}/sources/{source_id}/sync")
async def sync_source(project_id: str, source_id: str, body: SyncRequest, user: _CurrentUser) -> dict[str, Any]:
    src = _require_source(user, project_id, source_id)
    if src.get("type") == "folder":
        report = sync_folder(src, dry_run=body.dry_run)
        report["projectSearch"] = project_search_status(user.uid, project_id)
        return report
    report = await run_sync(
        src,
        client=await client_for(src, user),
        destination=ProjectDestination(user, project_id, str(src.get("folderPrefix") or "")),
        dry_run=body.dry_run,
        confirm_removals=body.confirm_removals,
        force=body.force,
        budget_seconds=DEFAULT_BUDGET_SECONDS,
    )
    if report.get("status") == "busy":
        raise _error(409, "SOURCE_SYNC_RUNNING", report.get("message") or "A sync is already running.")
    # Indexed means searchable: the project's own search index, not the upload.
    search = project_search_status(user.uid, project_id)
    report["projectSearch"] = search
    report["indexFailed"] = _index_failures(source_id, search)
    return report


def _index_failures(source_id: str, search: dict[str, Any] | None) -> list[dict[str, str]]:
    """This source's files the project index refused for good, by path (§8)."""
    failed = {str(d.get("docId")): str(d.get("error") or "") for d in (search or {}).get("failedDocuments") or []}
    if not failed:
        return []
    return [
        {"path": path, "error": failed[str(row.get("docId"))]}
        for path, row in sorted(store.manifest(source_id).items())
        if str(row.get("docId")) in failed
    ]
