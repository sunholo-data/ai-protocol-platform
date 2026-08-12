"""Tests for adk/session.py service singletons."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from adk.session import (
    _reset_artifact_service_for_tests,
    _reset_session_service_for_tests,
    get_artifact_service,
    get_session_service,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Ensure singletons are reset between tests."""
    _reset_artifact_service_for_tests()
    _reset_session_service_for_tests()
    yield
    _reset_artifact_service_for_tests()
    _reset_session_service_for_tests()


class TestArtifactServiceSingleton:
    def test_returns_same_instance_on_repeated_calls(self):
        svc1 = get_artifact_service()
        svc2 = get_artifact_service()
        assert svc1 is svc2

    def test_without_bucket_env_returns_in_memory(self):
        from google.adk.artifacts import InMemoryArtifactService

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADK_ARTIFACT_BUCKET", None)
            svc = get_artifact_service()
        assert isinstance(svc, InMemoryArtifactService)

    def test_with_bucket_env_returns_gcs(self):
        from google.adk.artifacts import GcsArtifactService

        with patch.dict(os.environ, {"ADK_ARTIFACT_BUCKET": "my-test-bucket"}):
            svc = get_artifact_service()
        assert isinstance(svc, GcsArtifactService)

    def test_reset_clears_singleton(self):
        svc1 = get_artifact_service()
        _reset_artifact_service_for_tests()
        svc2 = get_artifact_service()
        assert svc1 is not svc2


class TestSessionServiceSingleton:
    def test_returns_same_instance_on_repeated_calls(self):
        svc1 = get_session_service()
        svc2 = get_session_service()
        assert svc1 is svc2

    def test_without_agent_engine_returns_in_memory(self):
        from google.adk.sessions import InMemorySessionService

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_ENGINE_ID", None)
            svc = get_session_service()
        assert isinstance(svc, InMemorySessionService)


class TestLocalSessionEscapeHatch:
    """TTFT-OPTIMIZATION 1.21 — AITANA_LOCAL_SESSION=memory forces
    InMemory* services even when AGENT_ENGINE_ID is set, so laptop dev
    avoids per-turn round-trips to Vertex Agent Engine in europe-west1.
    Production unaffected (env var is only set in dev shells)."""

    def test_force_in_memory_overrides_agent_engine_id(self):
        from google.adk.sessions import InMemorySessionService

        with patch.dict(
            os.environ,
            {"AGENT_ENGINE_ID": "999", "AITANA_LOCAL_SESSION": "memory"},
        ):
            svc = get_session_service()
        assert isinstance(svc, InMemorySessionService), "AITANA_LOCAL_SESSION=memory must override AGENT_ENGINE_ID"

    def test_force_in_memory_also_applies_to_memory_service(self):
        from google.adk.memory import InMemoryMemoryService

        from adk.session import get_memory_service

        with patch.dict(
            os.environ,
            {"AGENT_ENGINE_ID": "999", "AITANA_LOCAL_SESSION": "memory"},
        ):
            mem_svc = get_memory_service()
        assert isinstance(mem_svc, InMemoryMemoryService)

    def test_unrelated_value_does_not_force_in_memory(self):
        """Only the literal value 'memory' opts in. Other values (typo,
        empty, '0', 'true') must keep Vertex behaviour so a stray env
        var can't silently downgrade production."""
        from adk.session import _force_in_memory_session

        for val in ("", "true", "1", "yes", "memry", "MEMORY ", "in-memory"):
            with patch.dict(os.environ, {"AITANA_LOCAL_SESSION": val}):
                if val.strip().lower() == "memory":
                    assert _force_in_memory_session(), f"value {val!r} should opt in"
                else:
                    assert not _force_in_memory_session(), f"value {val!r} must NOT opt in"

    def test_uri_helpers_also_respect_the_escape_hatch(self):
        """get_session_service_uri / get_memory_service_uri are passed to
        get_fast_api_app at import time and decide which backend ADK
        uses for its built-in agent endpoints. They MUST honour the
        escape hatch or `make dev` will route the SSE/agent endpoints
        through Vertex even though our skill_processor uses in-memory."""
        from adk.session import get_memory_service_uri, get_session_service_uri

        with patch.dict(
            os.environ,
            {"AGENT_ENGINE_ID": "999", "AITANA_LOCAL_SESSION": "memory"},
        ):
            assert get_session_service_uri() is None
            assert get_memory_service_uri() is None

    def test_memory_value_is_case_insensitive_and_trimmed(self):
        """`MEMORY`, `Memory`, ` memory ` should all opt in — devs will
        type any of these from muscle memory."""
        from adk.session import _force_in_memory_session

        for val in ("memory", "Memory", "MEMORY", " memory", "memory ", "  Memory  "):
            with patch.dict(os.environ, {"AITANA_LOCAL_SESSION": val}):
                assert _force_in_memory_session(), f"value {val!r} should opt in (case/trim)"
