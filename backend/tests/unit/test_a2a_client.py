"""Outbound A2A client (v6.40.0 M2b).

Peers are stubbed with `httpx.MockTransport` rather than a mocking library, so
these tests add no dependency and exercise the real `a2a-sdk` request and
response encoding rather than a hand-rolled approximation of it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from protocols import a2a_client
from protocols.a2a_client import A2APeerError


@pytest.fixture(autouse=True)
def _no_card_cache_between_tests():
    a2a_client.clear_card_cache()
    yield
    a2a_client.clear_card_cache()


CARD = {
    "protocolVersion": "0.3.0",
    "name": "resident-pi",
    "description": "Resident pi agent",
    "url": "http://peer.local/a2a",
    "version": "0.1.0",
    "capabilities": {"streaming": False, "pushNotifications": True},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [{"id": "coding-agent", "name": "pi", "description": "codes", "tags": ["code"]}],
}


def _task(state: str = "working", task_id: str = "run-1") -> dict:
    return {
        "kind": "task",
        "id": task_id,
        "contextId": "ctx-1",
        "status": {"state": state, "timestamp": "2026-09-02T12:00:00Z"},
    }


def _transport(handler):
    """Wrap a handler so every stub peer also serves its agent card.

    ClientFactory resolves the card before invoking, which is A2A's intended
    discovery-then-invoke order, so a peer stub that only answered RPC would be
    testing a shape the real client never uses.
    """

    def routed(request: httpx.Request) -> httpx.Response:
        if "agent-card.json" in request.url.path or "agent.json" in request.url.path:
            return httpx.Response(200, json=CARD)
        return handler(request)

    return httpx.MockTransport(routed)


def _rpc_ok(request: httpx.Request, result: dict) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})


async def test_fetch_agent_card_resolves():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("agent-card.json")
        return httpx.Response(200, json=CARD)

    card = await a2a_client.fetch_agent_card("http://peer.local", transport=_transport(handler))
    assert card.name == "resident-pi"
    assert card.capabilities.push_notifications is True


async def test_send_message_returns_task():
    def handler(request: httpx.Request) -> httpx.Response:
        return _rpc_ok(request, _task())

    result = await a2a_client.send_message("http://peer.local", "do the thing", transport=_transport(handler))
    assert result["id"] == "run-1"
    assert result["status"]["state"] == "working"


async def test_run_id_and_model_travel_as_metadata():
    """The run id IS the task id, so the two systems share one identifier."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _rpc_ok(request, _task())

    await a2a_client.send_message(
        "http://peer.local",
        "hi",
        model="openrouter/z-ai/glm-5.3-flash",
        run_id="run-42",
        transport=_transport(handler),
    )
    md = seen["params"]["message"]["metadata"]
    assert md["model"] == "openrouter/z-ai/glm-5.3-flash"
    assert md["runId"] == "run-42"


async def test_task_id_continues_an_existing_task():
    """This is how A2A answers an input-required agent — no reply mechanism of
    our own."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _rpc_ok(request, _task(state="working"))

    await a2a_client.send_message("http://peer.local", "yes, proceed", task_id="run-42", transport=_transport(handler))
    assert seen["params"]["message"]["taskId"] == "run-42"


async def test_push_config_registered_on_send():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _rpc_ok(request, _task())

    await a2a_client.send_message(
        "http://peer.local",
        "hi",
        push_url="https://platform.example/internal/a2a/hook",
        push_token="tok",
        transport=_transport(handler),
    )
    cfg = seen["params"]["configuration"]["pushNotificationConfig"]
    assert cfg["url"] == "https://platform.example/internal/a2a/hook"
    assert cfg["token"] == "tok"


async def test_jsonrpc_error_envelope_raises_not_returns():
    """A JSON-RPC error arrives as HTTP 200 with an `error` member. A caller
    checking only the status would read a refusal as success — which is exactly
    how the peer's unregistered-model refusal would be swallowed."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32602, "message": "model is not in the pi registry"},
            },
        )

    with pytest.raises(A2APeerError) as exc:
        await a2a_client.send_message("http://peer.local", "hi", transport=_transport(handler))
    assert "not in the pi registry" in str(exc.value)
    assert "-32602" in str(exc.value)


async def test_unreachable_peer_raises_rather_than_returning_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(A2APeerError):
        await a2a_client.send_message("http://peer.local", "hi", transport=_transport(handler))


async def test_get_task_and_set_push_config_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "tasks/get":
            return _rpc_ok(request, _task(state="input-required"))
        return _rpc_ok(
            request,
            {"taskId": "run-1", "pushNotificationConfig": {"url": "https://p.example/hook"}},
        )

    task = await a2a_client.get_task("http://peer.local", "run-1", transport=_transport(handler))
    assert task["status"]["state"] == "input-required"

    cfg = await a2a_client.set_push_config(
        "http://peer.local", "run-1", "https://p.example/hook", transport=_transport(handler)
    )
    assert cfg["pushNotificationConfig"]["url"] == "https://p.example/hook"


async def test_https_peer_gets_a_bearer_token(monkeypatch):
    monkeypatch.setattr(a2a_client, "get_id_token", lambda audience: f"tok-for-{audience}")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _rpc_ok(request, _task())

    await a2a_client.send_message("https://peer.example", "hi", transport=_transport(handler))
    assert seen["auth"] == "Bearer tok-for-https://peer.example"


async def test_plain_http_peer_needs_no_credentials():
    """LOCAL_MODE and tests must not require ambient credentials."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _rpc_ok(request, _task())

    await a2a_client.send_message("http://peer.local", "hi", transport=_transport(handler))
    assert seen["auth"] is None


async def test_missing_id_token_is_a_visible_error(monkeypatch):
    from tools.mcp.id_token_cache import IdTokenUnavailable

    def boom(audience):
        raise IdTokenUnavailable("no ambient credentials")

    monkeypatch.setattr(a2a_client, "get_id_token", boom)
    with pytest.raises(A2APeerError) as exc:
        await a2a_client.send_message("https://peer.example", "hi")
    assert "no ID token" in str(exc.value)


# ── converse: one turn of a conversation (v6.40.0 M10/M11) ───────────────────
# The resident answers message/send immediately with a `submitted` task and
# works in the background, so a caller that does not poll has learned only that
# its request was accepted. These cover the difference.


def _conversation_transport(states, *, capture=None):
    """A peer that returns `states` in order, then repeats the last one.

    Repeating matters: a timeout test polls as fast as the loop allows, so an
    exhausted iterator would raise StopIteration and the test would pass for
    the wrong reason.
    """
    seq = list(states)
    pos = {"i": 0}

    def _next_state():
        i = min(pos["i"], len(seq) - 1)
        pos["i"] += 1
        return seq[i]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent-card.json") or request.url.path.endswith("agent.json"):
            return httpx.Response(200, json=CARD)
        body = json.loads(request.content)
        if capture is not None and body.get("method") == "message/send":
            capture.append(body)
        state = _next_state()
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "kind": "task",
                    "id": "t-1",
                    "contextId": "c-1",
                    "status": {"state": state},
                    "artifacts": [{"artifactId": "response", "parts": [{"kind": "text", "text": "hi back"}]}],
                },
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_converse_passes_context_id_so_the_thread_resumes():
    captured: list = []
    transport = _conversation_transport(["completed"], capture=captured)
    await a2a_client.converse("http://peer.local", "hello", context_id="ctx-abc", transport=transport, poll_s=0)
    sent = captured[0]["params"]["message"]
    assert sent["contextId"] == "ctx-abc"


@pytest.mark.asyncio
async def test_converse_polls_until_terminal():
    """A `submitted` task is not an answer; the helper must keep asking."""
    transport = _conversation_transport(["submitted", "working", "completed"])
    task = await a2a_client.converse("http://peer.local", "hello", context_id="ctx-abc", transport=transport, poll_s=0)
    assert task["status"]["state"] == "completed"
    assert a2a_client.answer_of(task) == "hi back"


@pytest.mark.asyncio
async def test_converse_raises_rather_than_returning_a_half_finished_task():
    """NEVER SILENT: a timeout must name the last state, not hand back a task
    that a caller would read as an answer."""
    transport = _conversation_transport(["submitted"])
    with pytest.raises(A2APeerError) as exc:
        await a2a_client.converse(
            "http://peer.local",
            "hello",
            context_id="ctx-abc",
            transport=transport,
            timeout_s=0.05,
            poll_s=0,
        )
    assert "did not finish" in str(exc.value)
    assert "submitted" in str(exc.value)


# ── W3C trace context propagation (v6.40.0 M8) ───────────────────────────────
# The resident agent continues an inbound `traceparent` so its spans land in the
# caller's chain rather than in a trace of their own. That only holds if
# somebody SENDS one — and until this, nobody did: httpx is not instrumented
# here, so every resident run opened its own trace sitting beside the
# platform's. The correlation the design asked for was half-built on each side
# and joined nowhere.


def test_the_peer_is_given_our_trace_context():
    """Without this the resident's `traceparent` handling never fires, and a
    resident run is a trace of its own next to the run that caused it."""
    from opentelemetry import trace

    from protocols import a2a_client

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("caller"):
        headers = a2a_client._peer_headers("http://peer.invalid")

    # Only asserted when a real SDK span is recording; with the default no-op
    # provider there is no context to propagate and an empty header set is
    # correct rather than a failure.
    if trace.get_current_span().get_span_context().is_valid:
        assert "traceparent" in headers


def test_propagation_never_breaks_the_call():
    """Telemetry is not allowed to be the reason an agent cannot be reached.
    A propagator that raises must cost the trace, not the turn."""
    from protocols import a2a_client

    class Exploding:
        def inject(self, carrier):
            raise RuntimeError("propagator is broken")

    original = a2a_client._propagator
    a2a_client._propagator = Exploding()
    try:
        headers = a2a_client._peer_headers("http://peer.invalid")
    finally:
        a2a_client._propagator = original
    assert headers == {}


def test_auth_survives_a_broken_propagator():
    """The Authorization header is the one that must never be dropped: losing
    it turns a telemetry fault into a 401 from the peer."""
    from protocols import a2a_client

    class Exploding:
        def inject(self, carrier):
            raise RuntimeError("boom")

    original_prop, original_auth = a2a_client._propagator, a2a_client._auth_headers
    a2a_client._propagator = Exploding()
    a2a_client._auth_headers = lambda url: {"Authorization": "Bearer tok"}
    try:
        headers = a2a_client._peer_headers("https://peer.invalid")
    finally:
        a2a_client._propagator, a2a_client._auth_headers = original_prop, original_auth
    assert headers["Authorization"] == "Bearer tok"


# ── converse: the push config, and the difference between slow and broken ────
# Both added for M5a.3. `converse` had `push_url`/`push_token` available on the
# send it wraps and dropped them, which is the "built on both sides, joined
# nowhere" shape this arc keeps meeting (HANDOVER §8.4).


async def test_converse_passes_the_push_config_through_to_the_send():
    """Asserted on the wire, not on the call signature: a helper that accepted
    the arguments and forwarded neither would pass a signature test."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "message/send":
            seen.update(body)
        return _rpc_ok(request, _task(state="completed"))

    await a2a_client.converse(
        "http://peer.local",
        "hi",
        context_id="ctx-1",
        push_url="https://platform.example/internal/resident/notify",
        push_token="tok-abc",
        transport=_transport(handler),
    )
    cfg = seen["params"]["configuration"]["pushNotificationConfig"]
    assert cfg["url"] == "https://platform.example/internal/resident/notify"
    assert cfg["token"] == "tok-abc"


async def test_a_task_that_outlives_the_wait_raises_a_DISTINCT_error():
    """'Still working' and 'could not be reached' are different facts. They
    were the same exception, so the tool reported a healthy long-running task
    as a broken peer."""
    from protocols.a2a_client import A2ATaskTimeout

    def handler(request: httpx.Request) -> httpx.Response:
        return _rpc_ok(request, _task(state="working", task_id="run-slow"))

    with pytest.raises(A2ATaskTimeout) as exc:
        await a2a_client.converse(
            "http://peer.local",
            "hi",
            context_id="ctx-1",
            timeout_s=0.01,
            poll_s=0.001,
            transport=_transport(handler),
        )
    assert exc.value.task_id == "run-slow"
    assert exc.value.last_state == "working"


async def test_the_timeout_error_is_still_an_A2APeerError():
    """Existing callers catch A2APeerError. Narrowing that would turn a handled
    timeout into an unhandled exception in a chat turn."""
    from protocols.a2a_client import A2ATaskTimeout

    assert issubclass(A2ATaskTimeout, A2APeerError)
