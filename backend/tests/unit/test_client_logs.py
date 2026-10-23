"""``POST /api/client-logs`` — FRONTEND-OBS M2.

The browser's only route into Cloud Logging. It is public-internet-reachable,
which makes it the one piece of attack surface this feature adds, and the
milestone where a wrong-but-plausible implementation passes shallow tests: an
endpoint that accepts anonymous writes, or trusts a ``uid`` in the body, looks
perfectly healthy from a frontend that always sends a real token. So most of
what follows is negative.

Assertions are made against the FORMATTED line — the JSON ``StructuredFormatter``
would hand to the Cloud Run logging agent — not against the LogRecord. That is
the only level at which "cannot forge a second log line" and "the Error
Reporting envelope is present" mean anything.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from observability import client_logs
from observability.client_logs import (
    MAX_BATCH_EVENTS,
    MAX_BODY_BYTES,
    MAX_EVENT_BYTES,
    MAX_STACK_CHARS,
    router,
)
from observability.logging_setup import StructuredFormatter

# --- harness -----------------------------------------------------------------


def _client(uid: str = "u-token", group_tags: frozenset[str] = frozenset()) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(
        uid=uid, email=f"{uid}@example.com", domain="example.com", group_tags=group_tags
    )
    return TestClient(app)


def _no_auth_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def lines():
    """Capture what the Cloud Run logging agent would receive: one JSON object per line."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(StructuredFormatter())
    lg = logging.getLogger(client_logs.LOGGER_NAME)
    prev_level, prev_propagate = lg.level, lg.propagate
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    try:
        yield lambda: [json.loads(line) for line in buf.getvalue().splitlines() if line]
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
        lg.propagate = prev_propagate


@pytest.fixture(autouse=True)
def _fresh_limiter():
    client_logs.reset_for_tests()
    yield
    client_logs.reset_for_tests()


def _event(severity: str = "INFO", body: str = "stream_run_failed", **attrs) -> dict:
    return {
        "timestamp": "2026-09-04T10:12:33.123Z",
        "severityText": severity,
        "body": body,
        "attributes": attrs,
    }


def _post(client: TestClient, events: list[dict], **kw):
    return client.post("/api/client-logs", json={"events": events}, **kw)


# --- the happy path, so the gate is closed and not welded shut ---------------


def test_accepts_a_batch_and_reports_counts_only(lines):
    r = _post(_client(), [_event(), _event(severity="WARN", body="stream_truncated_no_terminal")])
    assert r.status_code == 202
    assert r.json() == {"accepted": 2, "dropped": 0, "droppedFields": 0}
    assert len(lines()) == 2


def test_never_echoes_event_content_back():
    r = _post(_client(), [_event(body="secret_event_name", errorMessage="do not echo")])
    assert "secret_event_name" not in r.text
    assert "do not echo" not in r.text


def test_every_line_is_tagged_as_browser_source(lines):
    _post(_client(), [_event()])
    (line,) = lines()
    assert line["source"] == "browser"
    assert line["message"] == "stream_run_failed"


def test_client_source_attribute_cannot_overwrite_the_discriminator(lines):
    _post(_client(), [_event("ERROR", errorName="E", errorMessage="m", source="unhandledrejection")])
    (line,) = lines()
    assert line["source"] == "browser"
    assert line["errorSource"] == "unhandledrejection"


def test_severity_maps_to_cloud_logging_severity(lines):
    _post(
        _client(),
        [_event("DEBUG"), _event("INFO"), _event("WARN"), _event("ERROR", errorName="E", errorMessage="m")],
    )
    assert [line["severity"] for line in lines()] == ["DEBUG", "INFO", "WARNING", "ERROR"]


def test_ambient_context_and_allowed_attributes_pass_through(lines):
    _post(_client(), [_event(threadId="t-1", sessionId="s-1", skillId="k-1", route="/chat", durationMs=42)])
    (line,) = lines()
    assert line["threadId"] == "t-1"
    assert line["sessionId"] == "s-1"
    assert line["skillId"] == "k-1"
    assert line["route"] == "/chat"
    assert line["durationMs"] == 42


# --- identity: from the token, never the body --------------------------------


def test_unauthenticated_post_is_refused():
    r = _no_auth_client().post("/api/client-logs", json={"events": [_event()]})
    assert r.status_code in (401, 403), "an open ingest route is a log-injection endpoint"


def test_uid_comes_from_the_token_and_a_body_uid_is_discarded(lines):
    r = _post(_client(uid="u-token"), [_event(uid="u-forged", userId="u-forged2", email="x@y.z")])
    assert r.status_code == 202
    (line,) = lines()
    assert line["uid"] == "u-token"
    assert "u-forged" not in json.dumps(line)
    assert "u-forged2" not in json.dumps(line)
    assert line["email"] == "u-token@example.com"
    # Every forged identity key counts as a dropped field — the client is misbehaving.
    assert r.json()["droppedFields"] == 3


def test_group_tags_come_from_the_token(lines):
    _post(_client(group_tags=frozenset({"tag-a"})), [_event()])
    (line,) = lines()
    assert line["groupTags"] == ["tag-a"]


# --- caps: rejected, never truncated ----------------------------------------


def test_over_cap_batch_is_rejected_not_truncated(lines):
    r = _post(_client(), [_event() for _ in range(MAX_BATCH_EVENTS + 1)])
    assert r.status_code == 413
    assert lines() == [], "a rejected batch must log NOTHING — partial acceptance is truncation"


def test_exactly_at_cap_batch_is_accepted():
    r = _post(_client(), [_event() for _ in range(MAX_BATCH_EVENTS)])
    assert r.status_code == 202
    assert r.json()["accepted"] == MAX_BATCH_EVENTS


def test_over_cap_body_is_rejected_before_parsing(lines):
    # Under EVERY other cap — batch count, per-event bytes (stacks excluded),
    # each stack field at its own limit — and over the BODY cap by bytes. The
    # body gate must be an independent first gate, not a consequence of the
    # others.
    stack, component = "s" * MAX_STACK_CHARS, "c" * MAX_STACK_CHARS
    events = [
        _event("ERROR", errorName="E", errorMessage="m", stack=stack, componentStack=component)
        for _ in range(MAX_BATCH_EVENTS)
    ]
    assert len(json.dumps({"events": events}).encode()) > MAX_BODY_BYTES
    r = _post(_client(), events)
    assert r.status_code == 413
    assert lines() == []


def test_over_cap_single_event_is_rejected(lines):
    r = _post(_client(), [_event(errorMessage="x" * (MAX_EVENT_BYTES + 1))])
    assert r.status_code == 413
    assert lines() == []


def test_over_cap_stack_is_rejected(lines):
    # The client truncates to MAX_STACK_CHARS itself, so a legitimate client can
    # never trip this. One that does is forged or broken — refuse it whole.
    r = _post(_client(), [_event("ERROR", errorName="E", errorMessage="m", stack="s" * (MAX_STACK_CHARS + 1))])
    assert r.status_code == 413
    assert lines() == []


def test_malformed_json_is_400_and_missing_fields_are_422():
    c = _client()
    assert (
        c.post("/api/client-logs", content=b"{not json", headers={"content-type": "application/json"}).status_code
        == 400
    )
    assert c.post("/api/client-logs", json={"events": [{"body": "no-severity"}]}).status_code == 422
    assert c.post("/api/client-logs", json={"events": [_event("FATAL")]}).status_code == 422
    assert c.post("/api/client-logs", json={"nope": []}).status_code == 422


def test_empty_batch_is_a_noop_202(lines):
    r = _post(_client(), [])
    assert r.status_code == 202
    assert r.json()["accepted"] == 0
    assert lines() == []


# --- the allowlist: unknown fields dropped AND counted ------------------------


def test_unknown_attribute_keys_are_dropped_and_counted(lines):
    r = _post(_client(), [_event(threadId="t-1", documentText="a whole contract", cardNumber="4111")])
    assert r.status_code == 202
    assert r.json()["droppedFields"] == 2
    (line,) = lines()
    assert line["threadId"] == "t-1"
    assert "documentText" not in line
    assert "a whole contract" not in json.dumps(line)
    assert "cardNumber" not in line


def test_nested_attribute_values_are_flattened_and_capped(lines):
    _post(_client(), [_event(componentStack={"deep": ["x" * 5000]})])
    (line,) = lines()
    assert isinstance(line["componentStack"], str)
    assert len(line["componentStack"]) <= client_logs.MAX_ATTR_CHARS


# --- Error Reporting envelope: ERROR only -----------------------------------


def test_error_severity_carries_the_error_reporting_envelope(lines, monkeypatch):
    monkeypatch.setenv("COMMIT_SHA", "abc1234")
    monkeypatch.setenv("CLIENT_LOG_SERVICE_NAME", "platform-frontend")
    _post(
        _client(),
        [
            _event(
                "ERROR",
                errorName="TypeError",
                errorMessage="cannot read x",
                stack="TypeError: cannot read x\n    at a.js:1:1",
            )
        ],
    )
    (line,) = lines()
    assert "@type" not in line, "BigQuery cannot take an @type column; the sink drops the row"
    assert line["serviceContext"] == {"service": "platform-frontend", "version": "abc1234"}
    # Error Reporting parses the stack out of `message`; a bare event name would
    # group every browser error into one bucket.
    assert line["message"].startswith("TypeError: cannot read x")
    assert "at a.js:1:1" in line["message"]


def test_non_error_severities_do_not_carry_the_envelope(lines):
    _post(_client(), [_event("INFO"), _event("WARN"), _event("DEBUG")])
    for line in lines():
        assert "serviceContext" not in line, "over-reporting makes Error Reporting alerts useless"
        assert "@type" not in line


def test_error_without_a_stack_still_groups_by_name_and_message(lines):
    _post(_client(), [_event("ERROR", errorName="RangeError", errorMessage="bad")])
    (line,) = lines()
    assert "serviceContext" in line
    assert line["message"] == "RangeError: bad"


def test_service_context_defaults_are_generic_not_a_deployment_name(lines, monkeypatch):
    # This module is template content; the real service name is per-deployment.
    monkeypatch.delenv("CLIENT_LOG_SERVICE_NAME", raising=False)
    monkeypatch.delenv("COMMIT_SHA", raising=False)
    _post(_client(), [_event("ERROR", errorName="E", errorMessage="m")])
    (line,) = lines()
    assert line["serviceContext"]["service"] == "browser"
    assert line["serviceContext"]["version"] == "dev"


# --- log injection ----------------------------------------------------------


def test_newlines_in_a_body_cannot_forge_a_second_log_line(lines):
    forged = 'harmless\n{"severity":"CRITICAL","message":"pwned","source":"backend"}'
    _post(_client(), [_event(body=forged)])
    out = lines()
    assert len(out) == 1, "one event must be exactly one line to the logging agent"
    assert out[0]["severity"] == "INFO"
    assert out[0]["source"] == "browser"
    assert "pwned" in out[0]["message"], "escaped, not dropped — the content is still evidence"


def test_control_characters_in_attributes_are_escaped_not_emitted(lines):
    _post(_client(), [_event(route="/chat\r\n\x00\x1b[31m")])
    out = lines()
    assert len(out) == 1
    assert out[0]["route"] == "/chat\r\n\x00\x1b[31m", "round-trips through JSON intact"


# --- rate limit: drop and count, never queue --------------------------------


def test_rate_limit_drops_excess_events_and_counts_them(lines, monkeypatch):
    monkeypatch.setenv("CLIENT_LOG_RATE_LIMIT_PER_MIN", "5")
    client_logs.reset_for_tests()
    c = _client(uid="chatty")
    r1 = _post(c, [_event() for _ in range(4)])
    assert r1.json() == {"accepted": 4, "dropped": 0, "droppedFields": 0}
    r2 = _post(c, [_event() for _ in range(3)])
    assert r2.status_code == 202, "over-limit is not an error to the client — it is a drop"
    assert r2.json()["accepted"] == 1
    assert r2.json()["dropped"] == 2
    assert len(lines()) == 5


def test_rate_limit_is_per_uid(lines, monkeypatch):
    monkeypatch.setenv("CLIENT_LOG_RATE_LIMIT_PER_MIN", "2")
    client_logs.reset_for_tests()
    _post(_client(uid="a"), [_event(), _event()])
    r = _post(_client(uid="b"), [_event(), _event()])
    assert r.json()["accepted"] == 2, "one user's flood must not silence another"


def test_rate_limited_events_are_not_queued_for_later(lines, monkeypatch):
    monkeypatch.setenv("CLIENT_LOG_RATE_LIMIT_PER_MIN", "1")
    client_logs.reset_for_tests()
    c = _client(uid="q")
    _post(c, [_event(body="first"), _event(body="second")])
    client_logs.reset_for_tests()  # simulate the window refilling
    _post(c, [_event(body="third")])
    assert [ln["message"] for ln in lines()] == ["first", "third"], "'second' was dropped, not deferred"
