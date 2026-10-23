"""POST /api/skills with a body the request schema accepts but SkillConfig
rejects (empty description, a name that slugifies to a reserved word) must be
a 422 saying why — it used to escape as a bare 500 (found when a machine
principal created a skill with {"name": "x"})."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from auth.firebase_auth import User
from skills.routes import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(uid="u1", email="a@b.com", domain="b.com")
    return TestClient(app)


def test_missing_description_is_422_not_500():
    with patch("skills.routes.unique_slug", side_effect=lambda uid, base, **kw: base):
        resp = _client().post("/api/skills", json={"name": "A good name"})
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["code"] == "SKILL_INVALID"
    assert "description" in body["message"]


def test_one_character_name_no_longer_hits_the_reserved_slug():
    with patch("skills.routes.unique_slug", side_effect=lambda uid, base, **kw: base):
        resp = _client().post("/api/skills", json={"name": "x"})
    # Still invalid (no description) — but not because of the slug.
    assert resp.status_code == 422
    assert "reserved" not in resp.json()["detail"]["message"]
