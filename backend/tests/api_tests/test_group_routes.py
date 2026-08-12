"""API tests for the anonymous group-ID auth endpoints (sprint 2.11, M2).

Covers the four endpoints + the token-shape dispatcher in
``auth.__init__.get_current_user``. Pattern mirrors
``test_iframe_context_routes.py``: TestClient with mocked Firebase
auth for the teacher path; no auth for the student path.

The seven-gate matrix from M1 is re-tested at the HTTP layer here —
each gate maps to a specific status code per the design's API table:
  gate 1: 422 (Pydantic body shape)
  gate 2: 401 with reason 'unknown'
  gate 3: 401 with reason 'expired'
  gate 4: 401 with reason 'revoked'
  gate 5: 429 (rate-limit) + Retry-After header
  gate 6: 503 (at-capacity)
  gate 7: 200 + JoinResponse body
"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

# Ensure secret is set BEFORE module imports.
os.environ.setdefault("GROUP_AUTH_SIGNING_SECRET", "test-secret-for-pytest-only")

from auth import User, get_current_user
from auth.access_context import AccessContext
from auth.group_id_auth import AnonymousGroupAuth
from auth.group_routes import router


@pytest.fixture(autouse=True)
def isolate_state():
    AnonymousGroupAuth.reset_for_tests()
    yield
    AnonymousGroupAuth.reset_for_tests()


def _make_teacher_client(uid: str = "teacher-1") -> TestClient:
    """TestClient with Firebase auth mocked to a teacher identity."""
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain)
    test_app = FastAPI()
    test_app.include_router(router)

    @test_app.middleware("http")
    async def _inject_access(request, call_next):
        request.state.access = ctx
        return await call_next(request)

    test_app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(test_app)


def _make_anonymous_client() -> TestClient:
    """No auth override — anonymous students hit /join without a token."""
    test_app = FastAPI()
    test_app.include_router(router)
    return TestClient(test_app)


# ─── POST /api/auth/group/create ────────────────────────────────────────────


class TestCreateGroup:
    def test_teacher_can_create_group(self):
        client = _make_teacher_client("teacher-1")
        resp = client.post(
            "/api/auth/group/create",
            json={
                "title": "Physics 2A",
                "skill_ids": ["physics-tutor", "lab-helper"],
                "ttl_days": 7,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "group_id" in body
        assert "expires_at" in body
        assert "join_url" in body
        # Code shape
        assert len(body["group_id"]) >= 6

    def test_create_rejects_unauthenticated(self):
        """No Firebase token → can't create a group."""
        client = _make_anonymous_client()
        resp = client.post(
            "/api/auth/group/create",
            json={"title": "x", "skill_ids": ["s"]},
        )
        # No auth override → dispatcher returns 401 (no header)
        assert resp.status_code in (401, 403, 422)

    def test_create_validates_body_shape(self):
        """Missing required fields → 422."""
        client = _make_teacher_client()
        # Missing skill_ids
        resp = client.post("/api/auth/group/create", json={"title": "x"})
        assert resp.status_code == 422

    def test_create_accepts_per_create_overrides(self):
        client = _make_teacher_client()
        resp = client.post(
            "/api/auth/group/create",
            json={
                "title": "short test",
                "skill_ids": ["s"],
                "ttl_days": 1,
                "max_concurrent_sessions": 5,
            },
        )
        assert resp.status_code == 201


# ─── POST /api/auth/group/join — the 7-gate matrix at HTTP layer ───────────


class TestJoinGroupGates:
    def _seed_group(self, **overrides) -> str:
        """Helper: seed a group via the create endpoint and return its id."""
        teacher = _make_teacher_client("teacher-1")
        body = {"title": "x", "skill_ids": ["s"], **overrides}
        resp = teacher.post("/api/auth/group/create", json=body)
        assert resp.status_code == 201
        return resp.json()["group_id"]

    def test_gate_1_malformed_body_returns_422(self):
        """Pydantic catches missing group_id → 422."""
        client = _make_anonymous_client()
        resp = client.post("/api/auth/group/join", json={})
        assert resp.status_code == 422

    def test_gate_2_unknown_group_returns_401(self):
        client = _make_anonymous_client()
        resp = client.post("/api/auth/group/join", json={"group_id": "NOPE-XXXX"})
        assert resp.status_code == 401
        assert "not found" in resp.text.lower() or "unknown" in resp.text.lower()

    def test_gate_3_expired_group_returns_401(self):
        """Expire the group via clock-frozen time."""
        gid = self._seed_group(ttl_days=1)
        # Force clock forward 2 days.
        AnonymousGroupAuth.time_provider = staticmethod(lambda: __import__("time").time() + 2 * 86400)
        try:
            client = _make_anonymous_client()
            resp = client.post("/api/auth/group/join", json={"group_id": gid})
            assert resp.status_code == 401
            assert "expired" in resp.text.lower()
        finally:
            import time as t

            AnonymousGroupAuth.time_provider = staticmethod(t.time)

    def test_gate_4_revoked_group_returns_401(self):
        gid = self._seed_group()
        teacher = _make_teacher_client("teacher-1")
        del_resp = teacher.delete(f"/api/auth/group/{gid}")
        assert del_resp.status_code == 204

        client = _make_anonymous_client()
        resp = client.post("/api/auth/group/join", json={"group_id": gid})
        assert resp.status_code == 401

    def test_gate_5_rate_limit_returns_429(self):
        """Spending all 10 tokens from one IP → 11th call → 429 with
        Retry-After header."""
        gid = self._seed_group(max_concurrent_sessions=1000)
        client = _make_anonymous_client()
        # 10 joins from the same client (TestClient uses 'testclient' as
        # default IP — see FastAPI docs; deterministic).
        for _ in range(10):
            r = client.post("/api/auth/group/join", json={"group_id": gid})
            assert r.status_code == 200, r.text
        # 11th → 429
        r = client.post("/api/auth/group/join", json={"group_id": gid})
        assert r.status_code == 429
        # Retry-After header advisory
        assert "retry-after" in {k.lower() for k in r.headers}

    def test_gate_6_at_capacity_returns_503(self):
        gid = self._seed_group(max_concurrent_sessions=2)
        # Use a permissive TestClient setup but force each call to look
        # like a different IP so rate-limit (gate 5) doesn't fire first.
        client = _make_anonymous_client()
        # First 2 joins succeed.
        for i in range(2):
            r = client.post(
                "/api/auth/group/join",
                json={"group_id": gid},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )
            assert r.status_code == 200, r.text
        # 3rd → 503 (cap exceeded)
        r = client.post(
            "/api/auth/group/join",
            json={"group_id": gid},
            headers={"X-Forwarded-For": "10.0.0.99"},
        )
        assert r.status_code == 503
        assert "cap" in r.text.lower() or "capacity" in r.text.lower()

    def test_gate_7_happy_path_returns_token_and_uid(self):
        gid = self._seed_group()
        client = _make_anonymous_client()
        r = client.post("/api/auth/group/join", json={"group_id": gid})
        assert r.status_code == 200
        body = r.json()
        assert body["token"]
        assert body["uid"].startswith("anon-")
        assert body["expires_at"] > 0


# ─── DELETE /api/auth/group/{id} ────────────────────────────────────────────


class TestDeleteGroup:
    def _seed_group(self, creator: str = "teacher-1") -> str:
        teacher = _make_teacher_client(creator)
        resp = teacher.post(
            "/api/auth/group/create",
            json={"title": "x", "skill_ids": ["s"]},
        )
        assert resp.status_code == 201
        return resp.json()["group_id"]

    def test_creator_can_delete(self):
        gid = self._seed_group("teacher-1")
        client = _make_teacher_client("teacher-1")
        r = client.delete(f"/api/auth/group/{gid}")
        assert r.status_code == 204

    def test_non_creator_cannot_delete(self):
        gid = self._seed_group("teacher-1")
        # Different teacher tries to delete
        client = _make_teacher_client("teacher-2")
        r = client.delete(f"/api/auth/group/{gid}")
        assert r.status_code == 403

    def test_delete_nonexistent_returns_404(self):
        client = _make_teacher_client("teacher-1")
        r = client.delete("/api/auth/group/NOPE-XXXX")
        assert r.status_code == 404


# ─── GET /api/auth/group/{id} ───────────────────────────────────────────────


class TestGetGroup:
    def test_get_returns_metadata_for_existing_group(self):
        teacher = _make_teacher_client("teacher-1")
        create_resp = teacher.post(
            "/api/auth/group/create",
            json={"title": "Physics 2A", "skill_ids": ["s"]},
        )
        gid = create_resp.json()["group_id"]

        r = teacher.get(f"/api/auth/group/{gid}")
        assert r.status_code == 200
        body = r.json()
        assert body["group_id"] == gid
        assert body["title"] == "Physics 2A"
        # No member list in the response (privacy).
        assert "members" not in body
        assert "joined_uids" not in body

    def test_get_returns_404_for_missing(self):
        client = _make_teacher_client("teacher-1")
        r = client.get("/api/auth/group/NOPE-XXXX")
        assert r.status_code == 404


# ─── Token-shape dispatcher integration ─────────────────────────────────────


class TestDispatcher:
    """The platform's main get_current_user must dispatch to the right
    verifier based on token shape. These tests check the dispatch via a
    minimal protected endpoint."""

    def _make_app_with_protected_endpoint(self):
        """App with a tiny protected route that returns the User."""
        app = FastAPI()

        @app.get("/whoami")
        async def whoami(user: User = Depends(get_current_user)):  # noqa: B008
            return {
                "uid": user.uid,
                "auth_mode": user.auth_mode,
                "group_id": user.group_id,
                "email": user.email,
            }

        return TestClient(app)

    def test_dispatcher_accepts_anonymous_group_token(self):
        """Mint a group token via M1's join_group, then call /whoami with it."""
        from auth.group_id_auth import create_group, join_group

        rec = create_group(title="x", skill_ids=["s"], creator_uid="t", ttl_days=7)
        res = join_group(rec.group_id, client_ip="1.1.1.1")

        client = self._make_app_with_protected_endpoint()
        r = client.get("/whoami", headers={"Authorization": f"Bearer {res.token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["auth_mode"] == "anonymous_group_id"
        assert body["group_id"] == rec.group_id
        assert body["uid"].startswith("anon-")
        assert body["email"] == ""

    def test_dispatcher_rejects_malformed_bearer(self):
        client = self._make_app_with_protected_endpoint()
        r = client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401

    def test_dispatcher_rejects_missing_auth(self):
        client = self._make_app_with_protected_endpoint()
        r = client.get("/whoami")
        assert r.status_code == 401

    def test_dispatcher_local_mode_stub_still_works(self, monkeypatch):
        """Back-compat: LOCAL_MODE stub token continues to work."""
        monkeypatch.setenv("LOCAL_MODE", "1")
        client = self._make_app_with_protected_endpoint()
        r = client.get(
            "/whoami",
            headers={"Authorization": "Bearer local-mode-stub-token"},
        )
        assert r.status_code == 200
        body = r.json()
        # workshop user — auth_mode unchanged from existing behaviour
        # (the default "firebase" because the stub uses build_workshop_user).
        assert body["uid"] == "workshop-user"


# ─── End-to-end happy + access-control path ─────────────────────────────────


class TestEndToEndPermission:
    """Combines: teacher creates group with skill_ids → student joins →
    student calling a permitted skill is allowed via group-level lookup,
    while non-permitted skill is denied. Uses the `User`'s `group_id`
    field threaded through to permissions.can_use_tool."""

    def test_group_user_can_access_permitted_skill_ids(self):
        """The skill_ids list on the GroupRecord defines membership.
        At HTTP layer we use a tiny custom endpoint that exercises the
        check via AccessContext fields."""
        from auth.group_id_auth import create_group, join_group

        rec = create_group(
            title="x",
            skill_ids=["physics-tutor"],
            creator_uid="t",
            ttl_days=7,
        )
        res = join_group(rec.group_id, client_ip="2.2.2.2")

        # Tiny endpoint demonstrating the skill-id membership check —
        # mirrors what the real skill stream route will do.
        from fastapi import HTTPException

        app = FastAPI()

        @app.get("/test-skill/{skill_id}")
        async def access_skill(skill_id: str, user: User = Depends(get_current_user)):  # noqa: B008
            if user.auth_mode == "anonymous_group_id":
                from auth.group_id_auth import get_group

                grp = get_group(user.group_id)
                if grp is None or skill_id not in grp.skill_ids:
                    raise HTTPException(403, "skill not permitted for this group")
            return {"ok": True, "skill_id": skill_id}

        client = TestClient(app)
        hdr = {"Authorization": f"Bearer {res.token}"}

        # Permitted skill → 200
        r = client.get("/test-skill/physics-tutor", headers=hdr)
        assert r.status_code == 200

        # Non-permitted skill → 403
        r = client.get("/test-skill/forbidden-skill", headers=hdr)
        assert r.status_code == 403
