"""Tests for POST /api/admin/documents/prewarm-from-blocks."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from admin.prewarm_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _body() -> dict:
    return {
        "bucket": "multivac-acme-energy-bucket",
        "object": "PPAs/longform/example-A.pdf",
        "skillId": "one-ppa-expert",
        "originalFilename": "example-A.pdf",
        "sourceFormat": "pdf",
        "blocks": [
            {"type": "text", "text": "FINANCIAL POWER PURCHASE AGREEMENT", "style": "Normal", "level": 0},
            {"type": "text", "text": "as Seller, and EDP, S.A.", "style": "Normal", "level": 0},
        ],
    }


class TestPrewarmFromBlocks:
    def test_creates_sentinel_record_with_platform_owner_uid(self, client: TestClient):
        stored = []

        def capture_store(doc_id, **kwargs):
            stored.append((doc_id, kwargs))

        with (
            patch("admin.prewarm_routes._assert_caller_is_service_account"),
            patch("admin.prewarm_routes.query_documents", return_value=[]),
            patch("admin.prewarm_routes._store_document", side_effect=capture_store),
        ):
            resp = client.post("/api/admin/documents/prewarm-from-blocks", json=_body())

        assert resp.status_code == 200
        body = resp.json()
        assert body["ownerUid"] == "aitana-platform"
        assert body["sourceUrl"] == "gs://multivac-acme-energy-bucket/PPAs/longform/example-A.pdf"
        assert body["blockCount"] == 2
        assert len(stored) == 1
        _, kwargs = stored[0]
        assert kwargs["user_id"] == "aitana-platform"
        assert kwargs["parse_result"].status == "parsed"
        assert len(kwargs["parse_result"].blocks) == 2

    def test_idempotent_overwrite_reuses_existing_doc_id(self, client: TestClient):
        existing = [{"__id": "sentinel-existing-id", "sourceUrl": "gs://bucket/o.pdf"}]
        stored = []

        def capture_store(doc_id, **kwargs):
            stored.append(doc_id)

        with (
            patch("admin.prewarm_routes._assert_caller_is_service_account"),
            patch("admin.prewarm_routes.query_documents", return_value=existing),
            patch("admin.prewarm_routes._store_document", side_effect=capture_store),
        ):
            resp = client.post("/api/admin/documents/prewarm-from-blocks", json=_body())

        assert resp.status_code == 200
        assert resp.json()["docId"] == "sentinel-existing-id"
        assert stored == ["sentinel-existing-id"]

    def test_auth_gate_rejects_unprivileged_caller(self, client: TestClient):
        from fastapi import HTTPException

        def deny(_request):
            raise HTTPException(status_code=403, detail="Not authorized")

        with patch("admin.prewarm_routes._assert_caller_is_service_account", side_effect=deny):
            resp = client.post("/api/admin/documents/prewarm-from-blocks", json=_body())

        assert resp.status_code == 403

    def test_precheck_413_when_payload_exceeds_firestore_limit(self, client: TestClient):
        body = _body()
        # Inflate blocks until > 900 KB
        body["blocks"] = [{"type": "text", "text": "x" * 1000, "style": "Normal", "level": 0} for _ in range(1000)]

        with patch("admin.prewarm_routes._assert_caller_is_service_account"):
            resp = client.post("/api/admin/documents/prewarm-from-blocks/precheck", json=body)

        assert resp.status_code == 413
        assert "Firestore limit" in resp.json()["detail"]
