"""Save each finished turn to the memory bank, so `load_memory` has something to find.

Recall has been wired since v6.0.0 (`load_memory_tool` + `preload_memory_tool`
on every skill with memory on), but nothing WROTE memories on the request path.
The only writer was ag_ui_adk's session sweep, which saves a session when it
goes idle past `session_timeout_seconds` — raised to 24h in `adk/agui.py` so the
sweep never deletes Vertex sessions. That sweep tracks sessions in process
memory, so any restart, deploy or Cloud Run recycle inside those 24h forgets
them: in practice nothing was ever saved, and recall always came back empty.

This writer streams each turn's events to Memory Bank's `ingest_events` with the
session as the stream. Vertex buffers them and generates memories once the
stream has been idle for `MEMORY_AUTOSAVE_IDLE_MINUTES` (default 5) — so a conversation is
digested once it pauses, not re-extracted on every turn. The call is fire-and-
forget inside ADK (no latency on the reply), and any failure is logged, never
raised: a turn must not fail because memory could not be saved.

What is sent: only the user's and the model's TEXT. Tool calls and tool results
are dropped — a result can be a whole confidential document, and a memory should
come from what was said, not from everything the agent read.

Scope: the session's own (app_name, user_id) — per user. In a shared thread the
turn runs as the thread's owner, so memories land with the owner. A chat scoped to
one of the owner's projects writes to that project's memory instead
(``{app_name, user_id, project_id}``, projects.md §6.6) through
``adk.project_memory.ingest_project_turn``, because ADK's own call cannot carry a
third scope key; unscoped turns keep the ADK call exactly as before.

Opt-in: `MEMORY_AUTOSAVE=1`. Skills with memory off (`tool_configs.defaults.memory:
false`, or code-execution skills) never get the callback.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.genai import types

log = logging.getLogger(__name__)

# Memory Bank rejects any idle_duration that is not a whole number of minutes.
_DEFAULT_IDLE_MINUTES = 5


def memory_autosave_enabled() -> bool:
    return os.environ.get("MEMORY_AUTOSAVE", "").strip().lower() in ("1", "true", "yes", "on")


def _idle_minutes() -> int:
    try:
        return max(1, int(os.environ.get("MEMORY_AUTOSAVE_IDLE_MINUTES", _DEFAULT_IDLE_MINUTES)))
    except ValueError:
        return _DEFAULT_IDLE_MINUTES


def turn_text_events(events: list[Any], invocation_id: str) -> list[Any]:
    """This invocation's events, reduced to their user/model text parts.

    Thoughts, tool calls and tool results are dropped; an event with no text
    left is dropped whole.
    """
    out = []
    for event in events:
        if getattr(event, "invocation_id", None) != invocation_id:
            continue
        content = getattr(event, "content", None)
        if content is None or not content.parts:
            continue
        parts = [types.Part(text=p.text) for p in content.parts if p.text and not p.thought]
        if not parts:
            continue
        out.append(event.model_copy(update={"content": types.Content(role=content.role, parts=parts)}))
    return out


def make_memory_autosave_callback() -> Any:
    """An ``after_agent_callback`` that hands the finished turn to the memory bank."""

    async def _autosave(callback_context: Any) -> None:
        try:
            ctx = callback_context._invocation_context
            # Delegates built by the same factory fire too, before the root has
            # replied. Only the root sees the whole turn.
            if getattr(ctx.agent, "parent_agent", None) is not None or ctx.memory_service is None:
                return
            events = turn_text_events(list(ctx.session.events), ctx.invocation_id)
            if not events:
                return
            from adk.memory_tools import memory_scope_async

            scope = await memory_scope_async(callback_context)
            if "project_id" in scope:
                from adk.project_memory import ingest_project_turn

                await ingest_project_turn(
                    callback_context, scope, events, stream_id=ctx.session.id, idle_seconds=_idle_minutes() * 60
                )
                return
            await callback_context.add_events_to_memory(
                events=events,
                custom_metadata={
                    "stream_id": ctx.session.id,
                    "generation_trigger_config": {"generation_rule": {"idle_duration": f"{_idle_minutes() * 60}s"}},
                },
            )
        except Exception:
            log.exception("memory autosave failed (the turn itself is unaffected)")
        return None

    return _autosave
