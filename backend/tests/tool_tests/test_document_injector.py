"""Tests for make_document_injector (adk/callbacks.py).

The injector is a before_model_callback that eagerly inlines loaded
document blocks into the LLM request whenever any documents are
attached — bypassing the agent's choice to call ``load_artifacts``
(which Gemini sometimes flubs by passing empty arg lists). Scope
broadened from "only resumed sessions" to "any attached docs" in
commit 6a1e440 (chat-history-deep-fixes-3 / Bug F): fresh chats with
attached docs were relying on Gemini's flaky tool-discovery and the
agent kept missing the doc.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai.types import Blob, Content, FunctionResponse, Part

from adk.callbacks import _STATE_DOCS_LOADED, _STATE_RESUMED_SESSION, make_document_injector


def _make_artifact(blocks: list[dict]) -> Part:
    return Part(
        inline_data=Blob(
            data=json.dumps(blocks).encode("utf-8"),
            mime_type="application/json",
        )
    )


def _make_ctx(state: dict | None, artifacts: dict[str, Part] | None = None) -> MagicMock:
    """Build a CallbackContext mock with an async load_artifact backed by a dict."""
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    arts = artifacts or {}

    async def _load_artifact(*, filename: str):
        return arts.get(filename)

    ctx.load_artifact = AsyncMock(side_effect=_load_artifact)
    return ctx


def _make_request(user_text: str = "what's in this?") -> SimpleNamespace:
    """Build an LlmRequest-shaped object with the user message at the end."""
    return SimpleNamespace(contents=[Content(role="user", parts=[Part.from_text(text=user_text)])])


_BLOCKS_A = [{"type": "paragraph", "text": "Doc A content."}]
_BLOCKS_B = [{"type": "paragraph", "text": "Doc B content."}]


class TestMakeDocumentInjector:
    @pytest.mark.asyncio
    async def test_fires_on_fresh_chats_with_attached_docs(self):
        """Bug F (commit 6a1e440): fresh chats with docs in
        ``_STATE_DOCS_LOADED`` must also get eager injection — the
        prior "resumed-only" gate left Gemini guessing whether to call
        ``load_artifacts`` and it sometimes guessed wrong, leaving the
        agent saying it couldn't find the doc.
        """
        injector = make_document_injector()
        ctx = _make_ctx(
            state={_STATE_DOCS_LOADED: ["docA"]},
            artifacts={"doc:docA.json": _make_artifact(_BLOCKS_A)},
        )
        req = _make_request()
        original_count = len(req.contents)

        await injector(ctx, req)

        assert len(req.contents) == original_count + 1, (
            "fresh chats with attached docs must get eager injection — see Bug F in chat-history-deep-fixes-3"
        )
        ctx.load_artifact.assert_awaited_once_with(filename="doc:docA.json")

    @pytest.mark.asyncio
    async def test_no_op_when_no_loaded_docs(self):
        injector = make_document_injector()
        ctx = _make_ctx(state={_STATE_RESUMED_SESSION: True, _STATE_DOCS_LOADED: []})
        req = _make_request()

        await injector(ctx, req)

        assert len(req.contents) == 1
        ctx.load_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_injects_one_doc_before_user_message(self):
        injector = make_document_injector()
        ctx = _make_ctx(
            state={_STATE_RESUMED_SESSION: True, _STATE_DOCS_LOADED: ["docA"]},
            artifacts={"doc:docA.json": _make_artifact(_BLOCKS_A)},
        )
        req = _make_request("question")

        await injector(ctx, req)

        # [doc-A, user-question]
        assert len(req.contents) == 2
        injected_text = req.contents[0].parts[0].text
        assert "doc:docA.json" in injected_text
        assert "Doc A content" in injected_text
        assert req.contents[-1].parts[0].text == "question"

    @pytest.mark.asyncio
    async def test_injects_multiple_docs_in_order(self):
        injector = make_document_injector()
        ctx = _make_ctx(
            state={
                _STATE_RESUMED_SESSION: True,
                _STATE_DOCS_LOADED: ["docA", "docB"],
            },
            artifacts={
                "doc:docA.json": _make_artifact(_BLOCKS_A),
                "doc:docB.json": _make_artifact(_BLOCKS_B),
            },
        )
        req = _make_request("compare")

        await injector(ctx, req)

        # [doc-A, doc-B, user-compare]
        assert len(req.contents) == 3
        assert "Doc A content" in req.contents[0].parts[0].text
        assert "Doc B content" in req.contents[1].parts[0].text
        assert req.contents[-1].parts[0].text == "compare"

    @pytest.mark.asyncio
    async def test_skips_when_last_content_is_function_response(self):
        """In-turn tool roundtrips trail a function_response — don't re-inject."""
        injector = make_document_injector()
        ctx = _make_ctx(
            state={_STATE_RESUMED_SESSION: True, _STATE_DOCS_LOADED: ["docA"]},
            artifacts={"doc:docA.json": _make_artifact(_BLOCKS_A)},
        )
        # Last Content carries a function_response from a prior tool call —
        # this is a mid-turn LLM round, not the user's first message.
        function_part = Part(function_response=FunctionResponse(name="some_tool", response={"ok": True}))
        req = SimpleNamespace(
            contents=[
                Content(role="user", parts=[Part.from_text(text="prior question")]),
                Content(role="user", parts=[function_part]),
            ]
        )

        await injector(ctx, req)

        assert len(req.contents) == 2
        ctx.load_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_silently_skips_missing_artifact(self):
        injector = make_document_injector()
        ctx = _make_ctx(
            state={_STATE_RESUMED_SESSION: True, _STATE_DOCS_LOADED: ["docMissing"]},
            artifacts={},  # nothing to load
        )
        req = _make_request()

        await injector(ctx, req)

        # No injection, but no crash either.
        assert len(req.contents) == 1

    @pytest.mark.asyncio
    async def test_no_op_when_state_is_none(self):
        injector = make_document_injector()
        ctx = MagicMock()
        ctx.state = None
        ctx.load_artifact = AsyncMock()
        req = _make_request()

        await injector(ctx, req)

        ctx.load_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_op_when_request_has_no_contents(self):
        injector = make_document_injector()
        ctx = _make_ctx(
            state={_STATE_RESUMED_SESSION: True, _STATE_DOCS_LOADED: ["docA"]},
            artifacts={"doc:docA.json": _make_artifact(_BLOCKS_A)},
        )
        req = SimpleNamespace(contents=[])

        await injector(ctx, req)

        assert req.contents == []
        ctx.load_artifact.assert_not_called()
