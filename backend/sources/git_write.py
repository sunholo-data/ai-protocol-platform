"""One commit of whole text files to a branch, without losing anyone's update
(project-source-write.md §5).

Through the Git Data API, so several files land as ONE commit and nothing is
force-pushed:

1. read the branch head H;
2. every file being changed must still be the blob the caller last saw
   (``expected``: path → blob sha), and every new file must not exist yet —
   otherwise ``SOURCE_WRITE_STALE``: someone else changed it, read it again;
3. a tree on H's tree with the new contents, then a commit with parent H;
4. move the branch to it with ``force: false``. If the branch moved meanwhile,
   GitHub refuses; start again from 1 once, then give up as stale.

The commit is authored by whatever the token belongs to: for a GitHub App
installation token, the App's bot, which GitHub shows as verified.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from sources.github import API, GitHubError

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True)
class Committed:
    commit: str
    parent: str
    blobs: dict[str, str]  # repo path → new blob sha
    url: str


def blob_sha(data: bytes) -> str:
    """Git's id for a blob with these bytes (what GitHub will report for it)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # git's object id, not a security hash


class _Api:
    def __init__(self, token: str, http: httpx.AsyncClient | None) -> None:
        self._token = token
        self._http = http

    async def call(self, method: str, path: str, *, json: Any = None, allow: tuple[int, ...] = ()) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-protocol-platform-sources",
        }
        url = f"{API}{path}"
        try:
            if self._http is not None:
                resp = await self._http.request(method, url, headers=headers, json=json, timeout=_TIMEOUT)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(method, url, headers=headers, json=json, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GitHubError("GITHUB_UNREACHABLE", f"GitHub could not be reached: {exc}") from exc
        if resp.is_success or resp.status_code in allow:
            return resp
        raise _error(resp)


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message") or "")
    except ValueError:
        return ""


def _error(resp: httpx.Response) -> GitHubError:
    status, text = resp.status_code, _message(resp)
    lowered = text.lower()
    protected = ("protected branch", "branch protection", "rule violation")
    if status in (403, 409, 422) and any(p in lowered for p in protected):
        return GitHubError("SOURCE_WRITE_PROTECTED", f"The branch is protected against this App: {text}", 403)
    if status in (401, 403, 404):
        return GitHubError(
            "SOURCE_WRITE_DENIED",
            "The writer GitHub App can't write this repository. Install it on the repository (Contents: read and write).",
            403,
        )
    return GitHubError("GITHUB_ERROR", f"GitHub answered {status}: {text[:200]}", 502)


async def _blob_at(api: _Api, repo: str, ref: str, path: str) -> str | None:
    """The blob sha of ``path`` at ``ref``; None when there is no file there."""
    resp = await api.call("GET", f"/repos/{repo}/contents/{quote(path)}?ref={quote(ref)}", allow=(404,))
    if resp.status_code == 404:
        return None
    data = resp.json()
    if isinstance(data, list) or data.get("type") != "file":
        raise GitHubError("SOURCE_WRITE_INVALID", f"{path} is a folder in the repository, not a file.", 422)
    return str(data.get("sha") or "")


async def commit_files(
    token: str,
    repo: str,
    branch: str,
    files: dict[str, bytes | None],
    expected: dict[str, str | None],
    message: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> Committed:
    """Commit ``files`` (repo path → bytes, or None to delete it) to ``branch``
    as one commit.

    ``expected`` gives, per path, the blob sha the caller's copy came from, or
    None for a file that should not exist yet. A file is only deleted as the
    caller last saw it. Raises ``GitHubError``."""
    api = _Api(token, http)
    ref_path = f"/repos/{repo}/git/refs/heads/{quote(branch)}"
    for attempt in range(2):
        head = str((await api.call("GET", ref_path)).json()["object"]["sha"])
        stale = []
        for path in files:
            current = await _blob_at(api, repo, head, path)
            if current != expected.get(path):
                stale.append(path)
        if stale:
            raise GitHubError(
                "SOURCE_WRITE_STALE",
                "Changed on GitHub since the project last synced: " + ", ".join(sorted(stale)),
                409,
            )
        base_tree = str((await api.call("GET", f"/repos/{repo}/git/commits/{head}")).json()["tree"]["sha"])
        entries = [
            {"path": path, "mode": "100644", "type": "blob", "sha": None}
            if data is None
            else {"path": path, "mode": "100644", "type": "blob", "content": data.decode("utf-8")}
            for path, data in sorted(files.items())
        ]
        tree = str(
            (await api.call("POST", f"/repos/{repo}/git/trees", json={"base_tree": base_tree, "tree": entries})).json()[
                "sha"
            ]
        )
        commit = str(
            (
                await api.call(
                    "POST", f"/repos/{repo}/git/commits", json={"message": message, "tree": tree, "parents": [head]}
                )
            ).json()["sha"]
        )
        moved = await api.call("PATCH", ref_path, json={"sha": commit, "force": False}, allow=(422,))
        if moved.status_code == 422:
            text = _message(moved).lower()
            if "fast forward" in text or "fast-forward" in text:
                if attempt == 0:
                    continue  # someone pushed in between: check again on the new head
                raise GitHubError(
                    "SOURCE_WRITE_STALE", "The branch kept moving while committing. Read the files again.", 409
                )
            raise _error(moved)
        return Committed(
            commit=commit,
            parent=head,
            blobs={path: blob_sha(data) for path, data in files.items() if data is not None},
            url=f"https://github.com/{repo}/commit/{commit}",
        )
    raise AssertionError("unreachable")  # pragma: no cover


async def read_file(
    token: str, repo: str, ref: str, path: str, *, http: httpx.AsyncClient | None = None
) -> tuple[bytes, str] | None:
    """(bytes, blob sha) of ``path`` at ``ref``, or None when it doesn't exist."""
    api = _Api(token, http)
    sha = await _blob_at(api, repo, ref, path)
    if sha is None:
        return None
    resp = await api.call("GET", f"/repos/{repo}/git/blobs/{sha}")
    data = resp.json()
    content = str(data.get("content") or "")
    raw = base64.b64decode(content) if data.get("encoding") == "base64" else content.encode()
    return raw, sha
