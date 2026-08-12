"""`/internal/tasks/recompact` — the OIDC gate and the retry protocol.

The gate is the security boundary for a route on a PUBLIC service, so every
rejection path is asserted, not assumed: unconfigured env, missing/blank
bearer, failed verification, wrong email, unverified email, wrong issuer. Then
the status-code-as-retry-protocol mapping: no-ops are 200 (don't retry),
declines are 503 (retry — the §3.2 conversion), flag-off is 404.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from internal_tasks.recompact_routes import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

SA = "platform-tasks@your-project-id.iam.gserviceaccount.com"
AUD = "https://backend.example/internal/tasks/recompact"

GOOD_CLAIMS = {"iss": "https://accounts.google.com", "email": SA, "email_verified": True}


def _configure(monkeypatch, *, enabled: bool = True):
    monkeypatch.setenv("COMPACTION_TASKS_OIDC_SA", SA)
    monkeypatch.setenv("COMPACTION_TASKS_TARGET_URL", AUD)
    if enabled:
        monkeypatch.setenv("COMPACTION_SECOND_PASS_ENABLED", "true")
    else:
        monkeypatch.delenv("COMPACTION_SECOND_PASS_ENABLED", raising=False)


def _verify_returns(monkeypatch, claims):
    def _fake_verify(token, request, audience=None):
        assert audience == AUD, "audience must be enforced — without it any Google token passes"
        return claims

    monkeypatch.setattr("internal_tasks.auth.id_token.verify_oauth2_token", _fake_verify)


def _post(payload=None):
    return client.post(
        "/internal/tasks/recompact",
        json=payload or {"session_id": "s-1", "user_id": "u-1"},
        headers={"Authorization": "Bearer tok"},
    )


class TestOidcGate:
    def test_unconfigured_env_rejects_everything(self, monkeypatch):
        """Fail closed: no SA/audience configured means no caller is trusted."""
        monkeypatch.delenv("COMPACTION_TASKS_OIDC_SA", raising=False)
        monkeypatch.delenv("COMPACTION_TASKS_TARGET_URL", raising=False)
        assert _post().status_code == 403

    def test_missing_bearer_is_403(self, monkeypatch):
        _configure(monkeypatch)
        r = client.post("/internal/tasks/recompact", json={"session_id": "s", "user_id": "u"})
        assert r.status_code == 403

    def test_failed_verification_is_403(self, monkeypatch):
        _configure(monkeypatch)

        def _boom(token, request, audience=None):
            raise ValueError("bad token")

        monkeypatch.setattr("internal_tasks.auth.id_token.verify_oauth2_token", _boom)
        assert _post().status_code == 403

    @pytest.mark.parametrize(
        "claims",
        [
            {**GOOD_CLAIMS, "email": "someone-else@your-project-id.iam.gserviceaccount.com"},
            {**GOOD_CLAIMS, "email_verified": False},
            {**GOOD_CLAIMS, "iss": "https://evil.example"},
        ],
        ids=["wrong-sa", "unverified-email", "wrong-issuer"],
    )
    def test_wrong_principal_is_403(self, monkeypatch, claims):
        _configure(monkeypatch)
        _verify_returns(monkeypatch, claims)
        assert _post().status_code == 403


class TestRetryProtocol:
    """Status codes are the retry protocol: 2xx = done, 503 = retry, 404 = off."""

    def test_flag_off_is_404_even_for_the_queue(self, monkeypatch):
        _configure(monkeypatch, enabled=False)
        _verify_returns(monkeypatch, GOOD_CLAIMS)
        assert _post().status_code == 404

    def _wire_session(self, monkeypatch, session):
        class _Service:
            def __init__(self):
                self.appended = []

            async def get_session(self, **_kw):
                return session

            async def append_event(self, *, session, event):
                self.appended.append(event)

        service = _Service()
        monkeypatch.setattr("adk.session.get_session_service", lambda: service)
        return service

    def _wire_summarizer(self, monkeypatch, text):
        class _Stub:
            async def maybe_summarize_events(self, *, events):
                if text is None:
                    return None
                return Event(
                    author="user",
                    actions=EventActions(
                        compaction=EventCompaction(
                            start_timestamp=events[0].timestamp,
                            end_timestamp=events[-1].timestamp,
                            compacted_content=types.Content(role="model", parts=[types.Part(text=text)]),
                        )
                    ),
                )

        monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: _Stub())

    def _session_with_compaction(self):
        raw = Event(author="user", content=types.Content(role="user", parts=[types.Part(text="turn 0")]))
        raw.timestamp = 1.0
        comp = Event(
            author="user",
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=1.0,
                    end_timestamp=1.0,
                    compacted_content=types.Content(role="model", parts=[types.Part(text="live")]),
                )
            ),
        )
        comp.timestamp = 1.0

        class _S:
            def __init__(self):
                self.id = "s-1"
                self.events = [raw, comp]

        return _S()

    def test_vanished_session_is_dropped_not_retried(self, monkeypatch):
        _configure(monkeypatch)
        _verify_returns(monkeypatch, GOOD_CLAIMS)
        self._wire_session(monkeypatch, None)
        r = _post()
        assert r.status_code == 200
        assert "dropped" in r.json()

    def test_decline_is_503_so_cloud_tasks_retries(self, monkeypatch):
        """The §3.2 conversion: a silent no-op becomes a retried job."""
        _configure(monkeypatch)
        _verify_returns(monkeypatch, GOOD_CLAIMS)
        self._wire_session(monkeypatch, self._session_with_compaction())
        self._wire_summarizer(monkeypatch, None)
        assert _post().status_code == 503

    def test_success_is_200_metadata_only(self, monkeypatch):
        """The response transits Cloud Tasks logging — summary text (customer
        conversation content) must never ride it."""
        _configure(monkeypatch)
        _verify_returns(monkeypatch, GOOD_CLAIMS)
        service = self._wire_session(monkeypatch, self._session_with_compaction())
        self._wire_summarizer(monkeypatch, "better summary")
        r = _post()
        assert r.status_code == 200
        body = r.json()
        assert body["appended"] is True
        assert "summary" not in body
        assert body["summary_chars"] == len("better summary")
        assert len(service.appended) == 1

    def test_stale_task_is_200_no_op(self, monkeypatch):
        """A task enqueued for an older compaction yields to the newer one's."""
        _configure(monkeypatch)
        _verify_returns(monkeypatch, GOOD_CLAIMS)
        session = self._session_with_compaction()
        newer_raw = Event(author="user", content=types.Content(role="user", parts=[types.Part(text="new turn")]))
        newer_raw.timestamp = 3.0
        newer_comp = Event(
            author="user",
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=1.0,
                    end_timestamp=3.0,
                    compacted_content=types.Content(role="model", parts=[types.Part(text="newer live")]),
                )
            ),
        )
        newer_comp.timestamp = 3.0
        session.events = [*session.events, newer_raw, newer_comp]
        service = self._wire_session(monkeypatch, session)
        self._wire_summarizer(monkeypatch, "should not be called")
        r = _post({"session_id": "s-1", "user_id": "u-1", "compaction_end_ts": 1.0})
        assert r.status_code == 200
        assert r.json()["stale"] is True
        assert not service.appended
