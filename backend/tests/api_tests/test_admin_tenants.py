"""API tests for /api/admin/tenants — validation + atomic onboard (v6.9.0 M4).

All Firestore + GCS calls are mocked. Tests exercise:
  - deny-by-default: no admin tag -> 403
  - tenant-admin scoping: a tenant-admin of acme.com may onboard/validate ONLY
    acme.com, never other.com (no cross-tenant reach)
  - platform admin may act on any domain
  - unknown skill ref -> 422 BEFORE any write (atomic)
  - happy-path onboard -> 201 with config + per-step validation verdicts
  - 409 when the tenant already exists (edit via PUT instead)
  - bucket-reachability verdict is NAME + BOOLEAN only (never object contents)
  - every mutation records an admin_audit action

The GCS reachability probe itself needs real creds -> its live test lives under
``@pytest.mark.integration`` (test-fast skips it); here we mock ``probe_bucket``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_PLATFORM_ADMIN = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_ACME_ADMIN = User(
    uid="acme-admin-uid",
    email="boss@acme.com",
    domain="acme.com",
    group_tags=frozenset({"tenant-admin:acme.com"}),
)
_PLAIN_USER = User(
    uid="user-uid",
    email="user@acme.com",
    domain="acme.com",
    group_tags=frozenset(),
)


def _make_app() -> FastAPI:
    from admin.tenants import router

    app = FastAPI()
    app.include_router(router)
    return app


def _client(user: User) -> TestClient:
    app = _make_app()

    async def _override(request: Request) -> User:
        return user

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


# A probe result standing in for "bucket reachable" — name + booleans only.
_PROBE_OK = {"bucket": "acme-docs", "exists": True, "readable": True, "checked": True}
_PROBE_UNREACHABLE = {"bucket": "acme-docs", "exists": True, "readable": False, "checked": True}


def _skill(slug: str) -> MagicMock:
    m = MagicMock()
    m.slug = slug
    return m


# ---------------------------------------------------------------------------
# Gating / deny-by-default
# ---------------------------------------------------------------------------


def test_onboard_denied_for_plain_user() -> None:
    resp = _client(_PLAIN_USER).post("/api/admin/tenants", json={"domain": "acme.com"})
    assert resp.status_code == 403


def test_tenant_admin_cannot_reach_other_tenant() -> None:
    """A tenant-admin of acme.com must NOT onboard other.com (no cross-tenant)."""
    resp = _client(_ACME_ADMIN).post("/api/admin/tenants", json={"domain": "other.com"})
    assert resp.status_code == 403


def test_tenant_admin_can_onboard_own_domain() -> None:
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=[]),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document"),
        patch("admin.tenants.invalidate_client_cache"),
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_ACME_ADMIN).post("/api/admin/tenants", json={"domain": "acme.com"})
    assert resp.status_code == 201
    assert resp.json()["domain"] == "acme.com"


# ---------------------------------------------------------------------------
# Skill-ref validation (422 on unknown ref, BEFORE any write)
# ---------------------------------------------------------------------------


def test_onboard_rejects_unknown_skill_ref_before_write() -> None:
    known = [_skill("one-assistant"), _skill("one-ppa-expert")]
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=known),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document") as mock_set,
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_PLATFORM_ADMIN).post(
            "/api/admin/tenants",
            json={"domain": "acme.com", "enabled_skills": ["one-assistant", "does-not-exist"]},
        )
    assert resp.status_code == 422
    assert "does-not-exist" in str(resp.json())
    # Atomic: nothing was written.
    mock_set.assert_not_called()


def test_onboard_rejects_unknown_default_skill() -> None:
    known = [_skill("one-assistant")]
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=known),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document") as mock_set,
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_PLATFORM_ADMIN).post(
            "/api/admin/tenants",
            json={"domain": "acme.com", "default_skill": "ghost-skill"},
        )
    assert resp.status_code == 422
    mock_set.assert_not_called()


def test_onboard_lenient_when_skill_set_unavailable() -> None:
    """Empty known-slug set (Firestore hiccup / no skills) degrades to accept —
    validation is a guardrail, not access control; it must never 500 or block a
    legitimate onboard because the skills collection couldn't be read."""
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=[]),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document") as mock_set,
        patch("admin.tenants.invalidate_client_cache"),
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_PLATFORM_ADMIN).post(
            "/api/admin/tenants",
            json={"domain": "acme.com", "enabled_skills": ["anything-goes"]},
        )
    assert resp.status_code == 201
    mock_set.assert_called_once()


# ---------------------------------------------------------------------------
# Happy path onboard + audit + 409
# ---------------------------------------------------------------------------


def test_onboard_writes_config_and_returns_verdicts() -> None:
    known = [_skill("one-assistant"), _skill("one-ppa-expert")]
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=known),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document") as mock_set,
        patch("admin.tenants.invalidate_client_cache") as mock_inval,
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action") as mock_audit,
    ):
        resp = _client(_PLATFORM_ADMIN).post(
            "/api/admin/tenants",
            json={
                "domain": "acme.com",
                "display_name": "Acme",
                "documents_bucket": "acme-docs",
                "enabled_skills": ["one-assistant", "one-ppa-expert"],
                "default_skill": "one-assistant",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["config"]["domain"] == "acme.com"
    assert body["config"]["documents_bucket"] == "acme-docs"
    assert body["validation"]["ok"] is True
    fields = {c["field"] for c in body["validation"]["checks"]}
    assert {"enabled_skills", "default_skill", "documents_bucket"} <= fields
    mock_set.assert_called_once()
    mock_inval.assert_called_once_with("acme.com")
    mock_audit.assert_called_once()
    kwargs = mock_audit.call_args.kwargs
    assert kwargs["action"] == "onboard_tenant"
    assert kwargs["target"] == "acme.com"


def test_onboard_conflict_when_tenant_exists() -> None:
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=[]),
        patch("admin.tenants.get_document", return_value={"documents_bucket": "already"}),
        patch("admin.tenants.set_document") as mock_set,
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_PLATFORM_ADMIN).post("/api/admin/tenants", json={"domain": "acme.com"})
    assert resp.status_code == 409
    mock_set.assert_not_called()


def test_onboard_rejects_bad_domain() -> None:
    resp = _client(_PLATFORM_ADMIN).post("/api/admin/tenants", json={"domain": "notadomain"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Bucket verdict is name + boolean ONLY (never contents)
# ---------------------------------------------------------------------------


def test_bucket_verdict_exposes_only_name_and_booleans() -> None:
    with (
        patch("admin.tenants.skill_config.list_skills", return_value=[]),
        patch("admin.tenants.get_document", return_value=None),
        patch("admin.tenants.set_document"),
        patch("admin.tenants.invalidate_client_cache"),
        patch("admin.tenants.probe_bucket", return_value=_PROBE_UNREACHABLE),
        patch("admin.tenants.record_admin_action"),
    ):
        resp = _client(_PLATFORM_ADMIN).post(
            "/api/admin/tenants",
            json={"domain": "acme.com", "documents_bucket": "acme-docs"},
        )
    assert resp.status_code == 201
    checks = resp.json()["validation"]["checks"]
    bucket_check = next(c for c in checks if c["field"] == "documents_bucket")
    # Unreachable-but-exists -> a WARNING, never blocks (ok stays True).
    assert bucket_check["level"] == "warning"
    assert resp.json()["validation"]["ok"] is True
    # Only the bucket NAME + booleans may be exposed — never object names/bytes.
    assert set(bucket_check["details"].keys()) <= {"bucket", "exists", "readable"}
    assert bucket_check["details"]["bucket"] == "acme-docs"
    assert isinstance(bucket_check["details"]["exists"], bool)
    assert isinstance(bucket_check["details"]["readable"], bool)


# ---------------------------------------------------------------------------
# GET /{domain}/validate (dry-run over the stored config)
# ---------------------------------------------------------------------------


def test_validate_stored_tenant() -> None:
    from db.clients import ClientConfig

    stored = ClientConfig(
        domain="acme.com",
        documents_bucket="acme-docs",
        enabled_skills=["one-assistant"],
        default_skill="one-assistant",
    )
    with (
        patch("admin.tenants.get_client_sync", return_value=stored),
        patch("admin.tenants.skill_config.list_skills", return_value=[_skill("one-assistant")]),
        patch("admin.tenants.probe_bucket", return_value=_PROBE_OK),
    ):
        resp = _client(_PLATFORM_ADMIN).get("/api/admin/tenants/acme.com/validate")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_validate_unknown_tenant_404() -> None:
    with patch("admin.tenants.get_client_sync", return_value=None):
        resp = _client(_PLATFORM_ADMIN).get("/api/admin/tenants/nope.com/validate")
    assert resp.status_code == 404


def test_validate_denied_for_other_tenant() -> None:
    resp = _client(_ACME_ADMIN).get("/api/admin/tenants/other.com/validate")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# probe_bucket unit-level guarantees (mock the GCS client)
# ---------------------------------------------------------------------------


def test_probe_bucket_returns_name_and_booleans_only() -> None:
    import admin.tenants as tenants

    # A successful 1-object list ⇒ exists + readable. No bucket.exists() call.
    fake_client = MagicMock()
    fake_client.list_blobs.return_value = iter([MagicMock()])

    with patch.object(tenants, "_storage_client", return_value=fake_client):
        result = tenants.probe_bucket("acme-docs")

    assert set(result.keys()) == {"bucket", "exists", "readable", "checked"}
    assert result["bucket"] == "acme-docs"
    assert result["exists"] is True
    assert result["readable"] is True
    assert result["checked"] is True


def test_probe_bucket_empty_but_present_is_reachable() -> None:
    """An empty bucket lists no objects (empty iterator, no error) — still reachable."""
    import admin.tenants as tenants

    fake_client = MagicMock()
    fake_client.list_blobs.return_value = iter([])

    with patch.object(tenants, "_storage_client", return_value=fake_client):
        result = tenants.probe_bucket("empty-but-real")

    assert result == {"bucket": "empty-but-real", "exists": True, "readable": True, "checked": True}


def test_probe_bucket_object_level_only_no_bucket_metadata_call() -> None:
    """REGRESSION (2026-07-23): the probe must NOT gate on bucket.exists()
    (needs storage.buckets.get, which the runtime SA lacks). A bucket the SA can
    LIST objects in but can't get metadata on must read as reachable — the old
    exists()-first path false-negated every such bucket."""
    from google.api_core.exceptions import Forbidden

    import admin.tenants as tenants

    fake_bucket = MagicMock()
    fake_bucket.exists.side_effect = Forbidden("no storage.buckets.get")  # would 403 if called
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = iter([MagicMock()])  # objects.list works

    with patch.object(tenants, "_storage_client", return_value=fake_client):
        result = tenants.probe_bucket("acme-docs")

    assert result["exists"] is True and result["readable"] is True and result["checked"] is True
    fake_client.bucket.assert_not_called()  # never touched the metadata path


def test_probe_bucket_missing_bucket_404() -> None:
    from google.api_core.exceptions import NotFound

    import admin.tenants as tenants

    fake_client = MagicMock()
    fake_client.list_blobs.side_effect = NotFound("no such bucket")

    with patch.object(tenants, "_storage_client", return_value=fake_client):
        result = tenants.probe_bucket("does-not-exist")

    assert result == {"bucket": "does-not-exist", "exists": False, "readable": False, "checked": True}


def test_probe_bucket_forbidden_is_exists_but_unreadable() -> None:
    from google.api_core.exceptions import Forbidden

    import admin.tenants as tenants

    fake_client = MagicMock()
    fake_client.list_blobs.side_effect = Forbidden("no objectViewer")

    with patch.object(tenants, "_storage_client", return_value=fake_client):
        result = tenants.probe_bucket("acme-docs")

    assert result["exists"] is True
    assert result["readable"] is False
    assert result["checked"] is True


def test_probe_bucket_error_degrades_gracefully() -> None:
    import admin.tenants as tenants

    with patch.object(tenants, "_storage_client", side_effect=RuntimeError("no creds")):
        result = tenants.probe_bucket("acme-docs")

    assert result["checked"] is False
    assert result["exists"] is False
    assert result["readable"] is False


@pytest.mark.integration
def test_probe_bucket_live_gcs() -> None:
    """Live SA-reachability probe — needs real GCS creds (RUN_LIVE_GCP=1)."""
    import admin.tenants as tenants

    result = tenants.probe_bucket("aitana-documents-bucket")
    assert set(result.keys()) == {"bucket", "exists", "readable", "checked"}
