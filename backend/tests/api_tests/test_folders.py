"""API tests for /api/buckets/{id}/folders — CRUD x access matrix + effectiveAccess.

Locks in the folder-specific contract on top of the bucket/folder matrix:
    - effectiveAccess is **always** present on folder responses
    - create: folder without explicit accessControl inherits parent's
    - create: folder with explicit accessControl overrides parent
    - update: changing accessControl recomputes effectiveAccess
    - reads use folder.effectiveAccess (NOT parent.accessControl) for access
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from buckets import folder_config
from buckets.routes import router
from db.models import AccessControl, BucketConfig, BucketFolderConfig

OWNER_UID = "owner-uid"
OWNER_EMAIL = "owner@yourcompany.com"


def _make_bucket(**overrides) -> BucketConfig:
    defaults: dict = {
        "bucketId": "bkt-1",
        "displayName": "Parent",
        "gcsBucket": "parent-bucket",
        "ownerEmail": OWNER_EMAIL,
        "ownerId": OWNER_UID,
        "accessControl": {"type": "domain", "domain": "yourcompany.com"},
    }
    defaults.update(overrides)
    return BucketConfig(**defaults)


def _make_folder(**overrides) -> BucketFolderConfig:
    defaults: dict = {
        "folderId": "fld-1",
        "bucketId": "bkt-1",
        "path": "reports/",
        "displayName": "Reports",
        "ownerId": OWNER_UID,
        "effectiveAccess": {"type": "domain", "domain": "yourcompany.com"},
    }
    defaults.update(overrides)
    return BucketFolderConfig(**defaults)


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


def _install_user(app: FastAPI, user: User) -> Callable[[], None]:
    async def _override(request: Request) -> User:
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    return lambda: app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# effectiveAccess inheritance (pure — no routes)
# ---------------------------------------------------------------------------


def test_compute_effective_access_inherits_when_none() -> None:
    parent = _make_bucket(accessControl={"type": "public"})
    effective = folder_config.compute_effective_access(None, parent)
    assert effective.type == "public"


def test_compute_effective_access_override() -> None:
    parent = _make_bucket(accessControl={"type": "public"})
    effective = folder_config.compute_effective_access({"type": "private"}, parent)
    assert effective.type == "private"


def test_compute_effective_access_accepts_model_instance() -> None:
    parent = _make_bucket()
    ac = AccessControl(type="private")
    effective = folder_config.compute_effective_access(ac, parent)
    assert effective is ac  # returned as-is


# ---------------------------------------------------------------------------
# Anonymous
# ---------------------------------------------------------------------------


def test_anon_list_folders_401(client: TestClient) -> None:
    assert client.get("/api/buckets/bkt-1/folders").status_code == 401


def test_anon_get_folder_401(client: TestClient) -> None:
    assert client.get("/api/buckets/bkt-1/folders/fld-1").status_code == 401


# ---------------------------------------------------------------------------
# Read access uses effectiveAccess, not parent.accessControl
# ---------------------------------------------------------------------------


def test_folder_read_uses_effective_access(app: FastAPI, client: TestClient) -> None:
    """Parent bucket is public; folder overrode to private → non-owner 404s on folder."""
    cleanup = _install_user(app, _make_user(uid="stranger", domain="evil.com"))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_folder,
        ):
            mock_parent.return_value = _make_bucket(accessControl={"type": "public"})
            mock_folder.return_value = _make_folder(
                accessControl={"type": "private"},
                effectiveAccess={"type": "private"},
            )
            resp = client.get("/api/buckets/bkt-1/folders/fld-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_folder_read_owner_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_folder,
        ):
            mock_parent.return_value = _make_bucket()
            mock_folder.return_value = _make_folder()
            resp = client.get("/api/buckets/bkt-1/folders/fld-1")
        assert resp.status_code == 200
        assert resp.json()["effectiveAccess"]["type"] == "domain"
    finally:
        cleanup()


def test_folder_read_parent_invisible_404(app: FastAPI, client: TestClient) -> None:
    """Parent bucket is private → stranger 404s even before we check the folder."""
    cleanup = _install_user(app, _make_user(uid="stranger", domain="evil.com"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_parent:
            mock_parent.return_value = _make_bucket(accessControl={"type": "private"})
            resp = client.get("/api/buckets/bkt-1/folders/fld-1")
        assert resp.status_code == 404
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# Create — inheritance vs override
# ---------------------------------------------------------------------------


def test_create_folder_owner_inherits_parent_access(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.create_folder") as mock_create,
        ):
            mock_parent.return_value = _make_bucket(accessControl={"type": "domain", "domain": "yourcompany.com"})
            # Simulate the real create path: no explicit accessControl → inherit.
            mock_create.return_value = _make_folder(
                accessControl=None,
                effectiveAccess={"type": "domain", "domain": "yourcompany.com"},
            )
            resp = client.post(
                "/api/buckets/bkt-1/folders",
                json={"path": "reports/", "displayName": "Reports"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["effectiveAccess"]["type"] == "domain"
        # Body never carried accessControl → stays None
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["access_control"] is None
    finally:
        cleanup()


def test_create_folder_owner_override_access(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.create_folder") as mock_create,
        ):
            mock_parent.return_value = _make_bucket(accessControl={"type": "public"})
            mock_create.return_value = _make_folder(
                accessControl={"type": "private"},
                effectiveAccess={"type": "private"},
            )
            resp = client.post(
                "/api/buckets/bkt-1/folders",
                json={
                    "path": "secrets/",
                    "displayName": "Secrets",
                    "accessControl": {"type": "private"},
                },
            )
        assert resp.status_code == 201
        assert resp.json()["effectiveAccess"]["type"] == "private"
    finally:
        cleanup()


def test_create_folder_non_owner_forbidden(app: FastAPI, client: TestClient) -> None:
    """Caller can see the bucket (domain match) but is not the owner → 403."""
    cleanup = _install_user(app, _make_user(uid="stranger", domain="yourcompany.com"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_parent:
            mock_parent.return_value = _make_bucket()
            resp = client.post(
                "/api/buckets/bkt-1/folders",
                json={"path": "x/", "displayName": "x"},
            )
        assert resp.status_code == 403
    finally:
        cleanup()


def test_create_folder_bad_path_400(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_parent:
            mock_parent.return_value = _make_bucket()
            resp = client.post(
                "/api/buckets/bkt-1/folders",
                json={"path": "../escape/", "displayName": "x"},
            )
        assert resp.status_code == 400
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# Update / Delete — bucket-owner-or-folder-owner
# ---------------------------------------------------------------------------


def test_update_folder_owner_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_get,
            patch("buckets.routes.folder_config.update_folder") as mock_update,
        ):
            mock_parent.return_value = _make_bucket()
            mock_get.return_value = _make_folder()
            mock_update.return_value = _make_folder(displayName="Renamed")
            resp = client.put(
                "/api/buckets/bkt-1/folders/fld-1",
                json={"displayName": "Renamed"},
            )
        assert resp.status_code == 200
    finally:
        cleanup()


def test_update_folder_non_owner_403(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="yourcompany.com"))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_get,
        ):
            mock_parent.return_value = _make_bucket()
            mock_get.return_value = _make_folder()
            resp = client.put(
                "/api/buckets/bkt-1/folders/fld-1",
                json={"displayName": "x"},
            )
        assert resp.status_code == 403
    finally:
        cleanup()


def test_update_folder_access_control_change_passes_bucket_to_update(app: FastAPI, client: TestClient) -> None:
    """PUT with accessControl change must pass the parent bucket to update_folder
    so it can recompute effectiveAccess correctly."""
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_get,
            patch("buckets.routes.folder_config.update_folder") as mock_update,
        ):
            parent = _make_bucket(accessControl={"type": "public"})
            mock_parent.return_value = parent
            mock_get.return_value = _make_folder()
            mock_update.return_value = _make_folder(
                accessControl={"type": "private"},
                effectiveAccess={"type": "private"},
            )
            resp = client.put(
                "/api/buckets/bkt-1/folders/fld-1",
                json={"accessControl": {"type": "private"}},
            )
        assert resp.status_code == 200
        # Verify update_folder was called with the parent bucket so it can
        # recompute effectiveAccess from the new accessControl.
        call_args = mock_update.call_args
        assert call_args.args[0] is parent or call_args.kwargs.get("bucket") is parent
    finally:
        cleanup()


def test_delete_folder_owner_204(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_parent,
            patch("buckets.routes.folder_config.get_folder") as mock_get,
            patch("buckets.routes.folder_config.delete_folder") as mock_del,
        ):
            mock_parent.return_value = _make_bucket()
            mock_get.return_value = _make_folder()
            mock_del.return_value = True
            resp = client.delete("/api/buckets/bkt-1/folders/fld-1")
        assert resp.status_code == 204
    finally:
        cleanup()
