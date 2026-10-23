"""`POST /internal/resident/notify` — the push-notification receiver (M5a.2).

THE GATE IS THE FEATURE. This route sits on a PUBLIC origin: the backend that
runs `ask_assistant` is the sidecar of the frontend service, which is deployed
`--allow-unauthenticated`, has no Next middleware, and whose `/api/proxy/**`
forwards `Authorization` untouched. Nothing upstream of this handler checks
anything. So the per-task token is the only thing between the internet and a
user's conversation, and the tests below are weighted accordingly.

The second theme is duplicates. The instance calls `notify()` on EVERY
`setState` (ailang docker/resident/lib/a2a.mjs:141), so `working` arrives
routinely and `completed` can arrive twice. One answer, once.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException


@pytest.fixture
def env(monkeypatch):
    """In-memory Firestore + a captured session service, so the assertions are
    about this route's behaviour rather than a mock's."""
    from db import firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    monkeypatch.setattr(fs, "_client", InMemoryFirestoreClient())

    from db import resident_tasks
    from internal_tasks import resident_routes as R

    appended: list = []

    class _Session:
        id = "sess-1"

    class _SessionService:
        async def get_session(self, **_kw):
            return _Session()

        async def create_session(self, **_kw):  # pragma: no cover - not the path under test
            return _Session()

        async def append_event(self, session=None, event=None, **_kw):
            appended.append(event)
            return event

    monkeypatch.setattr(R, "get_session_service", lambda: _SessionService())

    _rec, token = resident_tasks.create(
        "task-1",
        user_id="uid-1",
        session_id="sess-1",
        app_name="aitana_platform",
    )
    return {"routes": R, "tasks": resident_tasks, "token": token, "appended": appended}


class _Request:
    """Minimal stand-in: the handler reads the raw body and one header."""

    def __init__(self, body, auth=None):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Authorization": auth} if auth else {}

    async def body(self) -> bytes:
        return self._body


def _task(state="completed", text="the answer", task_id="task-1"):
    return {
        "kind": "task",
        "id": task_id,
        "contextId": "aitana-abc",
        "status": {"state": state},
        "artifacts": [{"parts": [{"kind": "text", "text": text}]}],
    }


async def _call(env, body, auth):
    return await env["routes"].resident_notify(_Request(body, auth))


def _text_of(event) -> str:
    return " ".join(p.text or "" for p in (event.content.parts or []))


# ---------------------------------------------------------------------------
# The gate — every failure the same 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_push_is_accepted_and_appends_the_answer(env):
    out = await _call(env, _task(), f"Bearer {env['token']}")
    assert out["delivered"] is True
    assert len(env["appended"]) == 1
    assert "the answer" in _text_of(env["appended"][0])


@pytest.mark.asyncio
async def test_a_wrong_token_is_403(env):
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(), "Bearer " + "x" * len(env["token"]))
    assert exc.value.status_code == 403
    assert env["appended"] == []


@pytest.mark.asyncio
async def test_an_unknown_task_is_403(env):
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(task_id="never-created"), f"Bearer {env['token']}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_missing_authorization_header_is_403(env):
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(), None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_malformed_body_is_403_not_422(env):
    """A Pydantic body model would make FastAPI answer 422 BEFORE the gate ran,
    telling an unauthenticated caller that the route exists and what shape it
    wants. The handler reads the raw body for exactly this reason."""
    with pytest.raises(HTTPException) as exc:
        await _call(env, b"{not json", f"Bearer {env['token']}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_body_with_no_task_id_is_403(env):
    with pytest.raises(HTTPException) as exc:
        await _call(env, {"kind": "task", "status": {"state": "completed"}}, f"Bearer {env['token']}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_task_id_with_a_slash_is_403_not_a_500(env):
    """The task id comes from the peer and lands in a Firestore document path."""
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(task_id="a/b"), f"Bearer {env['token']}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_every_rejection_is_indistinguishable(env):
    """Four different faults, one answer. A caller must not be able to use the
    response to learn whether a task id exists."""
    bodies = [
        (_task(), None),
        (_task(), "Bearer " + "x" * len(env["token"])),
        (_task(task_id="never-created"), f"Bearer {env['token']}"),
        (b"{not json", f"Bearer {env['token']}"),
    ]
    seen = set()
    for body, auth in bodies:
        with pytest.raises(HTTPException) as exc:
            await _call(env, body, auth)
        seen.add((exc.value.status_code, str(exc.value.detail)))
    assert len(seen) == 1, f"rejection responses differ: {seen}"


@pytest.mark.asyncio
async def test_a_bearer_prefix_is_required(env):
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(), env["token"])
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# States — three different facts, three different behaviours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_working_is_a_cheap_no_op(env):
    """The common case by volume: the instance notifies on every transition."""
    out = await _call(env, _task(state="working"), f"Bearer {env['token']}")
    assert out["delivered"] is False
    assert env["appended"] == []
    assert env["tasks"].get("task-1").state == "working"


@pytest.mark.asyncio
async def test_input_required_records_without_terminalising(env):
    """M5 criterion 3, the one part of the original spec still correct. The
    reply path that would unblock it is M5b and is blocked on M9 — so this must
    NOT be marked delivered, or the answer that eventually arrives is dropped."""
    out = await _call(env, _task(state="input-required"), f"Bearer {env['token']}")
    assert out["delivered"] is False
    assert env["appended"] == []
    assert env["tasks"].get("task-1").state == "input-required"
    assert env["tasks"].get("task-1").delivered_at is None


@pytest.mark.asyncio
async def test_failed_appends_a_visible_notice_rather_than_silence(env):
    """CLAUDE.md #8: an error swallowed while the UI stays put is a bug
    regardless of tests."""
    body = _task(state="failed", text="")
    body["artifacts"] = []
    body["status"]["message"] = {"parts": [{"kind": "text", "text": "pi exited 1"}]}
    out = await _call(env, body, f"Bearer {env['token']}")
    assert out["delivered"] is True
    assert len(env["appended"]) == 1
    said = _text_of(env["appended"][0])
    assert "pi exited 1" in said
    assert "could not" in said.lower() or "fail" in said.lower()


@pytest.mark.asyncio
async def test_a_completed_task_with_no_text_still_says_something(env):
    """An empty artifact must not render as an empty bubble — the reader would
    conclude the assistant returned nothing when it may have returned nothing
    *readable*."""
    body = _task(text="")
    body["artifacts"] = []
    out = await _call(env, body, f"Bearer {env['token']}")
    assert out["delivered"] is True
    assert _text_of(env["appended"][0]).strip()


# ---------------------------------------------------------------------------
# Duplicates — the normal case, not the exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_identical_completed_pushes_produce_one_message(env):
    first = await _call(env, _task(), f"Bearer {env['token']}")
    second = await _call(env, _task(), f"Bearer {env['token']}")
    assert first["delivered"] is True
    assert second["delivered"] is False
    assert len(env["appended"]) == 1


@pytest.mark.asyncio
async def test_a_failed_after_a_completed_does_not_append_twice(env):
    await _call(env, _task(), f"Bearer {env['token']}")
    await _call(env, _task(state="failed", text="late failure"), f"Bearer {env['token']}")
    assert len(env["appended"]) == 1


# ---------------------------------------------------------------------------
# What lands in the session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_appended_event_renders_as_an_assistant_message(env):
    """`_events_to_messages` (protocols/sessions_route.py) treats any event with
    text and `author != "user"` as an assistant bubble. That is the whole read
    path — no new endpoint is needed, but the event has to match it."""
    await _call(env, _task(), f"Bearer {env['token']}")
    ev = env["appended"][0]
    assert ev.author != "user"
    assert ev.content is not None and ev.content.parts
    assert _text_of(ev).strip()


@pytest.mark.asyncio
async def test_the_author_is_a_friendly_name_not_an_opaque_id(env):
    """CLAUDE.md #9. The author is read by humans in the transcript and by the
    model on the next turn; `s_<uuid>` and `res-<hash>` are neither."""
    await _call(env, _task(), f"Bearer {env['token']}")
    author = env["appended"][0].author
    assert not author.startswith("s_")
    assert not author.startswith("res-")
    assert "resident" in author.lower()


@pytest.mark.asyncio
async def test_the_session_is_addressed_by_the_recorded_triple(env, monkeypatch):
    """ADK sessions are keyed (app_name, user_id, session_id). Getting any of
    the three from the WEBHOOK rather than from the record would let the peer
    choose whose conversation to write into."""
    seen = {}

    class _Svc:
        async def get_session(self, **kw):
            seen.update(kw)

            class _S:
                id = "sess-1"

            return _S()

        async def append_event(self, session=None, event=None, **_kw):
            return event

    monkeypatch.setattr(env["routes"], "get_session_service", lambda: _Svc())
    body = _task()
    # Decoys everywhere a plausible implementation might have looked, not just
    # under metadata: top level, inside status, and the contextId.
    body["metadata"] = {"userId": "someone-else", "sessionId": "another-session", "appName": "other_app"}
    body["userId"] = "someone-else"
    body["sessionId"] = "another-session"
    body["appName"] = "other_app"
    body["contextId"] = "another-session"
    body["status"]["userId"] = "someone-else"
    await _call(env, body, f"Bearer {env['token']}")
    assert seen["user_id"] == "uid-1"
    assert seen["session_id"] == "sess-1"
    assert seen["app_name"] == "aitana_platform"


@pytest.mark.asyncio
async def test_a_failed_append_releases_the_claim_and_says_500(env, monkeypatch):
    """Claim-then-write is only safe if a failed write hands the claim back.
    Otherwise the task reads as delivered forever and the answer is gone."""

    class _Broken:
        async def get_session(self, **_kw):
            class _S:
                id = "sess-1"

            return _S()

        async def append_event(self, **_kw):
            raise RuntimeError("session store down")

    monkeypatch.setattr(env["routes"], "get_session_service", lambda: _Broken())
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(), f"Bearer {env['token']}")
    assert exc.value.status_code == 500
    assert env["tasks"].get("task-1").delivered_at is None, "the claim must be released"


@pytest.mark.asyncio
async def test_a_missing_session_is_reported_not_resurrected(env, monkeypatch):
    """The session was made by a real chat turn, so its absence means it was
    deleted or an in-memory store reset. Recreating a deleted conversation to
    put an answer in it is worse than a loud log."""
    created = []

    class _Gone:
        async def get_session(self, **_kw):
            return None

        async def create_session(self, **kw):
            created.append(kw)

        async def append_event(self, **_kw):  # pragma: no cover - must not be reached
            raise AssertionError("nothing may be appended to a session that is gone")

    monkeypatch.setattr(env["routes"], "get_session_service", lambda: _Gone())
    out = await _call(env, _task(), f"Bearer {env['token']}")
    assert out["delivered"] is False
    assert out["reason"] == "session_unavailable"
    assert created == [], "a deleted conversation must not be resurrected"
    assert env["tasks"].get("task-1").delivered_at is None, "an undelivered answer keeps its claim free"


def test_the_route_is_mounted_where_NOTIFY_PATH_says_it_is():
    """The webhook URL is built from `NOTIFY_PATH`. If the router moved and the
    constant did not, every real push would 404 while every test above passed."""
    from internal_tasks.resident_routes import NOTIFY_PATH, router

    mounted = {r.path for r in router.routes}
    assert NOTIFY_PATH in mounted, f"{NOTIFY_PATH} not among {mounted}"


def test_the_route_is_registered_on_the_application():
    """A router nobody includes is a route nobody can reach."""
    from fast_api_app import app
    from internal_tasks.resident_routes import NOTIFY_PATH

    assert NOTIFY_PATH in {r.path for r in app.routes}


# ── added after the M5a.2 adversarial review ────────────────────────────────


def _client(env, monkeypatch):
    """Through the real app, so the gate is exercised the way FastAPI runs it —
    header-name casing, a body param that would 422 before the gate, any
    dependency in front. Calling the handler directly cannot see any of that."""
    from fastapi.testclient import TestClient

    from fast_api_app import app
    from internal_tasks import resident_routes as R

    monkeypatch.setattr(R, "get_session_service", env["routes"].get_session_service)
    return TestClient(app, raise_server_exceptions=False)


def test_every_rejection_is_identical_through_the_real_app(env, monkeypatch):
    """The peer sends a lowercase `authorization` header (a2a.mjs:178); the
    hand-rolled stub used a dict keyed "Authorization" and hid that entirely."""
    c = _client(env, monkeypatch)
    good = _task()
    calls = [
        (good, {}),
        (good, {"authorization": "Bearer " + "x" * len(env["token"])}),
        (_task(task_id="never-created"), {"authorization": f"Bearer {env['token']}"}),
        (_task(task_id="__x__"), {"authorization": f"Bearer {env['token']}"}),
        (good, {"authorization": f"bearer {env['token']}"}),
    ]
    seen = set()
    for body, headers in calls:
        r = c.post("/internal/resident/notify", json=body, headers=headers)
        seen.add((r.status_code, r.text))
    assert seen == {(403, '{"detail":"Not authorized"}')}, seen


def test_a_lowercase_authorization_header_is_accepted(env, monkeypatch):
    """It is the only casing the peer actually sends."""
    c = _client(env, monkeypatch)
    r = c.post(
        "/internal/resident/notify",
        json=_task(),
        headers={"authorization": f"Bearer {env['token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] is True


def test_a_malformed_body_is_403_through_the_real_app(env, monkeypatch):
    """A Pydantic body model would make FastAPI answer 422 here, telling an
    unauthenticated caller the route exists and what shape it wants."""
    c = _client(env, monkeypatch)
    r = c.post(
        "/internal/resident/notify",
        content=b"{not json",
        headers={"authorization": f"Bearer {env['token']}", "content-type": "application/json"},
    )
    assert (r.status_code, r.text) == (403, '{"detail":"Not authorized"}')


def test_the_route_is_not_published_in_the_openapi_schema(env, monkeypatch):
    """/openapi.json is reachable through the same public proxy."""
    c = _client(env, monkeypatch)
    paths = c.get("/openapi.json").json().get("paths", {})
    assert "/internal/resident/notify" not in paths


@pytest.mark.asyncio
async def test_a_token_cannot_be_replayed_against_another_task(env):
    """The record-level test has a route-level twin, because this is the shape
    an attacker would actually try: a token they legitimately hold, pointed at
    somebody else's task. Both records exist, so a lookup keyed on the token
    rather than the task id passes every other test in this file."""
    _rec, other = env["tasks"].create("task-2", user_id="uid-2", session_id="sess-2", app_name="aitana_platform")
    assert other, "the second task has a real token; it is simply not the one presented"
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(task_id="task-2"), f"Bearer {env['token']}")
    assert exc.value.status_code == 403
    assert env["appended"] == []


def test_the_route_accepts_POST(env):
    """A GET-only registration would satisfy a path-only assertion."""
    from internal_tasks.resident_routes import NOTIFY_PATH, router

    methods = {m for r in router.routes if r.path == NOTIFY_PATH for m in r.methods}
    assert "POST" in methods


@pytest.mark.asyncio
async def test_a_non_dict_status_is_handled_not_a_500(env):
    """`{"status": "completed"}` used to reach `str.get` and 500."""
    out = await _call(env, {"kind": "task", "id": "task-1", "status": "completed"}, f"Bearer {env['token']}")
    assert out["delivered"] is False
    assert out["state"] == "unknown"


@pytest.mark.asyncio
async def test_a_gate_that_raises_still_answers_403(env, monkeypatch):
    """A Firestore outage must not answer 'this backend can reach its database'
    to anyone who asks."""

    def _boom(*a, **kw):
        raise RuntimeError("firestore unreachable")

    monkeypatch.setattr(env["tasks"], "verify_token", _boom)
    with pytest.raises(HTTPException) as exc:
        await _call(env, _task(), f"Bearer {env['token']}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_oversized_answer_is_truncated_before_it_becomes_context(env):
    """The peer caps its artifact at 16,000 chars; a token holder does not have
    to. An uncapped answer inflates every later turn of that conversation."""
    body = _task(text="y" * 60_000)
    await _call(env, body, f"Bearer {env['token']}")
    said = _text_of(env["appended"][0])
    assert len(said) < 20_000
    assert "truncated" in said


@pytest.mark.asyncio
async def test_a_failed_delivery_keeps_the_answer_and_says_why(env, monkeypatch):
    """Nothing retries — `notify()` logs its non-2xx and moves on. So a lost
    answer must at least be a RECORDED loss."""

    class _Gone:
        async def get_session(self, **_kw):
            return None

        async def append_event(self, **_kw):  # pragma: no cover
            raise AssertionError("nothing may be appended to a session that is gone")

    monkeypatch.setattr(env["routes"], "get_session_service", lambda: _Gone())
    out = await _call(env, _task(), f"Bearer {env['token']}")
    assert out["reason"] == "session_unavailable"
    rec = env["tasks"].get("task-1")
    assert rec.delivered_at is None
    assert rec.answer == "the answer", "the text must survive the failure that lost it"
    assert rec.delivery_error


@pytest.mark.asyncio
async def test_a_late_working_does_not_unwind_a_delivered_task(env):
    await _call(env, _task(), f"Bearer {env['token']}")
    await _call(env, _task(state="working"), f"Bearer {env['token']}")
    rec = env["tasks"].get("task-1")
    assert rec.state == "completed"
    assert rec.delivered_at is not None
    assert len(env["appended"]) == 1


@pytest.mark.asyncio
async def test_delivery_moves_the_conversation_up_the_users_list(env, monkeypatch):
    """A message nothing points at is a message nobody finds: the sidebar
    orders on lastMessageAt."""
    touched = {}
    from db import chat_sessions

    monkeypatch.setattr(chat_sessions, "update_session_fields", lambda sid, fields: touched.update(sid=sid, **fields))
    await _call(env, _task(), f"Bearer {env['token']}")
    assert touched["sid"] == "sess-1"
    assert isinstance(touched["lastMessageAt"], str), "ISO string — a native timestamp mis-sorts the column"
