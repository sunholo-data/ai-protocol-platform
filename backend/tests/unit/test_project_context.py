"""wrap_with_project_context — "Your instructions" (projects.md §6.5).

A no-op when the user has none (byte-identical instruction), a fenced block
directly after the skill's own instruction when they do, and wired into every
agent built by create_agent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from adk import project_context
from adk.project_context import wrap_with_project_context


@pytest.fixture(autouse=True)
def _clear():
    project_context.clear_cache()
    yield
    project_context.clear_cache()


def _ctx(uid: str = "alice"):
    return SimpleNamespace(_invocation_context=SimpleNamespace(user_id=uid), state={})


def test_no_instructions_is_a_no_op(monkeypatch):
    monkeypatch.setattr("db.projects.get_user_instructions", lambda uid: "")
    assert asyncio.run(wrap_with_project_context("Base.")(_ctx())) == "Base."


def test_instructions_are_appended_fenced(monkeypatch):
    monkeypatch.setattr("db.projects.get_user_instructions", lambda uid: "Answer in Spanish.")
    out = asyncio.run(wrap_with_project_context("Base.")(_ctx()))
    assert out.startswith("Base.\n\n## The user's own instructions")
    assert "<user_instructions>\nAnswer in Spanish.\n</user_instructions>" in out
    assert "never grant permission" in out


def test_a_read_failure_never_fails_the_turn(monkeypatch):
    def _boom(uid):
        raise RuntimeError("firestore down")

    monkeypatch.setattr("db.projects.get_user_instructions", _boom)
    assert asyncio.run(wrap_with_project_context("Base.")(_ctx())) == "Base."


def test_cached_per_user(monkeypatch):
    calls: list[str] = []

    def _read(uid):
        calls.append(uid)
        return f"for {uid}"

    monkeypatch.setattr("db.projects.get_user_instructions", _read)
    wrapper = wrap_with_project_context("Base.")
    asyncio.run(wrapper(_ctx("alice")))
    asyncio.run(wrapper(_ctx("alice")))
    bob = asyncio.run(wrapper(_ctx("bob")))
    assert calls == ["alice", "bob"] and "for bob" in bob


def test_unidentified_caller_gets_nothing(monkeypatch):
    monkeypatch.setattr("db.projects.get_user_instructions", lambda uid: "x")
    ctx = SimpleNamespace(_invocation_context=SimpleNamespace(user_id=None), state={})
    assert asyncio.run(wrap_with_project_context("Base.")(ctx)) == "Base."


def test_every_agent_gets_it_right_after_the_skill_instruction(monkeypatch):
    from unittest.mock import MagicMock

    from adk.agent import create_agent
    from auth.firebase_auth import User
    from tests.unit.test_create_agent import _skill

    monkeypatch.setattr("db.projects.get_user_instructions", lambda uid: "Answer in Spanish.")
    agent = create_agent(_skill(), User(uid="alice", email="a@example.com", domain="example.com"))
    ctx = MagicMock()
    ctx.state = {}
    ctx._invocation_context.user_id = "alice"
    resolved = asyncio.run(agent.instruction(ctx))
    # (The platform preamble, when configured, comes before the skill's instruction.)
    assert "Do the thing.\n\n## The user's own instructions" in resolved
    assert resolved.index("Answer in Spanish.") < resolved.index("Today's date is")


def test_attached_block_names_the_users_own_attached_documents(monkeypatch):
    """A tab the user attached in a project chat is named, so the project rule
    does not read as 'ignore it' (2026-10-21: attached tabs used 7/10)."""
    from types import SimpleNamespace

    import adk.project_context as pc

    docs = {
        "d-mine": {"userId": "u1", "originalFilename": "site-memo.txt"},
        "d-theirs": {"userId": "u2", "originalFilename": "secret.txt"},
    }
    monkeypatch.setattr("db.firestore.get_document", lambda col, doc_id: docs.get(doc_id))
    ctx = SimpleNamespace(state={"document_ids": ["d-mine", "d-theirs", "d-missing"]})
    block = pc.attached_block("u1", ctx)
    assert "site-memo.txt [ref: d-mine]" in block
    assert "secret.txt" not in block and "d-theirs" not in block
    assert "<attached_documents>" in block


def test_attached_block_is_empty_without_attachments():
    from types import SimpleNamespace

    import adk.project_context as pc

    assert pc.attached_block("u1", SimpleNamespace(state={})) == ""
