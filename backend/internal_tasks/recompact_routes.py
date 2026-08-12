"""`POST /internal/tasks/recompact` — the Cloud Tasks target for second-pass compaction.

Status codes ARE the retry protocol — Cloud Tasks retries any non-2xx:

- 200: work done, or a successful no-op (stale task superseded by a newer one,
  nothing to improve). Do not retry.
- 404: feature flag off. Cloud Tasks would retry a 404, but flag-off also stops
  every enqueue, so no tasks exist to hit it; stragglers from a just-flipped
  flag burn out against the retry horizon harmlessly.
- 503: the summariser declined or raised — the §3.2 class this feature converts
  into guaranteed progress. RETRY.

The handler returns 2xx only after `append_event` has landed (or a no-op is
established), so at-least-once delivery + the staleness check give
exactly-once EFFECT. See docs/design/v6.23.0/compaction-second-pass.md.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adk.compaction_second_pass import SecondPassOutcome, run_second_pass, second_pass_enabled
from internal_tasks.auth import assert_caller_is_task_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/tasks", tags=["internal-tasks"])

_TaskCaller = Annotated[str, Depends(assert_caller_is_task_queue)]


class RecompactTask(BaseModel):
    """The queue payload. Identifiers only — session content never transits
    Cloud Tasks (design doc, Security)."""

    session_id: str
    user_id: str = Field(description="ADK user id owning the session (the Firebase uid).")
    compaction_end_ts: float | None = Field(
        default=None,
        description="end_timestamp of the live compaction this task was enqueued for. Staleness guard: "
        "a session whose latest compaction ends later has a newer task; one already second-pass-marked "
        "is a duplicate delivery. Both no-op.",
    )


@router.post("/recompact")
async def recompact(body: RecompactTask, caller: _TaskCaller) -> dict:
    if not second_pass_enabled():
        # Flag-off is total: the enqueue side is also disabled, so this only
        # catches stragglers after a flag flip.
        raise HTTPException(status_code=404, detail="Not found")

    from adk.agui import APP_NAME
    from adk.compaction_summarizer import build_compaction_summarizer
    from adk.session import get_session_service

    session_service = get_session_service()
    try:
        session = await session_service.get_session(app_name=APP_NAME, user_id=body.user_id, session_id=body.session_id)
    except Exception as exc:
        # Transient store trouble — retryable by definition.
        raise HTTPException(status_code=503, detail=f"could not read session: {exc}") from exc
    if session is None:
        # Deleted since enqueue (user delete, sweep). Permanent: do not retry.
        logger.info("internal_tasks.recompact: session %s gone; dropping task", body.session_id)
        return {"dropped": "session not found"}

    summarizer = build_compaction_summarizer()
    if summarizer is None:
        raise HTTPException(status_code=503, detail="no summariser model available")

    try:
        outcome = await run_second_pass(
            session=session,
            session_service=session_service,
            summarizer=summarizer,
            for_compaction_end_ts=body.compaction_end_ts,
        )
    except Exception as exc:
        # Nothing was appended (the append is the final statement) — retry.
        logger.warning("internal_tasks.recompact: pass raised %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail=f"second pass failed: {type(exc).__name__}") from exc

    if outcome.retryable:
        raise HTTPException(status_code=503, detail="summariser declined; retrying")

    return _public(outcome)


def _public(outcome: SecondPassOutcome) -> dict:
    """Metadata only on the wire — the summary text derives from customer
    conversation content and this response transits Cloud Tasks logging."""
    data = outcome.__dict__.copy()
    data.pop("summary", None)
    return data
