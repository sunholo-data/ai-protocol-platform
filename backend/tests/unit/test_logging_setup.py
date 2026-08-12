"""Logging configuration contract (issue #39).

The bug this guards: the backend configured no logging, so the root logger sat
at WARNING and every `logger.info` from our own modules was discarded inside the
process. `LatencyTracker.emit_log()` writes the per-turn TTFT breakdown at INFO,
so the platform measured first-token latency on every chat turn and then threw
it away — we could not answer "how fast was that session?" for a real customer
session despite having computed the number.

These tests assert the two properties that make the data survive: it is EMITTED
(level), and it is QUERYABLE (parseable JSON carrying json_fields).
"""

from __future__ import annotations

import importlib
import json
import logging

import pytest


@pytest.fixture
def fresh_module(monkeypatch):
    """Reimport with a clean module-level `_configured` flag and saved root state."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def _load(*, on_cloud_run: bool, log_level: str | None = None):
        monkeypatch.setenv("K_SERVICE", "platform-frontend") if on_cloud_run else monkeypatch.delenv(
            "K_SERVICE", raising=False
        )
        if log_level is None:
            monkeypatch.delenv("LOG_LEVEL", raising=False)
        else:
            monkeypatch.setenv("LOG_LEVEL", log_level)
        import observability.logging_setup as mod

        importlib.reload(mod)
        return mod

    yield _load
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


class TestLevel:
    def test_defaults_to_info_so_ttft_lines_survive(self, fresh_module):
        """THE REGRESSION: at the stdlib default (WARNING) the TTFT line vanishes."""
        mod = fresh_module(on_cloud_run=False)
        assert mod.setup_logging() == "INFO"
        assert logging.getLogger().level == logging.INFO
        assert logging.getLogger("observability.timing").isEnabledFor(logging.INFO)

    def test_level_is_overridable(self, fresh_module):
        mod = fresh_module(on_cloud_run=False, log_level="WARNING")
        assert mod.setup_logging() == "WARNING"
        assert logging.getLogger().level == logging.WARNING

    def test_nonsense_level_falls_back_to_info(self, fresh_module):
        """A typo in LOG_LEVEL must not silently disable logging."""
        mod = fresh_module(on_cloud_run=False, log_level="LOUD")
        assert mod.setup_logging() == "INFO"

    def test_idempotent(self, fresh_module):
        """Re-import/second call must not stack handlers and double every line."""
        mod = fresh_module(on_cloud_run=False)
        mod.setup_logging()
        n = len(logging.getLogger().handlers)
        mod.setup_logging()
        assert len(logging.getLogger().handlers) == n


class TestStructuredShape:
    """On Cloud Run the line must be JSON so it lands in `jsonPayload`."""

    def test_ttft_payload_is_queryable_json(self, fresh_module, capsys):
        mod = fresh_module(on_cloud_run=True)
        mod.setup_logging()

        # Exactly what LatencyTracker.emit_log() does.
        logging.getLogger("observability.timing").info(
            "ttft skill=%s ttft_ms=%s",
            "one-assistant",
            812.5,
            extra={"json_fields": {"event": "ttft", "first_model_token_ms": 812.5, "skill_id": "one-assistant"}},
        )

        line = capsys.readouterr().out.strip().splitlines()[-1]
        payload = json.loads(line)  # must parse — otherwise it lands as textPayload
        assert payload["severity"] == "INFO"
        assert payload["event"] == "ttft", "jsonPayload.event='ttft' is the query we rely on"
        assert payload["first_model_token_ms"] == 812.5
        assert payload["skill_id"] == "one-assistant"
        assert "ttft skill=one-assistant" in payload["message"]

    def test_plain_text_off_cloud_run(self, fresh_module, capsys):
        """Local dev keeps readable logs — JSON lines would make `make dev` unusable."""
        mod = fresh_module(on_cloud_run=False)
        mod.setup_logging()
        logging.getLogger("x").info("hello")
        out = capsys.readouterr().out.strip()
        assert "hello" in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out.splitlines()[-1])

    def test_unserialisable_extra_never_raises(self, fresh_module, capsys):
        """Logging must never break the request path it is instrumenting."""
        mod = fresh_module(on_cloud_run=True)
        mod.setup_logging()

        class Exploding:
            def __repr__(self):
                raise RuntimeError("boom")

        logging.getLogger("x").info("msg", extra={"json_fields": {"bad": Exploding()}})
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["message"] == "msg"

    def test_exception_is_carried(self, fresh_module, capsys):
        mod = fresh_module(on_cloud_run=True)
        mod.setup_logging()
        try:
            raise ValueError("kaboom")
        except ValueError:
            logging.getLogger("x").exception("failed")
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["severity"] == "ERROR"
        assert "kaboom" in payload["exception"]
