"""ADK state-key SCOPING contract (issue #38).

ADK state prefixes decide who shares a value:

    ``app:``   → every session of the app, ACROSS ALL USERS AND TENANTS
    ``user:``  → every session of one user
    (none)     → this session only

Getting this wrong is invisible in a single-session test — which is exactly how
`app:chat_session_turn_count` survived: it read back correctly every time, while
on the deployed env the counter climbed 125→131 across sessions belonging to two
different tenants, and `app:rag_corpus_name` (a PER-USER RAG corpus) was shared
with every other user of the platform.

These tests assert the SCOPE of each key, not its value.
"""

from __future__ import annotations

import adk.callbacks as cb
from tools import rag_tool

APP_PREFIX = "app:"
USER_PREFIX = "user:"


def _scope(key: str) -> str:
    if key.startswith(APP_PREFIX):
        return "app"
    if key.startswith(USER_PREFIX):
        return "user"
    return "session"


class TestStateKeyScoping:
    """Each key is scoped to the thing it actually describes."""

    def test_turn_count_is_session_scoped(self):
        """A turn counter describes ONE conversation.

        As `app:` it was a global counter: every session inherited the running
        total, so admin analytics reported platform traffic instead of session
        length, and `turn_count == 2` (title generation) could never fire again.
        """
        assert _scope(cb._STATE_TURN_COUNT) == "session"

    def test_initialized_flag_is_session_scoped(self):
        """`initialized` gates "first turn of THIS session".

        As `app:` the first session to initialise set it for every later
        session, so the `turn_count = 0` reset never ran again — the mechanism
        behind the runaway counter.
        """
        assert _scope(cb._STATE_INITIALIZED) == "session"

    def test_resumed_session_is_session_scoped(self):
        assert _scope(cb._STATE_RESUMED_SESSION) == "session"

    def test_rag_corpus_name_is_user_scoped(self):
        """SECURITY: the corpus is created per user (get_or_create_user_corpus).

        As `app:` a user who had uploaded nothing inherited another user's
        corpus name, and `search_documents` would query THEIR private documents
        — the "no documents uploaded yet" guard fails open because the key is
        non-empty. Must never be `app:`.
        """
        assert _scope(cb._STATE_RAG_CORPUS_NAME) == "user"

    def test_rag_tool_reads_the_same_scoped_key(self):
        """The tool and the loader must agree, or search silently finds nothing."""
        assert rag_tool._STATE_RAG_CORPUS_NAME == cb._STATE_RAG_CORPUS_NAME
        assert _scope(rag_tool._STATE_RAG_CORPUS_NAME) == "user"

    def test_doc_tracking_keys_are_user_scoped(self):
        """Loaded/imported doc ids describe one user's corpus + artifacts.

        `user:` preserves the documented "survives across sessions" intent
        while removing the cross-user bleed (one user's ids suppressing
        another user's document load).
        """
        assert _scope(cb._STATE_DOCS_FILES) == "user"
        assert _scope(cb._STATE_DOCS_LOADED) == "user"
        assert _scope(cb._STATE_DOC_LOAD_ERROR) == "user"

    def test_no_user_or_session_concept_is_app_scoped(self):
        """Backstop: nothing describing a user/session may be `app:`.

        Catches a NEW mis-scoped key added later — the failure mode here is
        silent, so the guard has to be categorical rather than per-key.
        """
        offenders = {
            name: value
            for name, value in vars(cb).items()
            if name.startswith("_STATE_") and isinstance(value, str) and value.startswith(APP_PREFIX)
        }
        assert not offenders, (
            f"app:-scoped state keys share across ALL users and tenants: {offenders}. "
            "Use `user:` for per-user data, no prefix for per-session data. "
            "If a key is genuinely app-global (e.g. a content-keyed cache), add it to "
            "this test's allowlist with the reason."
        )


class TestTurnCounterIsolation:
    """The behaviour the scoping buys: two sessions count independently."""

    @staticmethod
    def _session_view(app_state: dict) -> dict:
        """A session's state as ADK presents it: app-scoped keys merged in,
        session-scoped keys absent (they live only on that session)."""
        return dict(app_state)

    def test_two_sessions_count_independently(self):
        # App-scoped store, populated by whatever ran before this session.
        # Under the OLD key this held the running total; under the fix the
        # counter is session-scoped and never lands here at all.
        app_state = {k: v for k, v in {cb._STATE_TURN_COUNT: 3}.items() if k.startswith(APP_PREFIX)}

        session_b = self._session_view(app_state)
        session_b[cb._STATE_TURN_COUNT] = int(session_b.get(cb._STATE_TURN_COUNT) or 0) + 1

        assert session_b[cb._STATE_TURN_COUNT] == 1, (
            "session B inherited a prior session's turn count — the counter key is app-scoped"
        )
