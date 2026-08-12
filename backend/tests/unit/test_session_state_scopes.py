"""Tests locking in session state scope contracts and _handle_large_output behaviour.

Covers:
  1. _handle_large_output: large content (> 50K chars) → artifact offload
  2. _handle_large_output: small content (≤ 50K chars) → pass-through
  3. user: prefix → persists across sessions for the same user in InMemorySessionService
  4. temp: prefix → NOT persisted; stripped at state-delta extraction time
  5. app: prefix → persists across all sessions in InMemorySessionService

Key InMemorySessionService internals (from ADK source):
  - _session_util.extract_state_delta splits a flat state dict into three buckets:
      {"app": ..., "user": ..., "session": ...}
    temp:-prefixed keys are deliberately excluded from all three buckets, so they
    are never written to self.app_state or self.user_state.  This means:
      * temp: state written during session creation is silently dropped.
      * temp: state written by append_event is dropped at storage commit time.
    In production (VertexAiSessionService) the same contract holds: temp: keys
    survive the current invocation only and are never persisted to the backend.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from google.adk.sessions import InMemorySessionService
from google.adk.sessions.state import State

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_THRESHOLD = 50_000  # must match adk/callbacks.py _LARGE_OUTPUT_THRESHOLD


def _make_tool_context(*, invocation_id: str = "inv-001") -> MagicMock:
    """Return a mock ToolContext with save_artifact wired up."""
    ctx = MagicMock()
    ctx.invocation_id = invocation_id
    ctx.save_artifact = AsyncMock(return_value=None)
    return ctx


def _make_tool(*, name: str = "test_tool") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


# ---------------------------------------------------------------------------
# _handle_large_output tests
# ---------------------------------------------------------------------------


class TestHandleLargeOutput:
    """Contract tests for the after_tool_callback that offloads large responses."""

    async def test_large_content_returns_pointer_string(self):
        """Content > 50K chars must return an artifact pointer, not the raw content."""
        from adk.callbacks import _handle_large_output

        large_text = "x" * (_THRESHOLD + 1)
        tool = _make_tool()
        ctx = _make_tool_context()

        with patch("google.genai.types.Part.from_text") as mock_part:
            mock_part.return_value = MagicMock()
            result = await _handle_large_output(tool, {}, ctx, large_text)

        assert isinstance(result, str), "result must be a string pointer"
        assert "artifact" in result.lower(), "pointer must mention 'artifact'"
        assert large_text not in result, "raw content must NOT appear in the pointer"

    async def test_large_content_calls_save_artifact(self):
        """save_artifact must be called exactly once for oversized content."""
        from adk.callbacks import _handle_large_output

        large_text = "y" * (_THRESHOLD + 1)
        tool = _make_tool(name="my_tool")
        ctx = _make_tool_context(invocation_id="inv-abc")

        with patch("google.genai.types.Part.from_text") as mock_part:
            mock_part.return_value = MagicMock()
            await _handle_large_output(tool, {}, ctx, large_text)

        ctx.save_artifact.assert_called_once()
        call_kwargs = ctx.save_artifact.call_args.kwargs
        assert "filename" in call_kwargs
        assert "my_tool" in call_kwargs["filename"]
        assert "inv-abc" in call_kwargs["filename"]

    async def test_large_content_artifact_name_in_pointer(self):
        """The pointer string must contain the artifact filename so the model can reference it."""
        from adk.callbacks import _handle_large_output

        large_text = "z" * (_THRESHOLD + 1)
        tool = _make_tool(name="search_tool")
        ctx = _make_tool_context(invocation_id="inv-xyz")

        with patch("google.genai.types.Part.from_text") as mock_part:
            mock_part.return_value = MagicMock()
            result = await _handle_large_output(tool, {}, ctx, large_text)

        assert "search_tool_response_inv-xyz" in result

    async def test_small_content_returned_unchanged(self):
        """Content at or below 50K chars must pass through unmodified."""
        from adk.callbacks import _handle_large_output

        small_text = "a" * _THRESHOLD  # exactly at threshold — should NOT offload
        tool = _make_tool()
        ctx = _make_tool_context()

        result = await _handle_large_output(tool, {}, ctx, small_text)

        assert result == small_text, "content at threshold must be returned unchanged"
        ctx.save_artifact.assert_not_called()

    async def test_small_content_no_save_artifact(self):
        """save_artifact must never be called for small responses."""
        from adk.callbacks import _handle_large_output

        small_text = "b" * (_THRESHOLD - 1)
        tool = _make_tool()
        ctx = _make_tool_context()

        await _handle_large_output(tool, {}, ctx, small_text)

        ctx.save_artifact.assert_not_called()

    async def test_non_string_response_below_threshold_returned_unchanged(self):
        """Non-string tool responses whose str() is ≤ threshold pass through untouched."""
        from adk.callbacks import _handle_large_output

        response = {"key": "value"}  # str() is well below 50K
        tool = _make_tool()
        ctx = _make_tool_context()

        result = await _handle_large_output(tool, {}, ctx, response)

        assert result is response, "dict response below threshold must be the same object"

    async def test_pointer_contains_char_count(self):
        """The artifact pointer string must include the character count for LLM context."""
        from adk.callbacks import _handle_large_output

        large_text = "w" * (_THRESHOLD + 500)
        tool = _make_tool()
        ctx = _make_tool_context()

        with patch("google.genai.types.Part.from_text") as mock_part:
            mock_part.return_value = MagicMock()
            result = await _handle_large_output(tool, {}, ctx, large_text)

        # The pointer should mention the character count so the model knows the size.
        # The format uses {:,} so the number may be comma-separated — strip commas before comparing.
        expected_digits = str(len(large_text))
        assert expected_digits in result.replace(",", ""), f"pointer should mention char count; got: {result!r}"


# ---------------------------------------------------------------------------
# Session state scope tests — InMemorySessionService
# ---------------------------------------------------------------------------


class TestUserScopeStatePersistence:
    """Document the user: state scope contract.

    InMemorySessionService stores user:-prefixed keys in self.user_state
    (a separate dict keyed by app_name → user_id) and merges them back into
    every new session for the same user via _merge_state().

    This means user: state DOES persist across sessions in InMemory — matching
    the contract that VertexAiSessionService also persists user: keys to
    the backend store.
    """

    def test_user_pref_visible_in_new_session(self):
        """user:pref set in session A must be visible in a new session B for the same user."""
        svc = InMemorySessionService()

        # Session A: create with user-scoped state
        sess_a = svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
            state={"user:pref": "dark"},
        )
        assert sess_a.state["user:pref"] == "dark"

        # Session B: fresh session for the same user — should inherit user: state
        sess_b = svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
        )
        assert "user:pref" in sess_b.state, (
            "user: state must be visible in a new session for the same user. "
            "InMemorySessionService stores user: keys in self.user_state and merges "
            "them back into every new session via _merge_state(). "
            "VertexAiSessionService provides the same contract in production."
        )
        assert sess_b.state["user:pref"] == "dark"

    def test_user_pref_not_visible_for_different_user(self):
        """user:pref set for user u1 must NOT be visible for a different user u2."""
        svc = InMemorySessionService()

        svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
            state={"user:pref": "dark"},
        )
        sess_u2 = svc.create_session_sync(
            app_name="test_app",
            user_id="u2",
        )

        assert "user:pref" not in sess_u2.state

    def test_user_state_isolated_across_app_names(self):
        """user: state for app_A must NOT bleed into app_B."""
        svc = InMemorySessionService()

        svc.create_session_sync(
            app_name="app_a",
            user_id="u1",
            state={"user:theme": "light"},
        )
        sess_app_b = svc.create_session_sync(
            app_name="app_b",
            user_id="u1",
        )

        assert "user:theme" not in sess_app_b.state

    def test_user_prefix_constant(self):
        """State.USER_PREFIX must be 'user:' — guards against future ADK renames."""
        assert State.USER_PREFIX == "user:"


class TestAppScopeStatePersistence:
    """app: state persists across all sessions and users within the same app."""

    def test_app_state_visible_across_users(self):
        """app: state set in one session must be visible for a different user."""
        svc = InMemorySessionService()

        svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
            state={"app:config_version": "2"},
        )
        sess_u2 = svc.create_session_sync(
            app_name="test_app",
            user_id="u2",
        )

        assert sess_u2.state.get("app:config_version") == "2"

    def test_app_prefix_constant(self):
        """State.APP_PREFIX must be 'app:'."""
        assert State.APP_PREFIX == "app:"


class TestTempScopeContract:
    """Document the temp: state scope contract.

    CONTRACT (from ADK source _session_util.extract_state_delta):
      Keys prefixed with 'temp:' are deliberately excluded from the app, user,
      and session state buckets. They are NEVER persisted by append_event and
      are silently dropped when passed to create_session.

    CONSEQUENCE:
      temp: state written during one invocation is NOT visible in any subsequent
      session or invocation. The Agent Engine service in production enforces the
      same contract — temp: keys are transient and live only within a single
      invocation's in-memory state proxy.

    This test class documents the contract with direct InMemorySessionService
    calls and, where the behaviour is a no-op (dropped at extraction), with
    explicit commentary.
    """

    def test_temp_prefix_constant(self):
        """State.TEMP_PREFIX must be 'temp:'."""
        assert State.TEMP_PREFIX == "temp:"

    def test_temp_state_dropped_at_session_creation(self):
        """temp: state passed to create_session must NOT appear in the returned session state.

        extract_state_delta (called inside create_session) silently discards
        temp:-prefixed keys — they go into none of the three storage buckets
        (app, user, session).  This is by design: temp: is meant for within-
        invocation scratch storage only.
        """
        svc = InMemorySessionService()

        sess = svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
            state={"temp:scratch": "ephemeral_value"},
        )

        assert "temp:scratch" not in sess.state, (
            "temp: state passed to create_session must be silently discarded. "
            "The contract: temp: keys are never written to self.user_state, "
            "self.app_state, or the session's own state dict."
        )

    def test_temp_state_not_visible_across_sessions_via_mock(self):
        """Document: temp: state written by a tool must NOT survive to the next session.

        In the ADK agent loop, a tool writes to tool_context.state['temp:key'].
        After the invocation, the Agent Engine discards all temp: keys.  The
        next session for the same user must NOT see 'temp:key'.

        We verify the underlying mechanism — extract_state_delta drops temp:
        keys — using a direct call to the utility function.
        """
        from google.adk.sessions._session_util import extract_state_delta

        state_with_temp = {
            "user:pref": "dark",
            "temp:scratch": "should_not_persist",
            "plain_key": "plain_value",
            "app:version": "3",
        }

        deltas = extract_state_delta(state_with_temp)

        # temp: key must not appear in any storage bucket
        assert "scratch" not in deltas["app"]
        assert "scratch" not in deltas["user"]
        assert "scratch" not in deltas["session"]
        assert "temp:scratch" not in deltas["app"]
        assert "temp:scratch" not in deltas["user"]
        assert "temp:scratch" not in deltas["session"]

        # Other keys should route correctly
        assert deltas["user"]["pref"] == "dark"
        assert deltas["app"]["version"] == "3"
        assert deltas["session"]["plain_key"] == "plain_value"

    def test_session_scoped_state_not_visible_in_new_session(self):
        """Session-scoped keys (no prefix) must NOT persist across sessions.

        This confirms that only user: and app: keys are cross-session; plain
        keys live only in the session they were created in.
        """
        svc = InMemorySessionService()

        sess_a = svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
            state={"plain_key": "session_local_value"},
        )
        assert "plain_key" in sess_a.state

        sess_b = svc.create_session_sync(
            app_name="test_app",
            user_id="u1",
        )
        assert "plain_key" not in sess_b.state, (
            "Session-scoped (no-prefix) state must not bleed into new sessions. "
            "Only user: and app: prefixed keys are persisted across sessions."
        )
