"""/api/projects/{id}/sources — a project kept in step with a GitHub folder
(github-sources.md §5, §14 acceptance).

Runs against the in-memory Firestore with a fake GitHub and a fake ingest, so
the routes, the engine, the manifest and the project destination execute for
real. What is faked is the network and the parser, nothing that decides
ownership.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from db.firestore import get_document, query_documents, set_document
from sources import store as sources_store
from sources.github import GitHubError, RefState, TreeEntry
from tools.documents.ingest import ParsedDocumentResponse

_DOCS = "parsed_documents"


class FakeRepo:
    """One repository: path → bytes, under a commit that changes on every push."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commit = "0" * 40

    def push(self, **changes: bytes | None) -> None:
        for path, data in changes.items():
            path = path.replace("__", "/")
            if data is None:
                self.files.pop(path, None)
            else:
                self.files[path] = data
        self.commit = uuid.uuid4().hex + "0" * 8

    def sha(self, data: bytes) -> str:
        # Git's blob id, as GitHub reports it (a written file must match on the next sync).
        return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


REPO = FakeRepo()


class FakeGitHub:
    def __init__(self, token: str, **_kw) -> None:
        assert token == "read-only-token"

    async def resolve_ref(self, repo: str, ref: str, *, etag: str = "") -> RefState | None:
        return RefState(commit=REPO.commit, etag=f'"{REPO.commit}"')

    async def tree(self, repo: str, commit: str, path: str) -> list[TreeEntry]:
        prefix = f"{path}/" if path else ""
        if path and not any(p.startswith(prefix) for p in REPO.files):
            raise GitHubError("SOURCE_PATH_NOT_FOUND", f"No folder {path!r}.", 404)
        return [
            TreeEntry(path=p[len(prefix) :], sha=REPO.sha(d), size=len(d))
            for p, d in sorted(REPO.files.items())
            if p.startswith(prefix)
        ]

    async def blob(self, repo: str, sha: str) -> bytes:
        return next(d for d in REPO.files.values() if REPO.sha(d) == sha)


async def fake_ingest(*, user, file_bytes, filename, folder_path, skip_unchanged, project_id, source, source_ref):
    """The parts of ingest_document a sync relies on: folder-by-path, dedupe by
    folder + filename, skip identical bytes, tag into the project."""
    import db.folders as folders_db

    folder_id = folders_db.ensure_named_folder(user.uid, folder_path)
    digest = hashlib.sha256(file_bytes).hexdigest()
    rows = query_documents(
        _DOCS,
        filters=[("userId", "==", user.uid), ("folderId", "==", folder_id), ("originalFilename", "==", filename)],
        limit=1,
    )
    if rows and skip_unchanged and rows[0].get("contentSha256") == digest:
        return ParsedDocumentResponse(
            docId=rows[0]["__id"], status="parsed", originalFilename=filename, storagePath="", unchanged=True
        )
    doc_id = rows[0]["__id"] if rows else str(uuid.uuid4())
    prior = rows[0] if rows else {}
    set_document(
        _DOCS,
        doc_id,
        {
            "userId": user.uid,
            "folderId": folder_id,
            "originalFilename": filename,
            "contentSha256": digest,
            "text": file_bytes.decode(),
            "parseStatus": "parsed",
            "status": "parsed",
            "source": source,
            "sourceRef": source_ref,
            "projectIds": sorted({*(prior.get("projectIds") or []), project_id}),
            "createdAt": "2026-10-30T00:00:00+00:00",
        },
    )
    return ParsedDocumentResponse(docId=doc_id, status="parsed", originalFilename=filename, storagePath="")


# Alice may read the clients repository; nobody else may use the credential.
CREDENTIALS = (
    '{"credentials": {"clients": {"token_env": "CLIENTS_TOKEN", "repos": ["example-org/clients"],'
    ' "users": {"type": "specific", "emails": ["alice@example.com"]}}}}'
)


@pytest.fixture()
def store(monkeypatch):
    import db.firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    client = InMemoryFirestoreClient()
    monkeypatch.setattr(fs, "_client", client)
    monkeypatch.setattr("extensions.document_event", lambda *_a, **_k: None)
    monkeypatch.setattr("sources.routes.GitHubClient", FakeGitHub)
    monkeypatch.setattr("sources.project_destination.ingest_document", fake_ingest)
    monkeypatch.setattr(
        "tools.documents.routes._delete_owned_document", lambda doc_id, doc, user: fs.delete_document(_DOCS, doc_id)
    )
    monkeypatch.setenv("CLIENTS_TOKEN", "read-only-token")
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", CREDENTIALS)
    # The scheduler acts as the owner as they are now; here, as a request would.
    monkeypatch.setattr(
        "auth.firebase_auth.resolve_user_by_uid",
        lambda uid: User(uid=uid, email=f"{uid}@example.com", domain="example.com"),
    )
    REPO.files.clear()
    REPO.push(
        **{
            "client-a__index.md": b"# Client A",
            "client-a__sessions__01__agenda.md": b"agenda one",
            "client-a__sessions__01__logo.svg": b"<svg/>",
            "client-a__plan.yaml": b"phase: 0",
            "client-b__secret.md": b"another client's file",
        }
    )
    return client


def _client(uid: str) -> TestClient:
    from projects.routes import router as projects_router
    from sources.routes import router

    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _project(api: TestClient) -> str:
    return api.post("/api/projects", json={"name": "Client A"}).json()["id"]


def _source(api: TestClient, pid: str, **body) -> dict:
    resp = api.post(f"/api/projects/{pid}/sources", json={"repo": "example-org/clients", "path": "client-a", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _sync(api: TestClient, pid: str, sid: str, **body) -> dict:
    resp = api.post(f"/api/projects/{pid}/sources/{sid}/sync", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _library(uid: str = "alice") -> dict[str, dict]:
    """(folder name/filename) → document, for the user's whole library."""
    import db.folders as folders_db

    names = {f["id"]: f["name"] for f in folders_db.list_folders(uid)}
    rows = query_documents(_DOCS, filters=[("userId", "==", uid)], limit=1000)
    return {f"{names.get(r['folderId'], '?')}/{r['originalFilename']}": r for r in rows}


def test_create_defaults_and_hides_bookkeeping(store):
    alice = _client("alice")
    pid = _project(alice)
    src = _source(alice, pid)
    assert (src["repo"], src["ref"], src["path"], src["folderPrefix"], src["credentialRef"]) == (
        "example-org/clients",
        "main",
        "client-a",
        "client-a",
        "clients",
    )
    assert "uid" not in src and "ownerEmail" not in src and "runId" not in src
    listed = alice.get(f"/api/projects/{pid}/sources").json()
    assert [s["id"] for s in listed["sources"]] == [src["id"]]
    assert listed["credentials"] == [{"ref": "clients", "repos": ["example-org/clients"]}]


def test_create_validates_before_storing(store, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    bad = [
        ({"repo": "not a repo"}, 422, "SOURCE_REPO_INVALID"),
        ({"repo": "example-org/clients", "path": "../client-b"}, 422, "SOURCE_PATH_INVALID"),
        ({"repo": "example-org/clients", "ref": "main..evil"}, 422, "SOURCE_REF_INVALID"),
        ({"repo": "example-org/clients", "path": "client-c"}, 422, "SOURCE_PATH_NOT_FOUND"),
        ({"repo": "example-org/other"}, 403, "SOURCE_REPO_NOT_COVERED"),
        ({"repo": "example-org/clients", "credential_ref": "other"}, 403, "SOURCE_CREDENTIAL_NOT_ALLOWED"),
    ]
    for body, status, code in bad:
        resp = alice.post(f"/api/projects/{pid}/sources", json={"path": "client-a", **body})
        assert resp.status_code == status, body
        assert resp.json()["detail"]["code"] == code
    monkeypatch.delenv("GITHUB_SOURCE_CREDENTIALS")
    resp = alice.post(f"/api/projects/{pid}/sources", json={"repo": "example-org/clients", "path": "client-a"})
    assert resp.json()["detail"]["code"] == "NO_SOURCE_CREDENTIALS"
    assert sources_store.list_project_sources("alice", pid) == []


def test_two_sources_cannot_share_a_folder(store):
    alice = _client("alice")
    pid = _project(alice)
    _source(alice, pid)
    resp = alice.post(
        f"/api/projects/{pid}/sources",
        json={"repo": "example-org/clients", "path": "client-a/sessions", "folder_prefix": "client-a"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SOURCE_FOLDER_TAKEN"


def test_another_users_sources_are_404(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    bob = _client("bob")
    assert bob.get(f"/api/projects/{pid}/sources").status_code == 404
    assert bob.post(f"/api/projects/{pid}/sources", json={"repo": "o/r"}).status_code == 404
    assert bob.get(f"/api/projects/{pid}/sources/{sid}").status_code == 404
    assert bob.post(f"/api/projects/{pid}/sources/{sid}/sync", json={}).status_code == 404
    assert bob.delete(f"/api/projects/{pid}/sources/{sid}").status_code == 404
    # Bob's own project cannot reach Alice's source either.
    bob_pid = _project(bob)
    assert bob.post(f"/api/projects/{bob_pid}/sources/{sid}/sync", json={}).status_code == 404


def _earlier_upload(store, uid: str, pid: str, folder: str, filename: str, data: bytes) -> str:
    """What `aiplatform docs upload -r` left behind before the source existed."""
    import db.folders as folders_db

    doc_id = str(uuid.uuid4())
    set_document(
        _DOCS,
        doc_id,
        {
            "userId": uid,
            "folderId": folders_db.ensure_named_folder(uid, folder),
            "originalFilename": filename,
            "contentSha256": hashlib.sha256(data).hexdigest(),
            "parseStatus": "parsed",
            "source": "uploaded",
            "projectIds": [pid],
        },
    )
    return doc_id


def test_first_sync_adopts_adds_skips_and_leaves_uploads_alone(store):
    alice = _client("alice")
    pid = _project(alice)
    adopted_id = _earlier_upload(store, "alice", pid, "client-a", "index.md", b"# Client A")
    intake_id = _earlier_upload(store, "alice", pid, "client-a/intake", "brief.pdf", b"not in git")
    sid = _source(alice, pid)["id"]

    preview = _sync(alice, pid, sid, dry_run=True)
    assert preview["plan"]["adopt"] == ["index.md"]
    assert sorted(preview["plan"]["add"]) == ["plan.yaml", "sessions/01/agenda.md"]
    assert [s["path"] for s in preview["skipped"]] == ["sessions/01/logo.svg"]
    assert sources_store.manifest(sid) == {}  # a preview writes nothing

    report = _sync(alice, pid, sid)
    assert report["status"] == "ok"
    counts = report["counts"]
    assert (counts["adopted"], counts["added"], counts["skipped"], counts["failed"]) == (1, 2, 1, 0)
    lib = _library()
    assert lib["client-a/index.md"]["__id"] == adopted_id  # same document, now owned
    assert lib["client-a/index.md"]["sourceRef"]["path"] == "client-a/index.md"
    assert lib["client-a/sessions/01/agenda.md"]["projectIds"] == [pid]
    assert lib["client-a/plan.yaml"]["source"] == "git"
    assert "client-a/sessions/01/logo.svg" not in lib
    assert not any(k.startswith("client-b") for k in lib)  # outside the path
    assert get_document(_DOCS, intake_id)["source"] == "uploaded"
    assert set(sources_store.manifest(sid)) == {"index.md", "plan.yaml", "sessions/01/agenda.md"}

    again = _sync(alice, pid, sid)
    assert again["message"] == "Nothing new since the last sync."


def test_push_updates_in_place_and_removes_deleted_files(store):
    alice = _client("alice")
    pid = _project(alice)
    intake_id = _earlier_upload(store, "alice", pid, "client-a/sessions/01", "notes.pdf", b"manual")
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    agenda_id = _library()["client-a/sessions/01/agenda.md"]["__id"]

    REPO.push(**{"client-a__sessions__01__agenda.md": b"agenda one, revised", "client-a__plan.yaml": None})
    report = _sync(alice, pid, sid)
    assert report["counts"]["updated"] == 1 and report["counts"]["removed"] == 1
    lib = _library()
    assert lib["client-a/sessions/01/agenda.md"]["__id"] == agenda_id
    assert lib["client-a/sessions/01/agenda.md"]["text"] == "agenda one, revised"
    assert "client-a/plan.yaml" not in lib
    assert get_document(_DOCS, intake_id) is not None  # same folder, not the source's


# --- moves (project-source-write.md follow-up: a moved file keeps its document) ---


def test_a_moved_or_renamed_file_keeps_its_document_and_is_not_parsed_again(store, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    agenda = _library()["client-a/sessions/01/agenda.md"]
    index = _library()["client-a/index.md"]

    parsed: list[str] = []

    async def counting_ingest(**kw):
        parsed.append(kw["filename"])
        return await fake_ingest(**kw)

    monkeypatch.setattr("sources.project_destination.ingest_document", counting_ingest)
    REPO.push(
        **{
            "client-a__sessions__01__agenda.md": None,
            "client-a__archive__2026__agenda.md": b"agenda one",  # moved
            "client-a__index.md": None,
            "client-a__home.md": b"# Client A",  # renamed
        }
    )
    preview = _sync(alice, pid, sid, dry_run=True)
    assert sorted(preview["plan"]["move"]) == [
        "index.md → home.md",
        "sessions/01/agenda.md → archive/2026/agenda.md",
    ]
    report = _sync(alice, pid, sid)
    assert report["counts"]["moved"] == 2
    assert report["counts"]["added"] == 0 and report["counts"]["removed"] == 0
    assert parsed == []  # nothing downloaded and parsed again
    lib = _library()
    assert lib["client-a/archive/2026/agenda.md"]["__id"] == agenda["__id"]
    assert lib["client-a/home.md"]["__id"] == index["__id"]
    assert "client-a/sessions/01/agenda.md" not in lib and "client-a/index.md" not in lib
    assert lib["client-a/home.md"]["sourceRef"]["path"] == "client-a/home.md"
    assert pid in lib["client-a/home.md"]["projectIds"]
    # And it stays put: the next sync finds everything unchanged.
    again = _sync(alice, pid, sid, force=True)
    assert again["counts"].get("moved", 0) == 0 and again["counts"].get("added", 0) == 0


def test_a_move_onto_a_taken_name_is_an_add_then_a_remove(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    old_id = _library()["client-a/index.md"]["__id"]
    _earlier_upload(store, "alice", pid, "client-a/archive", "index.md", b"someone's upload")
    REPO.push(**{"client-a__index.md": None, "client-a__archive__index.md": b"# Client A"})
    report = _sync(alice, pid, sid)
    assert report["counts"]["moved"] == 0
    lib = _library()
    assert "client-a/index.md" not in lib and get_document(_DOCS, old_id) is None
    assert lib["client-a/archive/index.md"]["text"] == "# Client A"


def test_a_changed_type_is_not_a_move(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    REPO.push(**{"client-a__index.md": None, "client-a__index.txt": b"# Client A"})
    report = _sync(alice, pid, sid)
    assert report["counts"]["moved"] == 0
    assert report["counts"]["added"] == 1 and report["counts"]["removed"] == 1


def test_a_mass_delete_waits_for_confirmation(store):
    alice = _client("alice")
    pid = _project(alice)
    REPO.push(**{f"client-a__notes__n{i}.md": f"note {i}".encode() for i in range(12)})
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    before = len(_library())

    REPO.push(**{f"client-a__notes__n{i}.md": None for i in range(12)})
    held = _sync(alice, pid, sid)
    assert held["status"] == "needs_review"
    assert len(held["removalsHeld"]) == 12
    assert len(_library()) == before
    assert alice.get(f"/api/projects/{pid}/sources/{sid}").json()["status"] == "needs_review"

    done = _sync(alice, pid, sid, confirm_removals=True)
    assert done["status"] == "ok" and done["counts"]["removed"] == 12
    assert len(_library()) == before - 12


def test_a_running_sync_refuses_a_second(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    assert sources_store.acquire_lease(sid, "other-run", seconds=300)
    resp = alice.post(f"/api/projects/{pid}/sources/{sid}/sync", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SOURCE_SYNC_RUNNING"


def test_removing_a_source_keeps_its_documents(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    before = _library()
    assert alice.delete(f"/api/projects/{pid}/sources/{sid}").status_code == 204
    assert alice.get(f"/api/projects/{pid}/sources/{sid}").status_code == 404
    assert _library().keys() == before.keys()


def test_deleting_the_project_deletes_its_sources(store, monkeypatch):
    monkeypatch.setattr("projects.routes._purge_memories", lambda *_a: 0)
    alice = _client("alice")
    pid = _project(alice)
    _source(alice, pid)
    assert alice.delete(f"/api/projects/{pid}").status_code == 204
    assert sources_store.list_project_sources("alice", pid) == []


def test_a_run_out_of_time_stops_cleanly_and_the_next_continues(store, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    monkeypatch.setattr("sources.routes.DEFAULT_BUDGET_SECONDS", 0)
    stopped = _sync(alice, pid, sid)
    assert stopped["status"] == "partial"
    assert stopped["counts"]["remaining"] == 3
    assert sources_store.manifest(sid) == {}
    monkeypatch.setattr("sources.routes.DEFAULT_BUDGET_SECONDS", 240)
    # Same commit, but the last run was partial: it plans again and finishes.
    done = _sync(alice, pid, sid)
    assert done["status"] == "ok" and done["counts"]["added"] == 3


def test_index_failures_are_reported_by_path(store, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    agenda = _library()["client-a/sessions/01/agenda.md"]["__id"]
    monkeypatch.setattr(
        "sources.routes.project_search_status",
        lambda uid, p: {
            "indexed": 2,
            "failed": 1,
            "total": 3,
            "status": "ready",
            "failedDocuments": [
                {"docId": agenda, "error": "no readable text"},
                {"docId": "someone-elses-upload", "error": "not this source's"},
            ],
        },
    )
    report = _sync(alice, pid, sid, force=True)
    assert report["indexFailed"] == [{"path": "sessions/01/agenda.md", "error": "no readable text"}]


# --- the scheduler (github-sources.md §7) ------------------------------------------


@pytest.fixture()
def scheduler(store, monkeypatch):
    from sources import scheduler as mod

    monkeypatch.setattr(mod, "GitHubClient", FakeGitHub)
    return mod


def _outcomes(result: dict) -> dict[str, str]:
    return {r["id"]: r["outcome"] for r in result["results"]}


async def test_scheduler_waits_for_the_first_sync_then_follows_pushes(scheduler):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    assert _outcomes(await scheduler.sync_due()) == {sid: "skipped: new"}  # the owner previews first

    _sync(alice, pid, sid)
    assert _outcomes(await scheduler.sync_due()) == {sid: "unchanged"}

    REPO.push(**{"client-a__sessions__02__agenda.md": b"agenda two"})
    assert _outcomes(await scheduler.sync_due()) == {sid: "ok"}
    lib = _library()
    assert lib["client-a/sessions/02/agenda.md"]["projectIds"] == [pid]
    assert lib["client-a/sessions/02/agenda.md"]["userId"] == "alice"


async def test_scheduler_finishes_a_partial_run(scheduler, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    monkeypatch.setattr("sources.routes.DEFAULT_BUDGET_SECONDS", 0)
    assert _sync(alice, pid, sid)["status"] == "partial"
    assert _outcomes(await scheduler.sync_due()) == {sid: "ok"}
    assert len(sources_store.manifest(sid)) == 3


async def test_scheduler_drops_sources_of_deleted_projects(scheduler):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    from db.projects import _projects_ref

    _projects_ref("alice").document(pid).delete()  # deleted without the route
    assert _outcomes(await scheduler.sync_due()) == {sid: "removed: project deleted"}
    assert sources_store.get_source(sid) is None


def test_scheduler_route_is_closed_without_its_oidc_config(store, monkeypatch):
    from sources.scheduler import router

    monkeypatch.delenv("SOURCES_SCHEDULER_OIDC_SA", raising=False)
    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).post("/internal/sources/sync-due", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 403


# --- folder sources (github-sources.md §10) ---------------------------------------


def _library_doc(doc_id: str, folder_id: str, projects: list[str] | None = None) -> None:
    set_document(
        _DOCS,
        doc_id,
        {
            "userId": "alice",
            "folderId": folder_id,
            "originalFilename": f"{doc_id}.pdf",
            "parseStatus": "parsed",
            "createdAt": "2026-10-30T00:00:00+00:00",
            "projectIds": projects or [],
        },
    )


async def test_a_folder_source_follows_the_folder_tree(scheduler):
    import db.folders as folders_db
    from db.firestore import update_document

    alice = _client("alice")
    pid = _project(alice)
    reports = folders_db.create_folder("alice", "Reports")["id"]
    reports_q3 = folders_db.create_folder("alice", "Reports/Q3")["id"]
    other = folders_db.create_folder("alice", "Other")["id"]
    _library_doc("r1", reports)
    _library_doc("q1", reports_q3)
    _library_doc("o1", other)
    _library_doc("hand", reports, projects=[pid])  # in the project before the source

    src = alice.post(f"/api/projects/{pid}/sources", json={"type": "folder", "folder_id": reports})
    assert src.status_code == 201, src.text
    sid = src.json()["id"]
    assert src.json()["folderName"] == "Reports"
    first = _sync(alice, pid, sid)
    assert first["counts"]["added"] == 2 and first["counts"]["unchanged"] == 1
    tagged = {d["__id"] for d in query_documents(_DOCS, filters=[("projectIds", "array_contains", pid)], limit=50)}
    assert tagged == {"r1", "q1", "hand"}

    _library_doc("r2", reports_q3)  # uploaded later
    update_document(_DOCS, "r1", {"folderId": other})  # moved out
    update_document(_DOCS, "hand", {"folderId": other})  # moved out, but tagged by hand
    assert _outcomes(await scheduler.sync_due()) == {sid: "ok"}
    tagged = {d["__id"] for d in query_documents(_DOCS, filters=[("projectIds", "array_contains", pid)], limit=50)}
    assert tagged == {"q1", "r2", "hand"}
    assert get_document(_DOCS, "r1") is not None  # untagged, never deleted
    assert _outcomes(await scheduler.sync_due()) == {sid: "unchanged"}


def test_a_folder_source_needs_your_own_folder(store):
    import db.folders as folders_db

    alice = _client("alice")
    pid = _project(alice)
    bobs = folders_db.create_folder("bob", "Private")["id"]
    resp = alice.post(f"/api/projects/{pid}/sources", json={"type": "folder", "folder_id": bobs})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FOLDER_NOT_FOUND"


# --- the push webhook (P5) -----------------------------------------------------------


def _push(app_client: TestClient, payload: dict, *, secret: str = "s3cret", event: str = "push"):
    import hashlib
    import hmac
    import json as _json

    body = _json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return app_client.post(
        "/api/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": event, "Content-Type": "application/json"},
    )


@pytest.fixture()
def webhook(scheduler, monkeypatch):
    from sources.webhook import router

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_webhook_is_off_without_a_secret(store, monkeypatch):
    from sources.webhook import router

    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    app = FastAPI()
    app.include_router(router)
    assert _push(TestClient(app), {}).status_code == 404


def test_the_webhook_refuses_a_bad_signature(webhook):
    assert _push(webhook, {"ref": "refs/heads/main"}, secret="wrong").status_code == 401


def test_a_push_syncs_the_sources_whose_folder_it_touched(webhook):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    other = _source(alice, pid, path="client-b", folder_prefix="client-b")["id"]
    _sync(alice, pid, sid)
    _sync(alice, pid, other)

    REPO.push(**{"client-a__notes.md": b"pushed"})
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "Example-Org/clients"},
        "commits": [{"added": ["client-a/notes.md"], "modified": [], "removed": []}],
    }
    resp = _push(webhook, payload)
    assert resp.status_code == 200
    assert _outcomes(resp.json()) == {sid: "ok"}  # client-b was not touched
    assert "client-a/notes.md" in _library()
    assert _push(webhook, {"zen": "hi"}, event="ping").json() == {"ok": True}


# --- who may use a credential (github-sources.md §16) -------------------------------


def test_a_credential_is_only_offered_to_its_users(store):
    bob = _client("bob")
    pid = _project(bob)
    assert bob.get(f"/api/projects/{pid}/sources").json()["credentials"] == []
    resp = bob.post(f"/api/projects/{pid}/sources", json={"repo": "example-org/clients", "path": "client-a"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "NO_SOURCE_CREDENTIALS"


async def test_withdrawn_access_stops_manual_and_scheduled_syncs(scheduler, monkeypatch):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)

    # Alice is taken off the credential after adding the source.
    monkeypatch.setenv("GITHUB_SOURCE_CREDENTIALS", CREDENTIALS.replace("alice@example.com", "carol@example.com"))
    resp = alice.post(f"/api/projects/{pid}/sources/{sid}/sync", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SOURCE_CREDENTIAL_NOT_ALLOWED"

    REPO.push(**{"client-a__new.md": b"after the access was withdrawn"})
    assert _outcomes(await scheduler.sync_due()) == {sid: "refused: credential not allowed"}
    assert "client-a/new.md" not in _library()
    src = alice.get(f"/api/projects/{pid}/sources/{sid}").json()
    assert src["status"] == "failed"
    assert src["lastReport"]["error"]["code"] == "SOURCE_CREDENTIAL_NOT_ALLOWED"


# --- progress while a sync runs -----------------------------------------------------


def test_a_sync_writes_its_progress_and_clears_it_at_the_end(store, monkeypatch):
    monkeypatch.setattr("sources.engine.PROGRESS_EVERY_S", 0)
    seen: list[dict] = []
    real = sources_store.update_source

    def spy(source_id, fields):
        if fields.get("progress"):
            seen.append(dict(fields["progress"]))
        real(source_id, fields)

    monkeypatch.setattr(sources_store, "update_source", spy)
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    _sync(alice, pid, sid)
    assert seen[0] == {"done": 0, "total": 3} and seen[-1] == {"done": 3, "total": 3}
    assert alice.get(f"/api/projects/{pid}/sources/{sid}").json().get("progress") is None


def test_a_running_sync_shows_progress_and_a_dead_one_does_not(store):
    alice = _client("alice")
    pid = _project(alice)
    sid = _source(alice, pid)["id"]
    assert sources_store.acquire_lease(sid, "run-1", seconds=300)
    sources_store.update_source(sid, {"progress": {"done": 4, "total": 10}})
    view = alice.get(f"/api/projects/{pid}/sources/{sid}").json()
    assert view["status"] == "running" and view["progress"] == {"done": 4, "total": 10}
    # The instance running it stopped: its lease expires, and it no longer shows as running.
    sources_store.update_source(sid, {"leaseUntil": "2000-01-01T00:00:00+00:00"})
    view = alice.get(f"/api/projects/{pid}/sources/{sid}").json()
    assert view["status"] == "partial" and "progress" not in view
