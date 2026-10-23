"""Projects P2 (projects.md §6.3, §6.6): scoped chats, the project instruction
block, and project memory isolated both ways.

What must hold:
* a project id is used only when the caller owns it — sent by the frontend or
  restored from the chat's row — and a stale or forged one runs unscoped;
* the state key is session-scoped (``project_id``, never ``app:``);
* the project block follows "Your instructions", fenced, on every skill;
* project memory and personal memory never see each other (Memory Bank matches
  scope exactly), and an unowned project falls back to PERSONAL, never to
  another project;
* deleting a project purges only that project's memories.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from adk import project_context, project_scope
from adk.memory_tools import memory_scope
from tests.support.state_doubles import ScopedState

UID = "alice"
OWNED = {"id": "p1", "name": "Iberia portfolio review", "instructions": "Quote clause numbers."}


@pytest.fixture(autouse=True)
def _clear():
    project_scope.clear_cache()
    project_context.clear_cache()
    yield
    project_scope.clear_cache()
    project_context.clear_cache()


@pytest.fixture
def projects(monkeypatch):
    """alice owns p1; nobody owns anything else."""
    store = {(UID, "p1"): dict(OWNED)}
    monkeypatch.setattr(
        "db.projects.get_project", lambda uid, pid: dict(store[(uid, pid)]) if (uid, pid) in store else None
    )
    return store


def _ctx(project_id: str | None = None, uid: str = UID, *, bank=None, app_store=None):
    state = ScopedState(app_store=app_store if app_store is not None else {})
    if project_id is not None:
        state[project_scope.SESSION_KEY] = project_id
    service = SimpleNamespace(_agent_engine_id="123", _get_api_client=lambda: bank) if bank else None
    inv = SimpleNamespace(user_id=uid, app_name="aitana_platform", memory_service=service)
    return SimpleNamespace(_invocation_context=inv, state=state)


# --- ownership --------------------------------------------------------------


def test_owned_project_scopes_the_chat(projects):
    assert project_scope.current_project(_ctx("p1"))["name"] == "Iberia portfolio review"


def test_someone_elses_or_deleted_project_runs_unscoped(projects):
    assert project_scope.current_project(_ctx("p1", uid="bob")) is None
    assert project_scope.current_project(_ctx("gone")) is None
    assert project_scope.current_project(_ctx()) is None


def test_the_state_key_is_session_scoped(projects):
    shared_app: dict = {}
    a, b = _ctx("p1", app_store=shared_app), _ctx(app_store=shared_app)
    assert not project_scope.SESSION_KEY.startswith(("app:", "user:"))
    assert project_scope.current_project(a) is not None
    assert project_scope.current_project(b) is None  # another session of the same app is untouched


def test_turn_project_prefers_the_request_then_the_row(projects):
    assert project_scope.resolve_turn_project(UID, "p1", None) == "p1"
    assert project_scope.resolve_turn_project(UID, None, "p1") == "p1"  # resumed chat, nothing sent
    assert project_scope.resolve_turn_project(UID, "forged", "p1") == "p1"
    assert project_scope.resolve_turn_project(UID, "forged", "deleted") == ""
    assert project_scope.resolve_turn_project("bob", "p1", "p1") == ""


def test_the_request_reads_forwarded_props_only():
    from fast_api_app import _extract_project_id, _StreamSkillRequest

    assert _extract_project_id(_StreamSkillRequest(forwardedProps={"project_id": " p1 "})) == "p1"
    # state round-trips the previous turn: never read it (the document_ids lesson).
    assert _extract_project_id(_StreamSkillRequest(state={"project_id": "p1"})) is None
    assert _extract_project_id(_StreamSkillRequest(forwardedProps={"project_id": 5})) is None


def test_session_index_stamps_and_returns_the_project(monkeypatch):
    from skills import skill_processor

    stamped: list[tuple[str, str | None]] = []
    monkeypatch.setattr("db.projects.set_session_project", lambda sid, pid: stamped.append((sid, pid)))
    row = SimpleNamespace(project_id=None, provisional=False, skill_id="s", turn_count=1, last_message_at=None)
    monkeypatch.setattr("db.chat_sessions.get_session_index", lambda sid: row)

    assert skill_processor._ensure_session_index("t1", "s", UID, None, project_id="p1") == "p1"
    assert stamped == [("t1", "p1")]

    row.project_id = "p1"
    stamped.clear()
    assert skill_processor._ensure_session_index("t1", "s", UID, None) == "p1"  # resumed: from the row
    assert stamped == []


# --- the project block ------------------------------------------------------


def _wrap(ctx, monkeypatch, *, instructions="", docs=None, total=None):
    monkeypatch.setattr("db.projects.get_user_instructions", lambda uid: instructions)
    docs = docs if docs is not None else [{"originalFilename": "EDP.pdf"}, {"originalFilename": "LEAP.pdf"}]
    monkeypatch.setattr("db.projects.list_project_documents", lambda uid, pid, page_size=50: (docs[:page_size], None))
    monkeypatch.setattr(
        "db.projects.count_project_documents", lambda uid, pid: total if total is not None else len(docs)
    )
    return asyncio.run(project_context.wrap_with_project_context("Base.")(ctx))


def test_project_block_follows_your_instructions(projects, monkeypatch):
    out = _wrap(_ctx("p1"), monkeypatch, instructions="Answer in Spanish.")
    mine, proj = out.index("<user_instructions>"), out.index("This chat is in the user's project")
    assert mine < proj
    assert "<project_documents>\n- EDP.pdf\n- LEAP.pdf\n</project_documents>" in out
    assert "<project_instructions>\nQuote clause numbers.\n</project_instructions>" in out


def test_no_project_block_when_unscoped_or_not_owned(projects, monkeypatch):
    assert _wrap(_ctx(), monkeypatch) == "Base."
    assert _wrap(_ctx("p1", uid="bob"), monkeypatch) == "Base."


def test_long_projects_list_fifty_and_say_how_many_more(projects, monkeypatch):
    docs = [{"originalFilename": f"doc{i}.pdf"} for i in range(60)]
    out = _wrap(_ctx("p1"), monkeypatch, docs=docs, total=60)
    assert "- doc49.pdf" in out and "- doc50.pdf" not in out
    assert "and 10 more" in out


# --- memory: isolated both ways ---------------------------------------------


class _Pager:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                yield item

        return _gen()


class _Bank:
    """Exact-scope matching, like Memory Bank."""

    def __init__(self, memories):
        self.memories = memories  # list of SimpleNamespace(name, fact, scope, update_time)
        self.ingested: list[dict] = []
        self.deleted: list[str] = []
        self.agent_engines = SimpleNamespace(memories=self)

    async def retrieve(self, *, name, scope, **kw):
        return _Pager(SimpleNamespace(memory=m) for m in self.memories if m.scope == scope)

    async def ingest_events(self, **kw):
        self.ingested.append(kw)

    async def delete(self, *, name):
        self.deleted.append(name)


def _memory(name, fact, **scope):
    full = {"app_name": "aitana_platform", "user_id": UID, **scope}
    return SimpleNamespace(name=name, fact=fact, scope=full, update_time=None)


def test_memory_scope_gains_the_project(projects):
    assert memory_scope(_ctx("p1")) == {"app_name": "aitana_platform", "user_id": UID, "project_id": "p1"}
    assert memory_scope(_ctx("p1", uid="bob")) == {"app_name": "aitana_platform", "user_id": "bob"}  # personal


def test_project_recall_never_sees_personal_and_vice_versa(projects):
    from adk.project_memory import search_scoped_memory

    bank = _Bank([_memory("m1", "personal fact"), _memory("m2", "project fact", project_id="p1")])
    scoped = _ctx("p1", bank=bank)
    facts = [e.content.parts[0].text for e in asyncio.run(search_scoped_memory(scoped, "fact"))]
    assert facts == ["project fact"]

    personal = _ctx(bank=bank)

    async def _adk_search(query):  # ADK's own path for unscoped chats
        return SimpleNamespace(memories=["from-adk"])

    personal.search_memory = _adk_search
    assert asyncio.run(search_scoped_memory(personal, "fact")) == ["from-adk"]


def test_preload_injects_project_memories_in_adks_format(projects):
    from adk.project_memory import project_preload_memory_tool

    bank = _Bank([_memory("m2", "prefers clause numbers", project_id="p1")])
    ctx = _ctx("p1", bank=bank)
    ctx.user_content = SimpleNamespace(parts=[SimpleNamespace(text="what do I prefer?")])
    request = MagicMock()
    asyncio.run(project_preload_memory_tool.process_llm_request(tool_context=ctx, llm_request=request))
    (instructions,), _ = request.append_instructions.call_args
    assert "<PAST_CONVERSATIONS>\nuser: prefers clause numbers\n</PAST_CONVERSATIONS>" in instructions[0]


def test_recall_tools_keep_adks_names():
    from adk.project_memory import project_load_memory_tool, project_preload_memory_tool

    assert project_load_memory_tool.name == "load_memory"
    assert project_preload_memory_tool.name == "preload_memory"
    assert project_load_memory_tool._get_declaration().parameters_json_schema["required"] == ["query"]


def test_scoped_autosave_writes_to_the_project(projects, monkeypatch):
    from google.genai import types

    from adk.memory_autosave import make_memory_autosave_callback

    bank = _Bank([])
    ctx = _ctx("p1", bank=bank)
    inv = ctx._invocation_context
    inv.agent = SimpleNamespace(parent_agent=None)
    inv.invocation_id = "inv"
    inv.session = SimpleNamespace(
        id="s1",
        events=[
            SimpleNamespace(
                invocation_id="inv",
                content=types.Content(role="user", parts=[types.Part(text="hello")]),
                id="e1",
                timestamp=1.0,
                model_copy=lambda update: SimpleNamespace(content=update["content"], id="e1", timestamp=1.0),
            )
        ],
    )
    ctx.add_events_to_memory = MagicMock(side_effect=AssertionError("must not use ADK's personal path"))

    async def _go():
        await make_memory_autosave_callback()(ctx)
        await asyncio.sleep(0)  # let the fire-and-forget ingest run

    asyncio.run(_go())
    assert bank.ingested and bank.ingested[0]["scope"] == {
        "app_name": "aitana_platform",
        "user_id": UID,
        "project_id": "p1",
    }
    assert bank.ingested[0]["stream_id"] == "s1"


def test_purge_deletes_only_the_projects_memories(monkeypatch):
    from adk import project_memory

    bank = _Bank(
        [
            _memory("m1", "personal"),
            _memory("m2", "in p1", project_id="p1"),
            _memory("m3", "in p2", project_id="p2"),
        ]
    )
    monkeypatch.setattr(
        "adk.session.get_memory_service", lambda: SimpleNamespace(_agent_engine_id="123", _get_api_client=lambda: bank)
    )
    assert asyncio.run(project_memory.purge_project_memories(UID, "p1")) == 1
    assert bank.deleted == ["m2"]


# --- list_documents defaults to the chat's project --------------------------


def test_list_documents_defaults_to_the_project(projects, monkeypatch):
    from adk.tools import list_documents

    monkeypatch.setattr(
        "db.projects.list_project_documents",
        lambda uid, pid, page_size=50: ([{"id": "d1", "originalFilename": "EDP.pdf", "parseStatus": "parsed"}], None),
    )
    out = asyncio.run(list_documents(tool_context=_ctx("p1")))
    assert "This chat is in the project “Iberia portfolio review”" in out
    assert "EDP.pdf" in out
    # Strict: no way out of the project from inside it (Mark, 2026-10-20).
    assert 'project="all"' not in out
    assert "never look elsewhere" in out
    for wider in ("all", "*", "Some other project"):
        out = asyncio.run(list_documents(project=wider, tool_context=_ctx("p1")))
        assert "EDP.pdf" in out and "This chat is in the project" in out
