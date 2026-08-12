"""`/api/admin/compaction/recompact` — the human-driven second pass (CLI path).

Deliberately a SEPARATE module from `compaction_replay_routes`, whose contract
is read-only-by-construction; this one mutates (it appends the superseding
compaction event) and must never be mistaken for a replay. Same admin gate as
replay (`PlatformScope`), same session loading, same summariser plumbing — the
actual work is `adk.compaction_second_pass.run_second_pass`, shared with the
Cloud Tasks route.

Unlike the task route this returns the summary text (an admin operator holds
the same trust as the replay endpoint, which already does) and treats a
decline as data rather than a retryable failure — a human is looking at it.

See docs/design/v6.23.0/compaction-second-pass.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.scope import PlatformScope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/compaction", tags=["admin-compaction"])


class RecompactRequest(BaseModel):
    session_id: str
    user_id: str = Field(description="ADK user id that owns the session (the Firebase uid).")
    dry_run: bool = Field(
        default=False,
        description="Compute and summarise but never append — prints what the second pass WOULD write.",
    )
    enqueue: bool = Field(
        default=False,
        description="Instead of running in-process, schedule a REAL Cloud Tasks delivery with zero delay — "
        "the end-to-end verification of the queue→OIDC→route chain.",
    )


@router.post("/recompact")
async def recompact(body: RecompactRequest, scope: PlatformScope) -> dict:
    """Run the second pass over a session now (or preview it with dry_run)."""
    from adk.agui import APP_NAME
    from adk.compaction_second_pass import run_second_pass
    from adk.compaction_summarizer import build_compaction_summarizer
    from adk.session import get_session_service

    session_service = get_session_service()
    try:
        session = await session_service.get_session(app_name=APP_NAME, user_id=body.user_id, session_id=body.session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not read session: {exc}") from exc
    if session is None:
        raise HTTPException(status_code=404, detail="session not found for that user_id")

    if body.enqueue:
        from google.adk.apps.compaction import _latest_compaction_event

        from internal_tasks.enqueue import schedule_second_pass

        latest = _latest_compaction_event(list(session.events or []))
        comp = latest.actions.compaction if latest is not None else None
        end_ts = getattr(comp, "end_timestamp", None)
        if end_ts is None:
            raise HTTPException(status_code=422, detail="session has no compaction to second-pass")
        created = schedule_second_pass(
            session_id=body.session_id, user_id=body.user_id, compaction_end_ts=end_ts, delay_seconds=0
        )
        log.info("admin.compaction_recompact: uid=%s session=%s ENQUEUED=%s", scope.user.uid, body.session_id, created)
        return {"enqueued": created, "compaction_end_ts": end_ts}

    summarizer = build_compaction_summarizer()
    if summarizer is None:
        raise HTTPException(status_code=503, detail="could not resolve a summariser model; see server logs")

    outcome = await run_second_pass(
        session=session,
        session_service=session_service,
        summarizer=summarizer,
        dry_run=body.dry_run,
    )
    log.info(
        "admin.compaction_recompact: uid=%s session=%s appended=%s dry_run=%s selected=%d out=%d %dms",
        scope.user.uid,
        body.session_id,
        outcome.appended,
        outcome.dry_run,
        outcome.selected_events,
        outcome.summary_chars,
        outcome.elapsed_ms,
    )
    return outcome.__dict__.copy()
