"""`/api/admin/compaction/replay` — run a compaction over a RECORDED session.

Why this exists. Every wrong turn in the compaction work was a **measurement**
failure, not an implementation failure: a canary that passed under both a
working and a broken config, a probe whose synthetic fixture was degenerate, a
threshold change that unit tests blessed and a live run disproved. The two
questions still open both need real conversations rather than invented ones:

- ~22% of compactions return nothing. Synthetic input never reproduced it.
- Nobody has proven a fact planted in a tool result survives a compaction.

So this replays the real summariser over a real session's events and reports
what it *would* produce — inputs, output, timing, and whether it declined.

**READ-ONLY BY CONSTRUCTION.** It never calls ``append_event``. Compaction
normally mutates conversation history; a debugging tool that did that would
corrupt the very sessions we are trying to study, and would make repeat runs
non-comparable. Selection uses ADK's own candidate logic so what is summarised
here is what would be summarised for real — replicating that logic locally
would drift from ADK and quietly invalidate every measurement taken with it.

See docs/projects/compaction/README.md.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.scope import PlatformScope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/compaction", tags=["admin-compaction"])


class ReplayRequest(BaseModel):
    """What to replay, and with which knobs.

    Every override is optional and defaults to the deployed configuration, so
    the zero-argument call answers "what does production actually do to this
    session" — the question you want first.
    """

    session_id: str
    user_id: str = Field(description="ADK user id that owns the session (the Firebase uid).")
    # Overrides. `event_retention_size` matters most: it gates whether ANY
    # candidates exist (see findings log §1), so a session that looks
    # un-compactable is often just below the floor.
    event_retention_size: int | None = Field(default=None, ge=0)
    prompt_template: str | None = Field(
        default=None, description="Override the summariser prompt. Must contain {conversation_history}."
    )
    model_ref: str | None = Field(default=None, description="Tier or registry id for the summariser model.")
    summarize: bool = Field(
        default=True,
        description="False = report the selection only, with no model call. Fast, free, and enough to answer "
        "'would this session compact at all, and over what'.",
    )


class ReplayResult(BaseModel):
    session_id: str
    total_events: int
    existing_compactions: int
    selected_events: int
    input_chars: int
    # `declined` is the point of the whole endpoint: the summariser ran and
    # returned nothing, which in production means history was NOT compacted and
    # the cost was paid for nothing.
    declined: bool = False
    summary_chars: int = 0
    summary: str | None = None
    elapsed_ms: int = 0
    notes: list[str] = []


@router.post("/replay", response_model=ReplayResult)
async def replay_compaction(body: ReplayRequest, scope: PlatformScope) -> ReplayResult:
    """Replay a compaction over a recorded session. Never mutates it."""
    from google.adk.apps.compaction import _events_to_compact_for_token_threshold

    from adk.agui import APP_NAME
    from adk.session import get_compaction_config, get_session_service

    notes: list[str] = []
    session_service = get_session_service()
    try:
        session = await session_service.get_session(app_name=APP_NAME, user_id=body.user_id, session_id=body.session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not read session: {exc}") from exc
    if session is None:
        raise HTTPException(status_code=404, detail="session not found for that user_id")

    events = list(session.events or [])
    existing = sum(1 for e in events if e.actions and getattr(e.actions, "compaction", None))

    config = get_compaction_config("")  # deployment default; only retention is read below
    retention = body.event_retention_size if body.event_retention_size is not None else config.event_retention_size
    if retention is None:
        retention = 0
        notes.append("no event_retention_size configured; replaying with 0")

    selected = _events_to_compact_for_token_threshold(events=events, event_retention_size=retention)
    input_chars = sum(len(p.text or "") for e in selected if e.content and e.content.parts for p in e.content.parts)

    if not selected:
        notes.append(
            f"nothing to compact: {len(events)} events, retention {retention}. "
            "Candidates must EXCEED retention before compaction can fire at all."
        )
        return ReplayResult(
            session_id=body.session_id,
            total_events=len(events),
            existing_compactions=existing,
            selected_events=0,
            input_chars=0,
            notes=notes,
        )

    if not body.summarize:
        notes.append("selection only — no model call made")
        return ReplayResult(
            session_id=body.session_id,
            total_events=len(events),
            existing_compactions=existing,
            selected_events=len(selected),
            input_chars=input_chars,
            notes=notes,
        )

    if body.prompt_template and "{conversation_history}" not in body.prompt_template:
        # Would raise inside str.format during summarisation — reject up front
        # rather than fail mid-call with a confusing KeyError.
        raise HTTPException(status_code=422, detail="prompt_template must contain {conversation_history}")

    summarizer = _build_summarizer(body.model_ref, body.prompt_template, notes)
    if summarizer is None:
        raise HTTPException(status_code=503, detail="could not resolve a summariser model; see server logs")

    started = time.perf_counter()
    try:
        event = await summarizer.maybe_summarize_events(events=selected)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        notes.append(f"summarisation RAISED {type(exc).__name__}: {str(exc)[:200]}")
        return ReplayResult(
            session_id=body.session_id,
            total_events=len(events),
            existing_compactions=existing,
            selected_events=len(selected),
            input_chars=input_chars,
            declined=True,
            elapsed_ms=elapsed,
            notes=notes,
        )
    elapsed = int((time.perf_counter() - started) * 1000)

    if event is None:
        notes.append(
            "SUMMARISER DECLINED — returned None. In production this means history was NOT "
            "compacted and the model call was paid for nothing (findings log §3.2)."
        )
        return ReplayResult(
            session_id=body.session_id,
            total_events=len(events),
            existing_compactions=existing,
            selected_events=len(selected),
            input_chars=input_chars,
            declined=True,
            elapsed_ms=elapsed,
            notes=notes,
        )

    comp = event.actions.compaction
    text = "".join(p.text or "" for p in (comp.compacted_content.parts or []))
    log.info(
        "admin.compaction_replay: uid=%s session=%s selected=%d in=%d out=%d %dms",
        scope.user.uid,
        body.session_id,
        len(selected),
        input_chars,
        len(text),
        elapsed,
    )
    return ReplayResult(
        session_id=body.session_id,
        total_events=len(events),
        existing_compactions=existing,
        selected_events=len(selected),
        input_chars=input_chars,
        summary_chars=len(text),
        summary=text,
        elapsed_ms=elapsed,
        notes=notes,
    )


def _build_summarizer(model_ref: str | None, prompt_template: str | None, notes: list[str]):
    """The deployed summariser, or one with overrides applied.

    Overrides build a fresh instance rather than mutating the shared one — ADK
    mutates compaction configs in place and the singleton is shared across every
    session, so a replay that altered it would change production behaviour for
    everyone (findings log trap 5).
    """
    from adk.compaction_summarizer import (
        FIDELITY_PROMPT_TEMPLATE,
        FidelityEventSummarizer,
        build_compaction_summarizer,
    )

    if not model_ref and not prompt_template:
        return build_compaction_summarizer()

    try:
        from adk.agent import resolve_model_chain

        llm = resolve_model_chain(model_ref or "pro")
    except Exception as exc:
        notes.append(f"could not resolve model {model_ref!r}: {exc}")
        return None
    if model_ref:
        notes.append(f"model override: {model_ref}")
    if prompt_template:
        notes.append("prompt override in use")
    return FidelityEventSummarizer(llm=llm, prompt_template=prompt_template or FIDELITY_PROMPT_TEMPLATE)
