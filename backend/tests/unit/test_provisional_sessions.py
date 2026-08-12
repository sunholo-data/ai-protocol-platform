"""Bootstrap-provisional sessions (issue #38 follow-up).

`POST /sessions/{id}/bootstrap` fires when the chat page MOUNTS, before the user
has typed anything — the row must exist that early (iframe context pushes 404
without it), but a chat someone opened and abandoned is not history. On test, 22
of the last 40 rows were these empty shells, 1-2 per real conversation.

Contract: bootstrap rows are `provisional`, hidden from the lists, and promoted
by the first real turn.
"""

from __future__ import annotations

from datetime import UTC, datetime

from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex

_T = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)


def _row(**overrides) -> ChatSessionIndex:
    base: dict = {
        "sessionId": "s1",
        "skillId": "one-assistant",
        "ownerUid": "u1",
        "accessControl": AccessControl(type="private"),
        "firstMessageAt": _T,
        "lastMessageAt": _T,
    }
    base.update(overrides)
    return ChatSessionIndex(**base)


class TestProvisionalField:
    def test_defaults_to_false(self):
        """A row created by the normal first-turn path is real, not provisional."""
        assert _row().provisional is False

    def test_legacy_row_without_the_field_is_not_provisional(self):
        """Rows written before the field existed must stay VISIBLE.

        This is why the list filter is a post-filter and not a Firestore
        `where(provisional == False)` — that query does not match documents
        missing the field, so every pre-existing session would vanish from the
        user's history.
        """
        legacy = ChatSessionIndex.model_validate(
            {
                "sessionId": "old",
                "skillId": "one-assistant",
                "ownerUid": "u1",
                "accessControl": {"type": "private"},
                "firstMessageAt": _T,
                "lastMessageAt": _T,
            }
        )
        assert legacy.provisional is False

    def test_bootstrap_row_is_provisional(self):
        assert _row(provisional=True).provisional is True


class TestListFiltering:
    """The visibility rule the list loops implement."""

    @staticmethod
    def _visible(rows: list[ChatSessionIndex]) -> list[str]:
        return [r.session_id for r in rows if not getattr(r, "provisional", False)]

    def test_provisional_rows_are_hidden(self):
        rows = [
            _row(sessionId="real-1"),
            _row(sessionId="shell-1", provisional=True),
            _row(sessionId="shell-2", provisional=True),
            _row(sessionId="real-2"),
        ]
        assert self._visible(rows) == ["real-1", "real-2"]

    def test_a_promoted_row_reappears(self):
        """The first turn clears the flag — the session becomes history."""
        row = _row(sessionId="promoted", provisional=True)
        assert self._visible([row]) == []
        row.provisional = False
        assert self._visible([row]) == ["promoted"]


class TestPromotionStamp:
    """`clear_provisional` re-stamps firstMessageAt — coherently (verified live).

    On test, promoting with a bare "now" wrote firstMessageAt 44s AFTER
    lastMessageAt: a session whose first message came after its last. The stamp
    is clamped to the turn's own timestamp, and written as an ISO STRING because
    every other write goes through `_to_firestore`, which isoformats these
    fields — a raw datetime becomes a native Firestore timestampValue and the
    column ends up mixed-type across rows.
    """

    def test_stamp_is_clamped_to_the_turn_timestamp(self):
        from datetime import timedelta
        from unittest.mock import patch

        from db import chat_sessions

        turn_ts = _T  # the row's lastMessageAt — i.e. when this turn started
        with patch.object(chat_sessions, "update_document") as upd:
            with patch.object(chat_sessions, "_utcnow", return_value=_T + timedelta(seconds=44)):
                chat_sessions.clear_provisional("s1", not_after=turn_ts)
        fields = upd.call_args.args[2]
        assert fields["provisional"] is False
        assert fields["firstMessageAt"] == turn_ts.isoformat(), "stamp must not run ahead of the turn"
        assert isinstance(fields["firstMessageAt"], str), "must be an ISO string, not a datetime"

    def test_stamp_uses_now_when_unclamped(self):
        from unittest.mock import patch

        from db import chat_sessions

        with patch.object(chat_sessions, "update_document") as upd:
            with patch.object(chat_sessions, "_utcnow", return_value=_T):
                chat_sessions.clear_provisional("s1")
        assert upd.call_args.args[2]["firstMessageAt"] == _T.isoformat()


class TestPromotionClampSource:
    """Regression (2026-08-05): the clamp defeated itself on a fresh row.

    ``clear_provisional`` clamps firstMessageAt to ``not_after`` so the stamp
    can't run ahead of the turn. But the caller passed the row's
    ``last_message_at`` unconditionally — and on a bootstrap-created row that
    has never flushed a turn, THAT VALUE IS THE PAGE-MOUNT TIME. Clamping to it
    pinned firstMessageAt back to the mount, i.e. exactly the skew
    clear_provisional exists to remove.
    """

    def _promote_and_capture(self, turn_count: int):
        from unittest.mock import MagicMock, patch

        from skills import skill_processor

        existing = MagicMock()
        existing.provisional = True
        existing.turn_count = turn_count
        existing.last_message_at = _T
        existing.skill_id = "skill-1"

        with (
            patch("db.chat_sessions.get_session_index", return_value=existing),
            patch("db.chat_sessions.clear_provisional") as clear,
            patch("db.chat_sessions.add_session_documents"),
            patch("db.chat_sessions.set_session_skill"),
        ):
            skill_processor._ensure_session_index("s1", "skill-1", "u1", [], "u1@example.com")
        return clear

    def test_never_flushed_row_is_not_clamped(self):
        clear = self._promote_and_capture(turn_count=0)
        clear.assert_called_once()
        assert clear.call_args.kwargs["not_after"] is None, (
            "clamping to a page-mount stamp pins firstMessageAt to the mount"
        )

    def test_row_with_real_turns_still_clamps(self):
        clear = self._promote_and_capture(turn_count=3)
        assert clear.call_args.kwargs["not_after"] == _T
