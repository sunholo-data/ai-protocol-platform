"""Tests for make_document_loader (adk/callbacks.py) — M3 of DOC-AI-PIPELINE."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adk.callbacks import (
    _STATE_DOC_LOAD_ERROR,
    _STATE_DOCS_LOADED,
    make_document_loader,
)


def _make_ctx(
    state: dict | None = None,
    artifacts: dict[str, object] | None = None,
) -> MagicMock:
    """Build a minimal CallbackContext mock.

    ``artifacts`` backs ``load_artifact`` for the loader's orphan-recovery
    probe. Default empty dict means "all prior-loaded ids are orphans" —
    fine for unit tests that start with empty ``_STATE_DOCS_LOADED``.
    save_artifact also writes back into this dict so a later load_artifact
    sees the freshly-saved blob (mirrors InMemoryArtifactService).
    """
    arts: dict[str, object] = dict(artifacts or {})
    ctx = MagicMock()
    ctx.state = state if state is not None else {}

    async def _load_artifact(*, filename: str):
        return arts.get(filename)

    async def _save_artifact(*, filename: str, artifact):
        arts[filename] = artifact
        return None

    ctx.load_artifact = AsyncMock(side_effect=_load_artifact)
    ctx.save_artifact = AsyncMock(side_effect=_save_artifact)
    return ctx


_SAMPLE_BLOCKS_A = [
    {"type": "heading", "text": "Introduction A", "page": 1, "block_id": "a1"},
    {"type": "paragraph", "text": "Hello A.", "page": 1, "block_id": "a2"},
]
_SAMPLE_BLOCKS_B = [
    {"type": "heading", "text": "Introduction B", "page": 1, "block_id": "b1"},
]
_SAMPLE_BLOCKS_C = [
    {"type": "paragraph", "text": "Doc C content.", "page": 1, "block_id": "c1"},
]


def _blocks_for(doc_id: str, *_args, **_kwargs):
    mapping = {
        "docA": _SAMPLE_BLOCKS_A,
        "docB": _SAMPLE_BLOCKS_B,
        "docC": _SAMPLE_BLOCKS_C,
    }
    return ("ignored", mapping[doc_id])


class TestMakeDocumentLoader:
    """Tests for the make_document_loader factory."""

    @pytest.mark.asyncio
    async def test_no_document_ids_in_state_marks_loaded_and_noop(self):
        loader = make_document_loader()
        ctx = _make_ctx(state={})
        await loader(ctx)
        assert ctx.state[_STATE_DOCS_LOADED] == []
        ctx.save_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_saves_artifact_for_single_id(self):
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA"]})

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)

        ctx.save_artifact.assert_awaited_once()
        call_kwargs = ctx.save_artifact.call_args.kwargs
        assert call_kwargs["filename"] == "doc:docA.json"
        assert call_kwargs["artifact"].inline_data.mime_type == "application/json"
        assert ctx.state[_STATE_DOCS_LOADED] == ["docA"]
        assert _STATE_DOC_LOAD_ERROR not in ctx.state

    @pytest.mark.asyncio
    async def test_saves_one_artifact_per_document(self):
        """The bug we are fixing: multiple selected docs must all be injected."""
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA", "docB", "docC"]})

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)

        assert ctx.save_artifact.await_count == 3
        filenames = sorted(call.kwargs["filename"] for call in ctx.save_artifact.call_args_list)
        assert filenames == ["doc:docA.json", "doc:docB.json", "doc:docC.json"]
        # Loaded set tracks every id we've seen so far.
        assert sorted(ctx.state[_STATE_DOCS_LOADED]) == ["docA", "docB", "docC"]
        # Decode each artifact to make sure the right blocks went to the right filename.
        per_filename = {
            call.kwargs["filename"]: json.loads(call.kwargs["artifact"].inline_data.data)
            for call in ctx.save_artifact.call_args_list
        }
        assert per_filename["doc:docA.json"] == _SAMPLE_BLOCKS_A
        assert per_filename["doc:docB.json"] == _SAMPLE_BLOCKS_B
        assert per_filename["doc:docC.json"] == _SAMPLE_BLOCKS_C

    @pytest.mark.asyncio
    async def test_second_turn_only_loads_new_documents(self):
        """When the user adds a doc mid-session, the loader picks up just the new one."""
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA"]})

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)  # first turn — loads docA
            assert ctx.state[_STATE_DOCS_LOADED] == ["docA"]
            ctx.save_artifact.reset_mock()

            # Turn 2: user adds docB. docA is already loaded; only docB must be saved.
            ctx.state["document_ids"] = ["docA", "docB"]
            await loader(ctx)

        assert ctx.save_artifact.await_count == 1
        assert ctx.save_artifact.call_args.kwargs["filename"] == "doc:docB.json"
        assert sorted(ctx.state[_STATE_DOCS_LOADED]) == ["docA", "docB"]

    @pytest.mark.asyncio
    async def test_same_documents_next_turn_is_noop(self):
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA", "docB"]})

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)
            ctx.save_artifact.reset_mock()
            await loader(ctx)  # nothing new — must not save again

        ctx.save_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_failure_loads_other_docs_and_records_error(self):
        # Self-healing contract (2026-04-28): only successfully-saved docs
        # enter _STATE_DOCS_LOADED; the failed id is left out so the next
        # turn retries it once Firestore recovers.
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA", "bad", "docB"]})

        def _side_effect(doc_id, *_a, **_kw):
            if doc_id == "bad":
                raise RuntimeError("Firestore unavailable")
            return _blocks_for(doc_id)

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_side_effect,
        ):
            await loader(ctx)  # must not raise

        saved = sorted(call.kwargs["filename"] for call in ctx.save_artifact.call_args_list)
        assert saved == ["doc:docA.json", "doc:docB.json"]
        assert sorted(ctx.state[_STATE_DOCS_LOADED]) == ["docA", "docB"]
        assert "bad" not in ctx.state[_STATE_DOCS_LOADED]
        assert "bad" in ctx.state[_STATE_DOC_LOAD_ERROR]
        assert "Firestore unavailable" in ctx.state[_STATE_DOC_LOAD_ERROR]["bad"]

    @pytest.mark.asyncio
    async def test_not_yet_parsed_records_per_doc_error(self):
        # Self-healing: a doc that's still being parsed does NOT enter
        # _STATE_DOCS_LOADED, so the next turn retries it once parsing
        # completes — no need for the user to re-attach.
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["doc-pending"]})

        with patch(
            "tools.documents.context.build_document_context",
            return_value=("still processing", None),
        ):
            await loader(ctx)

        assert ctx.state[_STATE_DOCS_LOADED] == []
        assert "Document has no parsed content" in ctx.state[_STATE_DOC_LOAD_ERROR]["doc-pending"]
        ctx.save_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_blocks_records_per_doc_error(self):
        """Older documents may have blocks=[] in Firestore — don't save empty artifact.

        Same self-healing contract: failure leaves the id out of
        _STATE_DOCS_LOADED so a re-parse self-heals on the next turn.
        """
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["doc-old"]})

        with patch(
            "tools.documents.context.build_document_context",
            return_value=("", []),
        ):
            await loader(ctx)

        assert ctx.state[_STATE_DOCS_LOADED] == []
        assert "Document has no parsed content" in ctx.state[_STATE_DOC_LOAD_ERROR]["doc-old"]
        ctx.save_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_state_is_handled_gracefully(self):
        loader = make_document_loader()
        ctx = MagicMock()
        ctx.state = None
        ctx.save_artifact = AsyncMock()
        await loader(ctx)  # must not raise
        ctx.save_artifact.assert_not_called()


class TestDocumentLoaderSelfHealing:
    """Regression: transient failures must self-heal on the next turn.

    User report 2026-04-28: two doc tabs were ticked, but the agent said
    "I couldn't find an artifact named ..." and tried retrieve_artifact.
    Root cause: the loader's ``finally`` clause marked failures in
    ``_STATE_DOCS_LOADED`` alongside successes — so a one-off Firestore
    hiccup (or a doc that was still being parsed) permanently stranded
    the id. The injector then iterated ``_STATE_DOCS_LOADED``, called
    ``load_artifact("doc:{id}.json")``, got nothing, and silently skipped
    — leaving the agent with no document context and no recovery path
    short of the user re-attaching the doc.

    The contract these tests lock: ``_STATE_DOCS_LOADED`` only contains
    ids whose artifacts are actually present. A failed load retries on
    the next turn so transient failures fix themselves.
    """

    @pytest.mark.asyncio
    async def test_exception_does_not_strand_doc_in_loaded_set(self):
        """Turn 1: build_document_context raises (Firestore unavailable,
        permission glitch, etc.). The failure must NOT mark the doc as
        loaded — otherwise the injector skips it forever.
        """
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA"]})

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=RuntimeError("Firestore unavailable"),
        ):
            await loader(ctx)

        ctx.save_artifact.assert_not_called()
        assert ctx.state.get(_STATE_DOCS_LOADED) == [], (
            "failed loads must not be recorded in _STATE_DOCS_LOADED — "
            "otherwise the injector treats the id as ready and silently "
            "skips when load_artifact returns nothing"
        )
        assert "docA" in ctx.state[_STATE_DOC_LOAD_ERROR]
        assert "Firestore unavailable" in ctx.state[_STATE_DOC_LOAD_ERROR]["docA"]

    @pytest.mark.asyncio
    async def test_no_blocks_does_not_strand_doc_in_loaded_set(self):
        """Doc was still being parsed on turn 1 (build_document_context
        returned blocks=None). The retry path must give the parser a
        chance to finish on subsequent turns.
        """
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["doc-pending"]})

        with patch(
            "tools.documents.context.build_document_context",
            return_value=("still processing", None),
        ):
            await loader(ctx)

        assert ctx.state.get(_STATE_DOCS_LOADED) == []
        assert "doc-pending" in ctx.state[_STATE_DOC_LOAD_ERROR]

    @pytest.mark.asyncio
    async def test_transient_failure_self_heals_on_next_turn(self):
        """End-to-end self-heal: turn 1 errors, turn 2 succeeds. Reproduces
        the user-visible bug shape: pre-fix the agent would never see the
        document on turn 2 because docA was already in _STATE_DOCS_LOADED
        with no artifact behind it. Post-fix, turn 2 retries and saves.
        """
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA"]})

        # Turn 1: transient failure.
        with patch(
            "tools.documents.context.build_document_context",
            side_effect=RuntimeError("Firestore unavailable"),
        ):
            await loader(ctx)
        ctx.save_artifact.assert_not_called()

        # Turn 2: backend recovered. The loader must retry docA (it isn't
        # in the loaded set yet) and save its artifact this time.
        ctx.save_artifact.reset_mock()
        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)

        ctx.save_artifact.assert_awaited_once()
        assert ctx.save_artifact.call_args.kwargs["filename"] == "doc:docA.json"
        assert ctx.state[_STATE_DOCS_LOADED] == ["docA"]
        # The error from turn 1 should be cleared once the retry succeeds
        # — a stale "Firestore unavailable" in state is misleading once
        # the doc actually loaded.
        assert "docA" not in (ctx.state.get(_STATE_DOC_LOAD_ERROR) or {})

    @pytest.mark.asyncio
    async def test_orphan_in_loaded_set_is_dropped_and_re_loaded(self):
        """Recovery path for sessions stranded by the *pre*-2026-04-28 loader.

        Pre-fix: a failed load still appended the id to ``_STATE_DOCS_LOADED``.
        Post-fix doesn't help those existing sessions — the id stays in the
        loaded set forever, ``to_load`` excludes it, and the agent never sees
        the doc no matter how many turns the user tries.

        The loader now probes ``load_artifact`` for every prior-loaded id
        at turn start; ids whose artifact is missing get dropped from the
        loaded set so they fall into ``to_load`` and re-save.
        """
        loader = make_document_loader()
        # docA is recorded as loaded but no artifact behind it (the strand).
        ctx = _make_ctx(
            state={
                "document_ids": ["docA"],
                _STATE_DOCS_LOADED: ["docA"],
            },
            artifacts={},  # NO doc:docA.json artifact present
        )

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_blocks_for,
        ):
            await loader(ctx)

        # The orphan was dropped, the id fell into to_load, and the
        # artifact was actually saved this time.
        ctx.save_artifact.assert_awaited_once()
        assert ctx.save_artifact.call_args.kwargs["filename"] == "doc:docA.json"
        assert ctx.state[_STATE_DOCS_LOADED] == ["docA"]

    @pytest.mark.asyncio
    async def test_partial_failure_only_strands_failed_ids_other_loads_succeed(self):
        """Mixed batch: 2 succeed, 1 fails. Successful ids enter
        _STATE_DOCS_LOADED so we don't re-save them; the failed id is
        absent so the next turn retries it.
        """
        loader = make_document_loader()
        ctx = _make_ctx(state={"document_ids": ["docA", "bad", "docB"]})

        def _side_effect(doc_id, *_a, **_kw):
            if doc_id == "bad":
                raise RuntimeError("transient")
            return _blocks_for(doc_id)

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=_side_effect,
        ):
            await loader(ctx)

        saved = sorted(call.kwargs["filename"] for call in ctx.save_artifact.call_args_list)
        assert saved == ["doc:docA.json", "doc:docB.json"]
        assert sorted(ctx.state[_STATE_DOCS_LOADED]) == ["docA", "docB"]
        assert "bad" not in ctx.state[_STATE_DOCS_LOADED]
        assert "bad" in ctx.state[_STATE_DOC_LOAD_ERROR]
