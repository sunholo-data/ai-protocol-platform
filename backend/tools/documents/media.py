"""Video and audio in the library: one table of formats, used everywhere.

A video or audio file is kept like any document (its bytes in the per-client
bucket, a ``parsed_documents`` record, project membership, GitHub sync) so the
agent can find it by name and path and show it with ``show_media``. It has no
text to parse: it is stored as "parsed" with one short block naming it, so
nothing waits on a parser that will never answer. A transcript as its searchable
text is a later step (a model call per file).
"""

from __future__ import annotations

from pathlib import PurePosixPath

VIDEO_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
AUDIO_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
MEDIA_TYPES: dict[str, str] = {**VIDEO_TYPES, **AUDIO_TYPES}
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})


def _ext(name: str) -> str:
    return PurePosixPath(name or "").suffix.lower()


def media_kind(name: str) -> str | None:
    """``"image"``, ``"video"``, ``"audio"`` or None, from a filename, path, url or format."""
    ext = _ext(name.split("?", 1)[0]) or ("." + name.lower().lstrip(".") if name and "." not in name else "")
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_TYPES:
        return "video"
    if ext in AUDIO_TYPES:
        return "audio"
    return None


def is_av(name: str) -> bool:
    """True for a video or audio file (not an image)."""
    return _ext(name) in MEDIA_TYPES


def media_blocks(filename: str) -> list[dict[str, str]]:
    """The one block a video or audio document holds instead of parsed text."""
    kind = media_kind(filename) or "media"
    return [
        {
            "type": "paragraph",
            "text": (
                f"{kind.capitalize()} file {filename}. It has no text to read; show it to the user with show_media."
            ),
            "block_id": "p0",
        }
    ]
