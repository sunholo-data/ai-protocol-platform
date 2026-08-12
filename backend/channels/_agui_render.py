"""Shared AG-UI → channel presentation layer.

ONE module owns the mapping from AG-UI events to what a user sees in a
chat channel. An adapter supplies a `ChannelSink` — native send / edit /
embed primitives — and inherits accumulation, edit throttling, length
clipping, tool progress, surface rendering and error surfacing.

    AG-UI events ──► AGUIChannelRenderer ──► ChannelSink (per adapter)
                     (throttle, accumulate,   update_text / show_progress
                      classify, clip)         render_surface / show_error
                                              begin / finish

Why this exists: before v6.21.0 each adapter re-derived event semantics
inline, and Discord's copy matched `"TOOL_CALL"` — an event the AG-UI
spec does not define and the backend never emits. A second adapter would
have re-derived the same thing again. Event semantics now live in one
place; adapters own only their native primitives.

Two sink methods return `str | None`, which is how one renderer serves
channels with and without out-of-band affordances:

  - return a string → the renderer splices it into the reply text
    (Discord has no progress affordance, so progress becomes italic text)
  - return None → the sink rendered it natively and the reply text is
    left alone (a Slack `task_card`, a terminal status line, an embed)

See `docs/design/v6.21.0/channels-agui-convergence.md`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

logger = logging.getLogger(__name__)

# --- user-visible text -----------------------------------------------------
# These live here (not in `_skill_invoke`) so the import graph stays
# one-directional: _skill_invoke → _agui_render. `_skill_invoke`
# re-exports them, so existing call sites keep working.

NO_RESPONSE_TEXT = "(no response)"
GENERIC_ERROR_TEXT = "Sorry — something went wrong while answering that. Please try again."


def error_reply_text(message: str | None) -> str:
    """User-visible text for a `RUN_ERROR`.

    The AG-UI `RunError` shape is `{message, code?}`. We surface the
    message when there is one — an opaque apology teaches the user
    nothing and hides real failures from bug reports.
    """
    if not message:
        return GENERIC_ERROR_TEXT
    return f"Sorry — that request failed: {message}"


# --- event classification --------------------------------------------------
# Every member of the AG-UI `EventType` vocabulary must appear in exactly
# one of these sets. `test_agui_render.py`'s drift guard enforces it
# against the installed `ag_ui.core.EventType`, so adding a protocol event
# forces a channel decision instead of silently doing nothing.

HANDLED_EVENT_TYPES = frozenset(
    {
        "TEXT_MESSAGE_CONTENT",  # the reply text itself
        "TOOL_CALL_START",  # progress ("Working with ai_search...")
        "RUN_ERROR",  # visible failure — CLAUDE.md #8
        "RUN_FINISHED",  # terminal; triggers the final atomic write
        "CUSTOM",  # STAGE_PROGRESS + A2UI_SURFACE ride on this
    }
)

# Deliberately ignored, with the reason. A chat channel is a far narrower
# surface than the web workbench: it has no thinking panel, no state
# mirror, no per-step timeline.
IGNORED_EVENT_TYPES = frozenset(
    {
        # Lifecycle bookkeeping — `begin()` already fired before the loop.
        "RUN_STARTED",
        "STEP_STARTED",
        "STEP_FINISHED",
        # Message framing — we accumulate deltas, so start/end are noise.
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_CHUNK",
        # Tool detail beyond the fact that a tool started. Args and results
        # are workbench material; a chat channel would drown in them.
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_CHUNK",
        # Reasoning / thinking — the web UI has a panel for this; a channel
        # would interleave it with the answer. Revisit if a channel grows a
        # collapsible affordance (Discord threads could carry it).
        "REASONING_START",
        "REASONING_END",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_MESSAGE_CHUNK",
        "REASONING_ENCRYPTED_VALUE",
        "THINKING_START",
        "THINKING_END",
        "THINKING_TEXT_MESSAGE_START",
        "THINKING_TEXT_MESSAGE_CONTENT",
        "THINKING_TEXT_MESSAGE_END",
        # Shared-state mirroring — no channel-side state to mirror into.
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        "MESSAGES_SNAPSHOT",
        "ACTIVITY_SNAPSHOT",
        "ACTIVITY_DELTA",
        # Transport escape hatch.
        "RAW",
    }
)

# CUSTOM event names the renderer acts on.
CUSTOM_STAGE_PROGRESS = "STAGE_PROGRESS"
CUSTOM_A2UI_SURFACE = "A2UI_SURFACE"


# --- sink ------------------------------------------------------------------


class ChannelSink:
    """Native presentation primitives for one channel.

    Only `update_text` is required. Every other method has a default that
    degrades safely, so a minimal adapter inherits error surfacing and a
    readable fallback for surfaces without writing either.
    """

    async def begin(self) -> None:
        """Called once before any event. Post a placeholder if useful."""

    async def update_text(self, text: str) -> None:
        """Render `text` as the current (possibly partial) reply."""
        raise NotImplementedError

    async def show_progress(self, label: str) -> str | None:
        """Report in-flight work.

        Return a string to splice into the reply text, or None if the
        sink surfaced it natively. Default: no-op — progress is optional,
        and a channel without an affordance should not get noise.
        """
        return None

    async def render_surface(self, surface: Any) -> str | None:
        """Render an A2UI surface.

        Return a string to splice into the reply text, or None if the
        sink rendered it natively (Discord embeds, M2). Default: a text
        summary — a surface must degrade to something readable, never
        vanish.
        """
        return summarize_surface(surface)

    async def show_error(self, text: str) -> None:
        """Render a terminal failure. Default: post it as the reply text.

        Overriding this to swallow the error is a CLAUDE.md #8 violation.
        """
        await self.update_text(text)

    async def finish(self, text: str) -> None:
        """Write the final, complete reply. Default: one text update."""
        await self.update_text(text)


class CollectingSink(ChannelSink):
    """Sink for non-streaming adapters — accumulates, never sends.

    The renderer still drives it, so collect-then-send channels get
    identical event semantics (notably `RUN_ERROR`) for free. Delivery is
    the framework's job via `BaseChannel.send`.
    """

    def __init__(self) -> None:
        self.text = ""

    async def update_text(self, text: str) -> None:
        self.text = text

    async def finish(self, text: str) -> None:
        self.text = text


def summarize_surface(surface: Any) -> str:
    """Best-effort readable text for an A2UI surface.

    Deliberately crude — a channel that cares renders natively
    (`render_surface`). This only guarantees the user learns something
    arrived rather than seeing silence.
    """
    if isinstance(surface, dict):
        for key in ("title", "heading", "label", "name"):
            value = surface.get(key)
            if isinstance(value, str) and value.strip():
                return f"\n[{value.strip()}]\n"
    return "\n[interactive content — open the workbench to view it]\n"


# --- renderer --------------------------------------------------------------


class AGUIChannelRenderer:
    """Drive a `ChannelSink` from an AG-UI event stream.

    Args:
        sink: the adapter's presentation primitives.
        max_message_length: clip intermediate updates to this many chars
            (Discord 2000). None disables clipping. The FINAL write is
            handed over whole — chunking is the sink's business, since
            only it knows whether overflow becomes follow-up messages.
        edit_interval_sec: minimum seconds between intermediate updates.
            0.0 updates on every delta. Rate limits make this load-bearing
            on Discord, not cosmetic.
        strip_final: strip surrounding whitespace from the final text.
            Discord does; the collected path historically does not, and
            preserving that difference keeps this refactor behaviour-safe.
        monotonic: clock for the throttle gate. Injectable so an adapter
            can supply its own (and so tests can pin it without patching
            a global). None reads `time.monotonic` at call time.
    """

    def __init__(
        self,
        sink: ChannelSink,
        *,
        max_message_length: int | None = None,
        edit_interval_sec: float = 0.0,
        strip_final: bool = False,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.sink = sink
        self.max_message_length = max_message_length
        self.edit_interval_sec = edit_interval_sec
        self.strip_final = strip_final
        self._monotonic = monotonic

    def _now(self) -> float:
        """Read the clock, resolving `time.monotonic` at call time."""
        if self._monotonic is not None:
            return self._monotonic()
        return time.monotonic()

    async def run(self, event_stream: AsyncIterator[dict[str, Any]]) -> str:
        """Consume `event_stream`, drive the sink, return the final text.

        A `RUN_ERROR` wins over whatever partial text preceded it: a
        truncated answer presented as complete is worse than an explicit
        failure.
        """
        await self.sink.begin()

        parts: list[str] = []
        last_update_ts = 0.0
        error_text: str | None = None

        async for event in event_stream:
            event_type = event.get("type")

            if event_type == "TEXT_MESSAGE_CONTENT":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    parts.append(delta)
                    now = self._now()
                    if now - last_update_ts >= self.edit_interval_sec:
                        last_update_ts = now
                        await self.sink.update_text(self._clip("".join(parts)))

            elif event_type == "RUN_ERROR":
                raw = event.get("message")
                error_text = error_reply_text(raw if isinstance(raw, str) else None)
                break

            elif event_type == "RUN_FINISHED":
                break

            elif event_type == "TOOL_CALL_START":
                name = event.get("toolCallName") or event.get("tool_call_name") or event.get("name") or "tool"
                inline = await self.sink.show_progress(str(name))
                if inline:
                    parts.append(inline)

            elif event_type == "CUSTOM":
                inline = await self._handle_custom(event)
                if inline:
                    parts.append(inline)

        if error_text is not None:
            await self.sink.show_error(self._clip(error_text))
            return error_text

        joined = "".join(parts)
        if self.strip_final:
            joined = joined.strip()
        final_text = joined or NO_RESPONSE_TEXT
        await self.sink.finish(final_text)
        return final_text

    async def _handle_custom(self, event: dict[str, Any]) -> str | None:
        """Route a CUSTOM event by name. Returns text to splice, if any."""
        name = event.get("name")
        value = event.get("value")

        if name == CUSTOM_STAGE_PROGRESS:
            label = value.get("label") if isinstance(value, dict) else None
            if isinstance(label, str) and label.strip():
                return await self.sink.show_progress(label.strip())
            return None

        if name == CUSTOM_A2UI_SURFACE:
            return await self.sink.render_surface(value)

        # Heartbeats and anything else channels don't present.
        return None

    def _clip(self, text: str) -> str:
        """Clip an intermediate update to the channel's message limit."""
        if self.max_message_length is None:
            return text
        return text[: self.max_message_length]


__all__ = [
    "CUSTOM_A2UI_SURFACE",
    "CUSTOM_STAGE_PROGRESS",
    "GENERIC_ERROR_TEXT",
    "HANDLED_EVENT_TYPES",
    "IGNORED_EVENT_TYPES",
    "NO_RESPONSE_TEXT",
    "AGUIChannelRenderer",
    "ChannelSink",
    "CollectingSink",
    "error_reply_text",
    "summarize_surface",
]
