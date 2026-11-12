"""The sync planner and the GitHub client (github-sources.md §5, §6).

The planner is pure: files under the source's path + its manifest in, a plan
out. The client runs against httpx's MockTransport, so the path walk, the
truncation refusal and the error mapping execute for real.
"""

from __future__ import annotations

import httpx
import pytest

from sources.engine import REMOVAL_CAP_COUNT, plan
from sources.github import GitHubClient, GitHubError, TreeEntry


def _entry(path: str, sha: str = "s1", size: int = 10) -> TreeEntry:
    return TreeEntry(path=path, sha=sha, size=size)


def _accepts(path: str) -> str | None:
    return None if path.endswith((".md", ".pdf")) else "type not supported"


def _plan(entries, manifest=None, **kw):
    kw.setdefault("include", [])
    kw.setdefault("exclude", [])
    return plan(entries, manifest or {}, accepts=_accepts, **kw)


def test_new_changed_unchanged_and_removed_files() -> None:
    manifest = {
        "same.md": {"path": "same.md", "blobSha": "a", "docId": "d1"},
        "changed.md": {"path": "changed.md", "blobSha": "old", "docId": "d2"},
        "gone.md": {"path": "gone.md", "blobSha": "c", "docId": "d3"},
    }
    p = _plan([_entry("same.md", "a"), _entry("changed.md", "new"), _entry("new.md", "n")], manifest)
    assert [e.path for e in p.add] == ["new.md"]
    assert [e.path for e in p.update] == ["changed.md"]
    assert p.unchanged == 1
    assert [r["path"] for r in p.remove] == ["gone.md"]
    assert not p.removals_held


def test_skips_have_reasons_and_are_never_stored() -> None:
    p = _plan(
        [
            _entry("notes.md"),
            _entry("logo.svg"),
            _entry(".github/workflows/ci.md"),
            _entry("big.pdf", size=60 * 1024 * 1024),
        ]
    )
    assert [e.path for e in p.add] == ["notes.md"]
    reasons = {s["path"]: s["reason"] for s in p.skipped}
    assert reasons["logo.svg"] == "type not supported"
    assert reasons[".github/workflows/ci.md"] == "hidden file or folder"
    assert reasons["big.pdf"].startswith("larger than")


def test_include_and_exclude_patterns_match_at_any_depth() -> None:
    entries = [_entry("docs/a.md"), _entry("docs/drafts/b.md"), _entry("src/c.md")]
    p = _plan(entries, include=["docs/*"], exclude=["*/drafts/*"])
    assert [e.path for e in p.add] == ["docs/a.md"]


def test_a_file_that_becomes_excluded_is_removed() -> None:
    manifest = {"docs/a.md": {"path": "docs/a.md", "blobSha": "s1", "docId": "d1"}}
    p = _plan([_entry("docs/a.md")], manifest, exclude=["docs/*"])
    assert [r["path"] for r in p.remove] == ["docs/a.md"]


def test_removal_cap_holds_back_a_mass_delete() -> None:
    manifest = {f"f{i}.md": {"path": f"f{i}.md", "blobSha": "s", "docId": f"d{i}"} for i in range(20)}
    # A wrong path or a force-push: the tree comes back almost empty.
    p = _plan([_entry("f0.md", "s")], manifest)
    assert len(p.remove) == 19
    assert p.removals_held
    confirmed = _plan([_entry("f0.md", "s")], manifest, confirm_removals=True)
    assert not confirmed.removals_held


def test_removal_cap_by_count() -> None:
    n = REMOVAL_CAP_COUNT * 10
    manifest = {f"f{i}.md": {"path": f"f{i}.md", "blobSha": "s", "docId": f"d{i}"} for i in range(n)}
    keep = [_entry(f"f{i}.md", "s") for i in range(n - REMOVAL_CAP_COUNT - 1)]
    assert _plan(keep, manifest).removals_held


def test_a_few_deletions_in_a_small_source_go_through() -> None:
    manifest = {f"f{i}.md": {"path": f"f{i}.md", "blobSha": "s", "docId": f"d{i}"} for i in range(3)}
    p = _plan([_entry("f0.md", "s")], manifest)
    assert len(p.remove) == 2
    assert not p.removals_held


# --- GitHub client --------------------------------------------------------------


def _client(routes: dict[str, httpx.Response]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        if request.headers.get("If-None-Match") == '"etag-1"':
            return httpx.Response(304)
        return routes.get(key, httpx.Response(404, json={"message": "Not Found"}))

    return GitHubClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


_COMMIT = "c" * 40
_ROUTES = {
    "/repos/o/r/commits/main": httpx.Response(200, text=_COMMIT, headers={"ETag": '"etag-1"'}),
    f"/repos/o/r/git/commits/{_COMMIT}": httpx.Response(200, json={"tree": {"sha": "root"}}),
    "/repos/o/r/git/trees/root": httpx.Response(
        200,
        json={
            "tree": [
                {"path": "client-a", "type": "tree", "sha": "ta"},
                {"path": "client-b", "type": "tree", "sha": "tb"},
            ]
        },
    ),
    "/repos/o/r/git/trees/ta?recursive=1": httpx.Response(
        200,
        json={
            "truncated": False,
            "tree": [
                {"path": "notes.md", "type": "blob", "sha": "b1", "size": 5, "mode": "100644"},
                {"path": "sessions", "type": "tree", "sha": "t2", "mode": "040000"},
                {"path": "sessions/one.md", "type": "blob", "sha": "b2", "size": 7, "mode": "100644"},
                {"path": "link", "type": "blob", "sha": "b3", "size": 4, "mode": "120000"},
                {"path": "vendored", "type": "commit", "sha": "b4", "mode": "160000"},
            ],
        },
    ),
}


async def test_tree_lists_only_files_under_the_path() -> None:
    entries = await _client(_ROUTES).tree("o/r", _COMMIT, "client-a")
    assert [(e.path, e.sha) for e in entries] == [("notes.md", "b1"), ("sessions/one.md", "b2")]


async def test_missing_path_is_a_clear_error() -> None:
    with pytest.raises(GitHubError) as exc:
        await _client(_ROUTES).tree("o/r", _COMMIT, "client-c")
    assert exc.value.code == "SOURCE_PATH_NOT_FOUND"


async def test_truncated_tree_is_refused_not_used() -> None:
    routes = {
        **_ROUTES,
        "/repos/o/r/git/trees/ta?recursive=1": httpx.Response(200, json={"truncated": True, "tree": []}),
    }
    with pytest.raises(GitHubError) as exc:
        await _client(routes).tree("o/r", _COMMIT, "client-a")
    assert exc.value.code == "TREE_TRUNCATED"


async def test_resolve_ref_and_unchanged_etag() -> None:
    client = _client(_ROUTES)
    state = await client.resolve_ref("o/r", "main")
    assert state is not None and state.commit == _COMMIT and state.etag == '"etag-1"'
    assert await client.resolve_ref("o/r", "main", etag='"etag-1"') is None


async def test_invisible_repo_and_rate_limit_map_to_stable_codes() -> None:
    with pytest.raises(GitHubError) as exc:
        await _client({}).resolve_ref("o/private", "main")
    assert exc.value.code == "SOURCE_NOT_VISIBLE"
    limited = {"/repos/o/r/commits/main": httpx.Response(403, headers={"x-ratelimit-remaining": "0"})}
    with pytest.raises(GitHubError) as exc:
        await _client(limited).resolve_ref("o/r", "main")
    assert exc.value.code == "GITHUB_RATE_LIMITED"
