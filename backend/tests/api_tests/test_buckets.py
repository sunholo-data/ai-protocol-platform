"""API tests for /api/buckets — the 4 CRUD ops x 5 access types x caller-identity matrix.

Mirrors the skills auth matrix (tests/api_tests/test_skills_auth.py). Mocks
`bucket_config.*` so the tests exercise route/handler logic against the
five-type evaluator — not Firestore round-trips.

Invariants locked in:
    - anon → 401 on everything
    - non-owner + no-access → 404 (don't leak existence)
    - non-owner + has-access → 200 GET, 403 PUT/DELETE
    - owner / admin → 200 / 201 / 204
    - POST never honours a body-supplied ownerId
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from buckets.routes import router
from db.models import BucketConfig

OWNER_UID = "owner-uid"
OWNER_EMAIL = "owner@yourcompany.com"


def _make_bucket(**overrides) -> BucketConfig:
    defaults: dict = {
        "bucketId": "bkt-1",
        "displayName": "Sample Bucket",
        "gcsBucket": "sample-bucket",
        "ownerEmail": OWNER_EMAIL,
        "ownerId": OWNER_UID,
        "accessControl": {"type": "private"},
    }
    defaults.update(overrides)
    return BucketConfig(**defaults)


def _make_user(
    uid: str = "caller-uid",
    email: str = "caller@yourcompany.com",
    domain: str = "yourcompany.com",
    group_tags: frozenset[str] = frozenset(),
) -> User:
    return User(uid=uid, email=email, domain=domain, group_tags=group_tags)


def _admin_user() -> User:
    """A platform super-admin — bypasses `_authorize_bucket_read` before Firestore."""
    return _make_user(uid="admin-uid", email="admin@yourcompany.com", group_tags=frozenset({"aitana-admin"}))


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

    def _cleanup() -> None:
        app.dependency_overrides.pop(get_current_user, None)

    return _cleanup


# ---------------------------------------------------------------------------
# Anonymous callers
# ---------------------------------------------------------------------------


def test_anon_list_401(client: TestClient) -> None:
    assert client.get("/api/buckets").status_code == 401


# ---------------------------------------------------------------------------
# Object preview (v6.6.0) — auth-gated inline preview, no public URL.
# ---------------------------------------------------------------------------


def test_anon_preview_401(client: TestClient) -> None:
    assert client.get("/api/buckets/my-bucket/preview?object=a.pdf").status_code == 401


def _mock_gcs_blob(data: bytes, content_type: str = "application/pdf", size: int | None = None):
    """A fake storage.Client whose blob.reload()/download_as_bytes() serve `data`."""
    from unittest.mock import MagicMock

    blob = MagicMock()
    blob.content_type = content_type
    blob.size = size if size is not None else len(data)
    blob.download_as_bytes.return_value = data
    blob.reload.return_value = None
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    factory = MagicMock(return_value=client)
    return factory, blob


def test_preview_streams_bytes_with_content_type(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _admin_user())  # authorized caller; authz matrix tested below
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 fake", "application/pdf")
    try:
        with patch("buckets.routes._gcs_storage.Client", factory):
            resp = client.get("/api/buckets/one-ppa-bucket/preview?object=ppa/demosolar.pdf")
    finally:
        cleanup()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.content == b"%PDF-1.7 fake"


def test_preview_rejects_bad_bucket_name(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user())
    try:
        resp = client.get("/api/buckets/Bad_NAME!/preview?object=a.pdf")
    finally:
        cleanup()
    assert resp.status_code == 400


def test_preview_413_when_object_too_large(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _admin_user())
    factory, _ = _mock_gcs_blob(b"x", "application/pdf", size=40 * 1024 * 1024)
    try:
        with patch("buckets.routes._gcs_storage.Client", factory):
            resp = client.get("/api/buckets/one-ppa-bucket/preview?object=huge.pdf")
    finally:
        cleanup()
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Thumbnail (v6.6.0) — clean page-1 PNG preview.
# ---------------------------------------------------------------------------


def _one_page_pdf_bytes() -> bytes:
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (420, 594), "white").save(buf, "PDF")
    return buf.getvalue()


def test_anon_thumbnail_401(client: TestClient) -> None:
    assert client.get("/api/buckets/b/thumbnail?object=a.pdf").status_code == 401


def test_thumbnail_renders_png(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _admin_user())
    factory, _ = _mock_gcs_blob(_one_page_pdf_bytes(), "application/pdf")
    try:
        with patch("buckets.routes._gcs_storage.Client", factory):
            resp = client.get("/api/buckets/one-ppa-bucket/thumbnail?object=ppa/a.pdf&width=300")
    finally:
        cleanup()
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_rejects_non_pdf(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user())
    try:
        resp = client.get("/api/buckets/one-ppa-bucket/thumbnail?object=notes.txt")
    finally:
        cleanup()
    assert resp.status_code == 415


def test_thumbnail_is_cached_second_call_skips_gcs(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _admin_user())
    factory, _ = _mock_gcs_blob(_one_page_pdf_bytes(), "application/pdf")
    try:
        with patch("buckets.routes._gcs_storage.Client", factory):
            first = client.get("/api/buckets/cache-bkt/thumbnail?object=uniq/cache-me.pdf&width=200")
            assert first.status_code == 200
            call_count_after_first = factory.call_count
            second = client.get("/api/buckets/cache-bkt/thumbnail?object=uniq/cache-me.pdf&width=200")
    finally:
        cleanup()
    assert second.status_code == 200
    assert second.content == first.content
    # Second request served from the LRU — no new GCS client constructed.
    assert factory.call_count == call_count_after_first


# ---------------------------------------------------------------------------
# v6.18.0 — per-tenant bucket-read authorization (the leak fix).
#
# The file endpoints stream bytes from an arbitrary bucket name under the SA's
# credentials; `_authorize_bucket_read` gates that. The load-bearing case is the
# CROSS-TENANT DENY: a non-ONE, non-admin caller must NOT read the ONE llmops
# bucket via any verb, and the SA read must never even be attempted.
# ---------------------------------------------------------------------------

ONE_LLMOPS_BUCKET = "your-project-id-dev-llmops-bucket"


def _one_user() -> User:
    return _make_user(uid="one-uid", email="analyst@acmeenergy.com", domain="acmeenergy.com")


@pytest.mark.parametrize(
    "verb_path",
    ["/list", "/preview?object=ppa%2Fa.pdf", "/thumbnail?object=ppa%2Fa.pdf"],
)
def test_cross_tenant_read_denied_403(app: FastAPI, client: TestClient, verb_path: str) -> None:
    """A stranger from another domain cannot read the ONE llmops bucket — any verb."""
    cleanup = _install_user(app, _make_user(uid="stranger", email="x@evil.com", domain="evil.com"))
    factory, _ = _mock_gcs_blob(_one_page_pdf_bytes(), "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value="a-different-bucket"),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=None),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}{verb_path}")
    finally:
        cleanup()
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BUCKET_NOT_AUTHORIZED"
    factory.assert_not_called()  # the SA read never happens for a denied caller


def test_tenant_user_reads_own_bucket_200(app: FastAPI, client: TestClient) -> None:
    """A ONE user reads the ONE llmops bucket (their resolved tenant bucket)."""
    cleanup = _install_user(app, _one_user())
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 fake", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value=ONE_LLMOPS_BUCKET),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}/preview?object=ppa%2Fa.pdf")
    finally:
        cleanup()
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 fake"


def test_platform_admin_reads_any_bucket_200(app: FastAPI, client: TestClient) -> None:
    """A platform admin reads any bucket (diagnostics) — bypasses before Firestore."""
    cleanup = _install_user(app, _admin_user())
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 fake", "application/pdf")
    try:
        # No resolve/find patch: admin returns before either is called.
        with patch("buckets.routes._gcs_storage.Client", factory):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}/preview?object=ppa%2Fa.pdf")
    finally:
        cleanup()
    assert resp.status_code == 200


def test_registered_config_access_allows_200(app: FastAPI, client: TestClient) -> None:
    """A registered config the caller can_access AND is domain-blessed for is readable.

    v6.18.1 (#37): `allowedDomains` is a second lock beside the ACL — the default
    `_make_user` domain is yourcompany.com, so that is what the config blesses.
    """
    cleanup = _install_user(app, _make_user(uid="member", group_tags=frozenset({"finance-team"})))
    cfg = _make_bucket(
        gcsBucket="shared-reports",
        accessControl={"type": "tagged", "tags": ["finance-team"]},
        allowedDomains=["yourcompany.com"],
    )
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 fake", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value="not-this-bucket"),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=cfg),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get("/api/buckets/shared-reports/preview?object=q.pdf")
    finally:
        cleanup()
    assert resp.status_code == 200


def test_fail_closed_unmapped_denied_403(app: FastAPI, client: TestClient) -> None:
    """A fail-closed unmapped tenant (resolve raises) is denied, not 500."""
    from db.clients import UnmappedTenantError

    cleanup = _install_user(app, _make_user(uid="nomap", email="x@nomap.com", domain="nomap.com"))
    factory, _ = _mock_gcs_blob(b"x", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", side_effect=UnmappedTenantError("nomap.com")),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=None),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}/list")
    finally:
        cleanup()
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BUCKET_NOT_AUTHORIZED"
    factory.assert_not_called()


def test_anon_get_401(client: TestClient) -> None:
    assert client.get("/api/buckets/bkt-1").status_code == 401


def test_anon_create_401(client: TestClient) -> None:
    body = {"displayName": "x", "gcsBucket": "sample-bucket"}
    assert client.post("/api/buckets", json=body).status_code == 401


def test_anon_update_401(client: TestClient) -> None:
    assert client.put("/api/buckets/bkt-1", json={"displayName": "y"}).status_code == 401


def test_anon_delete_401(client: TestClient) -> None:
    assert client.delete("/api/buckets/bkt-1").status_code == 401


# ---------------------------------------------------------------------------
# GET /api/buckets/{id} — read access matrix
# ---------------------------------------------------------------------------


def test_non_owner_private_is_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(accessControl={"type": "private"})
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_owner_private_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket()
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 200
        assert resp.json()["bucketId"] == "bkt-1"
    finally:
        cleanup()


def test_public_bucket_any_user_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(accessControl={"type": "public"})
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_domain_match_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="yourcompany.com"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(
                accessControl={"type": "domain", "domain": "yourcompany.com"},
            )
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_domain_no_match_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="evil.com"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(
                accessControl={"type": "domain", "domain": "yourcompany.com"},
            )
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_specific_email_match_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", email="invited@corp.com"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(
                accessControl={"type": "specific", "emails": ["invited@corp.com"]},
            )
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_tagged_match_200(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(
        app,
        _make_user(uid="stranger", group_tags=frozenset({"finance-team"})),
    )
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(
                accessControl={"type": "tagged", "tags": ["finance-team"]},
            )
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 200
    finally:
        cleanup()


def test_tagged_no_match_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", group_tags=frozenset()))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = _make_bucket(
                accessControl={"type": "tagged", "tags": ["finance-team"]},
            )
            resp = client.get("/api/buckets/bkt-1")
        assert resp.status_code == 404
    finally:
        cleanup()


def test_get_nonexistent_404(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock:
            mock.return_value = None
            resp = client.get("/api/buckets/missing")
        assert resp.status_code == 404
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# POST — ownerId must come from the JWT
# ---------------------------------------------------------------------------


def test_create_sets_owner_from_jwt(app: FastAPI, client: TestClient) -> None:
    """ownerId always comes from the JWT. Caller is an admin — registration is
    admin-only since #37; the body-supplied ownerId must still be ignored."""
    admin = _make_user(uid="jwt-uid", email="jwt@yourcompany.com", group_tags=frozenset({"aitana-admin"}))
    cleanup = _install_user(app, admin)
    try:
        with patch("buckets.routes.bucket_config.create_bucket") as mock:
            mock.return_value = _make_bucket(ownerId="jwt-uid", ownerEmail="jwt@yourcompany.com")
            resp = client.post(
                "/api/buckets",
                json={
                    "displayName": "My Bucket",
                    "gcsBucket": "my-bucket-dev",
                    "allowedDomains": ["yourcompany.com"],
                    # Client attempts to set ownerId — must be ignored.
                    "ownerId": "attacker",
                },
            )
        assert resp.status_code == 201
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["owner_id"] == "jwt-uid"
        assert call_kwargs["owner_email"] == "jwt@yourcompany.com"
        assert call_kwargs["allowedDomains"] == ["yourcompany.com"]
    finally:
        cleanup()


def test_create_rejects_bad_access_control(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _admin_user())  # admin-only since #37
    try:
        resp = client.post(
            "/api/buckets",
            json={
                "displayName": "x",
                "gcsBucket": "my-bucket-dev",
                "allowedDomains": ["yourcompany.com"],
                "accessControl": {"type": "not-a-real-type"},
            },
        )
        assert resp.status_code == 400
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# PUT — owner-only
# ---------------------------------------------------------------------------


def test_update_admin_200(app: FastAPI, client: TestClient) -> None:
    """PUT is platform-admin-only since #37 (it can widen the read gate's locks)."""
    cleanup = _install_user(app, _admin_user())
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_get,
            patch("buckets.routes.bucket_config.update_bucket") as mock_update,
        ):
            mock_get.return_value = _make_bucket()
            mock_update.return_value = _make_bucket(displayName="Renamed")
            resp = client.put("/api/buckets/bkt-1", json={"displayName": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["displayName"] == "Renamed"
    finally:
        cleanup()


def test_update_non_admin_403(app: FastAPI, client: TestClient) -> None:
    """Caller CAN see the bucket (public) but is not a platform admin → 403 (#37)."""
    cleanup = _install_user(app, _make_user(uid="stranger"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_get:
            mock_get.return_value = _make_bucket(accessControl={"type": "public"})
            resp = client.put("/api/buckets/bkt-1", json={"displayName": "Pwned"})
        assert resp.status_code == 403
    finally:
        cleanup()


def test_update_missing_404(app: FastAPI, client: TestClient) -> None:
    """Admin caller (post-#37) — a bucket that does not exist 404s."""
    cleanup = _install_user(app, _admin_user())
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_get:
            mock_get.return_value = None  # does not exist
            resp = client.put("/api/buckets/bkt-1", json={"displayName": "x"})
        assert resp.status_code == 404
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# DELETE — owner-or-admin
# ---------------------------------------------------------------------------


def test_delete_owner_204(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid=OWNER_UID, email=OWNER_EMAIL))
    try:
        with (
            patch("buckets.routes.bucket_config.get_bucket") as mock_get,
            patch("buckets.routes.bucket_config.delete_bucket") as mock_del,
        ):
            mock_get.return_value = _make_bucket()
            mock_del.return_value = True
            resp = client.delete("/api/buckets/bkt-1")
        assert resp.status_code == 204
    finally:
        cleanup()


def test_delete_non_owner_with_access_403(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger"))
    try:
        with patch("buckets.routes.bucket_config.get_bucket") as mock_get:
            mock_get.return_value = _make_bucket(accessControl={"type": "public"})
            resp = client.delete("/api/buckets/bkt-1")
        assert resp.status_code == 403
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# LIST — filters to what the caller can see
# ---------------------------------------------------------------------------


def test_list_filters_to_visible(app: FastAPI, client: TestClient) -> None:
    cleanup = _install_user(app, _make_user(uid="stranger", domain="evil.com"))
    try:
        with patch("buckets.routes.bucket_config.list_buckets") as mock_list:
            mock_list.return_value = [
                _make_bucket(bucketId="b-pub", accessControl={"type": "public"}),
                _make_bucket(bucketId="b-priv", accessControl={"type": "private"}),
            ]
            resp = client.get("/api/buckets")
        assert resp.status_code == 200
        ids = [b["bucketId"] for b in resp.json()]
        assert ids == ["b-pub"]
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# v6.18.1 (issue #37) — the bucket-config door needs TWO locks.
#
# Before: door 3 was `can_access(cfg)` alone, and `can_access` short-circuits on
# "owner always wins". `POST /api/buckets` was open to any authenticated user and
# stamped ownerId from their JWT, so a stranger could self-register a config
# naming ANY bucket the SA can read and walk straight through. Now: registration
# is platform-admin-only, and the config carries `allowedDomains` enforced beside
# the ACL.
# ---------------------------------------------------------------------------


def test_self_registered_config_does_not_open_the_gate(app: FastAPI, client: TestClient) -> None:
    """THE #37 BYPASS: a config owned by the caller must NOT grant bucket read.

    Simulates the post-fix world where a stranger somehow still holds a config
    naming the ONE bucket (e.g. written before the fix). `can_access` returns
    True on owner-wins, so ONLY the domain lock stands between them and the
    bytes — this test pins that it holds.
    """
    stranger = _make_user(uid="stranger", email="x@evil.com", domain="evil.com")
    cleanup = _install_user(app, stranger)
    self_made = _make_bucket(
        gcsBucket=ONE_LLMOPS_BUCKET,
        ownerId="stranger",  # <- owner-wins would return True
        ownerEmail="x@evil.com",
        accessControl={"type": "private"},
        allowedDomains=["acmeenergy.com"],
    )
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 secret", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value="a-different-bucket"),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=self_made),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}/preview?object=ppa%2Fa.pdf")
    finally:
        cleanup()
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BUCKET_NOT_AUTHORIZED"
    factory.assert_not_called()


def test_config_without_allowed_domains_fails_closed(app: FastAPI, client: TestClient) -> None:
    """A half-configured config (no allowedDomains) grants nothing — even to its tenant."""
    cleanup = _install_user(app, _one_user())
    legacy = _make_bucket(
        gcsBucket=ONE_LLMOPS_BUCKET,
        accessControl={"type": "domain", "domain": "acmeenergy.com"},
        allowedDomains=[],  # predates the field / written straight to Firestore
    )
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 secret", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value="a-different-bucket"),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=legacy),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get(f"/api/buckets/{ONE_LLMOPS_BUCKET}/list")
    finally:
        cleanup()
    assert resp.status_code == 403
    factory.assert_not_called()


def test_blessed_bucket_readable_by_its_domain(app: FastAPI, client: TestClient) -> None:
    """The intended path: an admin-blessed bucket, read by a user of a blessed domain.

    This is how a customer library OTHER than the tenant's own documents_bucket
    is meant to be attached — both locks open.
    """
    cleanup = _install_user(app, _one_user())
    blessed = _make_bucket(
        gcsBucket="one-extra-library-bucket",
        ownerId="admin-uid",
        ownerEmail="admin@yourcompany.com",
        accessControl={"type": "tagged", "tags": ["ONE"]},
        allowedDomains=["acmeenergy.com"],
    )
    one_user_with_tag = _make_user(
        uid="one-uid", email="analyst@acmeenergy.com", domain="acmeenergy.com", group_tags=frozenset({"ONE"})
    )
    cleanup()
    cleanup = _install_user(app, one_user_with_tag)
    factory, _ = _mock_gcs_blob(b"%PDF-1.7 fake", "application/pdf")
    try:
        with (
            patch("buckets.routes.resolve_documents_bucket", return_value="a-different-bucket"),
            patch("buckets.routes.bucket_config.find_by_gcs_name", return_value=blessed),
            patch("buckets.routes._gcs_storage.Client", factory),
        ):
            resp = client.get("/api/buckets/one-extra-library-bucket/preview?object=x%2Fa.pdf")
    finally:
        cleanup()
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 fake"


def test_non_admin_cannot_register_a_bucket(app: FastAPI, client: TestClient) -> None:
    """Registration is an operator act — a normal user gets 403, nothing is written."""
    cleanup = _install_user(app, _one_user())
    try:
        with patch("buckets.routes.bucket_config.create_bucket") as create:
            resp = client.post(
                "/api/buckets",
                json={
                    "displayName": "mine",
                    "gcsBucket": ONE_LLMOPS_BUCKET,
                    "allowedDomains": ["evil.com"],
                },
            )
    finally:
        cleanup()
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BUCKET_ADMIN_REQUIRED"
    create.assert_not_called()


def test_admin_register_requires_allowed_domains(app: FastAPI, client: TestClient) -> None:
    """Even an admin cannot register a bucket with no domain binding."""
    cleanup = _install_user(app, _admin_user())
    try:
        with patch("buckets.routes.bucket_config.create_bucket") as create:
            resp = client.post(
                "/api/buckets",
                json={"displayName": "x", "gcsBucket": "some-bucket", "allowedDomains": []},
            )
    finally:
        cleanup()
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "ALLOWED_DOMAINS_REQUIRED"
    create.assert_not_called()
