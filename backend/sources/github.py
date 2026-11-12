"""Read-only GitHub REST client for git sources (github-sources.md §6).

Three reads, no checkout: resolve a branch to a commit, list the tree under the
source's path at that commit, and download one blob. The same code runs on a
Cloud Run instance and on a host, with no disk state.

The source's path is resolved by walking the tree one segment at a time and then
listing only that subtree, so an entry outside the path is never even returned:
the path guard is structural, not a filter someone could get wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.github.com"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Blob modes git stores for ordinary files; 120000 (symlink) and 160000
# (submodule) are not documents.
_FILE_MODES = {"100644", "100755"}


class GitHubError(Exception):
    """A read GitHub refused or failed. ``code`` is stable for the API and UI."""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class TreeEntry:
    """A file under the source's path. ``path`` is relative to that path."""

    path: str
    sha: str
    size: int


@dataclass(frozen=True)
class RefState:
    commit: str
    etag: str


class GitHubClient:
    """One repository reader bound to a token. Pass ``http`` in tests."""

    def __init__(self, token: str, *, http: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._http = http

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-protocol-platform-sources",
        }

    async def _get(self, url: str, *, accept: str = "application/vnd.github+json", etag: str = "") -> httpx.Response:
        headers = self._headers(accept)
        if etag:
            headers["If-None-Match"] = etag
        try:
            if self._http is not None:
                resp = await self._http.get(url, headers=headers, timeout=_TIMEOUT, follow_redirects=True)
            else:
                async with httpx.AsyncClient() as http:
                    resp = await http.get(url, headers=headers, timeout=_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise GitHubError("GITHUB_UNREACHABLE", f"GitHub could not be reached: {exc}") from exc
        if resp.status_code == 304 or resp.is_success:
            return resp
        raise _error_for(resp)

    async def resolve_ref(self, repo: str, ref: str, *, etag: str = "") -> RefState | None:
        """The commit ``ref`` points at, or None when ``etag`` still matches.

        GitHub does not count a 304 against the rate limit, so a source whose
        branch has not moved costs nothing to check (§7)."""
        resp = await self._get(f"{API}/repos/{repo}/commits/{ref}", accept="application/vnd.github.sha", etag=etag)
        if resp.status_code == 304:
            return None
        commit = resp.text.strip()
        if len(commit) != 40:
            raise GitHubError("GITHUB_BAD_RESPONSE", f"Unexpected answer resolving {ref!r}.")
        return RefState(commit=commit, etag=resp.headers.get("ETag", ""))

    async def tree(self, repo: str, commit: str, path: str) -> list[TreeEntry]:
        """Every file under ``path`` at ``commit``, paths relative to ``path``.

        Raises ``TREE_TRUNCATED`` rather than return part of a tree: the missing
        entries would look like deleted files."""
        data = await self._json(f"{API}/repos/{repo}/git/commits/{commit}")
        tree_sha = str((data.get("tree") or {}).get("sha") or "")
        for segment in [s for s in path.split("/") if s]:
            listing = await self._json(f"{API}/repos/{repo}/git/trees/{tree_sha}")
            match = next(
                (e for e in listing.get("tree") or [] if e.get("type") == "tree" and e.get("path") == segment),
                None,
            )
            if match is None:
                raise GitHubError("SOURCE_PATH_NOT_FOUND", f"No folder {path!r} in {repo} at {commit[:7]}.", 404)
            tree_sha = str(match["sha"])
        listing = await self._json(f"{API}/repos/{repo}/git/trees/{tree_sha}?recursive=1")
        if listing.get("truncated"):
            raise GitHubError(
                "TREE_TRUNCATED",
                f"{repo}/{path or ''} has too many files for one listing. Point the source at a smaller folder.",
                422,
            )
        return [
            TreeEntry(path=str(e["path"]), sha=str(e["sha"]), size=int(e.get("size") or 0))
            for e in listing.get("tree") or []
            if e.get("type") == "blob" and e.get("mode") in _FILE_MODES
        ]

    async def blob(self, repo: str, sha: str) -> bytes:
        resp = await self._get(f"{API}/repos/{repo}/git/blobs/{sha}", accept="application/vnd.github.raw")
        return resp.content

    async def _json(self, url: str) -> dict[str, Any]:
        resp = await self._get(url)
        try:
            return dict(resp.json())
        except ValueError as exc:
            raise GitHubError("GITHUB_BAD_RESPONSE", f"GitHub sent something that is not JSON: {url}") from exc


def _error_for(resp: httpx.Response) -> GitHubError:
    status = resp.status_code
    if status == 401:
        return GitHubError("SOURCE_CREDENTIAL_REJECTED", "GitHub rejected the source's token.", 502)
    if status in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
        return GitHubError("GITHUB_RATE_LIMITED", "GitHub's rate limit is used up. The next run retries.", 503)
    if status in (403, 404):
        # GitHub answers 404 for a private repo the token cannot see, so the two
        # are one message: say what to check, never which it was.
        return GitHubError(
            "SOURCE_NOT_VISIBLE",
            "The repository, branch or folder doesn't exist, or the source's token can't read it.",
            404,
        )
    log.warning("GitHub %s for %s: %s", status, resp.request.url, resp.text[:200])
    return GitHubError("GITHUB_ERROR", f"GitHub answered {status}.", 502)
