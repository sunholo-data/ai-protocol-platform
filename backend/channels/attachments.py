"""Channel attachment pipeline.

Mirrors the document upload flow (`backend/tools/documents/upload.py`)
but for files arriving via channel webhooks instead of `multipart/form-data`
from the web UI. Channel attachments become user documents — so a PDF
emailed to the bot lands in the user's document library and the AI can
reference it the same way it references web-uploaded files.

Why the same destination as web uploads:
    - Email + Drive + chat attachments all serve the same use case
      ("AI, look at this file"). One library, one parse pipeline.
    - Forks that want session-only ephemeral artifacts can override
      `AttachmentPipeline._save_to_documents` to route elsewhere.

The pipeline is intentionally minimal in v1:
    - Size guard (per-channel `max_size` from `BaseChannel.max_attachment_size`)
    - Extension allowlist (delegates to `_ALLOWED_EXTENSIONS` below)
    - Download from the channel-supplied URL
    - Upload to GCS at `users/{uid}/docs/channel/{uuid}-{filename}`
    - Pending-state Firestore record so the file is visible in the
      user's library immediately
    - Background AILANG Parse trigger (best-effort; failure logged, not
      blocking)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


_ALLOWED_EXTENSIONS = {
    ".docx",
    ".pptx",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
    ".epub",
    ".eml",
    ".mbox",
    ".html",
    ".htm",
    ".md",
    ".csv",
    ".pdf",
    ".txt",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
}


class Attachment(BaseModel):
    """A file referenced by an inbound channel message.

    Channels populate `url` (where to download from) when the channel
    serves attachments via CDN (Discord, Mailgun). When attachment bytes
    arrive inline (Telegram photo payloads can do this), the adapter
    uploads to a staging URL and sets `url` to that — keeps the
    pipeline contract uniform.
    """

    model_config = ConfigDict(frozen=True)

    url: str = Field(description="Where the pipeline downloads bytes from")
    filename: str = Field(description="Original filename; used for extension + storage path")
    mime_type: str | None = Field(default=None)
    size_bytes: int | None = Field(
        default=None, description="Optional pre-known size; pipeline still re-validates after download"
    )


class AttachmentTooLargeError(Exception):
    """Raised when an attachment exceeds the channel's `max_attachment_size`."""


class AttachmentUnsupportedTypeError(Exception):
    """Raised when an attachment's extension is not in `_ALLOWED_EXTENSIONS`."""


class AttachmentPipeline:
    """Stateless pipeline. All entry points are classmethods.

    Channels call `AttachmentPipeline.upload(...)` from inside
    `BaseChannel.handle_webhook` — they do not instantiate this class.
    """

    @classmethod
    async def upload(
        cls,
        attachments: list[Attachment],
        firebase_uid: str,
        *,
        max_size: int,
    ) -> list[str]:
        """Process a list of inbound attachments. Returns list of new doc IDs.

        Each step is in its own helper so tests can mock at a granular
        level (download separately from storage separately from registry
        write).

        Failures are per-attachment: one bad file does not abort the
        whole batch — the caller (BaseChannel) gets the IDs of the
        ones that succeeded plus log lines for the ones that didn't.
        """
        doc_ids: list[str] = []
        for att in attachments:
            try:
                doc_id = await cls._process_one(att, firebase_uid, max_size=max_size)
            except (AttachmentTooLargeError, AttachmentUnsupportedTypeError) as exc:
                logger.warning(
                    "channel attachment rejected: filename=%s reason=%s",
                    att.filename,
                    exc.__class__.__name__,
                )
                continue
            except Exception:
                logger.exception("channel attachment failed: filename=%s", att.filename)
                continue
            doc_ids.append(doc_id)
        return doc_ids

    @classmethod
    async def _process_one(
        cls,
        att: Attachment,
        firebase_uid: str,
        *,
        max_size: int,
    ) -> str:
        """Process a single attachment end-to-end. Returns the new doc ID."""
        cls._enforce_extension(att.filename)

        if att.size_bytes is not None and att.size_bytes > max_size:
            raise AttachmentTooLargeError(f"{att.filename} is {att.size_bytes} bytes > limit {max_size}")

        data = await cls._download_bytes(att.url)
        if len(data) > max_size:
            # Size pre-check missed (channel didn't supply it) — re-check
            # after download so adversarial inputs can't bypass the cap.
            raise AttachmentTooLargeError(f"{att.filename} downloaded to {len(data)} bytes > limit {max_size}")

        ext = PurePosixPath(att.filename).suffix.lower()
        doc_id = str(uuid.uuid4())
        safe_filename = att.filename.replace("/", "_").replace("\\", "_")
        storage_path = f"users/{firebase_uid}/docs/channel/{doc_id}-{safe_filename}"

        gs_url = await cls._save_to_storage(
            storage_path=storage_path,
            data=data,
            mime_type=att.mime_type,
            filename=safe_filename,
        )
        await cls._register_document(
            doc_id=doc_id,
            firebase_uid=firebase_uid,
            gs_url=gs_url,
            storage_path=storage_path,
            filename=safe_filename,
            extension=ext,
        )
        await cls._trigger_parse(doc_id)

        return doc_id

    # --- helpers (overridable / mockable in tests) ------------------------

    @classmethod
    def _enforce_extension(cls, filename: str) -> None:
        ext = PurePosixPath(filename).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise AttachmentUnsupportedTypeError(f"Extension {ext!r} not in allowlist (got {filename!r})")

    @classmethod
    async def _download_bytes(cls, url: str) -> bytes:
        """Fetch attachment bytes from the channel-supplied URL."""
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    @classmethod
    async def _save_to_storage(
        cls,
        *,
        storage_path: str,
        data: bytes,
        mime_type: str | None,
        filename: str,
    ) -> str:
        """Upload bytes to GCS and return the `gs://` URL.

        Uses the same bucket as web uploads — `LOGS_BUCKET_NAME` env via
        `db.clients.resolve_documents_bucket` — but resolved with a
        synthetic User. Forks that want per-channel buckets override.
        """
        from google.cloud import storage as gcs

        from db.clients import resolve_channel_bucket

        bucket_name = resolve_channel_bucket()
        client = gcs.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(storage_path)
        content_type = mime_type or "application/octet-stream"
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{bucket_name}/{storage_path}"

    @classmethod
    async def _register_document(
        cls,
        *,
        doc_id: str,
        firebase_uid: str,
        gs_url: str,
        storage_path: str,
        filename: str,
        extension: str,
    ) -> None:
        """Write a parse-pending Firestore record so the file shows up in the user's library."""
        from db.firestore import set_document

        now = datetime.now(UTC)
        record = {
            "userId": firebase_uid,
            "skillId": "",
            "gsUrl": gs_url,
            "storagePath": storage_path,
            "originalFilename": filename,
            "sourceFormat": extension.lstrip("."),
            "folderId": "channel",
            "blocks": [],
            "blocksCount": 0,
            "status": "pending",
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "uploadedVia": "channel",
        }
        set_document("parsed_documents", doc_id, record)

    @classmethod
    async def _trigger_parse(cls, doc_id: str) -> None:
        """Best-effort kick to AILANG Parse — failure does not block ingest.

        TODO(channels M3 / v6.2.0 event-driven-skills): wire real
        Pub/Sub publish to `parse-requests` for immediate processing.
        Until then, channel-uploaded files stay in `parseStatus=pending`
        unless a background worker exists in this deployment. Acceptable
        for M1 framework; the first real consumer (Email adapter, M3)
        is the right time to upgrade.
        """
        logger.debug("parse trigger queued for doc_id=%s (handled by background worker)", doc_id)


__all__ = [
    "Attachment",
    "AttachmentPipeline",
    "AttachmentTooLargeError",
    "AttachmentUnsupportedTypeError",
]
