"""Per-tenant skill visibility filter on GET /api/skills (v6.4.0 ONE-DEMO M1).

When `clients/{user_domain}.enabled_skills` is a non-empty list, the list
response is narrowed to those slugs. None or missing falls through to the
existing "all skills the caller can access" behaviour.

Defence-in-depth: the filter is server-side. A client editing JS can't
enumerate hidden skills.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.clients import ClientConfig
from db.models import SkillConfig
from skills.routes import router


def _make_config(slug: str, **overrides) -> SkillConfig:
    defaults = {
        "name": slug,
        "description": f"Description for {slug}.",
        "instructions": "Help with stuff.",
        "skillId": f"id-{slug}",
        "displayName": slug.replace("-", " ").title(),
        "ownerEmail": "platform@aitana.ai",
        "ownerId": "platform-uid",
        "slug": slug,
        "accessControl": {"type": "public"},
    }
    defaults.update(overrides)
    return SkillConfig(**defaults)


def _app_for(user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


_ONE_USER = User(
    uid="one-user-1",
    email="anders@acmeenergy.com",
    domain="acmeenergy.com",
    group_tags=frozenset(),
)
_ADMIN_USER = User(
    uid="admin-1",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_UNMAPPED_USER = User(
    uid="rando-1",
    email="someone@example.com",
    domain="example.com",
    group_tags=frozenset(),
)


@pytest.fixture()
def all_skills() -> list[SkillConfig]:
    return [
        _make_config("one-ppa-expert"),
        _make_config("one-doc-compare"),
        _make_config("general-assistant"),
        _make_config("web-researcher"),
        _make_config("code-assistant"),
        _make_config("document-analyst"),
    ]


# ---------------------------------------------------------------------------
# Tenant filter active
# ---------------------------------------------------------------------------


def test_one_tenant_user_sees_only_enabled_skills(all_skills: list[SkillConfig]) -> None:
    """ONE-domain user with enabled_skills set sees ONLY those 3 slugs."""
    client = _app_for(_ONE_USER)
    one_config = ClientConfig(
        domain="acmeenergy.com",
        documents_bucket="multivac-acme-energy-bucket",
        display_name="Acme Energy",
        enabled_skills=["one-ppa-expert", "one-doc-compare", "general-assistant"],
    )
    with (
        patch("skills.routes.skill_config.list_skills", return_value=all_skills),
        patch("db.clients.get_client_sync", return_value=one_config),
    ):
        resp = client.get("/api/skills")
    assert resp.status_code == 200
    slugs = sorted(s["slug"] for s in resp.json())
    assert slugs == ["general-assistant", "one-doc-compare", "one-ppa-expert"]


# ---------------------------------------------------------------------------
# Tenant filter inactive (None = "all skills visible")
# ---------------------------------------------------------------------------


def test_admin_with_null_enabled_skills_sees_full_catalogue(all_skills: list[SkillConfig]) -> None:
    """Admin domain with `enabled_skills = None` falls through — no narrowing."""
    client = _app_for(_ADMIN_USER)
    admin_config = ClientConfig(
        domain="yourcompany.com",
        documents_bucket=None,
        display_name="Aitana Labs",
        enabled_skills=None,  # explicit None: no filter
    )
    with (
        patch("skills.routes.skill_config.list_skills", return_value=all_skills),
        patch("db.clients.get_client_sync", return_value=admin_config),
    ):
        resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


def test_unmapped_domain_sees_full_catalogue(all_skills: list[SkillConfig]) -> None:
    """Unmapped domain (no clients/{domain} doc at all) → unfiltered."""
    client = _app_for(_UNMAPPED_USER)
    with (
        patch("skills.routes.skill_config.list_skills", return_value=all_skills),
        patch("db.clients.get_client_sync", return_value=None),
    ):
        resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


# ---------------------------------------------------------------------------
# Admin bypass (v6.6.0) — an admin sees the full catalogue even when the
# domain HAS an enabled_skills filter, so they can test WIP / demo skills.
# ---------------------------------------------------------------------------


def test_admin_bypasses_active_enabled_skills_filter(all_skills: list[SkillConfig]) -> None:
    """An `aitana-admin` user in a domain WITH enabled_skills still sees all."""
    admin_in_one = User(
        uid="admin-one",
        email="owner@yourcompany.com",
        domain="acmeenergy.com",
        group_tags=frozenset({"aitana-admin"}),
    )
    client = _app_for(admin_in_one)
    one_config = ClientConfig(
        domain="acmeenergy.com",
        documents_bucket="multivac-acme-energy-bucket",
        display_name="Acme Energy",
        enabled_skills=["one-ppa-expert", "one-doc-compare"],
    )
    with (
        patch("skills.routes.skill_config.list_skills", return_value=all_skills),
        patch("db.clients.get_client_sync", return_value=one_config),
    ):
        resp = client.get("/api/skills")
    assert resp.status_code == 200
    # Admin bypass → full catalogue, not just the 2 enabled slugs.
    assert len(resp.json()) == 6


def test_non_admin_one_user_still_narrowed(all_skills: list[SkillConfig]) -> None:
    """Sanity: a normal ONE user in the same domain is still narrowed."""
    client = _app_for(_ONE_USER)
    one_config = ClientConfig(
        domain="acmeenergy.com",
        documents_bucket="multivac-acme-energy-bucket",
        display_name="Acme Energy",
        enabled_skills=["one-ppa-expert", "one-doc-compare"],
    )
    with (
        patch("skills.routes.skill_config.list_skills", return_value=all_skills),
        patch("db.clients.get_client_sync", return_value=one_config),
    ):
        resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert sorted(s["slug"] for s in resp.json()) == ["one-doc-compare", "one-ppa-expert"]
