"""Shared test fixtures for the aiplatform CLI."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a deterministic bearer token so tests never shell out to gcloud."""
    monkeypatch.setenv("AIPLATFORM_ID_TOKEN", "test-token-123")
    # Force local base URL so respx matches http://localhost:1956.
    monkeypatch.setenv("AIPLATFORM_API_URL", "http://localhost:1956")
    # Clear any project-level overrides that might surprise CI.
    for key in (
        "AIPLATFORM_API_URL_DEV",
        "AIPLATFORM_API_URL_TEST",
        "AIPLATFORM_API_URL_PROD",
        "AIPLATFORM_API_URL_LOCAL",
    ):
        os.environ.pop(key, None)
