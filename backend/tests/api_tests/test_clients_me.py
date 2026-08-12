"""GET /api/clients/me + ClientConfig.default_skill (v6.5.0 AUTH-LANDING)."""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.clients import ClientConfig, resolve_default_skill


def _make_user() -> User:
    return User(uid="caller-uid", email="user@acme-energy.example", domain="acme-energy.example")


# --- ClientConfig.default_skill model ---


def test_client_config_default_skill_round_trips():
    cfg = ClientConfig(domain="acme-energy.example", default_skill="one-ppa-expert")
    assert cfg.default_skill == "one-ppa-expert"
    restored = ClientConfig.model_validate(cfg.model_dump())
    assert restored.default_skill == "one-ppa-expert"


def test_client_config_default_skill_defaults_none():
    assert ClientConfig(domain="x.com").default_skill is None


# --- resolve_default_skill fallback chain ---


def test_resolve_default_skill_prefers_explicit():
    cfg = ClientConfig(domain="acme-energy.example", default_skill="one-ppa-expert", enabled_skills=["other", "x"])
    with mock.patch("db.clients.get_client_sync", return_value=cfg):
        assert resolve_default_skill(_make_user()) == "one-ppa-expert"


def test_resolve_default_skill_falls_back_to_first_enabled():
    cfg = ClientConfig(domain="acme-energy.example", enabled_skills=["one-ppa-expert", "web-researcher"])
    with mock.patch("db.clients.get_client_sync", return_value=cfg):
        assert resolve_default_skill(_make_user()) == "one-ppa-expert"


def test_resolve_default_skill_none_when_unconfigured():
    with mock.patch("db.clients.get_client_sync", return_value=None):
        assert resolve_default_skill(_make_user()) is None


# --- GET /api/clients/me ---


@pytest.fixture()
def app():
    import fast_api_app as module

    return module.app


@pytest.fixture()
def client(app):
    async def _override(request: Request) -> User:
        user = _make_user()
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_clients_me_returns_resolved_config(client):
    cfg = ClientConfig(
        domain="acme-energy.example",
        display_name="Acme Energy",
        enabled_skills=["one-ppa-expert", "one-doc-compare", "web-researcher"],
        default_skill="one-ppa-expert",
        documents_bucket="secret-bucket",
    )
    with mock.patch("db.clients.get_client_sync", return_value=cfg):
        resp = client.get("/api/clients/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["domain"] == "acme-energy.example"
    assert body["default_skill"] == "one-ppa-expert"
    assert body["enabled_skills"] == ["one-ppa-expert", "one-doc-compare", "web-researcher"]
    # documents_bucket must NOT leak via this non-admin endpoint.
    assert "documents_bucket" not in body


def test_clients_me_empty_for_unmapped_domain(client):
    with mock.patch("db.clients.get_client_sync", return_value=None):
        resp = client.get("/api/clients/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled_skills"] is None
    assert body["default_skill"] is None
