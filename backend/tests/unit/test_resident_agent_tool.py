"""`ask_assistant` — the ADK door to the resident agent (v6.40.0 M11).

The assertions that matter here are about IDENTITY and about REFUSAL. A tool
that talks to a long-lived agent is one that can read a conversation, so who
the thread belongs to must not be something the model can choose, and a run
that did not finish must not come back looking like an answer.
"""

from __future__ import annotations

import pytest

from tools import resident_agent
from tools.resident_agent import ask_assistant, thread_id


class _Session:
    def __init__(self, sid):
        self.id = sid


class _Invocation:
    def __init__(self, user_id, session_id):
        self.user_id = user_id
        self.session = _Session(session_id)


class _ToolContext:
    def __init__(self, user_id, session_id):
        self._invocation_context = _Invocation(user_id, session_id)


def test_thread_id_is_stable_for_a_session():
    """A follow-up question must land in the same conversation."""
    assert thread_id("u1", "s1") == thread_id("u1", "s1")


def test_thread_id_differs_per_user():
    """Two users on the same session id must not share a thread."""
    assert thread_id("u1", "s1") != thread_id("u2", "s1")


def test_thread_id_differs_per_session():
    assert thread_id("u1", "s1") != thread_id("u1", "s2")


def test_thread_id_does_not_leak_the_user_id():
    """User ids may be email addresses and the contextId travels to another
    project's logs, so it is hashed rather than concatenated."""
    tid = thread_id("someone@example.com", "s1")
    assert "someone" not in tid and "example.com" not in tid


def test_the_model_cannot_choose_a_thread():
    """THE SECURITY ASSERTION. If contextId were a parameter, a model could ask
    for another user's conversation by naming it."""
    import inspect

    params = set(inspect.signature(ask_assistant).parameters)
    assert params == {"message", "tool_context"}, params


@pytest.mark.asyncio
async def test_unconfigured_refuses_visibly(monkeypatch):
    """Unset config must say so, not answer 'I don't know' and teach the model
    to stop asking."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "")
    out = await ask_assistant("hello", tool_context=_ToolContext("u1", "s1"))
    assert out["configured"] is False
    assert "RESIDENT_AGENT_URL" in out["error"]
    assert "answer" not in out


@pytest.mark.asyncio
async def test_completed_task_returns_the_answer(monkeypatch):
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    seen = {}

    async def _fake_converse(base_url, text, *, context_id, model=None, timeout_s=0.0, **kw):
        seen.update(base_url=base_url, text=text, context_id=context_id)
        return {
            "id": "task-1",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": "42"}]}],
        }

    monkeypatch.setattr("protocols.a2a_client.converse", _fake_converse)
    out = await ask_assistant("the answer?", tool_context=_ToolContext("u1", "s1"))
    assert out["answer"] == "42"
    assert out["task_id"] == "task-1"
    assert seen["context_id"] == thread_id("u1", "s1")


@pytest.mark.asyncio
async def test_unfinished_task_is_not_an_answer(monkeypatch):
    """input-required and failed are real outcomes and neither is a reply. The
    partial text is offered under its own key so nothing mistakes it for one."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")

    async def _fake_converse(*a, **kw):
        return {
            "id": "task-2",
            "status": {"state": "input-required"},
            "artifacts": [{"parts": [{"kind": "text", "text": "which repo?"}]}],
        }

    monkeypatch.setattr("protocols.a2a_client.converse", _fake_converse)
    out = await ask_assistant("do the thing", tool_context=_ToolContext("u1", "s1"))
    assert "answer" not in out
    assert out["partial"] == "which repo?"
    assert "input-required" in out["error"]


@pytest.mark.asyncio
async def test_unreachable_peer_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")

    async def _boom(*a, **kw):
        from protocols.a2a_client import A2APeerError

        raise A2APeerError("connection refused")

    monkeypatch.setattr("protocols.a2a_client.converse", _boom)
    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert "could not be reached" in out["error"]
    assert "answer" not in out


# ── per-user resolution (v6.40.0 M14) ────────────────────────────────────────
# Every state a user can be in must read differently. "You may not have one",
# "yours is still starting" and "yours broke" are three different facts, and a
# single shrug for all three is the failure this design keeps meeting.


class _Rec:
    def __init__(self, state, url=None, error=None):
        self.state = state
        self.url = url
        self.error = error


@pytest.fixture
def per_user(monkeypatch):
    monkeypatch.setattr(resident_agent, "RESIDENT_PER_USER", True)

    def _install(rec, boom=None):
        import db.resident_agents as records

        if boom:
            monkeypatch.setattr(records, "get", lambda _u: (_ for _ in ()).throw(RuntimeError(boom)))
        else:
            monkeypatch.setattr(records, "get", lambda _u: rec)
        monkeypatch.setattr(records, "touch", lambda _u: None)
        return records

    return _install


def test_no_record_says_who_can_grant_one(per_user):
    per_user(None)
    url, err = resident_agent._resolve_agent("u1")
    assert url == ""
    assert err["eligible"] is False
    assert "resident-user" in err["error"]


def test_ready_record_resolves_to_that_users_own_instance(per_user):
    per_user(_Rec("ready", url="https://mine.invalid/"))
    url, err = resident_agent._resolve_agent("u1")
    assert err is None
    assert url == "https://mine.invalid"


def test_provisioning_is_not_the_same_as_ineligible(per_user):
    per_user(_Rec("provisioning"))
    _, err = resident_agent._resolve_agent("u1")
    assert "still being set up" in err["error"]
    assert "eligible" not in err


def test_revoked_reads_differently_again(per_user):
    per_user(_Rec("revoked"))
    _, err = resident_agent._resolve_agent("u1")
    assert "revoked" in err["error"].lower()


def test_a_failed_provision_carries_its_reason(per_user):
    per_user(_Rec("failed", error="quota exhausted"))
    _, err = resident_agent._resolve_agent("u1")
    assert "quota exhausted" in err["error"]


def test_per_user_mode_never_falls_back_to_the_shared_instance(per_user, monkeypatch):
    """THE ASSERTION THAT MATTERS. A silent fallback from per-user to shared is
    exactly how one user's question gets answered on another user's box."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://shared.invalid")
    per_user(None)
    url, err = resident_agent._resolve_agent("u1")
    assert url == ""
    assert err is not None


def test_a_lookup_failure_is_reported_not_swallowed(per_user):
    per_user(None, boom="firestore down")
    _, err = resident_agent._resolve_agent("u1")
    assert "firestore down" in err["error"]


# ── waking a stopped instance (v6.40.0 M4) ───────────────────────────────────
# A stopped instance answers 404 from the Cloud Run frontend, which as a
# transport error is indistinguishable from a crash. Telling a user their
# assistant is broken when it is merely idle is the failure this design keeps
# meeting — and it would be the NORMAL case once an idle sweep runs.


@pytest.mark.asyncio
async def test_a_stopped_peer_is_reported_as_asleep_not_broken(monkeypatch):
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_START_ENDPOINT", "https://lifecycle.invalid")

    async def _boom(*a, **kw):
        from protocols.a2a_client import A2APeerError

        raise A2APeerError("connect error")

    async def _asleep(_url):
        return True

    async def _woke(_url):
        return True

    monkeypatch.setattr("protocols.a2a_client.converse", _boom)
    monkeypatch.setattr(resident_agent, "_is_asleep", _asleep)
    monkeypatch.setattr(resident_agent, "_wake", _woke)

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert out["state"] == "waking"
    assert "30 seconds" in out["error"]
    assert "answer" not in out


@pytest.mark.asyncio
async def test_asleep_with_no_start_path_says_so_plainly(monkeypatch):
    """The honest message when the deployment cannot wake it. Not a transport
    error, and not a promise that it is coming back on its own."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_START_ENDPOINT", "")

    async def _boom(*a, **kw):
        from protocols.a2a_client import A2APeerError

        raise A2APeerError("connect error")

    async def _asleep(_url):
        return True

    monkeypatch.setattr("protocols.a2a_client.converse", _boom)
    monkeypatch.setattr(resident_agent, "_is_asleep", _asleep)

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert out["state"] == "stopped"
    assert "administrator" in out["error"]


@pytest.mark.asyncio
async def test_a_genuinely_broken_peer_is_still_reported_as_broken(monkeypatch):
    """The asleep path must not swallow real faults — that would trade one
    indistinguishable failure for another."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")

    async def _boom(*a, **kw):
        from protocols.a2a_client import A2APeerError

        raise A2APeerError("500 from peer")

    async def _awake(_url):
        return False

    monkeypatch.setattr("protocols.a2a_client.converse", _boom)
    monkeypatch.setattr(resident_agent, "_is_asleep", _awake)

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert "could not be reached" in out["error"]
    assert "500 from peer" in out["error"]


# ── the URL `_wake` actually posts to (v6.40.0 M4, added 2026-09-04) ─────────
# Every other wake test above stubs `_wake` itself, so none of them could see
# that it posted to `/start` while the coordinator serves `/instances/start`.
# The live symptom was a bare "404 page not found", reported to the user as
# "your assistant is stopped and cannot be started" — indistinguishable from a
# deployment with no start path at all. These tests exercise the real function
# through a mock transport so the path and the audience are both pinned.


@pytest.mark.asyncio
async def test_wake_posts_to_the_coordinators_instances_start_path(monkeypatch):
    import httpx

    monkeypatch.setattr(resident_agent, "RESIDENT_START_ENDPOINT", "https://coordinator.invalid")
    monkeypatch.setattr("tools.mcp.id_token_cache.get_id_token", lambda audience: f"tok-for:{audience}")

    seen: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"started": True, "state": "starting"})

    transport = httpx.MockTransport(_handler)
    real_client = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _client)

    assert await resident_agent._wake("https://peer.invalid") is True
    assert seen["url"] == "https://coordinator.invalid/instances/start", (
        "the coordinator serves /instances/start; /start is a 404 that reads as 'no start path'"
    )
    # The token audience must be the BARE service URL — the coordinator checks
    # it against its own RESIDENT_LIFECYCLE_AUDIENCE, which carries no path.
    assert seen["auth"] == "Bearer tok-for:https://coordinator.invalid"


@pytest.mark.asyncio
async def test_wake_returns_false_and_says_why_on_a_404(monkeypatch):
    """A wrong path must not read as success. It returns False, and the caller
    then tells the user the deployment cannot start it — which is why the path
    above has to be right."""
    import httpx

    monkeypatch.setattr(resident_agent, "RESIDENT_START_ENDPOINT", "https://coordinator.invalid")
    monkeypatch.setattr("tools.mcp.id_token_cache.get_id_token", lambda audience: "tok")

    transport = httpx.MockTransport(lambda r: httpx.Response(404, text="404 page not found"))
    real_client = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    assert await resident_agent._wake("https://peer.invalid") is False


# ── one owner at a time, and slow is not broken (M5a.3) ─────────────────────
# Two bugs shaped this section. Before A2ATaskTimeout, a question that outlived
# the wait came back as "the assistant could not be reached" — a busy agent
# reported as a broken one. And the first fix registered the webhook up front,
# so a question that finished in 20 seconds was answered TWICE: once by this
# tool, once by the receiver, prefixed "from earlier".


@pytest.fixture
def _store(monkeypatch):
    from db import firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    monkeypatch.setattr(fs, "_client", InMemoryFirestoreClient())
    from db import resident_tasks

    return resident_tasks


PUSH_BASE = "https://platform.example/api/proxy"


def _completed(task_id="task-1"):
    return {
        "id": task_id,
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"kind": "text", "text": "42"}]}],
    }


def _timeout(task_id="peer-task-1"):
    async def _slow(base_url, text, **kw):
        from protocols.a2a_client import A2ATaskTimeout

        raise A2ATaskTimeout("did not finish", task_id=task_id, last_state="working")

    return _slow


@pytest.mark.asyncio
async def test_a_turn_that_finishes_in_time_registers_NOTHING(monkeypatch, _store):
    """THE DOUBLE-DELIVERY TEST. The poll is the watcher while it runs, so an
    answer returned in-turn must leave no webhook to say it again later."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)
    seen = {}

    async def _fake_converse(base_url, text, **kw):
        seen.update(kw)
        return _completed()

    monkeypatch.setattr("protocols.a2a_client.converse", _fake_converse)
    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))

    assert out["answer"] == "42"
    assert not seen.get("push_url"), "nothing may be watching while the poll is"
    assert not seen.get("run_id")
    from db import firestore as fs

    assert list(fs.get_client().collection(_store.COLLECTION).stream()) == [], "no record, so no second delivery"


@pytest.mark.asyncio
async def test_the_webhook_is_registered_only_when_the_poll_gives_up(monkeypatch, _store):
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)
    registered = {}

    async def _set_push(base_url, task_id, *, push_url, push_token=None, **kw):
        registered.update(task_id=task_id, push_url=push_url, push_token=push_token)

    async def _still_working(base_url, task_id, **kw):
        return {"id": task_id, "status": {"state": "working"}}

    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())
    monkeypatch.setattr("protocols.a2a_client.set_push_config", _set_push)
    monkeypatch.setattr("protocols.a2a_client.get_task", _still_working)

    out = await ask_assistant("something long", tool_context=_ToolContext("u1", "s1"))

    assert out["state"] == "working"
    assert "error" not in out, "a task that is running fine is not an error"
    assert "could not be reached" not in str(out)
    assert "this conversation" in out["note"]
    assert registered["task_id"] == "peer-task-1", "the PEER named the task; we adopt its id"
    assert registered["push_token"], "a webhook with no token is an open door"
    rec = _store.get("peer-task-1")
    assert rec is not None and rec.user_id == "u1" and rec.session_id == "s1"


@pytest.mark.asyncio
async def test_the_push_url_is_built_from_the_route_that_serves_it(monkeypatch, _store):
    """Not a second literal. /start against /instances/start cost an afternoon
    and every test stubbed the call that would have caught it."""
    from internal_tasks.resident_routes import NOTIFY_PATH

    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)
    registered = {}

    async def _set_push(base_url, task_id, *, push_url, push_token=None, **kw):
        registered["push_url"] = push_url

    async def _still_working(base_url, task_id, **kw):
        return {"id": task_id, "status": {"state": "working"}}

    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())
    monkeypatch.setattr("protocols.a2a_client.set_push_config", _set_push)
    monkeypatch.setattr("protocols.a2a_client.get_task", _still_working)
    await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert registered["push_url"] == f"{PUSH_BASE}{NOTIFY_PATH}"
    assert "/api/proxy/" in registered["push_url"], (
        "the frontend forwards only under /api/proxy — without it every push is a Next 404 "
        "reported nowhere this side can see"
    )


@pytest.mark.asyncio
async def test_a_task_that_finished_in_the_gap_is_answered_here_not_later(monkeypatch, _store):
    """The peer notifies on a TRANSITION. If the task completed between the last
    poll and the registration, no webhook will ever fire for it — so the
    re-check is the only thing standing between the user and silence."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)

    async def _set_push(*a, **kw):
        return None

    async def _finished(base_url, task_id, **kw):
        return _completed(task_id)

    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())
    monkeypatch.setattr("protocols.a2a_client.set_push_config", _set_push)
    monkeypatch.setattr("protocols.a2a_client.get_task", _finished)

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert out["answer"] == "42"
    assert _store.get("peer-task-1").delivered_at is not None, "claimed, so the webhook cannot repeat it"


@pytest.mark.asyncio
async def test_no_push_base_url_means_no_record_and_no_promise(monkeypatch, _store):
    """The code lands before the config does. Unconfigured must be exactly
    today's behaviour, and must not promise a delivery nothing can make."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", "")
    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())

    out = await ask_assistant("something long", tool_context=_ToolContext("u1", "s1"))
    assert "error" in out
    assert "this conversation" not in str(out)
    assert "answer" not in out
    assert _store.get("peer-task-1") is None


@pytest.mark.asyncio
async def test_an_unknown_caller_gets_no_record_and_no_promise(monkeypatch, _store):
    """`_identity` falls back to "unknown". A record keyed to nobody mints a
    live token for an answer that can never be delivered."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)
    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())

    out = await ask_assistant("hi", tool_context=None)
    assert "error" in out
    assert _store.get("peer-task-1") is None


@pytest.mark.asyncio
async def test_a_timeout_never_reports_the_peer_as_asleep(monkeypatch, _store):
    """A timeout means the peer ANSWERED and took the work. Falling into the
    asleep/stopped branch would tell the user to start an agent that is
    already running."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)

    async def _must_not_run(_url):  # pragma: no cover - the assertion is that it is not called
        raise AssertionError("a timed-out task must not be probed for sleep")

    async def _set_push(*a, **kw):
        return None

    async def _still_working(base_url, task_id, **kw):
        return {"id": task_id, "status": {"state": "working"}}

    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())
    monkeypatch.setattr("protocols.a2a_client.set_push_config", _set_push)
    monkeypatch.setattr("protocols.a2a_client.get_task", _still_working)
    monkeypatch.setattr(resident_agent, "_is_asleep", _must_not_run)
    out = await ask_assistant("something long", tool_context=_ToolContext("u1", "s1"))
    assert out["state"] == "working"


@pytest.mark.asyncio
async def test_a_registration_that_fails_does_not_promise_delivery(monkeypatch, _store):
    """Losing the ability to deliver later must cost the promise, not the turn —
    and must never leave the user waiting for something nobody will send."""
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)

    async def _refused(*a, **kw):
        raise RuntimeError("peer refused the push config")

    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())
    monkeypatch.setattr("protocols.a2a_client.set_push_config", _refused)

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert "error" in out
    assert "this conversation" not in str(out)


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_written_does_not_fail_the_turn(monkeypatch, _store):
    monkeypatch.setattr(resident_agent, "RESIDENT_AGENT_URL", "https://peer.invalid")
    monkeypatch.setattr(resident_agent, "RESIDENT_PUSH_BASE_URL", PUSH_BASE)

    def _boom(*a, **kw):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(_store, "create", _boom)
    monkeypatch.setattr("protocols.a2a_client.converse", _timeout())

    out = await ask_assistant("hi", tool_context=_ToolContext("u1", "s1"))
    assert "error" in out, "an unpromisable delivery is reported, not silently dropped"
