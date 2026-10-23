"""Document version history — v6.34.0 DOC-WORKBENCH P3.

Regeneration updates a document in place, which is what makes an iterate loop
feel like editing — and, until this, silently destroyed the previous draft.

**The round-trip test is the point of this file.** `save_artifact` is a
coroutine: an un-awaited call returns a coroutine object, writes nothing, and
the later read 404s. That has already cost this codebase a document-fetch bug,
and a mock cannot catch it because a mock is happy to be un-awaited. Only a real
save → list → load against a working artifact service proves the await is there,
so `InMemoryArtifactService` (same `BaseArtifactService` contract as the GCS one)
stands in for storage.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.documents import versions as V


@pytest.fixture(autouse=True)
def _clean_services():
    V.reset_services()
    yield
    V.reset_services()


@pytest.fixture()
def in_memory_store():
    """A real artifact service, swapped in for the GCS one."""
    from google.adk.artifacts import InMemoryArtifactService

    service = InMemoryArtifactService()
    with patch.object(V, "_artifact_service", return_value=service):
        yield service


_ARGS = {
    "bucket_name": "client-bucket",
    "app_name": "aitana_platform",
    "user_id": "user1",
    "doc_id": "doc-1",
    "filename": "Offer.docx",
}
_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class TestArtifactKey:
    def test_keyed_on_the_doc_id_not_the_display_name(self):
        """Two folders may hold documents of the same name; a rename must not
        orphan the history."""
        a = V.artifact_filename("doc-1", "Offer.docx")
        b = V.artifact_filename("doc-2", "Offer.docx")
        assert a != b
        assert "doc-1" in a

    def test_uses_the_user_namespace(self):
        """`user:` is ADK's marker for user-scoped artifacts. Without it the
        service demands a session_id — and a document outlives the session that
        created it."""
        assert V.artifact_filename("doc-1", "Offer.docx").startswith("user:")

    def test_keeps_the_extension(self):
        assert V.artifact_filename("doc-1", "Offer.docx").endswith(".docx")

    def test_survives_a_document_with_no_extension(self):
        assert V.artifact_filename("doc-1", "") == "user:documents/doc-1"


class TestRoundTrip:
    """The await check. A mock would pass these while writing nothing."""

    @pytest.mark.asyncio
    async def test_save_then_load_returns_the_same_bytes(self, in_memory_store):
        version = await V.save_version(**_ARGS, data=b"PK\x03\x04v1", content_type=_MIME)
        assert version is not None, "save returned no version — is save_artifact awaited?"

        loaded = await V.load_version(**_ARGS, version=version)
        assert loaded == b"PK\x03\x04v1"

    @pytest.mark.asyncio
    async def test_each_save_is_a_new_version_and_history_is_kept(self, in_memory_store):
        v1 = await V.save_version(**_ARGS, data=b"draft one", content_type=_MIME)
        v2 = await V.save_version(**_ARGS, data=b"draft two", content_type=_MIME)

        assert v2 != v1, "regenerating must not overwrite the previous version"
        assert await V.list_versions(**_ARGS) == sorted([v1, v2])

        # The whole point: the earlier draft is still retrievable.
        assert await V.load_version(**_ARGS, version=v1) == b"draft one"
        assert await V.load_version(**_ARGS, version=v2) == b"draft two"

    @pytest.mark.asyncio
    async def test_versions_come_back_oldest_first(self, in_memory_store):
        for i in range(3):
            await V.save_version(**_ARGS, data=f"v{i}".encode(), content_type=_MIME)
        listed = await V.list_versions(**_ARGS)
        assert listed == sorted(listed)

    @pytest.mark.asyncio
    async def test_a_different_document_has_its_own_history(self, in_memory_store):
        await V.save_version(**_ARGS, data=b"a", content_type=_MIME)
        other = {**_ARGS, "doc_id": "doc-2"}
        await V.save_version(**other, data=b"b", content_type=_MIME)

        assert len(await V.list_versions(**_ARGS)) == 1
        assert len(await V.list_versions(**other)) == 1


class TestBestEffort:
    """History is valuable; it is not worth failing a save for."""

    @pytest.mark.asyncio
    async def test_no_store_returns_none_rather_than_raising(self):
        with patch.object(V, "_artifact_service", return_value=None):
            assert await V.save_version(**_ARGS, data=b"x", content_type=_MIME) is None

    @pytest.mark.asyncio
    async def test_empty_bytes_are_not_versioned(self, in_memory_store):
        assert await V.save_version(**_ARGS, data=b"", content_type=_MIME) is None

    @pytest.mark.asyncio
    async def test_a_failing_store_does_not_propagate(self):
        """A storage outage must not take the user's document down with it.

        (A stub rather than patching the service instance: the ADK services are
        pydantic models, so their methods are not patchable attributes.)"""

        class _Broken:
            async def save_artifact(self, **_kwargs):
                raise RuntimeError("gcs down")

            async def list_versions(self, **_kwargs):
                raise RuntimeError("gcs down")

            async def load_artifact(self, **_kwargs):
                raise RuntimeError("gcs down")

        with patch.object(V, "_artifact_service", return_value=_Broken()):
            assert await V.save_version(**_ARGS, data=b"x", content_type=_MIME) is None
            assert await V.list_versions(**_ARGS) == []
            assert await V.load_version(**_ARGS, version=1) is None

    @pytest.mark.asyncio
    async def test_listing_an_unknown_document_is_empty_not_an_error(self, in_memory_store):
        assert await V.list_versions(**_ARGS) == []

    @pytest.mark.asyncio
    async def test_loading_a_missing_version_returns_none(self, in_memory_store):
        await V.save_version(**_ARGS, data=b"x", content_type=_MIME)
        assert await V.load_version(**_ARGS, version=999) is None


class TestBucketBinding:
    """Versions live in the CALLER'S bucket, not a deployment-wide one."""

    def test_a_service_is_built_per_bucket(self):
        built: list[str] = []

        class _Fake:
            def __init__(self, bucket_name: str):
                built.append(bucket_name)

        with patch("google.adk.artifacts.GcsArtifactService", _Fake):
            V._artifact_service("client-a")
            V._artifact_service("client-b")
            V._artifact_service("client-a")  # cached

        assert built == ["client-a", "client-b"]

    def test_no_bucket_means_no_store(self):
        assert V._artifact_service("") is None
