"""`schedule_second_pass` — the fail-soft contract and the task shape.

This runs inside the post-invocation compaction path of a user's request, so
its contract is absolute: NEVER raises, and every skip path is deliberate
(flag off, NULL-tracker context, unconfigured queue, RPC failure, duplicate).
The task it builds is also asserted precisely — name uniqueness per live
compaction, OIDC audience, ids-only payload — because those are the security
and idempotency properties the design doc promises.
"""

from __future__ import annotations

import json

import pytest

from internal_tasks import enqueue

QUEUE = "projects/your-project-id/locations/europe-west1/queues/platform-compaction"
SA = "platform-tasks@your-project-id.iam.gserviceaccount.com"
URL = "https://backend.example/internal/tasks/recompact"


def _configure(monkeypatch):
    monkeypatch.setenv("COMPACTION_SECOND_PASS_ENABLED", "true")
    monkeypatch.setenv("COMPACTION_TASKS_QUEUE", QUEUE)
    monkeypatch.setenv("COMPACTION_TASKS_OIDC_SA", SA)
    monkeypatch.setenv("COMPACTION_TASKS_TARGET_URL", URL)


class _Client:
    def __init__(self):
        self.requests = []

    def create_task(self, request, timeout=None):
        self.requests.append(request)


@pytest.fixture
def client(monkeypatch):
    fake = _Client()
    monkeypatch.setattr(enqueue, "_client", lambda: fake)
    return fake


def test_flag_off_is_a_silent_no(monkeypatch, client):
    monkeypatch.delenv("COMPACTION_SECOND_PASS_ENABLED", raising=False)
    assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=1.0) is False
    assert client.requests == []


def test_null_tracker_context_is_a_silent_no(monkeypatch, client):
    _configure(monkeypatch)
    assert enqueue.schedule_second_pass(session_id="", user_id="", compaction_end_ts=1.0) is False
    assert client.requests == []


def test_enabled_but_unconfigured_queue_skips(monkeypatch, client):
    """A deploy mistake — enabled without the queue env — must not fail the
    turn, but must not be silent either (it logs loudly; asserted by the skip)."""
    monkeypatch.setenv("COMPACTION_SECOND_PASS_ENABLED", "true")
    monkeypatch.delenv("COMPACTION_TASKS_QUEUE", raising=False)
    monkeypatch.delenv("COMPACTION_TASKS_OIDC_SA", raising=False)
    monkeypatch.delenv("COMPACTION_TASKS_TARGET_URL", raising=False)
    assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=1.0) is False
    assert client.requests == []


def test_task_shape_name_audience_and_ids_only_payload(monkeypatch, client):
    _configure(monkeypatch)
    ok = enqueue.schedule_second_pass(session_id="thread/abc:1", user_id="uid-1", compaction_end_ts=1723.5)
    assert ok is True
    assert len(client.requests) == 1
    task = client.requests[0].task

    # Unique per live compaction; session id sanitised for the task-id charset.
    assert task.name == f"{QUEUE}/tasks/recompact-thread_abc_1-1723500"
    # OIDC identity + audience — the security chain the route verifies.
    assert task.http_request.oidc_token.service_account_email == SA
    assert task.http_request.oidc_token.audience == URL
    assert task.http_request.url == URL
    # Identifiers only — session content never transits the queue.
    body = json.loads(task.http_request.body.decode("utf-8"))
    assert body == {"session_id": "thread/abc:1", "user_id": "uid-1", "compaction_end_ts": 1723.5}


def test_rpc_failure_is_suppressed(monkeypatch):
    _configure(monkeypatch)

    class _Boom:
        def create_task(self, request, timeout=None):
            raise RuntimeError("queue unreachable")

    monkeypatch.setattr(enqueue, "_client", lambda: _Boom())
    assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=1.0) is False


def test_duplicate_task_name_is_benign(monkeypatch):
    """A retried turn re-enqueues the same compaction; AlreadyExists is the
    designed outcome of per-compaction task names, not an error."""
    from google.api_core.exceptions import AlreadyExists

    _configure(monkeypatch)

    class _Dup:
        def create_task(self, request, timeout=None):
            raise AlreadyExists("recompact-s-1000 exists")

    monkeypatch.setattr(enqueue, "_client", lambda: _Dup())
    assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=1.0) is False


def test_a_second_pass_never_enqueues_another(monkeypatch, client):
    """THE recursion guard. `run_second_pass` drives the same summarizer whose
    `maybe_summarize_events` calls this hook, so without the guard each pass
    would schedule the next one forever, appending a compaction event per
    cycle. Currently also masked by the task path having no bound tracker —
    this test pins the EXPLICIT guard, so binding a tracker there later (an
    obvious observability change) can't quietly open the loop."""
    from adk import compaction_second_pass as core

    _configure(monkeypatch)
    token = core._in_second_pass.set(True)
    try:
        assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=1.0) is False
    finally:
        core._in_second_pass.reset(token)
    assert client.requests == []
    # ...and the guard is released afterwards, so live compactions still enqueue.
    assert enqueue.schedule_second_pass(session_id="s", user_id="u", compaction_end_ts=2.0) is True


def test_bad_idle_secs_falls_back_loudly(monkeypatch):
    monkeypatch.setenv("COMPACTION_SECOND_PASS_IDLE_SECS", "soon")
    assert enqueue.idle_seconds() == 2700
    monkeypatch.setenv("COMPACTION_SECOND_PASS_IDLE_SECS", "-5")
    assert enqueue.idle_seconds() == 2700
    monkeypatch.setenv("COMPACTION_SECOND_PASS_IDLE_SECS", "600")
    assert enqueue.idle_seconds() == 600
