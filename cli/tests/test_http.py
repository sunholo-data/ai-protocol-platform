"""Tests for aiplatform.http — auth + URL resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aiplatform.http import AuthError, get_bearer_token, resolve_base_url


def test_env_token_wins_over_gcloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIPLATFORM_ID_TOKEN", "env-token")
    assert get_bearer_token() == "env-token"


def test_gcloud_fallback_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPLATFORM_ID_TOKEN", raising=False)

    class _Completed:
        returncode = 0
        stdout = "gcloud-token\n"
        stderr = ""

    with patch("subprocess.run", return_value=_Completed()):
        assert get_bearer_token() == "gcloud-token"


def test_gcloud_failure_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPLATFORM_ID_TOKEN", raising=False)

    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "not logged in"

    with patch("subprocess.run", return_value=_Completed()):
        with pytest.raises(AuthError) as exc:
            get_bearer_token()
    assert "gcloud auth" in str(exc.value.message)
    assert "AIPLATFORM_ID_TOKEN" in str(exc.value.message)


def test_gcloud_not_installed_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPLATFORM_ID_TOKEN", raising=False)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(AuthError) as exc:
            get_bearer_token()
    assert "gcloud CLI not found" in str(exc.value.message)


def test_resolve_base_url_override_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIPLATFORM_API_URL", "https://custom.example.com/")
    assert resolve_base_url("dev") == "https://custom.example.com"


def test_resolve_base_url_per_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPLATFORM_API_URL", raising=False)
    monkeypatch.setenv("AIPLATFORM_API_URL_DEV", "https://dev.example.com")
    assert resolve_base_url("dev") == "https://dev.example.com"


def test_resolve_base_url_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPLATFORM_API_URL", raising=False)
    monkeypatch.delenv("AIPLATFORM_API_URL_LOCAL", raising=False)
    assert resolve_base_url("local") == "http://localhost:1956"
