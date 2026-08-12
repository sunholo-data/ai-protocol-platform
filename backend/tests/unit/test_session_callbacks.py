"""Unit tests for CHAT-HISTORY M2 session callbacks.

Covers:
- make_session_tracker: creates index on first turn, skips on subsequent turns
- make_after_agent_response: bumps counter, flushes every 5 turns, generates
  title after turn 2 (not turn 1, not turn 3), never crashes on errors
- _derive_access_control: falls back to private when document unavailable
- _try_generate_title: returns None on any exception
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adk.callbacks import (
    _STATE_INITIALIZED,
    _STATE_TURN_COUNT,
    _TURN_FLUSH_INTERVAL,
    _derive_access_control,
    _try_generate_title,
    make_after_agent_response,
    make_session_tracker,
)
from db.models.access import AccessControl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    session_id="sess-1",
    initialized=False,
    turn_count=0,
    skill_id="skill-x",
    document_ids: list[str] | None = None,
):
    state = {
        "skill_id": skill_id,
        "document_ids": document_ids or [],
    }
    if initialized:
        state[_STATE_INITIALIZED] = True
        state[_STATE_TURN_COUNT] = turn_count

    session = MagicMock()
    session.id = session_id
    session.events = []

    ctx = MagicMock()
    ctx.state = state
    ctx.session = session
    return ctx


# ---------------------------------------------------------------------------
# make_session_tracker
# ---------------------------------------------------------------------------


class TestMakeSessionTracker:
    @patch("adk.callbacks.create_session_index", create=True)
    @patch("db.chat_sessions.create_session_index")
    def test_creates_index_on_first_turn(self, mock_create, _mock2):
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=False)

        with patch("adk.callbacks._derive_access_control", return_value=AccessControl(type="private")):
            tracker(ctx)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["session_id"] == "sess-1"
        assert call_kwargs["owner_uid"] == "uid-owner"
        assert call_kwargs["document_ids"] == []
        assert ctx.state[_STATE_INITIALIZED] is True
        assert ctx.state[_STATE_TURN_COUNT] == 0

    @patch("adk.callbacks.create_session_index", create=True)
    @patch("db.chat_sessions.create_session_index")
    def test_passes_full_document_ids_list(self, mock_create, _mock2):
        """First-turn index creation must include every doc the user attached
        so list_sessions_for_document(array_contains) finds the session under
        each of its docs' history panels."""
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=False, document_ids=["docA", "docB", "docC"])

        with patch("adk.callbacks._derive_access_control", return_value=AccessControl(type="private")):
            tracker(ctx)

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["document_ids"] == ["docA", "docB", "docC"]

    @patch("db.chat_sessions.create_session_index")
    def test_skips_on_subsequent_turns(self, mock_create):
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=True, turn_count=3)

        tracker(ctx)

        mock_create.assert_not_called()

    @patch("adk.callbacks.get_session_index", create=True)
    @patch("db.chat_sessions.create_session_index")
    def test_create_session_index_is_idempotent(self, mock_create, mock_get):
        """B1 idempotency (chat-history-fixes): when ``process_skill_request``
        has already written the index synchronously, the before_agent_callback
        must skip its own write — otherwise it clobbers any title / turnCount /
        documentIds updates that landed between the synchronous write and the
        callback firing.

        The callback should observe the existing row (via ``get_session_index``)
        and short-circuit, marking state as initialised so subsequent turns
        also skip. Pre-fix the callback only checked the in-memory state flag,
        so it always re-wrote on turn 1.
        """
        from datetime import UTC, datetime

        from db.models.chat_session import ChatSessionIndex

        existing = ChatSessionIndex(
            sessionId="sess-1",
            skillId="skill-x",
            ownerUid="uid-owner",
            accessControl=AccessControl(type="private"),
            firstMessageAt=datetime.now(UTC),
            lastMessageAt=datetime.now(UTC),
        )
        mock_get.return_value = existing
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=False)

        tracker(ctx)

        mock_create.assert_not_called()
        assert ctx.state[_STATE_INITIALIZED] is True, (
            "B1 idempotency: callback must mark state initialised when row already "
            "exists, so future turns short-circuit on the in-memory flag."
        )

    @patch("db.chat_sessions.create_session_index", side_effect=Exception("Firestore down"))
    def test_does_not_crash_on_firestore_error(self, _mock):
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=False)

        with patch("adk.callbacks._derive_access_control", return_value=AccessControl(type="private")):
            tracker(ctx)  # must not raise

        assert _STATE_INITIALIZED not in ctx.state  # flag not set on failure

    def test_no_op_when_state_is_none(self):
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = MagicMock()
        ctx.state = None
        tracker(ctx)  # must not raise

    def test_no_op_when_session_id_missing(self):
        tracker = make_session_tracker("uid-owner", "skill-x")
        ctx = _make_ctx(initialized=False)
        ctx.session.id = None
        tracker(ctx)  # must not raise


# ---------------------------------------------------------------------------
# make_after_agent_response
# ---------------------------------------------------------------------------


class TestMakeAfterAgentResponse:
    @patch("db.chat_sessions.update_session_fields")
    def test_increments_turn_count(self, mock_update):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=5)

        cb(ctx)

        assert ctx.state[_STATE_TURN_COUNT] == 6

    @patch("db.chat_sessions.update_session_fields")
    def test_no_flush_on_turn_3(self, mock_update):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=2)  # will become 3

        cb(ctx)

        mock_update.assert_not_called()

    @patch("db.chat_sessions.update_session_fields")
    def test_flushes_on_turn_5(self, mock_update):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=_TURN_FLUSH_INTERVAL - 1)  # will become 5

        cb(ctx)

        mock_update.assert_called_once()

    @patch("adk.callbacks._try_generate_title", return_value="Revenue drivers Q1")
    @patch("db.chat_sessions.update_session_fields")
    def test_generates_title_after_turn_2(self, mock_update, mock_title):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=1)  # will become 2

        cb(ctx)

        mock_title.assert_called_once()
        update_kwargs = mock_update.call_args[0][1]
        assert update_kwargs["title"] == "Revenue drivers Q1"

    @patch("adk.callbacks._try_generate_title", return_value="some title")
    @patch("db.chat_sessions.update_session_fields")
    def test_no_title_on_turn_1(self, mock_update, mock_title):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=0)  # will become 1

        cb(ctx)

        mock_title.assert_not_called()

    @patch("adk.callbacks._try_generate_title", return_value="some title")
    @patch("db.chat_sessions.update_session_fields")
    def test_no_title_on_turn_3(self, mock_update, mock_title):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=2)  # will become 3

        cb(ctx)

        mock_title.assert_not_called()

    @patch("db.chat_sessions.update_session_fields", side_effect=Exception("Firestore error"))
    def test_does_not_crash_on_update_error(self, _mock):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=_TURN_FLUSH_INTERVAL - 1)

        cb(ctx)  # must not raise

    def test_no_op_when_not_initialized(self):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=False)

        with patch("db.chat_sessions.update_session_fields") as mock_update:
            cb(ctx)
            mock_update.assert_not_called()

    @patch("adk.callbacks.add_session_documents", create=True)
    @patch("db.chat_sessions.update_session_fields")
    def test_after_agent_callback_syncs_document_ids_to_firestore_index(self, _mock_update, mock_add):
        """B2 (chat-history-fixes): docs added to ADK session state after turn 1
        (e.g. via ``make_document_loader`` when a user opens another tab) must
        be ArrayUnion-ed into ``chat_sessions/{id}.documentIds`` on every flush.

        Pre-fix ``_flush_session_index`` synced turnCount / lastMessageAt /
        title but never wrote ``documentIds``. The result: docs opened mid-
        session were missing from the index, so ``list_sessions_for_document``
        (filters by ``array_contains``) failed to surface the session in the
        Conversations panel for those docs.

        Post-fix: the after_agent_callback calls ``add_session_documents``
        with the current state's ``document_ids`` on every flush so
        ArrayUnion keeps Firestore in sync.
        """
        cb = make_after_agent_response()
        ctx = _make_ctx(
            initialized=True,
            turn_count=_TURN_FLUSH_INTERVAL - 1,  # will become 5 → flush
            document_ids=["docA", "docB", "docC"],
        )

        cb(ctx)

        mock_add.assert_called_once_with("sess-1", ["docA", "docB", "docC"])

    @patch("adk.callbacks._try_generate_title")
    @patch("db.chat_sessions.update_session_fields")
    def test_title_regenerates_when_turn_two_returns_empty(self, mock_update, mock_title):
        """B3 (chat-history-fixes): if turn-2 title generation returns ``None``
        (thin context — e.g. a one-word user message), the callback must retry
        on a later flush turn. Pre-fix, ``needs_title_gen = turn_count == 2``
        only — so a None at turn 2 meant the session never got a title.

        Post-fix: condition becomes ``turn_count == 2 or (turn_count >= 4 and
        not state.get("titleSet"))``. Successful generation sets
        ``state["titleSet"] = True`` so we retry only when needed.
        """
        cb = make_after_agent_response()

        # --- Turn 2: generation returns None (thin context) ---
        mock_title.return_value = None
        ctx = _make_ctx(initialized=True, turn_count=1)  # → 2
        cb(ctx)
        assert mock_title.call_count == 1, "first attempt fires on turn 2"
        assert not ctx.state.get("titleSet"), "B3: titleSet must remain False when generation returned None"

        # --- Turn 5 (next flush boundary): retry, this time succeeds ---
        mock_title.return_value = "Generated title"
        ctx.state[_STATE_TURN_COUNT] = 4  # → 5, multiple of _TURN_FLUSH_INTERVAL
        cb(ctx)

        assert mock_title.call_count == 2, (
            "B3: title generation must be retried at turn 5 because "
            "state['titleSet'] was never set on turn 2 (generation returned None)."
        )
        # Find the call that wrote the new title
        title_writes = [c.args[1] for c in mock_update.call_args_list if c.args[1].get("title") == "Generated title"]
        assert title_writes, (
            "B3: turn-5 retry must include title='Generated title' in the "
            f"Firestore update payload. Got: {[c.args[1] for c in mock_update.call_args_list]}"
        )
        assert ctx.state.get("titleSet") is True, (
            "B3: successful generation must set state['titleSet']=True so further turns short-circuit the retry path."
        )


# ---------------------------------------------------------------------------
# _derive_access_control
# ---------------------------------------------------------------------------


class TestDeriveAccessControl:
    def test_no_document_id_returns_private(self):
        ac = _derive_access_control(None)
        assert ac.type == "private"

    @patch("db.firestore.get_document", return_value={"accessControl": {"type": "tagged", "tags": ["finance"]}})
    def test_inherits_document_access_control(self, _mock):
        ac = _derive_access_control("doc-abc")
        assert ac.type == "tagged"
        assert ac.tags == ["finance"]

    @patch("db.firestore.get_document", return_value=None)
    def test_falls_back_to_private_when_doc_missing(self, _mock):
        ac = _derive_access_control("doc-missing")
        assert ac.type == "private"

    @patch("db.firestore.get_document", side_effect=Exception("network error"))
    def test_falls_back_to_private_on_error(self, _mock):
        ac = _derive_access_control("doc-xyz")
        assert ac.type == "private"


# ---------------------------------------------------------------------------
# _try_generate_title
# ---------------------------------------------------------------------------


class TestDocumentInjectorBugF:
    """Bug F (chat-history-deep-fixes-3): when the user clicks a document
    and asks about it in a FRESH chat (no resume), the agent doesn't see
    the document — load_artifacts_tool is unreliable, so the agent often
    answers "you haven't provided a document".

    Pre-fix: ``make_document_injector`` is gated on
    ``state[_STATE_RESUMED_SESSION] == True`` and skips fresh sessions
    entirely. Post-fix: inject whenever there are loaded docs, regardless
    of resume state. The per-turn-first-model-call check inside the
    injector still prevents re-injection during in-turn tool roundtrips.
    """

    @pytest.fixture
    def llm_request_with_user_message(self):
        from google.genai.types import Content, Part

        # Last content is a user message — this is the "first model call
        # of the turn" condition the injector checks.
        return MagicMock(
            contents=[
                Content(role="user", parts=[Part.from_text(text="What's in this doc?")]),
            ]
        )

    @pytest.fixture
    def fake_artifact(self):
        """A loaded doc artifact with inline JSON blocks."""
        from unittest.mock import MagicMock

        artifact = MagicMock()
        artifact.inline_data = MagicMock()
        artifact.inline_data.data = b'[{"type":"heading","text":"Hello"}]'
        return artifact

    @pytest.mark.asyncio
    async def test_d_bug_f_injector_runs_on_fresh_chat_when_docs_attached(
        self, llm_request_with_user_message, fake_artifact
    ):
        """Diagnostic + fix-locking: a fresh chat (resumed_session unset)
        with loaded docs MUST get them eagerly inlined. Pre-fix this
        test fails because the injector returns early on the resume gate.
        Post-fix: doc content is prepended to llm_request.contents.
        """
        from adk.callbacks import _STATE_DOCS_LOADED, make_document_injector

        ctx = MagicMock()
        ctx.state = {
            # NOTE: _STATE_RESUMED_SESSION is intentionally NOT set —
            # this is a fresh chat where the user attached a doc by
            # clicking it in the file browser.
            _STATE_DOCS_LOADED: ["doc-abc"],
        }
        ctx.load_artifact = MagicMock(return_value=fake_artifact)

        # Make load_artifact awaitable — ADK's API is async.
        async def _load(filename: str):
            return fake_artifact

        ctx.load_artifact = _load

        injector = make_document_injector()
        before_count = len(llm_request_with_user_message.contents)
        await injector(ctx, llm_request_with_user_message)
        after_count = len(llm_request_with_user_message.contents)

        assert after_count == before_count + 1, (
            "Bug F: fresh chat with attached docs must get the doc eagerly "
            "inlined (since load_artifacts_tool is unreliable). Pre-fix the "
            "injector gates on _STATE_RESUMED_SESSION and skips entirely."
        )

    @pytest.mark.asyncio
    async def test_d_bug_f_injector_still_skips_when_no_docs_loaded(self, llm_request_with_user_message):
        """Negative case: empty doc list — no injection. Locks the floor:
        the fix shouldn't make every turn pre-fetch artifacts.
        """
        from adk.callbacks import make_document_injector

        ctx = MagicMock()
        ctx.state = {}  # nothing loaded
        injector = make_document_injector()

        before_count = len(llm_request_with_user_message.contents)
        await injector(ctx, llm_request_with_user_message)
        after_count = len(llm_request_with_user_message.contents)

        assert after_count == before_count

    @pytest.mark.asyncio
    async def test_multi_doc_injector_prepends_all_loaded_docs_distinct_content(self, llm_request_with_user_message):
        """User report (2026-04-28): added a second doc tab mid-session and
        the agent kept seeing only the first one. Locks the multi-doc
        injection contract: when ``_STATE_DOCS_LOADED`` has multiple ids,
        the injector must prepend each one with its OWN content.
        """
        from adk.callbacks import _STATE_DOCS_LOADED, make_document_injector

        privacy = MagicMock()
        privacy.inline_data = MagicMock()
        privacy.inline_data.data = b'[{"type":"heading","text":"PRIVACY NOTICE southwest cornwall"}]'

        claim = MagicMock()
        claim.inline_data = MagicMock()
        claim.inline_data.data = b'[{"type":"heading","text":"CLAIM INCIDENT 158898 fraudulent calls"}]'

        async def _load(filename: str):
            if filename == "doc:privacy.json":
                return privacy
            if filename == "doc:claim.json":
                return claim
            return None

        ctx = MagicMock()
        ctx.state = {_STATE_DOCS_LOADED: ["privacy", "claim"]}
        ctx.load_artifact = _load

        injector = make_document_injector()
        before_count = len(llm_request_with_user_message.contents)
        await injector(ctx, llm_request_with_user_message)
        after_count = len(llm_request_with_user_message.contents)

        assert after_count == before_count + 2, (
            f"Expected both docs prepended (before={before_count}, after={after_count}). "
            "If only one was added, the injector dropped the second doc — "
            "the multi-doc bug the user reported."
        )

        # Each injected Content must contain ITS OWN doc's content.
        injected_texts: list[str] = []
        for c in llm_request_with_user_message.contents[:-1]:
            for p in getattr(c, "parts", []) or []:
                txt = getattr(p, "text", "")
                if "[Attached document:" in txt:
                    injected_texts.append(txt)

        assert len(injected_texts) == 2, (
            f"Expected 2 attached-document Contents, got {len(injected_texts)}: {injected_texts}"
        )
        privacy_seen = any("PRIVACY NOTICE" in t for t in injected_texts)
        claim_seen = any("CLAIM INCIDENT" in t for t in injected_texts)
        assert privacy_seen and claim_seen, (
            f"Both docs prepended but content diverged. privacy_seen={privacy_seen} "
            f"claim_seen={claim_seen}. Texts: {injected_texts}"
        )

    @pytest.mark.asyncio
    async def test_multi_doc_loader_loads_each_new_doc_across_turns(self):
        """Multi-turn doc-add scenario (matches the user's UI flow):
        Turn 1 — state.document_ids = [privacy] → loader saves doc:privacy.json,
        state[_STATE_DOCS_LOADED] = [privacy].
        Turn 2 — state.document_ids = [privacy, claim] → loader picks up
        [claim] as new and saves doc:claim.json with the CLAIM doc's blocks
        (NOT a stale copy of privacy).

        If this test fails the bug is in the loader's incremental load path.
        """
        from adk.callbacks import _STATE_DOCS_LOADED, make_document_loader

        blocks_by_doc = {
            "privacy": [{"type": "heading", "text": "PRIVACY NOTICE southwest cornwall"}],
            "claim": [{"type": "heading", "text": "CLAIM INCIDENT 158898 fraudulent calls"}],
        }

        saved: dict[str, bytes] = {}

        async def _load_artifact(filename: str):
            doc_id = filename.replace("doc:", "").replace(".json", "")
            data = saved.get(doc_id)
            if data is None:
                return None
            art = MagicMock()
            art.inline_data = MagicMock()
            art.inline_data.data = data
            return art

        async def _save_artifact(filename: str, artifact):
            doc_id = filename.replace("doc:", "").replace(".json", "")
            saved[doc_id] = artifact.inline_data.data

        ctx = MagicMock()
        ctx.state = {"document_ids": ["privacy"]}
        ctx.load_artifact = _load_artifact
        ctx.save_artifact = _save_artifact
        ctx.session = MagicMock()
        ctx.session.id = None  # skip the chat_sessions Firestore mirror

        loader = make_document_loader()

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=lambda doc_id, mode="blocks": (None, blocks_by_doc[doc_id]),
        ):
            # Turn 1: only privacy
            await loader(ctx)
            assert ctx.state[_STATE_DOCS_LOADED] == ["privacy"], (
                f"Turn 1: expected docs_loaded=['privacy'], got {ctx.state[_STATE_DOCS_LOADED]}"
            )
            assert "privacy" in saved
            assert b"PRIVACY NOTICE" in saved["privacy"]

            # Turn 2: user adds claim
            ctx.state["document_ids"] = ["privacy", "claim"]
            await loader(ctx)
            assert ctx.state[_STATE_DOCS_LOADED] == ["privacy", "claim"], (
                f"Turn 2: expected docs_loaded=['privacy','claim'], got "
                f"{ctx.state[_STATE_DOCS_LOADED]}. The new doc was NOT picked up — "
                "this is the multi-doc add-mid-session bug."
            )
            assert "claim" in saved
            assert b"CLAIM INCIDENT" in saved["claim"]
            assert b"PRIVACY NOTICE" not in saved["claim"], "claim artifact contains privacy's content — content bleed."


class TestLoaderTurnOneInvariant:
    """Stranded-session-prevention (1.23) — Option 2.

    When ``make_document_loader`` finishes turn 1 with every requested
    doc failing to load, the session row will land with ``documentIds=[]``
    and never appear in any per-doc Conversations panel until a
    subsequent turn succeeds. Today this is silent — only per-doc
    WARNINGs fire and they get lost in noise. We need exactly ONE
    ERROR with the session id + failing doc ids so the condition is
    greppable in ``.dev-logs/backend.log`` and Cloud Logging.
    """

    @pytest.mark.asyncio
    async def test_loader_logs_error_when_every_doc_fails_on_turn_one(self, caplog):
        """Mock build_document_context to raise for every doc, run the
        loader once, assert exactly one ERROR record was emitted with
        the session id and the failing doc ids.
        """
        import logging

        from adk.callbacks import make_document_loader

        async def _load_artifact(filename: str):
            return None  # no orphan artifacts

        async def _save_artifact(filename: str, artifact):
            # No-op: tests assert via logs, not artifact persistence.
            return None

        ctx = MagicMock()
        ctx.state = {"document_ids": ["doc-a", "doc-b"]}
        ctx.load_artifact = _load_artifact
        ctx.save_artifact = _save_artifact
        ctx.session = MagicMock()
        ctx.session.id = "sess-stranded-1"

        loader = make_document_loader()

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=RuntimeError("Firestore unavailable"),
        ):
            with caplog.at_level(logging.ERROR, logger="adk.callbacks"):
                await loader(ctx)

        invariant_records = [
            r for r in caplog.records if r.levelno == logging.ERROR and "TURN-1 INVARIANT" in r.getMessage()
        ]
        assert len(invariant_records) == 1, (
            f"Expected exactly one TURN-1 INVARIANT ERROR, got {len(invariant_records)}. "
            f"All ERROR records: {[r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]}"
        )
        msg = invariant_records[0].getMessage()
        assert "sess-stranded-1" in msg, f"ERROR must include session id: {msg}"
        assert "doc-a" in msg and "doc-b" in msg, f"ERROR must include failing doc ids: {msg}"

    @pytest.mark.asyncio
    async def test_loader_does_not_log_invariant_when_some_docs_succeed(self, caplog):
        """Negative case: if at least one doc loaded, the aggregate
        invariant did not fire — only the per-doc WARNINGs should fire.
        Locks the floor: this ERROR is reserved for the structural
        ALL-failed case.
        """
        import logging

        from adk.callbacks import make_document_loader

        async def _load_artifact(filename: str):
            return None

        async def _save_artifact(filename: str, artifact):
            # No-op: tests assert via logs, not artifact persistence.
            return None

        ctx = MagicMock()
        ctx.state = {"document_ids": ["doc-good", "doc-bad"]}
        ctx.load_artifact = _load_artifact
        ctx.save_artifact = _save_artifact
        ctx.session = MagicMock()
        ctx.session.id = "sess-mixed-1"

        loader = make_document_loader()

        def _fake_build(doc_id, mode="blocks"):
            if doc_id == "doc-good":
                return (None, [{"type": "heading", "text": "OK"}])
            raise RuntimeError("Firestore unavailable")

        with patch("tools.documents.context.build_document_context", side_effect=_fake_build):
            with patch("db.chat_sessions.add_session_documents"):
                with caplog.at_level(logging.ERROR, logger="adk.callbacks"):
                    await loader(ctx)

        invariant_records = [
            r for r in caplog.records if r.levelno == logging.ERROR and "TURN-1 INVARIANT" in r.getMessage()
        ]
        assert len(invariant_records) == 0, (
            f"Invariant must NOT fire when at least one doc loaded. Got: {[r.getMessage() for r in invariant_records]}"
        )

    @pytest.mark.asyncio
    async def test_loader_does_not_log_invariant_on_subsequent_turn(self, caplog):
        """Negative case: the invariant is turn-1 specific. If prior
        turns already loaded a doc (``_STATE_DOCS_LOADED`` non-empty),
        a subsequent turn that fails to load a NEW doc is a different
        condition — covered by per-doc WARNINGs, not this aggregate ERROR.
        """
        import logging

        from adk.callbacks import _STATE_DOCS_LOADED, make_document_loader

        # Prior turn loaded "doc-old" successfully — its artifact is here.
        prior_artifact = MagicMock()
        prior_artifact.inline_data = MagicMock()
        prior_artifact.inline_data.data = b'[{"type":"heading","text":"OLD"}]'

        async def _load_artifact(filename: str):
            if filename == "doc:doc-old.json":
                return prior_artifact
            return None

        async def _save_artifact(filename: str, artifact):
            # No-op: tests assert via logs, not artifact persistence.
            return None

        ctx = MagicMock()
        # turn 2: doc-old already loaded, doc-new requested but will fail
        ctx.state = {
            "document_ids": ["doc-old", "doc-new"],
            _STATE_DOCS_LOADED: ["doc-old"],
        }
        ctx.load_artifact = _load_artifact
        ctx.save_artifact = _save_artifact
        ctx.session = MagicMock()
        ctx.session.id = "sess-turn2-1"

        loader = make_document_loader()

        with patch(
            "tools.documents.context.build_document_context",
            side_effect=RuntimeError("Firestore unavailable"),
        ):
            with caplog.at_level(logging.ERROR, logger="adk.callbacks"):
                await loader(ctx)

        invariant_records = [
            r for r in caplog.records if r.levelno == logging.ERROR and "TURN-1 INVARIANT" in r.getMessage()
        ]
        assert len(invariant_records) == 0, (
            "Invariant must NOT fire on turn 2+: prior load succeeded, this is a different (non-stranded) condition."
        )


class TestTryGenerateTitle:
    def test_returns_none_on_exception(self):
        session = MagicMock()
        session.events = []

        with patch("db.title_generator.generate_title_fast", side_effect=Exception("model error")):
            result = _try_generate_title(session)

        assert result is None

    def test_returns_none_when_events_empty(self):
        session = MagicMock()
        session.events = []

        with patch("db.title_generator.generate_title_fast", return_value=None):
            result = _try_generate_title(session)

        assert result is None


# ---------------------------------------------------------------------------
# RAG document loader / injector (RAG_DOCUMENTS_ENABLED=true path)
# ---------------------------------------------------------------------------


class TestRagDocumentLoader:
    @pytest.mark.asyncio
    async def test_rag_loader_imports_new_docs(self):
        """When RAG enabled, new doc IDs are imported and tracked in app:docs_files."""
        from adk.callbacks import _STATE_DOCS_FILES, _STATE_RAG_CORPUS_NAME, make_document_loader

        corpus = "projects/p/locations/l/ragCorpora/1"
        ctx = MagicMock()
        ctx.state = {"document_ids": ["doc-a"], "user:id": "user-1"}
        ctx.session = MagicMock()
        ctx.session.id = None

        loader = make_document_loader()

        with (
            patch("adk.callbacks._RAG_DOCUMENTS_ENABLED", True),
            patch("rag.corpus.get_or_create_user_corpus", return_value=corpus),
            patch("rag.corpus.import_document_from_gcs"),
            patch("db.firestore.get_document", return_value={"sourceUrl": "gs://bucket/doc-a.pdf"}),
        ):
            await loader(ctx)

        assert ctx.state[_STATE_DOCS_FILES] == ["doc-a"]
        assert ctx.state[_STATE_RAG_CORPUS_NAME] == corpus

    @pytest.mark.asyncio
    async def test_rag_loader_idempotent_on_already_loaded_docs(self):
        """Already-imported doc IDs are not re-imported."""
        from adk.callbacks import _STATE_DOCS_FILES, make_document_loader

        corpus = "projects/p/locations/l/ragCorpora/1"
        ctx = MagicMock()
        ctx.state = {
            "document_ids": ["doc-a"],
            "user:id": "user-1",
            _STATE_DOCS_FILES: ["doc-a"],
        }
        ctx.session = MagicMock()
        ctx.session.id = None

        loader = make_document_loader()

        with (
            patch("adk.callbacks._RAG_DOCUMENTS_ENABLED", True),
            patch("rag.corpus.get_or_create_user_corpus", return_value=corpus),
            patch("rag.corpus.import_document_from_gcs") as mock_import,
            patch("db.firestore.get_document", return_value={"sourceUrl": "gs://bucket/doc-a.pdf"}),
        ):
            await loader(ctx)

        mock_import.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_loader_skips_doc_with_no_source_url(self):
        """Docs with no sourceUrl in Firestore are skipped without raising."""
        from adk.callbacks import _STATE_DOCS_FILES, make_document_loader

        ctx = MagicMock()
        ctx.state = {"document_ids": ["doc-b"], "user:id": "user-1"}
        ctx.session = MagicMock()
        ctx.session.id = None

        loader = make_document_loader()

        with (
            patch("adk.callbacks._RAG_DOCUMENTS_ENABLED", True),
            patch("rag.corpus.get_or_create_user_corpus", return_value="projects/p/corpora/1"),
            patch("rag.corpus.import_document_from_gcs") as mock_import,
            patch("db.firestore.get_document", return_value={}),
        ):
            await loader(ctx)

        mock_import.assert_not_called()
        assert ctx.state.get(_STATE_DOCS_FILES, []) == []

    @pytest.mark.asyncio
    async def test_rag_injector_skips_after_timing_mark(self):
        """When RAG enabled, injector marks timing but skips full-doc injection."""
        from adk.callbacks import make_document_injector

        ctx = MagicMock()
        ctx.state = {"app:docs_loaded": ["doc-a"]}

        llm_request = MagicMock()
        contents = [MagicMock(role="user", parts=[])]
        llm_request.contents = contents

        injector = make_document_injector()

        with (
            patch("adk.callbacks._RAG_DOCUMENTS_ENABLED", True),
            patch("observability.timing.get_current_tracker") as mock_tracker,
        ):
            await injector(ctx, llm_request)

        mock_tracker.return_value.mark.assert_called_once()
        # Contents should be untouched — no doc injection
        assert len(llm_request.contents) == 1


class TestFirstTurnFlush:
    """Regression (2026-08-05): "I just sent this and it says 52m ago".

    ``lastMessageAt`` drives both the label and the sort order of the history
    sidebar. With the flush debounced to every 5 turns, the first write landed on
    turn 2 (via title generation), so after a single message the row still
    carried the stamp written when the row was CREATED — page-mount time for a
    bootstrap-created row. Observed live on test: a session posted to at 12:12
    rendered "52m ago" because its stamp was the 11:20 mount.
    """

    @patch("db.chat_sessions.update_session_fields")
    def test_turn_1_flushes_last_message_at(self, mock_update):
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=0)  # will become 1

        cb(ctx)

        mock_update.assert_called_once()
        fields = mock_update.call_args[0][1]
        assert fields["turnCount"] == 1
        assert "lastMessageAt" in fields, (
            "without this the sidebar shows the row's creation time, not the message the user just sent"
        )

    @patch("db.chat_sessions.update_session_fields")
    def test_turn_3_still_debounced(self, mock_update):
        """The debounce itself is intact — only turn 1 was carved out."""
        cb = make_after_agent_response()
        ctx = _make_ctx(initialized=True, turn_count=2)  # will become 3

        cb(ctx)

        mock_update.assert_not_called()
