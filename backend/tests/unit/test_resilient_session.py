"""Unit tests for ResilientVertexSessionService (issue #30 session-write resilience).

Exercises the retry + loud-give-up behaviour WITHOUT touching Vertex: we patch
the *parent* VertexAiSessionService methods so no real client is constructed.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from google.api_core import exceptions as gapi

from adk.resilient_session import _MAX_ATTEMPTS, ResilientVertexSessionService


def _svc() -> ResilientVertexSessionService:
    # Bypass __init__ (which would build a real Vertex client) — we only test the
    # override wrappers, which call super().<method> that we patch.
    return ResilientVertexSessionService.__new__(ResilientVertexSessionService)


@pytest.mark.asyncio
async def test_create_session_retries_transient_then_succeeds(caplog):
    calls = {"n": 0}

    async def flaky(**_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise gapi.ServiceUnavailable("503 backend unavailable")
        return "SESSION"

    with (
        patch("asyncio.sleep", return_value=None),
        patch("google.adk.sessions.VertexAiSessionService.create_session", side_effect=flaky),
    ):
        out = await _svc().create_session(app_name="a", user_id="u", session_id="s")

    assert out == "SESSION"
    assert calls["n"] == 3  # failed twice, succeeded on the third


@pytest.mark.asyncio
async def test_append_event_gives_up_LOUDLY_and_reraises(caplog):
    """The whole point of #30: a persistent failure must be VISIBLE (ERROR log)
    and re-raised, never swallowed into a silent divergence."""

    async def always_503(*_a, **_k):
        raise gapi.ServiceUnavailable("503")

    class _Sess:
        id = "sess-123"

    class _Evt:
        author = "assistant"

    with (
        patch("asyncio.sleep", return_value=None),
        patch("google.adk.sessions.VertexAiSessionService.append_event", side_effect=always_503),
        caplog.at_level(logging.ERROR, logger="adk.resilient_session"),
    ):
        with pytest.raises(gapi.ServiceUnavailable):
            await _svc().append_event(_Sess(), _Evt())

    # Loud give-up log names the op + session and points at #30.
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("append_event FAILED" in r.getMessage() and "sess-123" in r.getMessage() for r in errors)


@pytest.mark.asyncio
async def test_permanent_4xx_is_not_retried(caplog):
    """A non-retryable error (e.g. NotFound/PermissionDenied) fails fast — no
    wasted retry budget."""
    calls = {"n": 0}

    async def not_found(**_kwargs):
        calls["n"] += 1
        raise gapi.NotFound("404 no such engine")

    with (
        patch("asyncio.sleep", return_value=None),
        patch("google.adk.sessions.VertexAiSessionService.create_session", side_effect=not_found),
    ):
        with pytest.raises(gapi.NotFound):
            await _svc().create_session(app_name="a", user_id="u", session_id="s")

    assert calls["n"] == 1  # tried once, not retried


@pytest.mark.asyncio
async def test_bounded_attempts(caplog):
    """Retries are bounded — a permanent transient error stops after _MAX_ATTEMPTS."""
    calls = {"n": 0}

    async def always_429(**_kwargs):
        calls["n"] += 1
        raise gapi.TooManyRequests("429")

    with (
        patch("asyncio.sleep", return_value=None),
        patch("google.adk.sessions.VertexAiSessionService.create_session", side_effect=always_429),
    ):
        with pytest.raises(gapi.TooManyRequests):
            await _svc().create_session(app_name="a", user_id="u", session_id="s")

    assert calls["n"] == _MAX_ATTEMPTS
