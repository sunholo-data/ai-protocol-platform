"""Bytes of a picture, clip or recording, ready to put in front of a model.

Library documents reach the agent as parsed text (a photo becomes the
description written at import), so a model that can see never got the picture.
This is the one place that reads a media document's ORIGINAL bytes for a model,
with the same access rule as get_document_content, and shrinks an image to what
a model actually uses: at most 1568 px on the long edge (Claude's recommended
ceiling; Gemini tiles larger images anyway), JPEG, EXIF orientation applied.

Used by the document loader (an image the user attached as a tab goes in as
pixels) and by ``look_at_media`` (an image the agent chose to look at).
"""

from __future__ import annotations

import io
import logging
from typing import Any

from tools.documents.media import media_kind

log = logging.getLogger(__name__)

MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85
# Originals larger than this are not read for a model: an image this size is a
# scan or a raw photo, and audio/video above it would not fit an inline request.
MAX_ORIGINAL_BYTES = 20 * 1024 * 1024


class MediaUnavailable(Exception):
    """A media document that cannot be given to a model, with a reason the model can relay."""


def prepare_image_for_model(data: bytes) -> tuple[bytes, str]:
    """``(jpeg_bytes, "image/jpeg")``, at most MAX_IMAGE_EDGE px on the long edge.

    Raises:
        MediaUnavailable: the bytes are not an image Pillow can open.
    """
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            if img.mode not in ("RGB", "L"):
                # Transparency over white, not black, for diagrams and screenshots.
                background = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception as exc:
        raise MediaUnavailable(f"the image could not be read ({exc})") from exc
    return out.getvalue(), "image/jpeg"


def _download(gs_url: str) -> bytes:
    from google.cloud import storage

    bucket_name, _, blob_name = gs_url[len("gs://") :].partition("/")
    if not bucket_name or not blob_name:
        raise MediaUnavailable("its stored original has a malformed address")
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    blob.reload()
    if (blob.size or 0) > MAX_ORIGINAL_BYTES:
        raise MediaUnavailable(f"it is larger than {MAX_ORIGINAL_BYTES // (1024 * 1024)} MB")
    return blob.download_as_bytes()


def media_document_kind(raw: dict[str, Any]) -> str | None:
    """``"image"`` / ``"video"`` / ``"audio"`` for a parsed_documents record, else None."""
    return media_kind(str(raw.get("originalFilename") or "")) or media_kind(str(raw.get("sourceFormat") or ""))


def read_document_media(raw: dict[str, Any], doc_id: str, caller_uid: str | None) -> tuple[str, bytes, str]:
    """``(kind, data, mime)`` of a media document the caller may read.

    Images come back resized as JPEG; audio and video as stored. Synchronous
    (Firestore record already in hand, one GCS read): call it in a thread.

    Raises:
        DocumentAccessError: the caller may not read this document.
        MediaUnavailable: it is not media, has no stored original, or is too large.
    """
    from tools.documents.context import assert_document_readable
    from tools.documents.media import MEDIA_TYPES

    assert_document_readable(raw, caller_uid, doc_id)
    kind = media_document_kind(raw)
    if kind is None:
        raise MediaUnavailable("it is not a picture, video or audio file")
    gs_url = str(raw.get("sourceUrl") or "")
    if not gs_url.startswith("gs://"):
        raise MediaUnavailable("its original file is not stored")
    data = _download(gs_url)
    if not data:
        raise MediaUnavailable("its stored file is empty")
    if kind == "image":
        return (kind, *prepare_image_for_model(data))
    filename = str(raw.get("originalFilename") or "")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return kind, data, MEDIA_TYPES.get(ext, "application/octet-stream")
