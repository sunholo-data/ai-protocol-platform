"""Tests for the shared AG-UI channel renderer (v6.21.0 M1).

Two jobs:

1.  **Drift guard** — every event type in the installed AG-UI vocabulary
    must be explicitly classified as handled or ignored. Adding a
    protocol event then forces a channel decision instead of silently
    doing nothing. This is the mechanism that stops the v6.7.0 frontend
    convergence story ("re-add capabilities one event type at a time")
    from repeating on the channel side.

2.  **Semantics** — the per-event sink calls, throttling, clipping and
    error precedence that every adapter now inherits.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator
from typing import Any

import pytest

from channels._agui_render import (
    GENERIC_ERROR_TEXT,
    HANDLED_EVENT_TYPES,
    IGNORED_EVENT_TYPES,
    NO_RESPONSE_TEXT,
    AGUIChannelRenderer,
    ChannelSink,
    CollectingSink,
    summarize_surface,
)


async def _events(seq: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for event in seq:
        yield event


class _RecordingSink(ChannelSink):
    """Captures every sink call in order, with inherited defaults intact."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.text = ""

    async def begin(self) -> None:
        self.calls.append(("begin", None))

    async def update_text(self, text: str) -> None:
        self.calls.append(("update_text", text))
        self.text = text

    async def finish(self, text: str) -> None:
        self.calls.append(("finish", text))
        self.text = text

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


class _InlineProgressSink(_RecordingSink):
    """Sink whose progress splices into the reply text (Discord-style)."""

    async def show_progress(self, label: str) -> str | None:
        self.calls.append(("show_progress", label))
        return f"[{label}]"


class _NativeProgressSink(_RecordingSink):
    """Sink with an out-of-band affordance (terminal/Slack-style)."""

    async def show_progress(self, label: str) -> str | None:
        self.calls.append(("show_progress", label))
        return None


class TestDriftGuard:
    """A new AG-UI event type must force a channel decision."""

    def test_every_event_type_is_classified(self) -> None:
        from ag_ui.core import EventType

        vocabulary = {e.value for e in EventType}
        classified = HANDLED_EVENT_TYPES | IGNORED_EVENT_TYPES
        unclassified = vocabulary - classified

        assert not unclassified, (
            f"Unclassified AG-UI event types: {sorted(unclassified)}. "
            "Add each to HANDLED_EVENT_TYPES (and handle it in "
            "AGUIChannelRenderer.run) or to IGNORED_EVENT_TYPES with a "
            "written reason. Silently dropping a new event type is how "
            "channels fall behind the protocol."
        )

    def test_no_type_is_both_handled_and_ignored(self) -> None:
        assert not (HANDLED_EVENT_TYPES & IGNORED_EVENT_TYPES)

    def test_classification_matches_real_vocabulary(self) -> None:
        """Guard against typos — a misspelled type would silently no-op."""
        from ag_ui.core import EventType

        vocabulary = {e.value for e in EventType}
        unknown = (HANDLED_EVENT_TYPES | IGNORED_EVENT_TYPES) - vocabulary

        assert not unknown, f"Not real AG-UI event types: {sorted(unknown)}"

    def test_types_the_backend_emits_are_handled(self) -> None:
        """The types `adk/agui.py` actually produces must all be acted on."""
        emitted = {
            "TEXT_MESSAGE_CONTENT",
            "TOOL_CALL_START",
            "RUN_ERROR",
            "RUN_FINISHED",
            "CUSTOM",
        }
        assert emitted <= HANDLED_EVENT_TYPES


class TestTextRendering:
    @pytest.mark.asyncio
    async def test_deltas_accumulate_into_final_text(self) -> None:
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "one "},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "two"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "one two"
        assert sink.calls[0] == ("begin", None)
        assert sink.calls[-1] == ("finish", "one two")

    @pytest.mark.asyncio
    async def test_empty_stream_yields_no_response(self) -> None:
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(_events([{"type": "RUN_FINISHED"}]))

        assert result == NO_RESPONSE_TEXT
        assert sink.calls[-1] == ("finish", NO_RESPONSE_TEXT)

    @pytest.mark.asyncio
    async def test_ignored_events_produce_no_sink_calls(self) -> None:
        """Reasoning/state/step events must not leak into a channel."""
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "RUN_STARTED"},
                    {"type": "TEXT_MESSAGE_START"},
                    {"type": "THINKING_START"},
                    {"type": "REASONING_MESSAGE_CONTENT", "delta": "secret"},
                    {"type": "STATE_SNAPSHOT", "snapshot": {}},
                    {"type": "TOOL_CALL_ARGS", "delta": "{}"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "visible"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert sink.text == "visible"
        assert sink.kinds() == ["begin", "update_text", "finish"]

    @pytest.mark.asyncio
    async def test_strip_final_is_opt_in(self) -> None:
        """The collected path never stripped; Discord does. Both preserved."""
        unstripped = _RecordingSink()
        await AGUIChannelRenderer(unstripped).run(
            _events([{"type": "TEXT_MESSAGE_CONTENT", "delta": "  padded  "}, {"type": "RUN_FINISHED"}])
        )
        stripped = _RecordingSink()
        await AGUIChannelRenderer(stripped, strip_final=True).run(
            _events([{"type": "TEXT_MESSAGE_CONTENT", "delta": "  padded  "}, {"type": "RUN_FINISHED"}])
        )

        assert unstripped.text == "  padded  "
        assert stripped.text == "padded"

    @pytest.mark.asyncio
    async def test_intermediate_updates_are_clipped(self) -> None:
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink, max_message_length=10).run(
            _events([{"type": "TEXT_MESSAGE_CONTENT", "delta": "x" * 50}, {"type": "RUN_FINISHED"}])
        )

        updates = [text for kind, text in sink.calls if kind == "update_text"]
        assert all(len(t) <= 10 for t in updates)

    @pytest.mark.asyncio
    async def test_final_text_is_not_clipped(self) -> None:
        """Chunking overflow is the sink's job — it may fan out to follow-ups."""
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink, max_message_length=10).run(
            _events([{"type": "TEXT_MESSAGE_CONTENT", "delta": "y" * 50}, {"type": "RUN_FINISHED"}])
        )

        assert sink.text == "y" * 50


class TestThrottling:
    @pytest.mark.asyncio
    async def test_updates_coalesce_within_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Discord rate limits make this load-bearing, not cosmetic."""
        monkeypatch.setattr("channels._agui_render.time.monotonic", lambda: 0.0)
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink, edit_interval_sec=1.0).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "a"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "c"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        # Clock pinned at 0 → no intermediate update crosses the gate.
        assert [k for k in sink.kinds() if k == "update_text"] == []
        assert sink.text == "abc"

    @pytest.mark.asyncio
    async def test_updates_resume_after_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A non-exhausting clock advancing 5s per call. `channels._agui_render.time`
        # IS the global time module, so this patch is process-wide and anything
        # else calling monotonic() during the test consumes ticks too — a finite
        # iterator here raises StopIteration nondeterministically.
        counter = itertools.count()
        monkeypatch.setattr("channels._agui_render.time.monotonic", lambda: next(counter) * 5.0)
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink, edit_interval_sec=1.0).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "a"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "c"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert [k for k in sink.kinds() if k == "update_text"]

    @pytest.mark.asyncio
    async def test_zero_interval_updates_every_delta(self) -> None:
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink, edit_interval_sec=0.0).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "a"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert sink.kinds().count("update_text") == 2


class TestProgress:
    @pytest.mark.asyncio
    async def test_tool_call_start_uses_canonical_field(self) -> None:
        """`toolCallName` is what the backend emits — not `name`."""
        sink = _InlineProgressSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "ai_search"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert ("show_progress", "ai_search") in sink.calls
        assert "ai_search" in result

    @pytest.mark.asyncio
    async def test_inline_progress_is_spliced_into_text(self) -> None:
        sink = _InlineProgressSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "checking "},
                    {"type": "TOOL_CALL_START", "toolCallName": "search"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": " done"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "checking [search] done"

    @pytest.mark.asyncio
    async def test_native_progress_is_not_spliced(self) -> None:
        """A sink with its own affordance keeps the reply text clean."""
        sink = _NativeProgressSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "answer"},
                    {"type": "TOOL_CALL_START", "toolCallName": "search"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "answer"
        assert ("show_progress", "search") in sink.calls

    @pytest.mark.asyncio
    async def test_default_sink_progress_is_silent(self) -> None:
        """Acceptance criterion: the ChannelSink default no-ops."""
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "answer"},
                    {"type": "TOOL_CALL_START", "toolCallName": "search"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "answer"

    @pytest.mark.asyncio
    async def test_stage_progress_custom_event(self) -> None:
        sink = _InlineProgressSink()
        await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "CUSTOM", "name": "STAGE_PROGRESS", "value": {"label": "Thinking…"}},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert ("show_progress", "Thinking…") in sink.calls

    @pytest.mark.asyncio
    async def test_unknown_custom_event_ignored(self) -> None:
        """Heartbeats and future CUSTOM names must not reach the user."""
        sink = _InlineProgressSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "answer"},
                    {"type": "CUSTOM", "name": "HEARTBEAT", "value": {"n": 1}},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "answer"


class TestSurfaces:
    @pytest.mark.asyncio
    async def test_default_surface_falls_back_to_text(self) -> None:
        """Acceptance criterion: a surface degrades, never vanishes."""
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {
                        "type": "CUSTOM",
                        "name": "A2UI_SURFACE",
                        "value": {"title": "Contract comparison"},
                    },
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert "Contract comparison" in result

    @pytest.mark.asyncio
    async def test_native_surface_render_suppresses_text(self) -> None:
        class _EmbedSink(_RecordingSink):
            async def render_surface(self, surface: Any) -> str | None:
                self.calls.append(("render_surface", surface))
                return None

        sink = _EmbedSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "see below"},
                    {"type": "CUSTOM", "name": "A2UI_SURFACE", "value": {"title": "x"}},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert result == "see below"
        assert any(k == "render_surface" for k, _ in sink.calls)

    def test_summarize_surface_never_returns_empty(self) -> None:
        for surface in ({}, {"title": ""}, None, [], "string", {"title": "Real"}):
            assert summarize_surface(surface).strip()


class TestErrorPrecedence:
    @pytest.mark.asyncio
    async def test_run_error_calls_show_error(self) -> None:
        class _ErrSink(_RecordingSink):
            async def show_error(self, text: str) -> None:
                self.calls.append(("show_error", text))
                self.text = text

        sink = _ErrSink()
        result = await AGUIChannelRenderer(sink).run(_events([{"type": "RUN_ERROR", "message": "model unavailable"}]))

        assert "model unavailable" in result
        assert ("show_error", result) in sink.calls
        assert "finish" not in sink.kinds()

    @pytest.mark.asyncio
    async def test_error_beats_partial_text(self) -> None:
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "half an answer"},
                    {"type": "RUN_ERROR", "message": "aborted"},
                ]
            )
        )

        assert "aborted" in result
        assert "half an answer" not in result

    @pytest.mark.asyncio
    async def test_error_without_message_is_generic_not_silent(self) -> None:
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(_events([{"type": "RUN_ERROR"}]))

        assert result == GENERIC_ERROR_TEXT

    @pytest.mark.asyncio
    async def test_default_show_error_posts_text(self) -> None:
        """Acceptance criterion: the ChannelSink default surfaces the error."""
        sink = _RecordingSink()
        await AGUIChannelRenderer(sink).run(_events([{"type": "RUN_ERROR", "message": "boom"}]))

        assert "boom" in sink.text

    @pytest.mark.asyncio
    async def test_events_after_error_are_not_processed(self) -> None:
        sink = _RecordingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events(
                [
                    {"type": "RUN_ERROR", "message": "first"},
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "late text"},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )

        assert "first" in result
        assert "late text" not in result


class TestCollectingSink:
    @pytest.mark.asyncio
    async def test_collects_without_sending(self) -> None:
        sink = CollectingSink()
        result = await AGUIChannelRenderer(sink).run(
            _events([{"type": "TEXT_MESSAGE_CONTENT", "delta": "collected"}, {"type": "RUN_FINISHED"}])
        )

        assert result == "collected"
        assert sink.text == "collected"

    @pytest.mark.asyncio
    async def test_collects_error_text(self) -> None:
        sink = CollectingSink()
        await AGUIChannelRenderer(sink).run(_events([{"type": "RUN_ERROR", "message": "nope"}]))

        assert "nope" in sink.text


class TestSinkContract:
    def test_update_text_is_the_only_required_method(self) -> None:
        """A minimal adapter should need one method, not six."""

        class _Minimal(ChannelSink):
            def __init__(self) -> None:
                self.text = ""

            async def update_text(self, text: str) -> None:
                self.text = text

        sink = _Minimal()
        assert sink.text == ""

    @pytest.mark.asyncio
    async def test_minimal_sink_gets_errors_and_surfaces_free(self) -> None:
        class _Minimal(ChannelSink):
            def __init__(self) -> None:
                self.text = ""

            async def update_text(self, text: str) -> None:
                self.text = text

        err = _Minimal()
        await AGUIChannelRenderer(err).run(_events([{"type": "RUN_ERROR", "message": "inherited"}]))
        assert "inherited" in err.text

        surf = _Minimal()
        await AGUIChannelRenderer(surf).run(
            _events(
                [
                    {"type": "CUSTOM", "name": "A2UI_SURFACE", "value": {"title": "Inherited"}},
                    {"type": "RUN_FINISHED"},
                ]
            )
        )
        assert "Inherited" in surf.text

    def test_base_update_text_raises(self) -> None:
        """The one method an adapter MUST implement fails loudly if skipped."""
        import asyncio

        with pytest.raises(NotImplementedError):
            asyncio.run(ChannelSink().update_text("x"))
