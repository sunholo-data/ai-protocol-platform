"""get_document_content records the document on the conversation.

Verified on dev 2026-10-20: a chat whose agent opened and summarised a
document by itself left ``chat_sessions.documentIds`` empty, so the document's
Conversations panel and ``GET /api/documents/recent`` never knew that chat
had used it. Only documents attached as tabs were recorded.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adk.tools import get_document_content


def _ctx(session_id: str | None = "chat-1"):
    session = SimpleNamespace(id=session_id) if session_id else None
    return SimpleNamespace(state={}, _invocation_context=SimpleNamespace(session=session))


@pytest.fixture()
def owned_doc():
    with (
        patch("tools.documents.context.resolve_caller_uid", return_value="user_a"),
        patch("adk.tools.build_document_context", return_value=("# Q4 memo", None)),
    ):
        yield


@pytest.mark.asyncio
async def test_a_successful_read_is_recorded_on_the_session(owned_doc):
    with patch("db.chat_sessions.add_session_documents") as add:
        out = await get_document_content("doc-1", tool_context=_ctx())
    assert out == "# Q4 memo"
    add.assert_called_once_with("chat-1", ["doc-1"])


@pytest.mark.asyncio
async def test_a_denied_read_records_nothing():
    from tools.documents.context import DocumentAccessError

    with (
        patch("tools.documents.context.resolve_caller_uid", return_value="user_a"),
        patch("adk.tools.build_document_context", side_effect=DocumentAccessError("nope")),
        patch("db.chat_sessions.add_session_documents") as add,
    ):
        out = await get_document_content("someone-elses", tool_context=_ctx())
    assert "not found" in out
    add.assert_not_called()


@pytest.mark.asyncio
async def test_a_recording_failure_never_fails_the_read(owned_doc):
    with patch("db.chat_sessions.add_session_documents", side_effect=RuntimeError("firestore down")):
        out = await get_document_content("doc-1", tool_context=_ctx())
    assert out == "# Q4 memo"


@pytest.mark.asyncio
async def test_no_session_means_nothing_to_record(owned_doc):
    with patch("db.chat_sessions.add_session_documents") as add:
        await get_document_content("doc-1", tool_context=_ctx(session_id=None))
    add.assert_not_called()
