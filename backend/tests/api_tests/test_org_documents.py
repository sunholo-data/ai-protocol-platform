"""Tests for org-scoped GCS bucket tools (G46 M3).

Four tests covering the per-deploy binding contract:

  1. Env-unset graceful — `list_org_documents()` returns `[]` and
     `read_org_document(name)` returns `{ok: False, ...}` when
     `A2A_AGENT_DOCUMENTS_BUCKET` is unset. The tools NEVER raise; the
     orchestrator can call them speculatively and fall back to text-only
     answering when nothing is bound.

  2. With-bucket happy path — `list_documents_in_bucket()` returns the
     per-object metadata dict (name / size / mimeType / timeCreated).
     Confirms the wire-shape the orchestrator's instructions depend on.

  3. IAM-missing / GCS-error graceful — any exception from the GCS
     client during LIST is caught; returns `[]` with a logged warning
     (never 500s the agent turn). A misconfigured IAM grant shouldn't
     break the whole agent.

  4. Malformed bucket URI — `get_bound_bucket()` returns None when the
     env var doesn't start with `gs://` (defence in depth so a
     misconfigured https:// or bare bucket name doesn't propagate into
     the GCS client as a confusing auth error far from source).

The GCS-touching paths are exercised via monkeypatch on `_gcs_client` so
unit tests run without Cloud credentials.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from tools import org_documents


@pytest.fixture(autouse=True)
def reset_module_caches() -> None:
    """Ensure the lru_cache singletons don't leak across tests."""
    org_documents._gcs_client.cache_clear()


# ---------------------------------------------------------------------------
# Test 1 — Env-unset graceful (covers both tools)
# ---------------------------------------------------------------------------


def test_org_document_tools_return_graceful_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both model-facing tools MUST return graceful results (never raise) when
    no bucket is bound. The orchestrator's instruction depends on this contract
    to know when to fall back to text-only answering.
    """
    monkeypatch.delenv("A2A_AGENT_DOCUMENTS_BUCKET", raising=False)

    # list_org_documents returns empty list.
    list_result = asyncio.run(org_documents.list_org_documents(prefix=""))
    assert list_result == [], f"unbound list_org_documents must return [], got {list_result!r}"

    # read_org_document returns failure dict with informative message.
    read_result = asyncio.run(org_documents.read_org_document(name="anything.pdf"))
    assert read_result["ok"] is False, f"unbound read_org_document must be ok=False, got {read_result!r}"
    assert read_result["doc_id"] is None
    assert "bound" in read_result["message"].lower() or "bucket" in read_result["message"].lower()


# ---------------------------------------------------------------------------
# Test 2 — With-bucket happy path: list returns per-object metadata
# ---------------------------------------------------------------------------


def test_list_documents_returns_object_metadata_with_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy-path: env set + mocked GCS client returns blobs → LIST returns
    name / size / mimeType / timeCreated dicts in the order GCS yields them.
    Confirms the wire-shape the orchestrator instruction depends on.
    """
    monkeypatch.setenv("A2A_AGENT_DOCUMENTS_BUCKET", "gs://my-bucket/")

    blob1 = MagicMock()
    blob1.name = "vendor-master/acme.json"
    blob1.size = 1024
    blob1.content_type = "application/json"
    blob1.time_created = datetime.datetime(2026, 1, 15, 12, 0, 0)

    blob2 = MagicMock()
    blob2.name = "invoices/2026/INV-001.pdf"
    blob2.size = 4096
    blob2.content_type = "application/pdf"
    blob2.time_created = datetime.datetime(2026, 2, 1, 9, 30, 0)

    class _Client:
        def list_blobs(self, bucket: Any, prefix: str = "", max_results: int | None = None) -> list[Any]:
            return [blob1, blob2]

        def bucket(self, name: str) -> Any:
            return MagicMock(name=name)

    monkeypatch.setattr(org_documents, "_gcs_client", lambda: _Client())

    result = asyncio.run(org_documents.list_documents_in_bucket("gs://my-bucket/", prefix=""))
    assert len(result) == 2
    assert result[0] == {
        "name": "vendor-master/acme.json",
        "size": 1024,
        "mimeType": "application/json",
        "timeCreated": "2026-01-15T12:00:00",
    }
    assert result[1]["name"] == "invoices/2026/INV-001.pdf"
    assert result[1]["mimeType"] == "application/pdf"

    # The tool layer should also be reachable end-to-end.
    tool_result = asyncio.run(org_documents.list_org_documents(prefix=""))
    assert len(tool_result) == 2
    assert tool_result[0]["name"] == "vendor-master/acme.json"


# ---------------------------------------------------------------------------
# Test 3 — IAM-missing / GCS-error graceful (defence in depth)
# ---------------------------------------------------------------------------


def test_list_documents_returns_empty_when_gcs_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception from the GCS client during LIST is caught and returns
    []. The tool degrades to "no documents available" rather than 500-ing
    the agent turn — critical for tenant-level resilience (a misconfigured
    IAM grant on `roles/storage.objectViewer` shouldn't break the whole
    agent's response).
    """

    class _BadClient:
        def list_blobs(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated GCS PermissionDenied")

        def bucket(self, name: str) -> Any:
            return MagicMock()

    monkeypatch.setattr(org_documents, "_gcs_client", lambda: _BadClient())

    result = asyncio.run(org_documents.list_documents_in_bucket("gs://my-bucket/", prefix=""))
    assert result == [], f"GCS error must degrade to [], got {result!r}"


# ---------------------------------------------------------------------------
# Test 4 — Malformed bucket URI rejected (returns None / [] not raise)
# ---------------------------------------------------------------------------


def test_get_bound_bucket_rejects_malformed_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured env (e.g. accidentally an https:// URL or bare bucket
    name) must not propagate into the GCS client — that would surface as a
    confusing auth error far from the source. Return None instead so the
    tool layer cleanly degrades to "no bucket bound".
    """
    # https:// URL — not a valid GCS URI.
    monkeypatch.setenv("A2A_AGENT_DOCUMENTS_BUCKET", "https://example.com/bucket")
    assert org_documents.get_bound_bucket() is None

    # Bare bucket name (no scheme).
    monkeypatch.setenv("A2A_AGENT_DOCUMENTS_BUCKET", "my-bucket")
    assert org_documents.get_bound_bucket() is None

    # Unset → None (sanity).
    monkeypatch.delenv("A2A_AGENT_DOCUMENTS_BUCKET", raising=False)
    assert org_documents.get_bound_bucket() is None

    # Valid gs:// → normalised trailing slash.
    monkeypatch.setenv("A2A_AGENT_DOCUMENTS_BUCKET", "gs://my-bucket")
    assert org_documents.get_bound_bucket() == "gs://my-bucket/"
    monkeypatch.setenv("A2A_AGENT_DOCUMENTS_BUCKET", "gs://my-bucket/prefix")
    assert org_documents.get_bound_bucket() == "gs://my-bucket/prefix/"
