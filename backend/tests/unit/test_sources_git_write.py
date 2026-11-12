"""One commit to a branch through the Git Data API (sources/git_write.py), and
the write-credential rules (project-source-write.md §3, §5).

GitHub is httpx's MockTransport holding one branch, so the requests are asserted
on the wire: one tree on the head's tree, one commit with the head as parent, a
non-forced ref update, and a refusal instead of an overwrite.
"""

from __future__ import annotations

import json

import httpx
import pytest

from auth.access_context import AccessContext
from sources import credentials, git_write, github_app
from sources.github import GitHubError

REPO = "example-org/notes"


class Branch:
    def __init__(self) -> None:
        self.head = "a" * 40
        self.files = {"docs/plan.md": "b" * 40}  # path → blob sha at head
        self.requests: list[tuple[str, str, dict | None]] = []
        self.move_answers: list[httpx.Response] = []
        self.on_move = None

    def client(self) -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content) if req.content else None
            self.requests.append((req.method, req.url.path, body))
            path = req.url.path
            if path == f"/repos/{REPO}/git/refs/heads/main" and req.method == "GET":
                return httpx.Response(200, json={"object": {"sha": self.head}})
            if path.startswith(f"/repos/{REPO}/contents/"):
                name = path.removeprefix(f"/repos/{REPO}/contents/")
                sha = self.files.get(name)
                return httpx.Response(200, json={"type": "file", "sha": sha}) if sha else httpx.Response(404)
            if path == f"/repos/{REPO}/git/commits/{self.head}":
                return httpx.Response(200, json={"tree": {"sha": "t" * 40}})
            if path == f"/repos/{REPO}/git/trees":
                return httpx.Response(201, json={"sha": "n" * 40})
            if path == f"/repos/{REPO}/git/commits":
                return httpx.Response(201, json={"sha": "c" * 40})
            if path == f"/repos/{REPO}/git/refs/heads/main" and req.method == "PATCH":
                if self.on_move:
                    self.on_move()
                return self.move_answers.pop(0) if self.move_answers else httpx.Response(200, json={})
            return httpx.Response(404)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_blob_sha_is_gits_object_id():
    # `printf 'hello\n' | git hash-object --stdin`
    assert git_write.blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


async def test_one_commit_on_the_head_never_forced():
    branch = Branch()
    done = await git_write.commit_files(
        "tok",
        REPO,
        "main",
        {"docs/plan.md": b"new plan", "docs/notes.md": b"a note"},
        {"docs/plan.md": "b" * 40, "docs/notes.md": None},
        "Update the plan",
        http=branch.client(),
    )
    assert done.commit == "c" * 40 and done.parent == branch.head
    assert done.blobs["docs/notes.md"] == git_write.blob_sha(b"a note")
    posted = {path: body for method, path, body in branch.requests if method in ("POST", "PATCH")}
    tree = posted[f"/repos/{REPO}/git/trees"]
    assert tree["base_tree"] == "t" * 40
    assert sorted(e["path"] for e in tree["tree"]) == ["docs/notes.md", "docs/plan.md"]
    assert posted[f"/repos/{REPO}/git/commits"] == {
        "message": "Update the plan",
        "tree": "n" * 40,
        "parents": ["a" * 40],
    }
    assert posted[f"/repos/{REPO}/git/refs/heads/main"] == {"sha": "c" * 40, "force": False}


@pytest.mark.parametrize(
    "expected",
    [
        {"docs/plan.md": "0" * 40},  # someone changed it since it was read
        {"docs/plan.md": None},  # "new" file that exists already
    ],
)
async def test_a_stale_file_is_refused_before_anything_is_written(expected):
    branch = Branch()
    with pytest.raises(GitHubError) as exc:
        await git_write.commit_files("tok", REPO, "main", {"docs/plan.md": b"x"}, expected, "m", http=branch.client())
    assert exc.value.code == "SOURCE_WRITE_STALE"
    assert not any(method in ("POST", "PATCH") for method, _p, _b in branch.requests)


async def test_a_branch_that_moved_is_checked_again_once():
    branch = Branch()
    branch.move_answers = [
        httpx.Response(422, json={"message": "Update is not a fast forward"}),
        httpx.Response(200, json={}),
    ]
    done = await git_write.commit_files(
        "tok", REPO, "main", {"docs/plan.md": b"x"}, {"docs/plan.md": "b" * 40}, "m", http=branch.client()
    )
    assert done.commit == "c" * 40
    assert sum(1 for m, p, _b in branch.requests if m == "PATCH") == 2


async def test_a_branch_that_moved_onto_our_file_is_stale():
    branch = Branch()

    def colleague_pushes() -> None:
        branch.head = "d" * 40
        branch.files["docs/plan.md"] = "e" * 40

    branch.on_move = colleague_pushes
    branch.move_answers = [httpx.Response(422, json={"message": "Update is not a fast forward"})]
    with pytest.raises(GitHubError) as exc:
        await git_write.commit_files(
            "tok", REPO, "main", {"docs/plan.md": b"x"}, {"docs/plan.md": "b" * 40}, "m", http=branch.client()
        )
    assert exc.value.code == "SOURCE_WRITE_STALE"


async def test_a_protected_branch_says_so():
    branch = Branch()
    branch.move_answers = [httpx.Response(422, json={"message": "Protected branch update failed for refs/heads/main."})]
    with pytest.raises(GitHubError) as exc:
        await git_write.commit_files(
            "tok", REPO, "main", {"docs/plan.md": b"x"}, {"docs/plan.md": "b" * 40}, "m", http=branch.client()
        )
    assert exc.value.code == "SOURCE_WRITE_PROTECTED"


# --- write credentials -----------------------------------------------------------

_CONFIG = {
    "credentials": {
        "docs": {"app_id": "1", "private_key_env": "K", "repos": ["example-org/*"], "users": {"type": "public"}},
        "docs-write": {
            "app_id": "2",
            "private_key_env": "K2",
            "access": "write",
            "repos": ["example-org/*", "!example-org/clients"],
            "users": {"type": "tagged", "tags": ["staff"]},
        },
    }
}


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(_CONFIG))
    github_app._forget_cached()


def test_access_must_be_read_or_write(monkeypatch):
    bad = {"credentials": {"x": {"app_id": "1", "private_key_env": "K", "repos": ["o/*"], "access": "admin"}}}
    with pytest.raises(credentials.CredentialConfigError, match="read or write"):
        credentials._parse(json.dumps(bad))


def test_only_an_app_can_write():
    bad = {"credentials": {"x": {"token_env": "T", "repos": ["o/*"], "access": "write"}}}
    with pytest.raises(credentials.CredentialConfigError, match="only a GitHub App can write"):
        credentials._parse(json.dumps(bad))


def test_writers_are_found_per_person_and_never_offered_for_reading(configured):
    staff = AccessContext(uid="u", email="a@example.com", domain="example.com", group_tags=frozenset({"staff"}))
    other = AccessContext(uid="v", email="b@example.com", domain="example.com")
    assert [c.ref for c in credentials.offered(staff)] == ["docs"]
    assert credentials.writer_for(staff, "example-org/notes").ref == "docs-write"
    assert credentials.writer_for(staff, "example-org/clients") is None
    assert credentials.writer_for(other, "example-org/notes") is None


async def test_a_read_credential_cannot_mint_a_write_token(configured):
    with pytest.raises(GitHubError) as exc:
        await credentials.write_token(credentials.get("docs"), "example-org/notes")
    assert exc.value.code == "SOURCE_WRITE_NOT_ALLOWED"


def test_the_github_mcp_never_uses_a_write_credential(configured):
    from tools.mcp.github_app_auth import usable_credentials

    staff = AccessContext(uid="u", email="a@example.com", domain="example.com", group_tags=frozenset({"staff"}))
    assert [c.ref for c in usable_credentials(staff, {})] == ["docs"]


async def test_a_delete_is_a_tree_entry_without_a_blob():
    branch = Branch()
    done = await git_write.commit_files(
        "tok", REPO, "main", {"docs/plan.md": None}, {"docs/plan.md": "b" * 40}, "Drop the plan", http=branch.client()
    )
    tree = next(body for method, path, body in branch.requests if path.endswith("/git/trees"))
    assert tree["tree"] == [{"path": "docs/plan.md", "mode": "100644", "type": "blob", "sha": None}]
    assert done.blobs == {}
