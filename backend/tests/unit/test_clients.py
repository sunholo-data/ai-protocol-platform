"""Tests for db/clients.py — domain→bucket resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from auth.firebase_auth import User


def _user(domain: str) -> User:
    return User(uid="uid1", email=f"alice@{domain}", domain=domain)


class TestResolveDocumentsBucket:
    def test_returns_mapped_bucket_for_known_domain(self):
        from db.clients import resolve_documents_bucket

        mock_client = MagicMock()
        mock_client.documents_bucket = "rockwool-documents"

        with patch("db.clients.get_client_sync", return_value=mock_client):
            result = resolve_documents_bucket(_user("rockwool.com"))

        assert result == "rockwool-documents"

    def test_falls_back_to_env_for_unknown_domain(self, monkeypatch):
        """Default (fail-OPEN) behaviour: an unmapped domain shares the env
        fallback bucket. Retained for the one-release migration window."""
        from db.clients import resolve_documents_bucket

        monkeypatch.setenv("DOCUMENTS_BUCKET", "aitana-documents-bucket")
        monkeypatch.delenv("TENANT_FALLBACK_FAIL_CLOSED", raising=False)

        with patch("db.clients.get_client_sync", return_value=None):
            result = resolve_documents_bucket(_user("unknown.com"))

        assert result == "aitana-documents-bucket"

    def test_falls_back_to_env_when_client_has_no_bucket(self, monkeypatch):
        from db.clients import resolve_documents_bucket

        monkeypatch.setenv("DOCUMENTS_BUCKET", "aitana-documents-bucket")
        monkeypatch.delenv("TENANT_FALLBACK_FAIL_CLOSED", raising=False)

        mock_client = MagicMock()
        mock_client.documents_bucket = None

        with patch("db.clients.get_client_sync", return_value=mock_client):
            result = resolve_documents_bucket(_user("partial.com"))

        assert result == "aitana-documents-bucket"

    def test_fails_closed_for_unmapped_domain_when_flag_set(self, monkeypatch):
        """M0 security fix: with TENANT_FALLBACK_FAIL_CLOSED set, an unmapped
        domain is DENIED a bucket rather than sharing the deployment-wide one
        (cross-tenant commingling)."""
        import pytest

        from db.clients import UnmappedTenantError, resolve_documents_bucket

        monkeypatch.setenv("DOCUMENTS_BUCKET", "aitana-documents-bucket")
        monkeypatch.setenv("TENANT_FALLBACK_FAIL_CLOSED", "1")

        with patch("db.clients.get_client_sync", return_value=None):
            with pytest.raises(UnmappedTenantError) as excinfo:
                resolve_documents_bucket(_user("unknown.com"))

        assert excinfo.value.domain == "unknown.com"

    def test_fails_closed_when_client_has_no_bucket_and_flag_set(self, monkeypatch):
        import pytest

        from db.clients import UnmappedTenantError, resolve_documents_bucket

        monkeypatch.setenv("TENANT_FALLBACK_FAIL_CLOSED", "true")

        mock_client = MagicMock()
        mock_client.documents_bucket = None

        with patch("db.clients.get_client_sync", return_value=mock_client):
            with pytest.raises(UnmappedTenantError):
                resolve_documents_bucket(_user("partial.com"))

    def test_mapped_tenant_bucket_returned_even_when_fail_closed(self, monkeypatch):
        """A tenant WITH its own bucket is unaffected by the fail-closed flag."""
        from db.clients import resolve_documents_bucket

        monkeypatch.setenv("TENANT_FALLBACK_FAIL_CLOSED", "1")

        mock_client = MagicMock()
        mock_client.documents_bucket = "rockwool-documents"

        with patch("db.clients.get_client_sync", return_value=mock_client):
            result = resolve_documents_bucket(_user("rockwool.com"))

        assert result == "rockwool-documents"

    def test_uses_domain_from_user(self):
        from db.clients import resolve_documents_bucket

        calls = []

        def capturing_get(domain: str):
            calls.append(domain)
            return None

        with patch("db.clients.get_client_sync", side_effect=capturing_get):
            resolve_documents_bucket(_user("acme.org"))

        assert calls == ["acme.org"]


class TestResolveDerivedGroupTags:
    def test_returns_tags_for_mapped_domain(self):
        from db.clients import resolve_derived_group_tags

        mock_client = MagicMock()
        mock_client.derived_group_tags = ["ONE", "beta"]

        with patch("db.clients.get_client_sync", return_value=mock_client):
            tags = resolve_derived_group_tags("acme-energy.example")

        assert tags == frozenset({"ONE", "beta"})

    def test_returns_empty_when_no_mapping(self):
        from db.clients import resolve_derived_group_tags

        with patch("db.clients.get_client_sync", return_value=None):
            assert resolve_derived_group_tags("unknown.com") == frozenset()

    def test_returns_empty_when_field_missing(self):
        from db.clients import resolve_derived_group_tags

        mock_client = MagicMock()
        mock_client.derived_group_tags = None

        with patch("db.clients.get_client_sync", return_value=mock_client):
            assert resolve_derived_group_tags("partial.com") == frozenset()

    def test_returns_empty_for_empty_domain(self):
        from db.clients import resolve_derived_group_tags

        # No Firestore call should happen.
        with patch("db.clients.get_client_sync") as mock_get:
            assert resolve_derived_group_tags("") == frozenset()
            mock_get.assert_not_called()


class TestGetClientSync:
    def test_returns_none_for_missing_doc(self):
        from db.clients import get_client_sync

        with patch("db.clients.get_document", return_value=None):
            assert get_client_sync("nope.com") is None

    def test_returns_client_config_for_existing_doc(self):
        from db.clients import get_client_sync

        with patch(
            "db.clients.get_document",
            return_value={
                "documents_bucket": "acme-docs",
                "display_name": "Acme Corp",
            },
        ):
            client = get_client_sync("acme.com")

        assert client is not None
        assert client.documents_bucket == "acme-docs"
        assert client.display_name == "Acme Corp"
        assert client.domain == "acme.com"

    def test_tolerates_stored_domain_field(self):
        """A doc that also stores `domain` (the id) must not raise
        "multiple values for keyword argument 'domain'" — that 500'd every
        caller (skill list, landing redirect, derived-tag resolution)."""
        from db.clients import get_client_sync

        with patch(
            "db.clients.get_document",
            return_value={"domain": "acme.com", "derived_group_tags": ["aitana-admin"]},
        ):
            client = get_client_sync("acme.com")

        assert client is not None
        assert client.domain == "acme.com"
        assert client.derived_group_tags == ["aitana-admin"]


class TestClientConfigCache:
    """Durable two-tier cache in front of get_client_sync (v6.9.0 M4).

    The autouse `_reset_client_config_cache` conftest fixture clears the module
    tier between tests; the durable tier is stubbed empty per test by
    `_stub_firestore_client`. So each test starts cold."""

    def test_cached_read_hits_source_once(self):
        from db import clients

        cfg = clients.ClientConfig(domain="acme.com", documents_bucket="acme-docs")
        with patch("db.clients.get_client_sync", return_value=cfg) as mock_sync:
            first = clients.get_client_cached("acme.com")
            second = clients.get_client_cached("acme.com")

        assert first is cfg
        assert second is cfg
        # Module tier served the second call — source read only once.
        assert mock_sync.call_count == 1

    def test_negative_result_is_cached(self):
        from db import clients

        with patch("db.clients.get_client_sync", return_value=None) as mock_sync:
            assert clients.get_client_cached("nope.com") is None
            assert clients.get_client_cached("nope.com") is None

        assert mock_sync.call_count == 1

    def test_empty_domain_returns_none_without_source_call(self):
        from db import clients

        with patch("db.clients.get_client_sync") as mock_sync:
            assert clients.get_client_cached("") is None
            mock_sync.assert_not_called()

    def test_invalidate_forces_reread(self):
        from db import clients

        cfg1 = clients.ClientConfig(domain="acme.com", default_skill="old")
        cfg2 = clients.ClientConfig(domain="acme.com", default_skill="new")
        with patch("db.clients.get_client_sync", side_effect=[cfg1, cfg2]) as mock_sync:
            assert clients.get_client_cached("acme.com").default_skill == "old"
            clients.invalidate_client_cache("acme.com")
            assert clients.get_client_cached("acme.com").default_skill == "new"

        assert mock_sync.call_count == 2

    def test_durable_read_error_degrades_to_live_read(self):
        from db import clients

        cfg = clients.ClientConfig(domain="acme.com", documents_bucket="acme-docs")
        with (
            patch("db.clients.get_document", side_effect=RuntimeError("firestore down")),
            patch("db.clients.get_client_sync", return_value=cfg) as mock_sync,
        ):
            result = clients.get_client_cached("acme.com")

        # A cache-tier error must never break the read — falls through to source.
        assert result is cfg
        assert mock_sync.call_count == 1

    def test_resolve_enabled_skills_uses_cache(self):
        from db import clients

        cfg = clients.ClientConfig(domain="acme.com", enabled_skills=["one-assistant"])
        user = _user("acme.com")
        with patch("db.clients.get_client_sync", return_value=cfg) as mock_sync:
            a = clients.resolve_enabled_skills(user)
            b = clients.resolve_default_skill(user)

        assert a == ["one-assistant"]
        assert b == "one-assistant"  # falls back to enabled_skills[0]
        # Two hot-path resolvers, ONE source read — the >=2x re-read is removed.
        assert mock_sync.call_count == 1
