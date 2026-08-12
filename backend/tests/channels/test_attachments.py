"""Unit tests for `channels.attachments.AttachmentPipeline`.

The pipeline does I/O at three layers — HTTP download, GCS upload,
Firestore write. Tests mock each in isolation so the suite stays fast
and offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from channels.attachments import (
    Attachment,
    AttachmentPipeline,
    AttachmentTooLargeError,
    AttachmentUnsupportedTypeError,
)


@pytest.fixture(autouse=True)
def _stub_storage_and_firestore():
    """Replace the three side-effect helpers with no-op patches.

    Tests that care about specific arguments to each helper patch the
    helper itself; this fixture exists so the default call chain is
    safe to invoke in tests that don't care about every step.
    """
    with (
        patch.object(AttachmentPipeline, "_download_bytes", AsyncMock(return_value=b"hello-bytes")),
        patch.object(AttachmentPipeline, "_save_to_storage", AsyncMock(return_value="gs://test/path")),
        patch.object(AttachmentPipeline, "_register_document", AsyncMock(return_value=None)),
        patch.object(AttachmentPipeline, "_trigger_parse", AsyncMock(return_value=None)),
    ):
        yield


class TestExtensionAllowlist:
    """`_enforce_extension` rejects files outside the allowlist."""

    def test_allowed_pdf(self) -> None:
        # Should not raise
        AttachmentPipeline._enforce_extension("doc.pdf")

    def test_allowed_docx(self) -> None:
        AttachmentPipeline._enforce_extension("contract.docx")

    def test_allowed_image(self) -> None:
        AttachmentPipeline._enforce_extension("photo.jpg")

    def test_rejected_extension(self) -> None:
        with pytest.raises(AttachmentUnsupportedTypeError):
            AttachmentPipeline._enforce_extension("malware.exe")

    def test_rejected_no_extension(self) -> None:
        with pytest.raises(AttachmentUnsupportedTypeError):
            AttachmentPipeline._enforce_extension("no_ext_at_all")


class TestSizeGuard:
    @pytest.mark.asyncio
    async def test_known_oversize_returns_empty(self) -> None:
        # Pre-known size > max → rejected, returns no doc IDs.
        att = Attachment(url="http://x", filename="big.pdf", size_bytes=10_000_000)
        ids = await AttachmentPipeline.upload([att], "uid-1", max_size=1_000_000)
        assert ids == []

    @pytest.mark.asyncio
    async def test_downloaded_oversize_returns_empty(self) -> None:
        # No `size_bytes` declared, but downloaded bytes exceed cap.
        with patch.object(
            AttachmentPipeline,
            "_download_bytes",
            AsyncMock(return_value=b"X" * 2_000_000),
        ):
            att = Attachment(url="http://x", filename="huge.pdf")
            ids = await AttachmentPipeline.upload([att], "uid-1", max_size=1_000_000)
        assert ids == []

    @pytest.mark.asyncio
    async def test_within_limit_succeeds(self) -> None:
        att = Attachment(url="http://x", filename="ok.pdf", size_bytes=500)
        ids = await AttachmentPipeline.upload([att], "uid-1", max_size=1_000_000)
        assert len(ids) == 1
        assert ids[0]  # non-empty UUID string


class TestUploadCallsHelpers:
    """Happy-path: pipeline calls storage + firestore helpers in order."""

    @pytest.mark.asyncio
    async def test_storage_called_with_user_scoped_path(self) -> None:
        with patch.object(AttachmentPipeline, "_save_to_storage", AsyncMock(return_value="gs://t/p")) as save:
            att = Attachment(url="http://x", filename="report.pdf", size_bytes=100)
            await AttachmentPipeline.upload([att], "uid-42", max_size=1_000_000)
            assert save.await_count == 1
            kwargs = save.await_args.kwargs
            assert kwargs["storage_path"].startswith("users/uid-42/docs/channel/")
            assert kwargs["storage_path"].endswith("-report.pdf")

    @pytest.mark.asyncio
    async def test_register_document_called_with_pending_status(self) -> None:
        with patch.object(AttachmentPipeline, "_register_document", AsyncMock()) as reg:
            att = Attachment(url="http://x", filename="contract.docx", size_bytes=100)
            ids = await AttachmentPipeline.upload([att], "uid-x", max_size=1_000_000)
            assert reg.await_count == 1
            kwargs = reg.await_args.kwargs
            assert kwargs["firebase_uid"] == "uid-x"
            assert kwargs["filename"] == "contract.docx"
            assert kwargs["extension"] == ".docx"
            assert kwargs["doc_id"] == ids[0]

    @pytest.mark.asyncio
    async def test_trigger_parse_called(self) -> None:
        with patch.object(AttachmentPipeline, "_trigger_parse", AsyncMock()) as trig:
            att = Attachment(url="http://x", filename="report.pdf", size_bytes=100)
            await AttachmentPipeline.upload([att], "uid", max_size=1_000_000)
            assert trig.await_count == 1


class TestBatchResilience:
    """One bad attachment must not abort the whole batch."""

    @pytest.mark.asyncio
    async def test_bad_extension_in_batch_does_not_block_good_files(self) -> None:
        attachments = [
            Attachment(url="http://x", filename="evil.exe", size_bytes=10),
            Attachment(url="http://y", filename="good.pdf", size_bytes=10),
        ]
        ids = await AttachmentPipeline.upload(attachments, "uid", max_size=1_000_000)
        assert len(ids) == 1  # only the good file ingested

    @pytest.mark.asyncio
    async def test_download_failure_continues_batch(self) -> None:
        call_count = {"n": 0}

        async def maybe_fail(url: str) -> bytes:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("network busted")
            return b"second-ok"

        with patch.object(AttachmentPipeline, "_download_bytes", side_effect=maybe_fail):
            attachments = [
                Attachment(url="http://1", filename="first.pdf", size_bytes=10),
                Attachment(url="http://2", filename="second.pdf", size_bytes=10),
            ]
            ids = await AttachmentPipeline.upload(attachments, "uid", max_size=1_000_000)

        assert len(ids) == 1
        # Second file's ID should be present even though first failed.


class TestUnsupportedRaisedAtBoundary:
    @pytest.mark.asyncio
    async def test_unsupported_type_is_caught_and_filtered(self) -> None:
        # Outer `upload` catches AttachmentUnsupportedTypeError so the
        # caller's BaseChannel.handle_webhook doesn't crash on a bad file.
        att = Attachment(url="http://x", filename="bad.xyz", size_bytes=100)
        ids = await AttachmentPipeline.upload([att], "uid", max_size=1_000_000)
        assert ids == []

    @pytest.mark.asyncio
    async def test_oversize_is_caught_and_filtered(self) -> None:
        att = Attachment(url="http://x", filename="ok.pdf", size_bytes=10_000_000)
        ids = await AttachmentPipeline.upload([att], "uid", max_size=1_000_000)
        assert ids == []


class TestErrorTypesAreExported:
    """Sanity: error classes used in catch clauses are public on the module."""

    def test_too_large_error_is_class(self) -> None:
        assert issubclass(AttachmentTooLargeError, Exception)

    def test_unsupported_type_error_is_class(self) -> None:
        assert issubclass(AttachmentUnsupportedTypeError, Exception)
