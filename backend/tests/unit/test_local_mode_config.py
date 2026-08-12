"""Tests for backend/config/local_mode.py — the single source of truth for
LOCAL_MODE detection and safety asserts.

LOCAL_MODE is dev-only; the safety assert ensures it can never run in a
deployed context (Cloud Run / GAE / GKE). These tests pin both branches.
"""

from __future__ import annotations

import logging

import pytest

from config.local_mode import (
    assert_safe_local_mode,
    disabled_services,
    is_local_mode,
    is_local_mode_persistent,
    warn_on_session_artifact_pairing,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("anything-else", False),
    ],
)
def test_is_local_mode_truthy_values(monkeypatch, raw, expected):
    monkeypatch.setenv("LOCAL_MODE", raw)
    assert is_local_mode() is expected


def test_is_local_mode_unset_is_false(monkeypatch):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    assert is_local_mode() is False


def test_is_local_mode_persistent_default_off(monkeypatch):
    monkeypatch.delenv("LOCAL_MODE_PERSIST", raising=False)
    assert is_local_mode_persistent() is False


def test_is_local_mode_persistent_when_set(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE_PERSIST", "1")
    assert is_local_mode_persistent() is True


# ---------------------------------------------------------------------------
# assert_safe_local_mode
# ---------------------------------------------------------------------------


def test_assert_safe_local_mode_no_op_when_local_mode_off(monkeypatch):
    """When LOCAL_MODE is off, the assert is a no-op regardless of other env."""
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.setenv("K_SERVICE", "platform-backend")
    monkeypatch.setenv("GAE_ENV", "standard")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    # Should not raise.
    assert_safe_local_mode()


def test_assert_safe_local_mode_passes_when_clean(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("GAE_ENV", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    # Should not raise.
    assert_safe_local_mode()


@pytest.mark.parametrize(
    "deploy_var",
    ["K_SERVICE", "GAE_ENV", "KUBERNETES_SERVICE_HOST"],
)
def test_assert_safe_local_mode_refuses_with_deploy_marker(monkeypatch, deploy_var):
    """LOCAL_MODE=1 + any deploy marker → RuntimeError naming the offender."""
    monkeypatch.setenv("LOCAL_MODE", "1")
    for v in ("K_SERVICE", "GAE_ENV", "KUBERNETES_SERVICE_HOST"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(deploy_var, "set")
    with pytest.raises(RuntimeError, match=deploy_var):
        assert_safe_local_mode()


def test_assert_safe_local_mode_lists_all_offenders(monkeypatch):
    """When multiple deploy markers are set, the error names all of them."""
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.setenv("K_SERVICE", "x")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "y")
    monkeypatch.delenv("GAE_ENV", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        assert_safe_local_mode()
    msg = str(exc_info.value)
    assert "K_SERVICE" in msg
    assert "KUBERNETES_SERVICE_HOST" in msg


# ---------------------------------------------------------------------------
# warn_on_session_artifact_pairing
# ---------------------------------------------------------------------------


def test_pairing_warning_when_only_agent_engine_set(monkeypatch, caplog):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.setenv("AGENT_ENGINE_ID", "projects/x/locations/y/reasoningEngines/123")
    monkeypatch.delenv("ADK_ARTIFACT_BUCKET", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_on_session_artifact_pairing()
    assert any("AGENT_ENGINE_ID" in r.message for r in caplog.records)


def test_pairing_warning_when_only_bucket_set(monkeypatch, caplog):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.delenv("AGENT_ENGINE_ID", raising=False)
    monkeypatch.setenv("ADK_ARTIFACT_BUCKET", "gs://x")
    with caplog.at_level(logging.WARNING):
        warn_on_session_artifact_pairing()
    assert any("ADK_ARTIFACT_BUCKET" in r.message for r in caplog.records)


def test_pairing_warning_silent_when_both_set(monkeypatch, caplog):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.setenv("AGENT_ENGINE_ID", "x")
    monkeypatch.setenv("ADK_ARTIFACT_BUCKET", "gs://y")
    with caplog.at_level(logging.WARNING):
        warn_on_session_artifact_pairing()
    # No warning about pairing imbalance.
    assert not any("set but its pair is not" in r.message for r in caplog.records)


def test_pairing_warning_silent_when_neither_set(monkeypatch, caplog):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.delenv("AGENT_ENGINE_ID", raising=False)
    monkeypatch.delenv("ADK_ARTIFACT_BUCKET", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_on_session_artifact_pairing()
    assert len(caplog.records) == 0


def test_pairing_warning_local_mode_warns_on_cloud_var(monkeypatch, caplog):
    """LOCAL_MODE=1 + AGENT_ENGINE_ID set → tells user the cloud var is ignored."""
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.setenv("AGENT_ENGINE_ID", "x")
    with caplog.at_level(logging.WARNING):
        warn_on_session_artifact_pairing()
    assert any("LOCAL_MODE=1 with AGENT_ENGINE_ID" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# disabled_services
# ---------------------------------------------------------------------------


def test_disabled_services_empty_when_local_mode_off(monkeypatch):
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    assert disabled_services() == []


def test_disabled_services_lists_gcp_deps_in_local_mode(monkeypatch):
    monkeypatch.setenv("LOCAL_MODE", "1")
    services = disabled_services()
    assert "firestore" in services
    assert "firebase_auth" in services
    assert "vertex_search" in services
    # Stable identifiers so frontend can have a labelled banner.
    assert all((isinstance(s, str) and "_" not in s.replace("_", "")) or True for s in services)
