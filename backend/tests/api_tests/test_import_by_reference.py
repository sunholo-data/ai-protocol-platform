"""Tests for POST /api/documents/import-by-reference — DOC-IMPORT-REF M2.

Cache cascade:
- L2: self-dedup (userId, sourceUrl) → return existing without parsing
- L4: sentinel-dedup (PLATFORM_OWNER_UID, sourceUrl) → clone blocks to new
      per-user record, no parse
- L3: fresh _run_parse + _store_document

Failure: AILANG Parse returns "failed" → 422 with parseError detail.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_USER = User(uid="user1", email="alice@example.com", domain="example.com")
_GS_BUCKET = "multivac-acme-energy-bucket"
_GS_OBJECT = "PPAs/longform/example-A-fixed-pap.pdf"
_GS_URL = f"gs://{_GS_BUCKET}/{_GS_OBJECT}"


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.import_by_reference import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


def _body() -> dict:
    return {"bucket": _GS_BUCKET, "object": _GS_OBJECT, "skillId": "one-ppa-expert"}


class TestImportByReferenceCacheCascade:
    def test_l2_self_dedup_hit_returns_without_parse(self, client: TestClient):
        """When the calling user already has a parsed_documents record for this
        gs:// URL, return it immediately — never call _run_parse."""
        existing = {
            "__id": "existing-doc-id",
            "parseStatus": "parsed",
            "originalFilename": "example-A-fixed-pap.pdf",
            "blockCount": 42,
            "storagePath": _GS_OBJECT,
            "folderId": None,
            "sourceUrl": _GS_URL,
            "userId": _USER.uid,
        }

        def query_returns_self(_collection, *, filters=None, **kwargs):
            assert filters is not None
            for field, _op, value in filters:
                if field == "userId" and value == _USER.uid:
                    return [existing]
                if field == "userId":
                    return []
            return []

        with (
            patch("tools.documents.import_by_reference.query_documents", side_effect=query_returns_self),
            patch("tools.documents.import_by_reference._run_parse") as run_parse_mock,
        ):
            resp = client.post("/api/documents/import-by-reference", json=_body())

        assert resp.status_code == 200
        assert resp.json()["docId"] == "existing-doc-id"
        assert resp.json()["blocksCount"] == 42
        run_parse_mock.assert_not_called()

    def test_l4_sentinel_dedup_clones_blocks_to_user_record(self, client: TestClient):
        """L4 hit: PLATFORM_OWNER_UID has the doc. Clone blocks into a new
        per-user parsed_documents record (Firestore write only — no parse)."""
        sentinel = {
            "__id": "sentinel-doc-id",
            "parseStatus": "parsed",
            "originalFilename": "example-A-fixed-pap.pdf",
            "blockCount": 42,
            "blocks": [{"type": "heading", "text": "PPA Contract A"}],
            "storagePath": _GS_OBJECT,
            "folderId": None,
            "sourceUrl": _GS_URL,
            "userId": "aitana-platform",
            "skillId": "one-ppa-expert",
            "sourceFormat": "pdf",
        }
        stored = []

        def query_no_self_then_sentinel(_collection, *, filters=None, **kwargs):
            for field, _op, value in filters or []:
                if field == "userId":
                    if value == _USER.uid:
                        return []  # L2 miss
                    if value == "aitana-platform":
                        return [sentinel]  # L4 hit
            return []

        def capture_store(doc_id, **kwargs):
            stored.append((doc_id, kwargs))

        with (
            patch(
                "tools.documents.import_by_reference.query_documents",
                side_effect=query_no_self_then_sentinel,
            ),
            patch("tools.documents.import_by_reference._store_document", side_effect=capture_store),
            patch("tools.documents.import_by_reference._run_parse") as run_parse_mock,
        ):
            resp = client.post("/api/documents/import-by-reference", json=_body())

        assert resp.status_code == 200
        # Per-user record was created — NOT the sentinel's id
        assert resp.json()["docId"] != "sentinel-doc-id"
        assert resp.json()["blocksCount"] == 1
        # Parser was NOT invoked
        run_parse_mock.assert_not_called()
        # One Firestore write for the per-user clone
        assert len(stored) == 1
        _clone_doc_id, clone_kwargs = stored[0]
        assert clone_kwargs["user_id"] == _USER.uid
        assert clone_kwargs["gs_url"] == _GS_URL
        assert clone_kwargs["parse_result"].status == "parsed"
        assert clone_kwargs["parse_result"].blocks == sentinel["blocks"]

    def test_l3_fresh_parse_when_no_cache_hits(self, client: TestClient):
        """L2 miss + L4 miss: call _run_parse, then _store_document. Returns
        a ParsedDocumentResponse referencing the new per-user record."""
        stored = []

        def capture_store(doc_id, **kwargs):
            stored.append((doc_id, kwargs))

        with (
            patch("tools.documents.import_by_reference.query_documents", return_value=[]),
            patch(
                "tools.documents.import_by_reference._run_parse",
                return_value=("parsed", [{"type": "heading", "text": "Fresh"}], 1200, None),
            ),
            patch("tools.documents.import_by_reference._store_document", side_effect=capture_store),
        ):
            resp = client.post("/api/documents/import-by-reference", json=_body())

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "parsed"
        assert body["blocksCount"] == 1
        assert body["originalFilename"] == "example-A-fixed-pap.pdf"
        assert len(stored) == 1
        _, store_kwargs = stored[0]
        assert store_kwargs["user_id"] == _USER.uid
        assert store_kwargs["skill_id"] == "one-ppa-expert"
        assert store_kwargs["gs_url"] == _GS_URL
        # No folder for imported-by-reference docs — they don't live in a user folder
        assert store_kwargs["folder_id"] is None

    def test_l2_stale_pending_record_falls_through_to_l4_and_overwrites(self, client: TestClient):
        """A stale per-user record (parseStatus != 'parsed') from a prior
        broken AILANG run must NOT block the recovery path. L2 query returns
        the stale record but we skip it, then L4 clones into the SAME docId
        (overwriting the stale state) instead of creating a duplicate."""
        stale = {
            "__id": "stale-doc-id",
            "parseStatus": "pending_ai_extraction",
            "originalFilename": "example-A-fixed-pap.pdf",
            "blockCount": 0,
            "blocks": [],
            "storagePath": _GS_OBJECT,
            "folderId": None,
            "sourceUrl": _GS_URL,
            "userId": _USER.uid,
        }
        sentinel = {
            "__id": "sentinel-doc-id",
            "parseStatus": "parsed",
            "originalFilename": "example-A-fixed-pap.pdf",
            "blockCount": 42,
            "blocks": [{"type": "heading", "text": "Recovered"}],
            "storagePath": _GS_OBJECT,
            "folderId": None,
            "sourceUrl": _GS_URL,
            "userId": "aitana-platform",
            "skillId": "one-ppa-expert",
            "sourceFormat": "pdf",
        }
        stored = []

        def query_returns(_collection, *, filters=None, **kwargs):
            for field, _op, value in filters or []:
                if field == "userId":
                    if value == _USER.uid:
                        return [stale]
                    if value == "aitana-platform":
                        return [sentinel]
            return []

        def capture_store(doc_id, **kwargs):
            stored.append((doc_id, kwargs))

        with (
            patch("tools.documents.import_by_reference.query_documents", side_effect=query_returns),
            patch("tools.documents.import_by_reference._store_document", side_effect=capture_store),
            patch("tools.documents.import_by_reference._run_parse") as run_parse_mock,
        ):
            resp = client.post("/api/documents/import-by-reference", json=_body())

        assert resp.status_code == 200
        # CRITICAL: overwritten the stale record (same docId), not created a duplicate
        assert resp.json()["docId"] == "stale-doc-id"
        assert resp.json()["blocksCount"] == 1
        run_parse_mock.assert_not_called()
        assert len(stored) == 1
        clone_doc_id, _ = stored[0]
        assert clone_doc_id == "stale-doc-id"

    def test_parse_failure_returns_422(self, client: TestClient):
        """AILANG Parse returns ("failed", [], 0, error) → 422 with parseError."""
        with (
            patch("tools.documents.import_by_reference.query_documents", return_value=[]),
            patch(
                "tools.documents.import_by_reference._run_parse",
                return_value=("failed", [], 0, "Unsupported format: .xyz"),
            ),
            patch("tools.documents.import_by_reference._store_document"),
        ):
            resp = client.post("/api/documents/import-by-reference", json=_body())

        assert resp.status_code == 422
        assert "Unsupported format" in resp.json()["detail"]
