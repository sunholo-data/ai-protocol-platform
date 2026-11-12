"""Writable project sources: the project's agent commits back to its GitHub
folder (project-source-write.md §10 P1 acceptance).

Same harness as test_project_sources_routes: the in-memory Firestore, a fake
repository and a fake ingest, so routes, manifest, project destination and the
write orchestration run for real. The commit itself is faked against the same
repository, with GitHub's staleness rule; git_write's HTTP is tested on its own
in tests/unit/test_sources_git_write.py.
"""

from __future__ import annotations

import json

import pytest

from sources import git_write, project_write
from sources.github import GitHubError
from sources.project_write import WriteRefused
from tests.api_tests.test_project_sources_routes import (  # noqa: F401 - the fixture is used by name
    REPO,
    _client,
    _library,
    _project,
    _source,
    _sync,
    store,
)

# Alice may read the clients repository, and write any example-org repository
# the writer App is installed on.
CREDENTIALS = {
    "credentials": {
        "clients": {
            "token_env": "CLIENTS_TOKEN",
            "repos": ["example-org/clients"],
            "users": {"type": "specific", "emails": ["alice@example.com"]},
        },
        "clients-write": {
            "app_id": "9",
            "private_key_env": "WRITER_KEY",
            "access": "write",
            "repos": ["example-org/*"],
            "users": {"type": "specific", "emails": ["alice@example.com"]},
        },
    }
}
INSTALL_URL = "https://github.com/apps/example-writer/installations/new"


class Writer:
    """The writer App's side: installed or not, and the commits it made."""

    def __init__(self) -> None:
        self.installed = True
        self.commits: list[dict] = []


@pytest.fixture()
def writer(store, monkeypatch):  # noqa: F811 - the imported fixture, by name
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(CREDENTIALS))
    w = Writer()

    async def installed_on(cred, repo, **_kw):
        assert cred.can_write
        return w.installed

    async def install_url(cred, **_kw):
        return INSTALL_URL

    async def write_token(cred, repo):
        assert cred.ref == "clients-write"
        return "write-token"

    async def commit_files(token, repo, branch, files, expected, message, **_kw):
        assert token == "write-token"
        stale = [p for p in files if (REPO.sha(REPO.files[p]) if p in REPO.files else None) != expected.get(p)]
        if stale:
            raise GitHubError("SOURCE_WRITE_STALE", "Changed on GitHub: " + ", ".join(stale), 409)
        parent = REPO.commit
        REPO.push(**files)
        w.commits.append(
            {
                "repo": repo,
                "branch": branch,
                "files": sorted(files),
                "deleted": sorted(p for p, d in files.items() if d is None),
                "message": message,
            }
        )
        return git_write.Committed(
            commit=REPO.commit,
            parent=parent,
            blobs={p: git_write.blob_sha(d) for p, d in files.items() if d is not None},
            url=f"https://github.com/{repo}/commit/{REPO.commit}",
        )

    async def read_file(token, repo, ref, path, **_kw):
        data = REPO.files.get(path)
        return None if data is None else (data, REPO.sha(data))

    monkeypatch.setattr("sources.github_app.installed_on", installed_on)
    monkeypatch.setattr("sources.github_app.install_url", install_url)
    monkeypatch.setattr("sources.credentials.write_token", write_token)
    monkeypatch.setattr("sources.git_write.commit_files", commit_files)
    monkeypatch.setattr("sources.git_write.read_file", read_file)
    return w


def _writable(api, pid: str) -> dict:
    src = _source(api, pid)
    _sync(api, pid, src["id"])
    resp = api.patch(f"/api/projects/{pid}/sources/{src['id']}", json={"writable": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- the switch ----------------------------------------------------------------


def test_the_page_offers_writing_only_where_it_would_work(writer):
    alice = _client("alice")
    pid = _project(alice)
    src = _source(alice, pid)
    listed = alice.get(f"/api/projects/{pid}/sources").json()["sources"][0]
    assert listed["write"] == {"canWrite": True}
    assert not listed.get("writable")

    writer.installed = False
    listed = alice.get(f"/api/projects/{pid}/sources").json()["sources"][0]
    assert listed["write"] == {"canWrite": False, "installUrl": INSTALL_URL}
    resp = alice.patch(f"/api/projects/{pid}/sources/{src['id']}", json={"writable": True})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SOURCE_WRITE_NOT_INSTALLED"


def test_a_write_credential_is_never_offered_for_reading(writer):
    alice = _client("alice")
    pid = _project(alice)
    assert [c["ref"] for c in alice.get(f"/api/projects/{pid}/sources").json()["credentials"]] == ["clients"]


def test_turning_writing_on_and_off(writer):
    alice = _client("alice")
    pid = _project(alice)
    src = _writable(alice, pid)
    assert src["writable"] is True and src["write"] == {"canWrite": True}
    assert "writeCredentialRef" not in src  # bookkeeping stays server-side
    off = alice.patch(f"/api/projects/{pid}/sources/{src['id']}", json={"writable": False}).json()
    assert off["writable"] is False


def test_nobody_else_can_switch_it(writer):
    alice = _client("alice")
    pid = _project(alice)
    src = _source(alice, pid)
    resp = _client("bob").patch(f"/api/projects/{pid}/sources/{src['id']}", json={"writable": True})
    assert resp.status_code == 404


# --- writing -------------------------------------------------------------------


async def test_a_write_is_one_commit_and_lands_in_the_project_at_once(writer):
    alice = _client("alice")
    pid = _project(alice)
    src = _writable(alice, pid)
    index = await project_write.read_file("alice", pid, "client-a/index.md")
    assert index["content"] == "# Client A" and len(index["version"]) == 40

    out = await project_write.write_files(
        "alice",
        pid,
        [
            {"path": "client-a/index.md", "content": "# Client A\n\nStatus: green", "version": index["version"]},
            {"path": "client-a/notes/plan.md", "content": "Next: phase 1"},
        ],
        "Record the status and the plan",
        session_id="s-1",
    )
    assert out["status"] == "committed"
    assert {f["path"]: f["outcome"] for f in out["files"]} == {
        "client-a/index.md": "updated",
        "client-a/notes/plan.md": "added",
    }
    [commit] = writer.commits
    assert commit["files"] == ["client-a/index.md", "client-a/notes/plan.md"]
    assert commit["message"].startswith("Record the status and the plan\n\n")
    assert "Co-authored-by: alice <alice@example.com>" in commit["message"]
    assert "Platform-Session: s-1" in commit["message"]

    library = _library()
    assert library["client-a/index.md"]["text"] == "# Client A\n\nStatus: green"
    plan = library["client-a/notes/plan.md"]
    assert plan["sourceRef"]["blobSha"] == REPO.sha(b"Next: phase 1")
    assert pid in plan["projectIds"]

    # The next sync finds what was written unchanged: nothing re-read, nothing re-parsed.
    report = _sync(alice, pid, src["id"], force=True)
    assert report["counts"].get("added", 0) == 0 and report["counts"].get("updated", 0) == 0


async def test_a_file_changed_on_github_is_refused_not_overwritten(writer):
    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    REPO.push(**{"client-a/index.md": b"# Client A (edited by a colleague)"})
    with pytest.raises(WriteRefused) as exc:
        await project_write.write_files("alice", pid, [{"path": "client-a/index.md", "content": "mine"}], "Overwrite")
    assert exc.value.code == "SOURCE_WRITE_STALE"
    assert REPO.files["client-a/index.md"] == b"# Client A (edited by a colleague)"
    # Re-reading gives the colleague's version, and writing with it goes through.
    fresh = await project_write.read_file("alice", pid, "client-a/index.md")
    out = await project_write.write_files(
        "alice",
        pid,
        [{"path": "client-a/index.md", "content": fresh["content"] + "\nmine", "version": fresh["version"]}],
        "Add to the colleague's edit",
    )
    assert out["status"] == "committed"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("client-b/secret.md", "SOURCE_WRITE_OUTSIDE"),  # another client's folder
        ("client-a/../client-b/secret.md", "SOURCE_WRITE_INVALID"),
        ("client-a/report.pdf", "SOURCE_WRITE_TYPE"),  # parsed text is not a PDF's source
        ("client-a/logo.png", "SOURCE_WRITE_TYPE"),
    ],
)
async def test_what_cannot_be_written(writer, path, code):
    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    with pytest.raises(WriteRefused) as exc:
        await project_write.write_files("alice", pid, [{"path": path, "content": "x"}], "Try")
    assert exc.value.code == code
    assert writer.commits == []


async def test_a_read_only_source_is_not_written(writer):
    alice = _client("alice")
    pid = _project(alice)
    src = _source(alice, pid)
    _sync(alice, pid, src["id"])
    with pytest.raises(WriteRefused) as exc:
        await project_write.write_files("alice", pid, [{"path": "client-a/n.md", "content": "x"}], "Try")
    assert exc.value.code == "SOURCE_WRITE_NONE"


async def test_withdrawn_write_access_stops_the_next_write(writer, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    narrowed = json.loads(json.dumps(CREDENTIALS))
    narrowed["credentials"]["clients-write"]["users"] = {"type": "specific", "emails": ["lead@example.com"]}
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(narrowed))
    with pytest.raises(WriteRefused) as exc:
        await project_write.write_files("alice", pid, [{"path": "client-a/n.md", "content": "x"}], "Try")
    assert exc.value.code == "SOURCE_WRITE_NOT_ALLOWED"


async def test_commits_per_hour_are_capped(writer, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    monkeypatch.setattr(project_write, "COMMITS_PER_HOUR", 2)
    for n in range(2):
        await project_write.write_files("alice", pid, [{"path": f"client-a/n{n}.md", "content": "x"}], f"Note {n}")
    with pytest.raises(WriteRefused) as exc:
        await project_write.write_files("alice", pid, [{"path": "client-a/n9.md", "content": "x"}], "One more")
    assert exc.value.code == "SOURCE_WRITE_LIMIT"


def test_the_agent_is_told_which_folders_it_can_write(writer):
    from adk import project_context

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    project_context.clear_cache()
    block = project_context._project_block_for("alice", {"id": pid, "name": "Client A"})
    assert "- client-a/ (from example-org/clients, branch main)" in block
    assert "write_project_files" in block


def test_the_write_tools_exist_only_with_a_write_credential(store, monkeypatch):  # noqa: F811
    from adk.project_tools import project_tool_names

    names = project_tool_names()
    assert "write_project_files" not in names  # store's credentials: read-only
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", json.dumps(CREDENTIALS))
    names = project_tool_names()
    assert {"read_project_file", "write_project_files", "delete_project_files"} <= names


@pytest.mark.parametrize(
    ("setting", "trailer"),
    [
        ("", "Co-authored-by: alice <alice@example.com>"),
        ("Example Platform <noreply@example.com>", "Co-authored-by: Example Platform <noreply@example.com>"),
        ("0", None),
        ("not an identity", "Co-authored-by: alice <alice@example.com>"),
    ],
)
async def test_who_a_commit_is_co_authored_by(writer, monkeypatch, setting, trailer):
    monkeypatch.setenv("GITHUB_WRITE_COAUTHOR", setting)
    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    await project_write.write_files("alice", pid, [{"path": "client-a/n.md", "content": "x"}], "Note", session_id="s-9")
    message = writer.commits[-1]["message"]
    if trailer:
        assert trailer in message
    else:
        assert "Co-authored-by" not in message
    assert "Platform-Session: s-9" in message  # the chat stays traceable inside the platform


# --- deleting, with the owner's approval (W7) ------------------------------------------


@pytest.fixture()
def deletes(writer):
    from adk import approvals
    from sources.delete_approvals import SourceDeleteApprovals

    approvals.clear_registry()
    approvals.register_provider(SourceDeleteApprovals())
    yield writer
    approvals.clear_registry()


async def _decide(approval_id: str, decision: str = "approve", digest: str | None = None):
    from adk import approvals
    from auth import User
    from auth.approvals import Credential

    owner = User(uid="alice", email="alice@example.com", domain="example.com", auth_mode="tailnet")
    request = approvals.get(approval_id, "alice")
    return await approvals.decide(
        approval_id,
        digest=digest or request.digest,
        decision=decision,
        user=owner,
        channel="web",
        credential=Credential(kind="session", id="web"),
    )


async def test_a_delete_waits_for_approval_then_is_one_commit(deletes):
    from adk import approvals
    from sources.delete_approvals import prepare

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    asked = await prepare(
        "alice", pid, ["client-a/plan.yaml", "client-a/sessions/01/agenda.md"], "Drop old notes", session_id="s-2"
    )
    assert asked["status"] == "needs_approval"
    assert asked["approve_url"].endswith(f"/approve/{asked['approval_id']}")
    assert deletes.commits == []  # nothing happens before the owner decides
    assert "client-a/plan.yaml" in REPO.files

    [pending] = approvals.pending_for("alice", "s-2")
    assert pending.title == "Delete 2 files from example-org/clients"
    assert "- client-a/plan.yaml" in pending.review and '"Drop old notes"' in pending.review

    receipt = await _decide(asked["approval_id"])
    assert receipt.state == "completed", receipt.message
    [commit] = deletes.commits
    assert commit["deleted"] == ["client-a/plan.yaml", "client-a/sessions/01/agenda.md"]
    assert "Platform-Session: s-2" in commit["message"]
    assert "client-a/plan.yaml" not in REPO.files
    lib = _library()
    assert "client-a/plan.yaml" not in lib and "client-a/sessions/01/agenda.md" not in lib

    with pytest.raises(approvals.ApprovalError) as again:
        await _decide(asked["approval_id"])
    assert again.value.status == 410  # once


async def test_a_cancelled_delete_changes_nothing(deletes):
    from sources.delete_approvals import prepare

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    asked = await prepare("alice", pid, ["client-a/plan.yaml"], "Drop it")
    receipt = await _decide(asked["approval_id"], "cancel")
    assert receipt.state == "cancelled"
    assert deletes.commits == [] and "client-a/plan.yaml" in REPO.files


async def test_a_file_changed_before_approval_is_not_deleted(deletes):
    from sources.delete_approvals import prepare

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    asked = await prepare("alice", pid, ["client-a/plan.yaml"], "Drop it")
    REPO.push(**{"client-a/plan.yaml": b"phase: 1 (a colleague's edit)"})
    receipt = await _decide(asked["approval_id"])
    assert receipt.state == "failed" and "Changed on GitHub" in receipt.message
    assert REPO.files["client-a/plan.yaml"] == b"phase: 1 (a colleague's edit)"


async def test_only_files_the_project_holds_can_be_deleted(deletes):
    from sources.delete_approvals import prepare

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    with pytest.raises(WriteRefused) as exc:
        await prepare("alice", pid, ["client-a/sessions/01/logo.svg"], "Drop it")  # skipped, never synced
    assert exc.value.code == "SOURCE_WRITE_INVALID"
    with pytest.raises(WriteRefused) as exc:
        await prepare("alice", pid, ["client-b/secret.md"], "Drop it")
    assert exc.value.code == "SOURCE_WRITE_OUTSIDE"


async def test_nobody_else_sees_or_decides_the_request(deletes):
    from adk import approvals
    from sources.delete_approvals import prepare

    alice = _client("alice")
    pid = _project(alice)
    _writable(alice, pid)
    asked = await prepare("alice", pid, ["client-a/plan.yaml"], "Drop it")
    assert approvals.pending_for("bob") == []
    with pytest.raises(approvals.ApprovalError) as exc:
        approvals.get(asked["approval_id"], "bob")
    assert exc.value.status == 404


def test_the_list_says_where_writing_could_be_offered(writer):
    alice = _client("alice")
    pid = _project(alice)
    body = alice.get(f"/api/projects/{pid}/sources").json()
    assert [w["ref"] for w in body["writers"]] == ["clients-write"]
    assert _client("bob").get(f"/api/projects/{_project(_client('bob'))}/sources").json()["writers"] == []


def test_the_model_sees_each_files_fields(writer):
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    from adk.project_write_tools import FILE_ENTRY_SCHEMA, WriteFilesTool
    from adk.schema_conformance import sanitize_function_declarations

    decl = WriteFilesTool()._get_declaration()
    req = LlmRequest(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[decl])]))
    sanitize_function_declarations(req)  # what every model call goes through
    sent = req.config.tools[0].function_declarations[0]
    schema = sent.parameters_json_schema or sent.parameters.model_dump(exclude_none=True)
    items = schema["properties"]["files"]["items"]
    assert set(items["properties"]) == {"path", "content", "version"}
    assert items["required"] == FILE_ENTRY_SCHEMA["required"]
    assert "$ref" not in str(schema)
