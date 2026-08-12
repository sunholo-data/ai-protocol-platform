"""Route-level auth matrix for /api/skills.

Exercises every interesting combination of (access_type x caller identity x
HTTP verb) against the five-type access model. `get_current_user` is
dependency-overridden per test so we can install a synthetic `User` and
populate `request.state.access` the way the real middleware would — no
firebase-admin mocks here (those are covered in test_firebase_auth.py).

`skill_config.*` is mocked so these tests are truly API-layer unit tests
against the access rules, not Firestore round-trips.

Key invariants:
    - anon → 401 on any non-public route
    - non-owner + no-access → 404 (don't leak existence)
    - non-owner + has-access → 200 GET, 403 PUT/DELETE (real forbidden)
    - owner → 200 / 204 always
    - POST sets ownerId from JWT, never from request body
    - list_marketplace stays public
    - list_skills filters to what the caller can see
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models import SkillConfig
from skills.routes import router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


OWNER_UID = "owner-uid"
OWNER_EMAIL = "owner@yourcompany.com"


def _make_skill(**overrides) -> SkillConfig:
    defaults: dict = {
        "name": "sample-skill",
        "description": "a sample",
        "instructions": "do the thing",
        "skillId": "skill-1",
        "displayName": "Sample Skill",
        "ownerEmail": OWNER_EMAIL,
        "ownerId": OWNER_UID,
        "accessControl": {"type": "private"},
    }
    defaults.update(overrides)
    return SkillConfig(**defaults)


def _make_user(
    uid: str = "caller-uid",
    email: str = "caller@yourcompany.com",
    domain: str = "yourcompany.com",
    group_tags: frozenset[str] = frozenset(),
) -> User:
    return User(uid=uid, email=email, domain=domain, group_tags=group_tags)


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_unique_slug():
    """Bypass Firestore in unique_slug — auth tests don't exercise slug logic."""
    with patch("skills.routes.unique_slug", side_effect=lambda owner_id, base, exclude_skill_id=None: base):
        yield


def _install_user(app: FastAPI, user: User) -> Callable[[], None]:
    """Override get_current_user to return `user` and populate request.state.access.

    Returns a cleanup closure; tests call it at end (or use try/finally).
    """

    async def _override(request: Request) -> User:
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override

    def _cleanup() -> None:
        app.dependency_overrides.pop(get_current_user, None)

    return _cleanup


# ---------------------------------------------------------------------------
# Anonymous callers
# ---------------------------------------------------------------------------


def test_anon_list_skills_401(client: TestClient) -> None:
    resp = client.get("/api/skills")
    assert resp.status_code == 401


def test_anon_get_skill_401(client: TestClient) -> None:
    resp = client.get("/api/skills/skill-1")
    assert resp.status_code == 401


def test_anon_create_skill_401(client: TestClient) -> None:
    resp = client.post("/api/skills", json={"name": "x", "description": "d"})
    assert resp.status_code == 401


def test_anon_update_skill_401(client: TestClient) -> None:
    resp = client.put("/api/skills/skill-1", json={"displayName": "z"})
    assert resp.status_code == 401


def test_anon_delete_skill_401(client: TestClient) -> None:
    resp = client.delete("/api/skills/skill-1")
    assert resp.status_code == 401


def test_anon_marketplace_stays_public_200(client: TestClient) -> None:
    """Marketplace is public — reachable without a token."""
    with patch("skills.routes.skill_config.list_marketplace") as mock:
        mock.return_value = [_make_skill(accessControl={"type": "public"})]
        resp = client.get("/api/skills/marketplace")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /api/skills/{id} — read access matrix
# ---------------------------------------------------------------------------


def test_non_owner_private_is_404(app: FastAPI, client: TestClient) -> None:
    """Don't leak existence — private skills are 404 to non-owners."""
    cleanup = _install_user(app, _make_user(uid="stranger"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "private"})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_non_owner_public_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="example.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "public"})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_same_domain_domain_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="coworker", domain="yourcompany.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "domain", "domain": "yourcompany.com"})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_other_domain_domain_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="outsider", domain="other.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "domain", "domain": "yourcompany.com"})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_listed_email_specific_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="ally", email="ally@partner.com", domain="partner.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(
                accessControl={"type": "specific", "emails": ["ally@partner.com", "other@x.com"]}
            )
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_unlisted_email_specific_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="random", email="random@x.com", domain="x.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "specific", "emails": ["ally@partner.com"]})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_matching_group_tag_tagged_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(
        app,
        _make_user(uid="teammate", group_tags=frozenset({"aitana-admin"})),
    )
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "tagged", "tags": ["aitana-admin", "finance-team"]})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_no_matching_group_tag_tagged_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="outsider", group_tags=frozenset({"unrelated"})))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "tagged", "tags": ["aitana-admin"]})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_owner_private_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = _make_skill(accessControl={"type": "private"})
            resp = client.get("/api/skills/skill-1")
        assert resp.status_code == 200
        assert resp.json()["skillId"] == "skill-1"
    finally:
        cleanup()


def test_get_nonexistent_skill_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user())
    try:
        with patch("skills.routes.skill_config.get_skill") as mock:
            mock.return_value = None
            resp = client.get("/api/skills/missing")
        assert resp.status_code == 404
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# PUT / DELETE — owner-only writes
# ---------------------------------------------------------------------------


def test_non_owner_visible_put_is_403(app: FastAPI, client: TestClient) -> None:
    """User can see the skill (domain access) but isn't the owner — real 403."""
    cleanup = _install_user(app, _make_user(uid="coworker", domain="yourcompany.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock_get:
            mock_get.return_value = _make_skill(accessControl={"type": "domain", "domain": "yourcompany.com"})
            resp = client.put("/api/skills/skill-1", json={"displayName": "Try Update"})
        assert resp.status_code == 403
    finally:
        cleanup()


def test_non_owner_invisible_put_is_404(app: FastAPI, client: TestClient) -> None:
    """Can't see → still 404 on PUT (don't leak existence via write path)."""
    cleanup = _install_user(app, _make_user(uid="stranger", domain="other.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock_get:
            mock_get.return_value = _make_skill(accessControl={"type": "private"})
            resp = client.put("/api/skills/skill-1", json={"displayName": "Z"})
        assert resp.status_code == 404
    finally:
        cleanup()


def test_owner_put_is_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("skills.routes.skill_config.get_skill") as mock_get,
            patch("skills.routes.skill_config.update_skill") as mock_update,
        ):
            mock_get.return_value = _make_skill(accessControl={"type": "private"})
            mock_update.return_value = _make_skill(displayName="Updated")
            resp = client.put("/api/skills/skill-1", json={"displayName": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["displayName"] == "Updated"
    finally:
        cleanup()


def test_non_owner_visible_delete_is_403(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="coworker", domain="yourcompany.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock_get:
            mock_get.return_value = _make_skill(accessControl={"type": "domain", "domain": "yourcompany.com"})
            resp = client.delete("/api/skills/skill-1")
        assert resp.status_code == 403
    finally:
        cleanup()


def test_non_owner_invisible_delete_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="other.com"))
    try:
        with patch("skills.routes.skill_config.get_skill") as mock_get:
            mock_get.return_value = _make_skill(accessControl={"type": "private"})
            resp = client.delete("/api/skills/skill-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_owner_delete_is_204(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("skills.routes.skill_config.get_skill") as mock_get,
            patch("skills.routes.skill_config.delete_skill") as mock_delete,
        ):
            mock_get.return_value = _make_skill(accessControl={"type": "private"})
            mock_delete.return_value = True
            resp = client.delete("/api/skills/skill-1")
        assert resp.status_code == 204
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# POST — ownerId sourced from JWT, not request body
# ---------------------------------------------------------------------------


def test_create_sets_owner_id_from_jwt(app: FastAPI, client: TestClient) -> None:
    """ownerId must never be client-controllable — sourced from User.uid."""
    cleanup = _install_user(app, _make_user(uid="caller-uid", email="caller@yourcompany.com"))
    try:
        with patch("skills.routes.skill_config.create_skill") as mock_create:
            mock_create.return_value = _make_skill(ownerId="caller-uid", ownerEmail="caller@yourcompany.com")
            resp = client.post(
                "/api/skills",
                # Even if the client tried to set ownerId, our route ignores it — we source from JWT.
                json={"name": "new-skill", "description": "x"},
            )
        assert resp.status_code == 201
        # Verify create_skill was called with owner_id from the JWT user.
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["owner_id"] == "caller-uid"
        assert call_kwargs["owner_email"] == "caller@yourcompany.com"
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# GET /api/skills (list) — filters to visible set
# ---------------------------------------------------------------------------


def test_list_skills_filters_to_visible(app: FastAPI, client: TestClient) -> None:
    """Stranger should see only public skills; their own private; not others'."""
    cleanup = _install_user(app, _make_user(uid="stranger", domain="example.com"))
    try:
        with patch("skills.routes.skill_config.list_skills") as mock_list:
            mock_list.return_value = [
                _make_skill(skillId="pub-1", accessControl={"type": "public"}),
                _make_skill(skillId="priv-1", accessControl={"type": "private"}, ownerId="other-uid"),
                _make_skill(skillId="dom-1", accessControl={"type": "domain", "domain": "yourcompany.com"}),
                _make_skill(skillId="own-1", accessControl={"type": "private"}, ownerId="stranger"),
            ]
            resp = client.get("/api/skills")
        assert resp.status_code == 200
        ids = {s["skillId"] for s in resp.json()}
        assert ids == {"pub-1", "own-1"}, f"unexpected visible set: {ids}"
    finally:
        cleanup()


def test_list_skills_owner_sees_own_private(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID))
    try:
        with patch("skills.routes.skill_config.list_skills") as mock_list:
            mock_list.return_value = [_make_skill(accessControl={"type": "private"})]
            resp = client.get("/api/skills")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
    finally:
        cleanup()
