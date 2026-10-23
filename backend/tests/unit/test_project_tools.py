"""Project agent tools (adk/project_tools.py) and their chat card.

Friendly names in, friendly names out; an ambiguous filename changes nothing;
the caller comes from the ADK invocation; every write result renders as a card.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import db.projects as projects_db
from adk import project_tools
from adk.a2ui_project_render import project_change_to_a2ui
from adk.a2ui_result_render import is_render_payload_tool, render_for_emit
from db.firestore import get_document


@pytest.fixture()
def store(monkeypatch):
    import db.firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    client = InMemoryFirestoreClient()
    monkeypatch.setattr(fs, "_client", client)
    return client


def _ctx(uid: str = "alice", session_id: str = "s1"):
    inv = SimpleNamespace(user_id=uid, app_name="aitana_platform", session=SimpleNamespace(id=session_id))
    return SimpleNamespace(_invocation_context=inv, state={})


def _doc(store, doc_id: str, name: str, *, owner: str = "alice", created: str = "2026-10-01"):
    store.collection("parsed_documents").document(doc_id).set(
        {
            "userId": owner,
            "originalFilename": name,
            "status": "parsed",
            "parseStatus": "parsed",
            "createdAt": f"{created}T00:00:00+00:00",
        }
    )


def _run(coro):
    return asyncio.run(coro)


def test_resolve_documents_tiers(store):
    _doc(store, "a", "EDP PPA 2025.pdf")
    _doc(store, "b", "EDP side letter.docx")
    _doc(store, "c", "LEAP contract.pdf")
    _doc(store, "x", "LEAP secret.pdf", owner="bob")
    out = project_tools.resolve_documents("alice", "LEAP contract, edp ppa 2025, EDP, nope")
    assert out["resolved"] == [("c", "LEAP contract.pdf"), ("a", "EDP PPA 2025.pdf")]
    assert out["ambiguous"] == [{"name": "EDP", "candidates": ["EDP PPA 2025.pdf", "EDP side letter.docx"]}]
    assert out["not_found"] == ["nope"]
    # Someone else's document is never a candidate.
    assert project_tools.resolve_documents("alice", "secret")["not_found"] == ["secret"]


def test_create_with_an_ambiguous_document_creates_nothing(store):
    _doc(store, "a", "EDP PPA.pdf")
    _doc(store, "b", "EDP side letter.docx")
    result = _run(project_tools.create_project(name="Iberia", documents="EDP", tool_context=_ctx()))
    assert result["status"] == "needs_choice"
    assert projects_db.list_projects("alice") == []


def test_create_with_documents(store):
    _doc(store, "a", "EDP PPA.pdf")
    result = _run(project_tools.create_project(name="Iberia", documents="EDP PPA, missing.pdf", tool_context=_ctx()))
    assert result["status"] == "created"
    assert result["added"] == ["EDP PPA.pdf"] and result["notFound"] == ["missing.pdf"]
    assert result["project"]["name"] == "Iberia"
    assert result["url"] == f"/projects/{result['project']['ref']}"
    assert get_document("parsed_documents", "a")["projectIds"] == [result["project"]["ref"]]


def test_create_needs_a_name_unless_from_this_chat(store):
    result = _run(project_tools.create_project(tool_context=_ctx()))
    assert result["status"] == "error" and projects_db.list_projects("alice") == []


def test_create_from_this_chat(store):
    _doc(store, "a", "EDP PPA.pdf")
    store.collection("chat_sessions").document("s1").set(
        {"ownerUid": "alice", "documentIds": ["a"], "title": "EDP pricing"}
    )
    result = _run(project_tools.create_project(from_this_chat=True, tool_context=_ctx()))
    assert result["status"] == "created" and result["chatAdded"] is True
    assert result["project"]["name"] == "EDP pricing"
    pid = result["project"]["ref"]
    assert get_document("chat_sessions", "s1")["projectId"] == pid
    assert get_document("parsed_documents", "a")["projectIds"] == [pid]


def test_from_this_chat_never_links_someone_elses_thread(store):
    store.collection("chat_sessions").document("s1").set({"ownerUid": "bob", "documentIds": []})
    result = _run(project_tools.create_project(name="Mine", from_this_chat=True, tool_context=_ctx()))
    assert result["chatAdded"] is False and result["chatNotLinked"] is True
    assert "projectId" not in get_document("chat_sessions", "s1")


def test_duplicate_name_is_an_error_card(store):
    projects_db.create_project("alice", "Iberia")
    result = _run(project_tools.create_project(name="iberia", tool_context=_ctx()))
    assert result["status"] == "error" and "already have" in result["error"]


def test_add_and_remove(store):
    _doc(store, "a", "EDP PPA.pdf")
    projects_db.create_project("alice", "Settlement dispute")
    added = _run(project_tools.add_to_project(project="settlement", documents="EDP PPA", tool_context=_ctx()))
    assert added["status"] == "updated" and added["added"] == ["EDP PPA.pdf"]
    again = _run(project_tools.add_to_project(project="settlement", documents="EDP PPA", tool_context=_ctx()))
    assert again["already"] == ["EDP PPA.pdf"]
    removed = _run(project_tools.remove_from_project(project="settlement", documents="EDP", tool_context=_ctx()))
    assert removed["removed"] == ["EDP PPA.pdf"]
    assert get_document("parsed_documents", "a") is not None


def test_unknown_project_lists_the_real_ones(store):
    projects_db.create_project("alice", "Iberia")
    result = _run(project_tools.add_to_project(project="nonexistent", documents="x", tool_context=_ctx()))
    assert result["status"] == "error" and "Iberia" in result["error"]


def test_bare_add_asks_instead_of_guessing(store):
    result = _run(project_tools.add_to_project(tool_context=_ctx()))
    assert result["status"] == "error" and "Do not guess" in result["message"]


def test_unidentified_caller_is_refused(store):
    ctx = SimpleNamespace(_invocation_context=SimpleNamespace(user_id=None), state={})
    assert _run(project_tools.create_project(name="X", tool_context=ctx))["status"] == "error"
    assert "Could not identify" in _run(project_tools.list_projects(tool_context=ctx))


def test_list_projects_is_friendly(store):
    assert "no projects yet" in _run(project_tools.list_projects(tool_context=_ctx()))
    p = projects_db.create_project("alice", "Iberia")
    text = _run(project_tools.list_projects(tool_context=_ctx()))
    assert "Iberia" in text and f"[ref: {p['id']}]" in text and "never show" in text


def test_list_documents_by_project(store):
    from adk.tools import list_documents

    _doc(store, "a", "EDP PPA.pdf")
    p = projects_db.create_project("alice", "Iberia")
    projects_db.tag_documents("alice", p["id"], ["a"])
    text = _run(list_documents(project="iberia", tool_context=_ctx()))
    assert "EDP PPA.pdf" in text and "[ref: a]" in text
    unknown = _run(list_documents(project="zzz", tool_context=_ctx()))
    assert "Iberia" in unknown


# --- the chat card ------------------------------------------------------------


def test_write_tools_are_offload_exempt_and_render_in_chat(store):
    for name in project_tools.WRITE_TOOL_NAMES:
        assert is_render_payload_tool(name)
    _doc(store, "a", "EDP PPA.pdf")
    result = _run(project_tools.create_project(name="Iberia", documents="EDP PPA", tool_context=_ctx()))
    rendered = render_for_emit("create_project", result)
    assert rendered is not None
    assert rendered.surface_id.startswith("project_card:")
    assert rendered.artifact["placement"] == "chat"
    assert rendered.artifact["title"] == "Project created: Iberia"
    texts = [c.get("text") for m in rendered.messages for c in m.get("updateComponents", {}).get("components", [])]
    assert "Added: EDP PPA.pdf" in texts and "Open project" in texts


def test_an_error_renders_a_card_without_an_open_button():
    result = project_tools._result("create", "error", error="You already have a project called 'Iberia'.")
    messages = project_change_to_a2ui(result)
    comps = messages[1]["updateComponents"]["components"]
    texts = [c.get("text") for c in comps]
    assert "You already have a project called 'Iberia'." in texts
    assert not any(c.get("component") == "Button" for c in comps)


def test_each_call_gets_its_own_card(store):
    a = project_tools._result("add", "updated")
    b = project_tools._result("add", "updated")
    assert render_for_emit("add_to_project", a).surface_id != render_for_emit("add_to_project", b).surface_id
