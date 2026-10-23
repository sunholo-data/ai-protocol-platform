"""Projects data layer (db/projects.py) against the in-memory Firestore.

The properties that matter (projects.md §5, §8 "Tests that must exist"):
derived chat membership follows the documents both ways, nothing of another
user's ever leaks in, and deleting a project never deletes a document or a chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import db.projects as projects_db
from db.chat_sessions import add_session_documents, list_sessions_for_project
from db.firestore import get_document


@pytest.fixture()
def store(monkeypatch):
    import db.firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    client = InMemoryFirestoreClient()
    monkeypatch.setattr(fs, "_client", client)
    # Indexing has its own integration tests; no worker may outlive this store.
    monkeypatch.setattr("extensions.document_event", lambda *_args, **_kwargs: None)
    return client


def _doc(client, doc_id: str, *, owner: str = "alice", name: str | None = None, created: str = "2026-10-01"):
    client.collection("parsed_documents").document(doc_id).set(
        {
            "userId": owner,
            "originalFilename": name or f"{doc_id}.pdf",
            "status": "parsed",
            "parseStatus": "parsed",
            "createdAt": f"{created}T00:00:00+00:00",
            "blocks": [{"type": "paragraph", "text": "confidential"}],
        }
    )


_T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _session(client, sid: str, docs: list[str], *, owner: str = "alice", minutes: int = 0, **extra):
    at = (_T0 + timedelta(minutes=minutes)).isoformat()
    client.collection("chat_sessions").document(sid).set(
        {
            "sessionId": sid,
            "skillId": "skill-1",
            "ownerUid": owner,
            "documentIds": docs,
            "accessControl": {"type": "private"},
            "firstMessageAt": at,
            "lastMessageAt": at,
            "archivedAt": None,
            **extra,
        }
    )


def _project_ids(sid: str) -> list[str]:
    return list((get_document("chat_sessions", sid) or {}).get("projectIds") or [])


def _chats(uid: str, pid: str) -> dict[str, str]:
    doc_ids = set(projects_db.project_document_ids(uid, pid))
    rows, _ = list_sessions_for_project(uid, pid, doc_ids, page_size=50)
    return {r["session"].session_id: r["membership"] for r in rows}


# --- projects ---------------------------------------------------------------


def test_create_rejects_a_duplicate_name_case_insensitively(store):
    projects_db.create_project("alice", "Iberia review")
    with pytest.raises(projects_db.ProjectError) as exc:
        projects_db.create_project("alice", "  iberia   REVIEW ")
    assert exc.value.code == "PROJECT_EXISTS"
    # Another user may use the same name.
    projects_db.create_project("bob", "Iberia review")


def test_unique_name_appends_a_number(store):
    projects_db.create_project("alice", "Disputes")
    assert projects_db.unique_name("alice", "Disputes") == "Disputes (2)"


def test_limit(store, monkeypatch):
    monkeypatch.setattr(projects_db, "MAX_PROJECTS", 2)
    projects_db.create_project("alice", "A")
    projects_db.create_project("alice", "B")
    with pytest.raises(projects_db.ProjectError) as exc:
        projects_db.create_project("alice", "C")
    assert exc.value.code == "PROJECT_LIMIT"


def test_another_users_project_is_not_found(store):
    p = projects_db.create_project("alice", "Mine")
    assert projects_db.get_project("bob", p["id"]) is None
    with pytest.raises(projects_db.ProjectError) as exc:
        projects_db.delete_project("bob", p["id"])
    assert exc.value.status == 404


def test_resolve_project_by_friendly_name(store):
    a = projects_db.create_project("alice", "Iberia portfolio review")
    projects_db.create_project("alice", "Settlement dispute")
    assert projects_db.resolve_project("alice", "iberia")[0]["id"] == a["id"]
    assert projects_db.resolve_project("alice", f"[ref: {a['id']}]")[0]["id"] == a["id"]
    found, candidates = projects_db.resolve_project("alice", "nothing like it")
    assert found is None and set(candidates) == {"Iberia portfolio review", "Settlement dispute"}


# --- derived chat membership --------------------------------------------------


def test_tagging_a_document_brings_its_past_chats(store):
    _doc(store, "edp")
    _session(store, "s1", ["edp"])
    p = projects_db.create_project("alice", "Iberia")
    result = projects_db.tag_documents("alice", p["id"], ["edp"])
    assert result["added"] == ["edp"]
    assert _chats("alice", p["id"]) == {"s1": "derived"}


def test_untagging_and_deleting_remove_the_chat(store):
    _doc(store, "edp")
    _session(store, "s1", ["edp"])
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp"])
    assert projects_db.untag_document("alice", p["id"], "edp") is True
    assert _project_ids("s1") == []
    assert _chats("alice", p["id"]) == {}

    projects_db.tag_documents("alice", p["id"], ["edp"])
    store.collection("parsed_documents").document("edp").delete()
    projects_db.on_document_deleted("alice", "edp", [p["id"]])
    assert _project_ids("s1") == []


def test_a_chat_with_two_project_documents_stays_when_one_is_untagged(store):
    _doc(store, "edp")
    _doc(store, "leap")
    _session(store, "s1", ["edp", "leap"])
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp", "leap"])
    projects_db.untag_document("alice", p["id"], "edp")
    assert _project_ids("s1") == [p["id"]]
    assert _chats("alice", p["id"]) == {"s1": "derived"}


def test_another_users_chat_about_my_document_never_joins(store):
    _doc(store, "edp")
    _session(store, "theirs", ["edp"], owner="bob")
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp"])
    assert _project_ids("theirs") == []
    assert _chats("alice", p["id"]) == {}


def test_attaching_a_project_document_to_a_chat_joins_it(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp"])
    _session(store, "s2", [])
    add_session_documents("s2", ["edp"])
    assert _project_ids("s2") == [p["id"]]


def test_a_shared_library_document_does_not_carry_its_owners_projects(store):
    _doc(store, "lib", owner="platform")
    store.collection("parsed_documents").document("lib").update({"projectIds": ["platform-project"]})
    _session(store, "s3", [])
    add_session_documents("s3", ["lib"])
    assert _project_ids("s3") == []


def test_tagging_reports_others_and_missing_documents_without_filenames(store):
    _doc(store, "mine")
    _doc(store, "theirs", owner="bob", name="secret-contract.pdf")
    p = projects_db.create_project("alice", "Iberia")
    result = projects_db.tag_documents("alice", p["id"], ["mine", "theirs", "gone"])
    assert result["added"] == ["mine"]
    assert {f["id"] for f in result["failed"]} == {"theirs", "gone"}
    assert all(f["filename"] == "" for f in result["failed"])
    assert "projectIds" not in (get_document("parsed_documents", "theirs") or {})
    assert projects_db.tag_documents("alice", p["id"], ["mine"])["already"] == ["mine"]


# --- explicit + listing ------------------------------------------------------


def test_explicit_and_derived_merge_newest_first(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    _session(store, "old", ["edp"], minutes=0)
    _session(store, "explicit", [], minutes=10, projectId=p["id"])
    projects_db.tag_documents("alice", p["id"], ["edp"])
    rows, _ = list_sessions_for_project("alice", p["id"], {"edp"}, page_size=10)
    assert [(r["session"].session_id, r["membership"]) for r in rows] == [("explicit", "explicit"), ("old", "derived")]
    assert rows[1]["linkedDocumentId"] == "edp"


def test_archived_and_provisional_chats_are_hidden(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    _session(store, "arch", ["edp"], archivedAt="2026-10-20T00:00:00+00:00")
    _session(store, "prov", ["edp"], provisional=True)
    projects_db.tag_documents("alice", p["id"], ["edp"])
    assert _chats("alice", p["id"]) == {}
    assert projects_db.count_project_sessions("alice", p["id"]) == 0


def test_a_stale_derived_row_is_dropped_and_repaired(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    _session(store, "stale", [], projectIds=[p["id"]])
    assert _chats("alice", p["id"]) == {}
    assert _project_ids("stale") == []


def test_paging(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    for i in range(5):
        _session(store, f"s{i}", ["edp"], minutes=i)
    projects_db.tag_documents("alice", p["id"], ["edp"])
    first, cursor = list_sessions_for_project("alice", p["id"], {"edp"}, page_size=3)
    assert [r["session"].session_id for r in first] == ["s4", "s3", "s2"]
    second, cursor2 = list_sessions_for_project("alice", p["id"], {"edp"}, page_size=3, cursor=cursor)
    assert [r["session"].session_id for r in second] == ["s1", "s0"]
    assert cursor2 is None


def test_counts(store):
    _doc(store, "edp")
    _doc(store, "leap")
    p = projects_db.create_project("alice", "Iberia")
    _session(store, "s1", ["edp"])
    _session(store, "s2", ["edp"], projectId=p["id"])
    projects_db.tag_documents("alice", p["id"], ["edp", "leap"])
    assert projects_db.count_project_documents("alice", p["id"]) == 2
    assert projects_db.count_project_sessions("alice", p["id"]) == 2


def test_project_documents_never_carry_blocks(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp"])
    docs, _ = projects_db.list_project_documents("alice", p["id"])
    assert docs[0]["id"] == "edp" and "blocks" not in docs[0]
    assert docs[0]["projectIds"] == [p["id"]]


# --- delete ------------------------------------------------------------------


def test_delete_untags_everything_and_keeps_documents_and_chats(store):
    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    _session(store, "s1", ["edp"])
    _session(store, "s2", [], projectId=p["id"])
    projects_db.tag_documents("alice", p["id"], ["edp"])
    assert projects_db.delete_project("alice", p["id"]) == {"documents": 1, "chats": 2}
    assert get_document("parsed_documents", "edp")["projectIds"] == []
    assert _project_ids("s1") == []
    assert get_document("chat_sessions", "s2")["projectId"] is None
    assert projects_db.get_project("alice", p["id"]) is None


# --- re-ingest (the §5 trap) ---------------------------------------------------


def test_a_reingest_keeps_the_project_tags(store):
    from datetime import datetime as _dt

    from tools.documents.ingest import _ParseResult, _store_document

    _doc(store, "edp")
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["edp"])
    _store_document(
        "edp",
        user_id="alice",
        skill_id="s",
        gs_url="gs://b/edp.pdf",
        storage_path="users/alice/edp.pdf",
        original_filename="edp.pdf",
        source_format="pdf",
        folder_id="f1",
        parse_result=_ParseResult(status="parsed", blocks=[], parsed_ms=1, error=None),
        now=_dt.now(UTC),
    )
    assert get_document("parsed_documents", "edp")["projectIds"] == [p["id"]]


# --- your instructions -----------------------------------------------------------


def test_user_instructions_round_trip_and_limit(store):
    assert projects_db.get_user_instructions("alice") == ""
    projects_db.set_user_instructions("alice", "  Answer in Spanish.  ")
    assert projects_db.get_user_instructions("alice") == "Answer in Spanish."
    with pytest.raises(projects_db.ProjectError):
        projects_db.set_user_instructions("alice", "x" * 4001)
