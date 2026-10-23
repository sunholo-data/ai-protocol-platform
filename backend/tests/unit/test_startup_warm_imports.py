"""fast_api_app._warm_lazy_imports: pays the cold vertexai agent_engines import
at startup (off the event loop) instead of inside the first session call, where
it stalled every request for ~110s after a restart. Must be a no-op without
Agent Engine and must never raise."""

from __future__ import annotations

import builtins

import pytest


@pytest.fixture
def warm():
    # No reload: re-executing fast_api_app re-registers global OTel state and
    # breaks tests that run after this one (test_tenant_context).
    from fast_api_app import _warm_lazy_imports

    return _warm_lazy_imports


def test_noop_without_agent_engine(monkeypatch, warm):
    monkeypatch.delenv("AGENT_ENGINE_ID", raising=False)
    real_import = builtins.__import__

    def guard(name, *a, **k):
        assert not name.startswith("vertexai._genai"), "must not import without AGENT_ENGINE_ID"
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)
    warm()


def test_import_failure_never_raises(monkeypatch, warm):
    monkeypatch.setenv("AGENT_ENGINE_ID", "123")
    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name.startswith("vertexai._genai"):
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    warm()  # logs a warning, returns
