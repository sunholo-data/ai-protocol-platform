"""The project floor: in a project chat the agent reads only the project's documents.

projects.md §6.3, "strict" (Mark, 2026-10-20). Found live: asked about a
document just taken out of the project, the agent searched the project, came up
empty, listed the whole library and answered from it. These pin the boundary at
the tool layer, where a prompt cannot talk its way past it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adk import project_floor, project_scope
from adk.project_floor import OUTSIDE_DOCUMENT_TOOLS, make_project_floor_callback
from tests.support.state_doubles import ScopedState

UID = "alice"
PROJECT = {"id": "p1", "name": "Iberia portfolio review"}

DOCS = {
    "d-in": {"userId": UID, "originalFilename": "EDP.pdf", "projectIds": ["p1"]},
    "d-out": {"userId": UID, "originalFilename": "LEAP draft.docx", "projectIds": []},
    "d-other-project": {"userId": UID, "originalFilename": "Notice.pdf", "projectIds": ["p2"]},
    "d-bob": {"userId": "bob", "originalFilename": "Bob secret.pdf", "projectIds": ["p1"]},
}


@pytest.fixture(autouse=True)
def _docs(monkeypatch):
    project_scope.clear_cache()
    monkeypatch.setattr(project_floor, "_get_doc", lambda doc_id: DOCS.get(doc_id))
    monkeypatch.setattr(project_floor, "_doc_by_source_url", lambda uid, url: None)
    yield
    project_scope.clear_cache()


def _ctx(*, scoped: bool = True, attached: list[str] | None = None):
    state = ScopedState(app_store={})
    if attached is not None:
        state["document_ids"] = attached
    inv = SimpleNamespace(user_id=UID, app_name="aitana_platform")
    ctx = SimpleNamespace(_invocation_context=inv, state=state)
    return ctx, (PROJECT if scoped else None)


def _call(tool_name: str, args: dict, *, scoped: bool = True, attached: list[str] | None = None):
    ctx, project = _ctx(scoped=scoped, attached=attached)
    with patch.object(project_floor, "current_project", return_value=project):
        return make_project_floor_callback()(tool=SimpleNamespace(name=tool_name), args=args, tool_context=ctx)


# --- searching outside the project -------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(OUTSIDE_DOCUMENT_TOOLS))
def test_outside_document_tools_are_refused_in_a_project(tool_name):
    out = _call(tool_name, {"query": "curtailment"})
    assert out["status"] == "outside_project"
    assert "Iberia portfolio review" in out["message"]
    assert "add_to_project" in out["message"]
    assert "do not look elsewhere" in out["message"]


@pytest.mark.parametrize(
    "tool_name",
    ["search_project_documents", "web_search_agent", "list_projects", "remember", "entsoe_day_ahead_prices"],
)
def test_project_web_data_and_memory_tools_stay_allowed(tool_name):
    assert _call(tool_name, {"query": "curtailment"}) is None


# --- naming a document ------------------------------------------------------


def test_a_project_document_is_allowed():
    assert _call("get_document_content", {"doc_id": "d-in"}) is None


def test_a_library_document_outside_the_project_is_refused_by_filename_not_id():
    out = _call("get_document_content", {"doc_id": "d-out"})
    assert out["status"] == "outside_project"
    assert "“LEAP draft.docx”" in out["message"]
    assert "d-out" not in out["message"]


def test_another_projects_document_is_refused():
    assert _call("get_document_content", {"doc_id": "d-other-project"})["status"] == "outside_project"


def test_someone_elses_document_is_refused_without_naming_it():
    out = _call("get_document_content", {"doc_id": "d-bob"})
    assert out["status"] == "outside_project"
    assert "Bob secret" not in out["message"]


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("extract_ppa_clauses", {"doc_id": "doc:d-out.json"}),
        ("map_ppa_obligations", {"doc_id": "d-out"}),
        ("compare_ppa_contracts", {"left_doc_id": "d-in", "right_doc_id": "d-out"}),
        ("get_document_content", {"doc_id": "[ref: d-out]"}),
        ("retrieve_artifact", {"artifact_id": "doc:d-out.json"}),
        ("load_artifacts", {"artifact_names": ["doc:d-in.json", "doc:d-out.json"]}),
        ("some_tool", {"doc_ids": ["d-in", "d-out"]}),
        ("some_tool", {"document_ids": "d-in,d-out"}),
    ],
)
def test_every_document_argument_shape_is_checked(tool_name, args):
    assert _call(tool_name, args)["status"] == "outside_project"


def test_a_source_url_outside_the_project_is_refused():
    assert (
        _call("extract_ppa_clauses", {"gs_url": "gs://bucket/example-datastore/PPAs/x.pdf"})["status"]
        == "outside_project"
    )


def test_a_source_url_of_a_project_document_is_allowed(monkeypatch):
    monkeypatch.setattr(project_floor, "_doc_by_source_url", lambda uid, url: ("d-in", DOCS["d-in"]))
    assert _call("extract_ppa_clauses", {"gs_url": "gs://bucket/example-datastore/PPAs/edp.pdf"}) is None


def test_non_document_artifacts_are_not_documents():
    assert _call("retrieve_artifact", {"artifact_id": "search_response_abc123"}) is None


def test_an_unknown_id_passes_through_for_the_tool_to_report():
    assert _call("get_document_content", {"doc_id": "no-such-doc"}) is None


# --- the user's own choice ---------------------------------------------------


def test_a_document_the_user_attached_as_a_tab_is_allowed():
    assert _call("get_document_content", {"doc_id": "d-out"}, attached=["d-out"]) is None


def test_agent_reads_recorded_on_the_chat_are_not_an_exception():
    # chat_sessions.documentIds also holds documents the AGENT opened; only the
    # user's attached tabs (state document_ids) count.
    ctx, _ = _ctx(attached=[])
    ctx.state["temp:document_id"] = "d-out"
    with patch.object(project_floor, "current_project", return_value=PROJECT):
        out = make_project_floor_callback()(
            tool=SimpleNamespace(name="get_document_content"), args={"doc_id": "d-out"}, tool_context=ctx
        )
    assert out["status"] == "outside_project"


# --- unscoped chats are untouched --------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("search_documents", {"query": "x"}),
        ("get_document_content", {"doc_id": "d-out"}),
        ("list_bucket_documents", {}),
    ],
)
def test_unscoped_chats_are_unchanged(tool_name, args):
    assert _call(tool_name, args, scoped=False) is None


def test_unscoped_chats_never_read_a_document(monkeypatch):
    reads: list[str] = []
    monkeypatch.setattr(project_floor, "_get_doc", lambda doc_id: reads.append(doc_id))
    assert _call("get_document_content", {"doc_id": "d-out"}, scoped=False) is None
    assert reads == []


def test_handoff_control_tool_is_never_inspected():
    assert _call("transfer_to_agent", {"agent_name": "x"}) is None


def test_a_scope_lookup_failure_does_not_break_the_turn():
    ctx, _ = _ctx()
    with patch.object(project_floor, "current_project", side_effect=RuntimeError("firestore down")):
        out = make_project_floor_callback()(tool=SimpleNamespace(name="search_documents"), args={}, tool_context=ctx)
    assert out is None


# --- wired for every skill ---------------------------------------------------


def test_create_agent_puts_the_floor_in_the_before_tool_chain():
    from adk.agent import create_agent
    from tests.unit.test_create_agent import _skill, _user

    with patch("adk.platform_preamble_context.get_platform_config") as cfg:
        from db.models import PlatformConfig

        cfg.return_value = PlatformConfig(preamble="", enabled=False)
        agent = create_agent(_skill(tools=["list_documents"]), _user())
    cb = agent.before_tool_callback
    cb = cb[0] if isinstance(cb, list) else cb
    ctx, _ = _ctx()
    with (
        patch("adk.callbacks.can_use_tool", return_value=True),
        patch.object(project_floor, "current_project", return_value=PROJECT),
    ):
        refused = cb(tool=SimpleNamespace(name="search_documents"), args={"query": "x"}, tool_context=ctx)
        allowed = cb(tool=SimpleNamespace(name="get_document_content"), args={"doc_id": "d-in"}, tool_context=ctx)
    assert refused["status"] == "outside_project"
    assert allowed is None


# --- inventory: every registered tool is classified --------------------------

# Tools that never name a user document. If a new registered tool is not in
# here, not in OUTSIDE_DOCUMENT_TOOLS, and not covered by the document-argument
# checks, this test fails so someone decides which it is.
_CLASSIFIED_SAFE = {
    "ailang_inbox_ack",
    "ailang_inbox_list",
    "ailang_inbox_read",
    "ailang_messages",
    "ask_assistant",  # the user's resident agent; not a document store (see report)
    "browse",  # the web
    "entsoe_day_ahead_prices",  # market data
    "generate_document",  # writes a NEW document from the model's markdown
    "my_context",
    "url_processing",  # the web
    "knowledge_search",  # project-filtered in scoped chats (private/knowledge/tools.py)
    "list_documents",  # project-only in scoped chats (adk/tools.py)
}
_DOC_ARG_TOOLS = {"get_document_content", "extract_ppa_clauses", "map_ppa_obligations", "compare_ppa_contracts"}


def test_every_registered_tool_is_classified():
    from adk.tools import TOOL_REGISTRY

    unclassified = set(TOOL_REGISTRY) - _CLASSIFIED_SAFE - _DOC_ARG_TOOLS - OUTSIDE_DOCUMENT_TOOLS
    assert not unclassified, f"classify these for the project floor: {sorted(unclassified)}"
