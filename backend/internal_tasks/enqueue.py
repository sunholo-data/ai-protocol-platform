"""Schedule the second-pass recompaction when a live compaction lands.

Called from the summarizer seam (`FidelityEventSummarizer`) — the only moment
a second pass becomes worth scheduling, because the pass covers exactly the
span live compaction has claimed and new raw turns don't change it. One live
compaction → one task; no per-turn task noise, no size-floor heuristic.

FAIL-SOFT BY CONTRACT: this runs inside the post-invocation compaction path of
a user's request. It must never raise, never block meaningfully (RPC timeout is
capped), and a lost enqueue costs only quality-later — the next live compaction
re-enqueues, and the (future) nightly sweep is the backstop.

Task naming: ``recompact-{session}-{end_ms}`` — unique per live compaction, so
a retried turn hitting AlreadyExists is a benign duplicate, and nothing ever
needs delete-and-recreate (Cloud Tasks tombstones a completed task's name for
~1h, so a stable name would be a trap).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

_TASK_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_DEFAULT_IDLE_SECS = 2700  # 45 min — the doc's idle timer default

_client_instance = None


def _make_client():
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def _client():
    global _client_instance
    if _client_instance is None:
        _client_instance = _make_client()
    return _client_instance


def _config() -> tuple[str, str, str] | None:
    """(queue path, OIDC SA, target URL) — or None if any is missing."""
    queue = os.environ.get("COMPACTION_TASKS_QUEUE", "").strip()
    sa = os.environ.get("COMPACTION_TASKS_OIDC_SA", "").strip()
    url = os.environ.get("COMPACTION_TASKS_TARGET_URL", "").strip()
    if not (queue and sa and url):
        return None
    return queue, sa, url


def idle_seconds() -> int:
    """Admin setting (1b) → env var → coded default."""
    raw = os.environ.get("COMPACTION_SECOND_PASS_IDLE_SECS", "").strip()
    env_value = _DEFAULT_IDLE_SECS
    if raw:
        try:
            parsed = int(raw)
            if parsed <= 0:
                raise ValueError(parsed)
            env_value = parsed
        except ValueError:
            # Loud, then default — a silently-wrong idle timer would be invisible.
            logger.warning(
                "COMPACTION_SECOND_PASS_IDLE_SECS=%r is not a positive int; using %d", raw, _DEFAULT_IDLE_SECS
            )
    from adk.compaction_settings import second_pass_idle_seconds

    return second_pass_idle_seconds(env_value)


def schedule_second_pass(
    *, session_id: str, user_id: str, compaction_end_ts: float, delay_seconds: int | None = None
) -> bool:
    """Enqueue one recompaction task for the live compaction that just landed.

    ``delay_seconds`` overrides the idle timer — the admin/CLI `--enqueue`
    verification path uses 0 to exercise the full queue→OIDC→route chain
    without waiting out the idle window. Returns True if a task was created.
    Never raises.
    """
    from adk.compaction_second_pass import in_second_pass, second_pass_enabled

    if not second_pass_enabled():
        return False
    if in_second_pass():
        # A second pass drives the same summarizer that calls this hook.
        # Scheduling here would schedule another pass, forever.
        logger.debug("second-pass enqueue skipped: already inside a second pass")
        return False
    if not session_id or not user_id:
        # NULL tracker (no bound request context) — nothing to address the task to.
        logger.debug("second-pass enqueue skipped: no session/user context")
        return False

    config = _config()
    if config is None:
        # Enabled-but-unconfigured is a deploy mistake worth hearing about
        # (errors feed loudly), unlike the disabled case above.
        logger.warning("COMPACTION_SECOND_PASS_ENABLED but queue env vars missing; enqueue skipped")
        return False
    queue, sa, url = config

    try:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        delay = idle_seconds() if delay_seconds is None else max(0, delay_seconds)
        schedule = timestamp_pb2.Timestamp()
        schedule.FromDatetime(datetime.now(UTC) + timedelta(seconds=delay))
        safe_session = _TASK_ID_UNSAFE.sub("_", session_id)[:80]
        task = tasks_v2.Task(
            name=f"{queue}/tasks/recompact-{safe_session}-{int(compaction_end_ts * 1000)}",
            schedule_time=schedule,
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=url,
                headers={"Content-Type": "application/json"},
                # Identifiers only — session content never transits the queue.
                body=json.dumps(
                    {"session_id": session_id, "user_id": user_id, "compaction_end_ts": compaction_end_ts}
                ).encode("utf-8"),
                oidc_token=tasks_v2.OidcToken(service_account_email=sa, audience=url),
            ),
        )
        try:
            _client().create_task(tasks_v2.CreateTaskRequest(parent=queue, task=task), timeout=10)
        except AlreadyExists:
            # Same compaction, already scheduled (e.g. a retried turn). Benign.
            logger.debug("second-pass task already scheduled for session=%s end=%s", session_id, compaction_end_ts)
            return False
        logger.info("second-pass task scheduled: session=%s end=%s in %ds", session_id, compaction_end_ts, delay)
        return True
    except Exception as exc:
        logger.warning("second-pass enqueue failed (suppressed): %s", exc)
        return False
