"""API tests for /api/skills endpoints using FastAPI TestClient.

Post-AUTH-PERMISSIONS-M2: routes are authenticated. These tests install an
`admin`-owner override on `get_current_user` so they remain focused on the
route/handler logic (create/list/update/delete shape) rather than access
control — that matrix lives in `test_skills_auth.py`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models import SkillConfig
from skills.routes import router


@pytest.fixture()
def app():
    app = FastAPI()
    app.include_router(router)

    # Install an owner-equivalent test user so the existing handler-shape tests
    # aren't burdened with access-matrix details. owner_id matches the sample
    # skill data so PUT/DELETE work.
    async def _override(request: Request) -> User:
        u = User(
            uid="user-1",
            email="owner@yourcompany.com",
            domain="aitana.ai",
            group_tags=frozenset({"aitana-admin"}),
        )
        request.state.access = build_access_context(u)
        return u

    app.dependency_overrides[get_current_user] = _override
    return app


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def app_plain():
    """App wired with a NON-admin user (no group tags) — for read-only guards."""
    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        u = User(uid="user-2", email="jo@example.com", domain="example.com", group_tags=frozenset())
        request.state.access = build_access_context(u)
        return u

    app.dependency_overrides[get_current_user] = _override
    return app


@pytest.fixture()
def client_plain(app_plain):
    return TestClient(app_plain)


@pytest.fixture(autouse=True)
def _stub_unique_slug():
    """Bypass the Firestore round-trip in unique_slug for non-slug tests.

    POST and PUT now call unique_slug() before delegating to skill_config,
    which would hit live Firestore. Tests that exercise slug behaviour
    explicitly override this patch.
    """
    with patch("skills.routes.unique_slug", side_effect=lambda owner_id, base, exclude_skill_id=None: base):
        yield


def _make_config(**overrides) -> SkillConfig:
    defaults = {
        "name": "test-skill",
        "description": "A test skill.",
        "instructions": "Help with tests.",
        "skillId": "abc-123",
        "displayName": "Test Skill",
        "ownerEmail": "owner@yourcompany.com",
        "ownerId": "user-1",
    }
    defaults.update(overrides)
    return SkillConfig(**defaults)


# === POST /api/skills ===


def test_create_skill(client):
    with patch("skills.routes.skill_config.create_skill") as mock_create:
        mock_create.return_value = _make_config()
        resp = client.post("/api/skills", json={"name": "test-skill", "description": "A test skill."})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-skill"
        assert data["skillId"] == "abc-123"


def test_create_skill_invalid_name(client):
    with patch("skills.routes.skill_config.create_skill") as mock_create:
        mock_create.side_effect = ValueError("name must be lowercase kebab-case")
        with pytest.raises(ValueError, match="kebab-case"):
            client.post("/api/skills", json={"name": "Invalid Name!"})


# === GET /api/skills ===


def test_list_skills(client):
    with patch("skills.routes.skill_config.list_skills") as mock_list:
        mock_list.return_value = [_make_config(), _make_config(name="other-skill", skillId="def-456")]
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


def test_list_skills_with_filters(client):
    with patch("skills.routes.skill_config.list_skills") as mock_list:
        mock_list.return_value = []
        resp = client.get("/api/skills?ownerId=user-1&tag=extraction")
        assert resp.status_code == 200
        mock_list.assert_called_once_with(owner_id="user-1", tag="extraction", access_type=None, limit=50)


# === GET /api/skills/marketplace ===


def test_list_marketplace(client):
    with patch("skills.routes.skill_config.list_marketplace") as mock_market:
        mock_market.return_value = [_make_config()]
        resp = client.get("/api/skills/marketplace")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# === GET /api/skills/{skill_id} ===


def test_get_skill(client):
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = _make_config()
        resp = client.get("/api/skills/abc-123")
        assert resp.status_code == 200
        assert resp.json()["skillId"] == "abc-123"


def test_get_skill_not_found(client):
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = None
        resp = client.get("/api/skills/nonexistent")
        assert resp.status_code == 404


# === PUT /api/skills/{skill_id} ===


def test_update_skill(client):
    # Route now fetches the skill first for owner/access checks, then updates.
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.update_skill") as mock_update,
    ):
        mock_get.return_value = _make_config()
        mock_update.return_value = _make_config(displayName="Updated")
        resp = client.put("/api/skills/abc-123", json={"displayName": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["displayName"] == "Updated"


def test_update_skill_not_found(client):
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = None
        resp = client.put("/api/skills/nonexistent", json={"displayName": "X"})
        assert resp.status_code == 404


def test_update_skill_empty_body(client):
    resp = client.put("/api/skills/abc-123", json={})
    assert resp.status_code == 400


# === DELETE /api/skills/{skill_id} ===


def test_delete_skill(client):
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.delete_skill") as mock_delete,
    ):
        mock_get.return_value = _make_config()
        mock_delete.return_value = True
        resp = client.delete("/api/skills/abc-123")
        assert resp.status_code == 204


def test_delete_skill_not_found(client):
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = None
        resp = client.delete("/api/skills/nonexistent")
        assert resp.status_code == 404


# === Platform-owned skills: read-only guard ===
#
# Platform-owned skills have ownerId == "aitana-platform" and
# accessControl.type == "public" so every user can see them, but nobody
# except the sentinel owner (which has no Firebase identity) can mutate
# them. The guard fires BEFORE is_skill_owner so the 403 message is
# specific ("Fork to customize") rather than generic.

_PLATFORM_SKILL_KWARGS = {
    "name": "doc-extraction",
    "skillId": "platform-doc-extract",
    "ownerId": "aitana-platform",
    "ownerEmail": "platform@yourcompany.com",
    "accessControl": {"type": "public"},
}


def test_get_platform_skill_is_visible(client):
    """Any authenticated user can GET a platform skill — it's public."""
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = _make_config(**_PLATFORM_SKILL_KWARGS)
        resp = client.get("/api/skills/platform-doc-extract")
        assert resp.status_code == 200
        assert resp.json()["ownerId"] == "aitana-platform"


def test_put_platform_skill_returns_fork_to_customize(client_plain):
    """PUT on a platform skill by a NON-admin is 403 'Fork to customize'."""
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = _make_config(**_PLATFORM_SKILL_KWARGS)
        resp = client_plain.put("/api/skills/platform-doc-extract", json={"displayName": "Mine"})
        assert resp.status_code == 403
        assert "fork" in resp.json()["detail"].lower()
        assert "read-only" in resp.json()["detail"].lower()


def test_delete_platform_skill_returns_fork_to_customize(client_plain):
    """DELETE on a platform skill by a NON-admin is 403 'Fork to customize'."""
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = _make_config(**_PLATFORM_SKILL_KWARGS)
        resp = client_plain.delete("/api/skills/platform-doc-extract")
        assert resp.status_code == 403
        assert "fork" in resp.json()["detail"].lower()


def test_platform_admin_can_update_platform_skill(client):
    """A platform admin (`aitana-admin`) may edit a platform-owned skill in place."""
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.update_skill") as mock_upd,
    ):
        mock_get.return_value = _make_config(**_PLATFORM_SKILL_KWARGS)
        mock_upd.return_value = _make_config(**{**_PLATFORM_SKILL_KWARGS, "displayName": "Edited"})
        resp = client.put("/api/skills/platform-doc-extract", json={"displayName": "Edited"})
        assert resp.status_code == 200
        assert resp.json()["displayName"] == "Edited"


def test_platform_admin_can_delete_platform_skill(client):
    """A platform admin (`aitana-admin`) may delete a platform-owned skill."""
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.delete_skill") as mock_del,
    ):
        mock_get.return_value = _make_config(**_PLATFORM_SKILL_KWARGS)
        mock_del.return_value = True
        resp = client.delete("/api/skills/platform-doc-extract")
        assert resp.status_code == 204


# === POST /api/skills/{id}/fork ===


def test_fork_platform_skill_creates_private_copy(client):
    """A random user forking a platform skill gets a private, user-owned copy."""
    source = _make_config(**_PLATFORM_SKILL_KWARGS, instructions="Do docs.")
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.create_skill") as mock_create,
    ):
        mock_get.return_value = source
        mock_create.side_effect = lambda **kw: _make_config(
            name=kw["name"],
            skillId="new-id-789",
            ownerId="user-1",
            ownerEmail="owner@yourcompany.com",
            accessControl={"type": "private"},
            instructions=kw["instructions"],
        )

        resp = client.post("/api/skills/platform-doc-extract/fork")
        assert resp.status_code == 201
        body = resp.json()
        assert body["ownerId"] == "user-1"
        assert body["accessControl"]["type"] == "private"
        # Name has a fork suffix so it doesn't clash with the source name
        assert body["name"].startswith("doc-extraction-fork-")
        # Instructions carried over
        assert body["instructions"] == "Do docs."


def test_fork_unseen_skill_returns_404(client):
    """Forking a skill the caller cannot see returns 404, not 403 — no leak."""
    # Simulate an invisible skill: private, owned by someone else.
    invisible = _make_config(skillId="hidden-xyz", ownerId="someone-else", accessControl={"type": "private"})
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = invisible
        resp = client.post("/api/skills/hidden-xyz/fork")
        assert resp.status_code == 404


def test_fork_missing_skill_returns_404(client):
    with patch("skills.routes.skill_config.get_skill") as mock_get:
        mock_get.return_value = None
        resp = client.post("/api/skills/nonexistent/fork")
        assert resp.status_code == 404


def test_fork_own_skill_is_allowed(client):
    """Users can fork their own skills — useful for quick branching."""
    own = _make_config(skillId="abc-123")  # ownerId defaults to user-1
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.create_skill") as mock_create,
    ):
        mock_get.return_value = own
        mock_create.side_effect = lambda **kw: _make_config(
            name=kw["name"],
            skillId="abc-fork-123",
            accessControl={"type": "private"},
        )
        resp = client.post("/api/skills/abc-123/fork")
        assert resp.status_code == 201
        assert resp.json()["skillId"] == "abc-fork-123"


def test_fork_name_suffix_is_short(client):
    """Fork suffix should be 4-6 chars so the name stays readable."""
    import re

    source = _make_config(**_PLATFORM_SKILL_KWARGS)
    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.create_skill") as mock_create,
    ):
        mock_get.return_value = source
        mock_create.side_effect = lambda **kw: _make_config(name=kw["name"])
        resp = client.post("/api/skills/platform-doc-extract/fork")
        name = resp.json()["name"]
        # "-fork-" + 4-6 chars
        assert re.match(r"^doc-extraction-fork-[a-z0-9]{4,6}$", name), name


def test_fork_display_name_gets_suffix(client):
    """Fork inherits the source's displayName but suffixed '(Fork)' so the
    user can tell the two apart in the skill picker."""
    source = _make_config(**_PLATFORM_SKILL_KWARGS, displayName="Doc Extraction")
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return _make_config(name=kw["name"], displayName=kw.get("displayName", ""))

    with (
        patch("skills.routes.skill_config.get_skill") as mock_get,
        patch("skills.routes.skill_config.create_skill") as mock_create,
    ):
        mock_get.return_value = source
        mock_create.side_effect = _capture
        resp = client.post("/api/skills/platform-doc-extract/fork")
        assert resp.status_code == 201
        assert captured["displayName"] == "Doc Extraction (Fork)"


# === Slug-aware behaviour: POST auto-slug, PUT collision, GET by-slug ===


def test_create_auto_generates_slug_from_name(client):
    """When the client doesn't supply a slug, the route derives one from name."""
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return _make_config(name=kw["name"], slug=kw["slug"])

    with patch("skills.routes.skill_config.create_skill", side_effect=_capture):
        resp = client.post("/api/skills", json={"name": "general-assistant", "description": "x"})
    assert resp.status_code == 201
    assert captured["slug"] == "general-assistant"
    assert resp.json()["slug"] == "general-assistant"


def test_create_silently_suffixes_on_collision(client):
    """POST is the 'just give me a URL' path — collision auto-suffixes, no 409."""
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return _make_config(name=kw["name"], slug=kw["slug"])

    with (
        patch(
            "skills.routes.unique_slug",
            side_effect=lambda owner_id, base, exclude_skill_id=None: f"{base}-2",
        ),
        patch("skills.routes.skill_config.create_skill", side_effect=_capture),
    ):
        resp = client.post("/api/skills", json={"name": "general-assistant", "description": "x"})
    assert resp.status_code == 201
    assert captured["slug"] == "general-assistant-2"


def test_update_slug_collision_returns_409_with_suggestion(client):
    """User-driven slug edits surface 409 + a free suggestion so the UI can offer it."""
    existing = _make_config()
    with (
        patch("skills.routes.skill_config.get_skill", return_value=existing),
        patch(
            "skills.routes.unique_slug",
            side_effect=lambda owner_id, base, exclude_skill_id=None: f"{base}-2",
        ),
        patch("skills.routes.skill_config.update_skill") as mock_update,
    ):
        resp = client.put("/api/skills/abc-123", json={"slug": "taken-slug"})
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body == {"error": "slug_taken", "suggestion": "taken-slug-2"}
    mock_update.assert_not_called()


def test_update_slug_self_no_collision(client):
    """Resaving the same slug must not be flagged as a collision."""
    existing = _make_config(slug="general-assistant")
    with (
        patch("skills.routes.skill_config.get_skill", return_value=existing),
        patch(
            "skills.routes.unique_slug",
            side_effect=lambda owner_id, base, exclude_skill_id=None: base,
        ),
        patch(
            "skills.routes.skill_config.update_skill",
            return_value=_make_config(slug="general-assistant"),
        ),
    ):
        resp = client.put("/api/skills/abc-123", json={"slug": "general-assistant"})
    assert resp.status_code == 200
    assert resp.json()["slug"] == "general-assistant"


def test_get_by_slug_returns_skill(client):
    """Friendly URL resolution: (owner_id, slug) -> SkillResponse."""
    with patch(
        "skills.routes.skill_config.find_by_slug",
        return_value=_make_config(slug="general-assistant"),
    ) as mock_find:
        resp = client.get("/api/skills/by-slug/user-1/general-assistant")
    assert resp.status_code == 200
    assert resp.json()["skillId"] == "abc-123"
    assert resp.json()["slug"] == "general-assistant"
    mock_find.assert_called_once_with("user-1", "general-assistant")


def test_get_by_slug_missing_returns_404(client):
    with patch("skills.routes.skill_config.find_by_slug", return_value=None):
        resp = client.get("/api/skills/by-slug/user-1/missing-slug")
    assert resp.status_code == 404


def test_get_by_slug_invisible_returns_404(client):
    """Private skill owned by someone else: 404, not 403 — don't leak existence."""
    other = _make_config(
        skillId="other-skill",
        ownerId="someone-else",
        accessControl={"type": "private"},
    )
    with patch("skills.routes.skill_config.find_by_slug", return_value=other):
        resp = client.get("/api/skills/by-slug/someone-else/general-assistant")
    assert resp.status_code == 404
