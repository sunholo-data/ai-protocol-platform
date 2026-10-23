"""Integration test: _before_tool callback blocks/allows tools per permissions.

Uses a minimal ADK Agent with a dummy FunctionTool and the permission
enforcer wired in. Firestore is mocked so this test doesn't require a live
project, but we exercise the full callback → permission → cache stack.

Marked @pytest.mark.integration per project convention (slow / heavier deps).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adk.callbacks import make_permission_enforcer
from auth.permissions import COLLECTION, clear_cache


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _mock_tool_context(agent_name: str = "test-agent") -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = agent_name
    return ctx


def _mock_docs(docs: dict[str, dict | None]):
    def _get(collection: str, doc_id: str):
        assert collection == COLLECTION
        return docs.get(doc_id)

    return patch("auth.permissions.fs.get_document", side_effect=_get)


@pytest.mark.integration
def test_enforcer_allows_permitted_tool():
    """User has wildcard access → callback returns None (proceed)."""
    docs = {"*": {"type": "wildcard", "tools": ["*"], "denied": []}}
    enforcer = make_permission_enforcer("owner@yourcompany.com", "yourcompany.com")
    with _mock_docs(docs):
        result = enforcer(_mock_tool("search"), {"query": "hello"}, _mock_tool_context())
    assert result is None


@pytest.mark.integration
def test_enforcer_blocks_denied_tool():
    """User's wildcard grants everything except admin_tool → denial as a RESULT.

    Not an exception: a raise becomes a RUN_ERROR that ends the turn with no
    reason shown (seen live on prod 2026-09-10). The short-circuit dict is
    what the model relays.
    """
    docs = {"*": {"type": "wildcard", "tools": ["*"], "denied": ["admin_tool"]}}
    enforcer = make_permission_enforcer("owner@yourcompany.com", "yourcompany.com")
    with _mock_docs(docs):
        result = enforcer(_mock_tool("admin_tool"), {}, _mock_tool_context())
    assert result is not None and result["permitted"] is False
    assert "admin_tool" in result["error"]
    assert "administrator" in result["hint"]


@pytest.mark.integration
def test_enforcer_blocks_when_no_permission():
    """No permission docs at all → denial as a result naming the caller."""
    enforcer = make_permission_enforcer("stranger@example.com", "example.com")
    with _mock_docs({}):
        result = enforcer(_mock_tool("any_tool"), {}, _mock_tool_context())
    assert result is not None and result["permitted"] is False
    assert "stranger@example.com" in result["error"]


@pytest.mark.integration
def test_enforcer_user_level_wins():
    """User doc allows, domain doc denies → user wins."""
    docs = {
        "owner@yourcompany.com": {"type": "user", "tools": ["special_tool"], "denied": []},
        "yourcompany.com": {"type": "domain", "tools": [], "denied": ["special_tool"]},
    }
    enforcer = make_permission_enforcer("owner@yourcompany.com", "yourcompany.com")
    with _mock_docs(docs):
        result = enforcer(_mock_tool("special_tool"), {}, _mock_tool_context())
    assert result is None
