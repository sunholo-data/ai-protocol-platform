"""Tests for the RAG doc-loader user-id resolution + attached-id hint.

Covers the fix for: RAG mode silently skipped doc-loading because `user:id`
was never populated in state, and even when it imports, the model was never
told which docs were attached (so compare/extract skills said "nothing attached").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from adk.callbacks import _inject_rag_doc_id_hint, _resolve_user_id


def test_resolve_user_id_prefers_invocation_context():
    cc = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="uid-123"))
    assert _resolve_user_id(cc, {"user:id": "stale"}) == "uid-123"


def test_resolve_user_id_falls_back_to_state():
    cc = SimpleNamespace(_invocation_context=None)
    assert _resolve_user_id(cc, {"user:id": "s-uid"}) == "s-uid"
    assert _resolve_user_id(cc, {"user_id": "s-uid2"}) == "s-uid2"


def test_resolve_user_id_empty_when_nowhere():
    assert _resolve_user_id(SimpleNamespace(_invocation_context=None), {}) == ""


def test_rag_hint_injected_before_last_user_message():
    user_msg = SimpleNamespace(role="user", parts=[])
    contents = [user_msg]
    _inject_rag_doc_id_hint(
        SimpleNamespace(state={"document_ids": ["doc-A", "doc-B"]}),
        SimpleNamespace(contents=contents),
    )
    assert len(contents) == 2  # hint inserted
    assert contents[1] is user_msg  # before the user's question
    text = "".join(getattr(p, "text", "") for p in contents[0].parts)
    assert "doc-A, doc-B" in text
    assert "do not" in text.lower() or "do NOT" in text


def test_rag_hint_noop_without_attached_docs():
    user_msg = SimpleNamespace(role="user", parts=[])
    contents = [user_msg]
    _inject_rag_doc_id_hint(
        SimpleNamespace(state={"document_ids": []}),
        SimpleNamespace(contents=contents),
    )
    assert len(contents) == 1  # unchanged


def test_rag_hint_noop_on_mid_turn_tool_roundtrip():
    fn_part = SimpleNamespace(function_response={"name": "x"})
    last = SimpleNamespace(role="user", parts=[fn_part])
    contents = [last]
    _inject_rag_doc_id_hint(
        SimpleNamespace(state={"document_ids": ["doc-A"]}),
        SimpleNamespace(contents=contents),
    )
    assert len(contents) == 1  # not re-injected mid-turn


def test_rag_hint_surfaces_load_errors_to_ai():
    """When _rag_loader recorded a RAG import failure for an attached doc, the
    injected hint tells the AI search is degraded so it uses doc_id tools and
    warns the user — fail loudly to the AI, degrade gracefully to the user."""
    from adk.callbacks import _STATE_DOC_LOAD_ERROR

    user_msg = SimpleNamespace(role="user", parts=[])
    contents = [user_msg]
    _inject_rag_doc_id_hint(
        SimpleNamespace(
            state={
                "document_ids": ["doc-A", "doc-B"],
                _STATE_DOC_LOAD_ERROR: {"doc-A": "could not add to document search (RAG import failed: 403 denied)"},
            }
        ),
        SimpleNamespace(contents=contents),
    )
    text = "".join(getattr(p, "text", "") for p in contents[0].parts)
    assert "WARNING" in text and "degraded" in text
    assert "403 denied" in text  # the concrete reason reaches the model
    assert "compare_ppa_contracts" in text  # steered to the direct-doc_id tool
    assert "temporarily unavailable" in text  # instructed to tell the user


def test_rag_hint_no_warning_when_loads_clean():
    """No error recorded → plain hint, no scary warning."""
    user_msg = SimpleNamespace(role="user", parts=[])
    contents = [user_msg]
    _inject_rag_doc_id_hint(
        SimpleNamespace(state={"document_ids": ["doc-A"]}),
        SimpleNamespace(contents=contents),
    )
    text = "".join(getattr(p, "text", "") for p in contents[0].parts)
    assert "WARNING" not in text


# --- _rag_loader graceful degradation (must never kill the turn) --------------


@pytest.mark.asyncio
async def test_rag_loader_records_error_and_never_raises_on_import_failure():
    """A RAG import 403 (Vertex RAG SA can't read the source bucket) must be
    caught, recorded for the AI, and NOT propagate — the turn continues and
    doc_id tools still work off parsed_documents."""
    from adk.callbacks import _STATE_DOC_LOAD_ERROR, _rag_loader

    state: dict = {"user:id": "u1"}
    cc = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(user_id="u1"), session=None)
    with (
        patch("rag.corpus.get_or_create_user_corpus", new=AsyncMock(return_value="corpus/1")),
        patch("rag.corpus.import_document_from_gcs", new=AsyncMock(side_effect=Exception("403 denied on bucket"))),
        patch("db.firestore.get_document", return_value={"sourceUrl": "gs://b/x.pdf"}),
    ):
        await _rag_loader(cc, state, ["doc-A"])  # must not raise

    errors = state.get(_STATE_DOC_LOAD_ERROR) or {}
    assert "doc-A" in errors and "403" in errors["doc-A"]


@pytest.mark.asyncio
async def test_rag_loader_degrades_when_whole_corpus_unavailable():
    """If the corpus itself can't be reached, every attached doc gets an error
    and the loader returns without raising."""
    from adk.callbacks import _STATE_DOC_LOAD_ERROR, _rag_loader

    state: dict = {"user:id": "u1"}
    cc = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(user_id="u1"), session=None)
    with patch("rag.corpus.get_or_create_user_corpus", new=AsyncMock(side_effect=Exception("corpus 403"))):
        await _rag_loader(cc, state, ["doc-A", "doc-B"])  # must not raise

    errors = state.get(_STATE_DOC_LOAD_ERROR) or {}
    assert set(errors) == {"doc-A", "doc-B"}


@pytest.mark.asyncio
async def test_rag_loader_clears_stale_error_on_successful_retry():
    """A later turn where the import succeeds must clear the prior error so the
    AI isn't told search is down when it's actually back."""
    from adk.callbacks import _STATE_DOC_LOAD_ERROR, _STATE_DOCS_FILES, _rag_loader

    state: dict = {"user:id": "u1", _STATE_DOC_LOAD_ERROR: {"doc-A": "old failure"}}
    cc = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(user_id="u1"), session=None)
    with (
        patch("rag.corpus.get_or_create_user_corpus", new=AsyncMock(return_value="corpus/1")),
        patch("rag.corpus.import_document_from_gcs", new=AsyncMock(return_value=None)),
        patch("db.firestore.get_document", return_value={"sourceUrl": "gs://b/x.pdf"}),
    ):
        await _rag_loader(cc, state, ["doc-A"])

    assert "doc-A" in (state.get(_STATE_DOCS_FILES) or [])
    assert _STATE_DOC_LOAD_ERROR not in state  # cleared
