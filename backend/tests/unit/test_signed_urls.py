"""Unit tests for auth.signed_urls — GCS signed URL issuance (M3).

All network I/O is mocked:
  - google.cloud.storage.Client.list_blobs → returns fake Blob stubs
  - Blob.generate_signed_url → returns a deterministic URL containing the
    object path, so tests can assert prefix-scoping
  - google.auth.default → returns a mocked (credentials, project) pair
  - impersonated_credentials.Credentials → constructed without hitting IAM

Coverage:
  (a) prefix scope — every URL sits under gs://{bucket}/{folder.path}*
  (b) TTL honored + clamped to 3600
  (c) AccessDenied when ctx.can_access_folder(folder) returns False
  (d) Fallback path: if the signer cannot be built, the agent-factory
      integration sets tool_context.state['signed_urls_unavailable']=True
      and does NOT raise
  (e) Once tool_context.state carries signed_urls, tool invocation reads
      zero Firestore documents (integration smoke)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from auth.access_context import AccessContext
from auth.signed_urls import (
    AccessDenied,
    _clamped_ttl,
    issue_bucket_read_urls,
    issue_folder_read_urls,
)
from db.models import AccessControl, BucketConfig, BucketFolderConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _owner_ctx() -> AccessContext:
    return AccessContext(uid="owner-uid", email="owner@yourcompany.com", domain="yourcompany.com")


def _stranger_ctx() -> AccessContext:
    return AccessContext(uid="stranger", email="s@x.com", domain="x.com")


def _folder(
    path: str = "reports/2026/",
    owner_id: str = "owner-uid",
    access_type: str = "private",
) -> BucketFolderConfig:
    ac = AccessControl(type=access_type)
    return BucketFolderConfig(
        folderId="folder-1",
        bucketId="proj-bucket",
        path=path,
        displayName="Reports",
        ownerId=owner_id,
        accessControl=ac,
        effectiveAccess=ac,
    )


def _bucket(owner_id: str = "owner-uid", access_type: str = "private") -> BucketConfig:
    return BucketConfig(
        bucketId="proj-bucket",
        displayName="Project",
        ownerEmail="owner@yourcompany.com",
        ownerId=owner_id,
        gcsBucket="aitana-proj-bucket",
        accessControl=AccessControl(type=access_type),
    )


def _fake_blob(name: str) -> MagicMock:
    """Build a MagicMock Blob whose generate_signed_url echoes the object name."""
    blob = MagicMock()
    blob.name = name

    def _sign(
        expiration=None,
        method="GET",
        version="v4",
        credentials=None,
        service_account_email=None,
        access_token=None,
        **_kwargs,
    ):
        # Echo the key facts in the URL so assertions can inspect them.
        ttl = int(getattr(expiration, "total_seconds", lambda: expiration)())
        return f"https://storage.googleapis.com/proj-bucket/{name}?ttl={ttl}&sa={service_account_email or 'default'}"

    blob.generate_signed_url.side_effect = _sign
    return blob


@pytest.fixture
def patched_storage(monkeypatch):
    """Patch google.cloud.storage.Client so tests never hit GCS."""
    blobs = [
        _fake_blob("reports/2026/q1.pdf"),
        _fake_blob("reports/2026/q2.pdf"),
    ]
    client = MagicMock()
    client.list_blobs.return_value = blobs

    # Patch the module-level factory
    monkeypatch.setattr("auth.signed_urls._get_storage_client", lambda: client)
    return client, blobs


@pytest.fixture
def patched_signer(monkeypatch):
    """Patch the signer-credential builder to return a dummy object."""
    signer = SimpleNamespace(service_account_email="impersonated@proj.iam.gserviceaccount.com")
    monkeypatch.setattr("auth.signed_urls._build_signer_credentials", lambda: signer)
    return signer


# ---------------------------------------------------------------------------
# _clamped_ttl
# ---------------------------------------------------------------------------


def test_clamped_ttl_default_is_900(monkeypatch):
    monkeypatch.delenv("SIGNED_URL_TTL_SECONDS", raising=False)
    assert _clamped_ttl(None) == 900


def test_clamped_ttl_honors_env(monkeypatch):
    monkeypatch.setenv("SIGNED_URL_TTL_SECONDS", "1200")
    assert _clamped_ttl(None) == 1200


def test_clamped_ttl_clamps_to_3600(monkeypatch):
    monkeypatch.delenv("SIGNED_URL_TTL_SECONDS", raising=False)
    assert _clamped_ttl(99999) == 3600


def test_clamped_ttl_clamps_env_over_3600(monkeypatch):
    monkeypatch.setenv("SIGNED_URL_TTL_SECONDS", "7200")
    assert _clamped_ttl(None) == 3600


def test_clamped_ttl_explicit_wins_over_env(monkeypatch):
    monkeypatch.setenv("SIGNED_URL_TTL_SECONDS", "1200")
    assert _clamped_ttl(60) == 60


# ---------------------------------------------------------------------------
# issue_folder_read_urls
# ---------------------------------------------------------------------------


def test_issue_folder_read_urls_scopes_prefix(patched_storage, patched_signer):
    client, _blobs = patched_storage
    folder = _folder(path="reports/2026/")

    urls = issue_folder_read_urls(folder, _owner_ctx())

    # One URL per blob
    assert len(urls) == 2
    # list_blobs called with the exact prefix
    client.list_blobs.assert_called_once()
    _, kwargs = client.list_blobs.call_args
    assert kwargs.get("prefix") == "reports/2026/"
    # Every URL string starts with the gs://{bucket}/{folder.path} equivalent
    for url in urls:
        assert "proj-bucket" in url
        assert "reports/2026/" in url


def test_issue_folder_read_urls_raises_access_denied_when_ctx_cannot_access():
    folder = _folder(access_type="private", owner_id="other-uid")
    with pytest.raises(AccessDenied):
        issue_folder_read_urls(folder, _stranger_ctx())


def test_issue_folder_read_urls_honors_ttl(patched_storage, patched_signer, monkeypatch):
    monkeypatch.delenv("SIGNED_URL_TTL_SECONDS", raising=False)
    folder = _folder()

    urls = issue_folder_read_urls(folder, _owner_ctx(), ttl_seconds=600)

    for url in urls:
        assert "ttl=600" in url


def test_issue_folder_read_urls_clamps_ttl(patched_storage, patched_signer):
    folder = _folder()

    urls = issue_folder_read_urls(folder, _owner_ctx(), ttl_seconds=99999)

    for url in urls:
        assert "ttl=3600" in url


def test_build_signer_credentials_uses_env_sa(monkeypatch):
    """_build_signer_credentials reads SIGNED_URL_SA_EMAIL and passes it as
    target_principal. Covers the env-var plumbing end-to-end without relying
    on network I/O.
    """
    monkeypatch.setenv("SIGNED_URL_SA_EMAIL", "custom-sa@proj.iam.gserviceaccount.com")
    from google.auth import credentials as ga_creds

    fake_source = MagicMock(spec=ga_creds.Credentials)

    with patch("auth.signed_urls.google_auth.default", return_value=(fake_source, "proj")):
        with patch("auth.signed_urls.impersonated_credentials.Credentials") as imp:
            from auth.signed_urls import _build_signer_credentials

            _build_signer_credentials()
            _, kwargs = imp.call_args
            assert kwargs["target_principal"] == "custom-sa@proj.iam.gserviceaccount.com"


def test_build_signer_credentials_falls_back_to_source_sa(monkeypatch):
    """Without SIGNED_URL_SA_EMAIL, fall back to the source principal's SA email."""
    monkeypatch.delenv("SIGNED_URL_SA_EMAIL", raising=False)

    fake_source = MagicMock()
    fake_source.service_account_email = "cloud-run-default@proj.iam.gserviceaccount.com"

    with patch("auth.signed_urls.google_auth.default", return_value=(fake_source, "proj")):
        with patch("auth.signed_urls.impersonated_credentials.Credentials") as imp:
            from auth.signed_urls import _build_signer_credentials

            _build_signer_credentials()
            _, kwargs = imp.call_args
            assert kwargs["target_principal"] == "cloud-run-default@proj.iam.gserviceaccount.com"


# ---------------------------------------------------------------------------
# issue_bucket_read_urls
# ---------------------------------------------------------------------------


def test_issue_bucket_read_urls_scopes_bucket_root(patched_storage, patched_signer):
    client, _ = patched_storage
    bucket = _bucket()

    urls = issue_bucket_read_urls(bucket, _owner_ctx())

    client.list_blobs.assert_called_once()
    _, kwargs = client.list_blobs.call_args
    # Bucket root: either empty prefix or None (we pass `prefix or None`)
    assert kwargs.get("prefix") in (None, "")
    # Each URL references the GCS bucket
    for url in urls:
        assert "proj-bucket" in url


def test_issue_bucket_read_urls_raises_when_cant_access():
    bucket = _bucket(owner_id="other-uid", access_type="private")
    with pytest.raises(AccessDenied):
        issue_bucket_read_urls(bucket, _stranger_ctx())


# ---------------------------------------------------------------------------
# Agent-factory integration — fallback path
# ---------------------------------------------------------------------------


def test_agent_factory_signer_unavailable_fallback(caplog):
    """If the IAM signer cannot be built, the pre-run callback sets
    signed_urls_unavailable=True on state and does NOT crash the run."""
    from auth.signed_urls import build_signed_urls_for_folders

    # Force the signer builder to raise — simulates DefaultCredentialsError
    with patch("auth.signed_urls._build_signer_credentials", side_effect=RuntimeError("no creds")):
        state = {}
        build_signed_urls_for_folders([], _owner_ctx(), state=state)
    assert state.get("signed_urls_unavailable") is True
    assert "signed_urls" not in state or state["signed_urls"] == {}


def test_agent_factory_populates_state_with_urls(patched_storage, patched_signer):
    """Happy path: build_signed_urls_for_folders stashes URLs keyed by folder_id."""
    from auth.signed_urls import build_signed_urls_for_folders

    folder = _folder()
    state: dict = {}
    build_signed_urls_for_folders([folder], _owner_ctx(), state=state)

    assert "signed_urls" in state
    assert "folder-1" in state["signed_urls"]
    assert len(state["signed_urls"]["folder-1"]) == 2
    assert state.get("signed_urls_unavailable") is not True


def test_agent_factory_access_denied_folder_skipped(patched_storage, patched_signer):
    """A folder the user can't access is silently skipped (does not crash)."""
    from auth.signed_urls import build_signed_urls_for_folders

    bad = _folder(access_type="private", owner_id="other-uid")
    good = _folder()
    # Fix folder_id so both are distinguishable
    good_dict = good.model_dump()
    good_dict["folderId"] = "good-folder"
    good = BucketFolderConfig.model_validate(good_dict)

    state: dict = {}
    build_signed_urls_for_folders([bad, good], _stranger_ctx(), state=state)

    # bad skipped; good also denied to stranger → nothing stashed but no crash
    assert "signed_urls" in state
    # Neither folder's id is present (stranger can't access either)
    assert state["signed_urls"] == {}


# ---------------------------------------------------------------------------
# Zero-Firestore-reads assertion (the whole point)
# ---------------------------------------------------------------------------


def test_tool_invocation_with_signed_urls_reads_zero_firestore_docs(patched_storage, patched_signer):
    """When signed_urls is already on tool_context.state, no Firestore reads
    happen during a tool invocation. The mock fixture counts calls to
    db.firestore.get_document / get_client."""
    from auth.signed_urls import build_signed_urls_for_folders

    folder = _folder()
    state: dict = {}

    with patch("db.firestore.get_document") as mock_get_doc, patch("db.firestore.get_client") as mock_get_cli:
        build_signed_urls_for_folders([folder], _owner_ctx(), state=state)
        # Simulate a tool reading URLs from state
        urls = state.get("signed_urls", {}).get("folder-1", [])
        assert len(urls) == 2
        # NO Firestore traffic occurred
        assert mock_get_doc.call_count == 0
        assert mock_get_cli.call_count == 0
