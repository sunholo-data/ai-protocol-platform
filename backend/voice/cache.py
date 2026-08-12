"""Content-hash TTS cache for the voice provider abstraction.

Goal: a message read aloud twice (user replays it later) costs zero
provider dollars on the second listen.

Key: `sha256(provider + voice + lang + rate + text)`. Any change to any
input invalidates the cache. Object path: `{hash[:2]}/{hash}.{ext}` -
two-character prefix sharding keeps GCS object listings small if we ever
need to inspect the bucket.

Mime is stored in object metadata so cache hits can return the right
Content-Type without inferring from the extension.

The GCS client is sync. We wrap calls in `asyncio.to_thread` so the
FastAPI request handler stays non-blocking, matching the pattern in
`backend/tools/documents/`.

Security: the cache bucket is read/written by the backend SA only. Audio
is served to the user via the authenticated /api/voice route as bytes -
NEVER via a public GCS URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass

from google.api_core.exceptions import NotFound
from google.cloud import storage

logger = logging.getLogger(__name__)


# Extension mapping. Add new entries as providers return new MIME types.
_MIME_TO_EXT = {
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/ogg;codecs=opus": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


@dataclass(frozen=True)
class CacheKey:
    """Components that fully determine a cached audio object.

    Any change to any field produces a different hash. Don't add fields
    here without considering whether existing cached audio should still
    be valid (it won't be).
    """

    provider: str
    voice: str
    lang: str
    rate: float
    text: str

    def hash(self) -> str:
        """Stable sha256 over the components. Hex digest."""
        # Pipe-separated; the components themselves don't contain pipes
        # under any current provider's voice naming scheme.
        payload = "|".join(
            (
                self.provider,
                self.voice,
                self.lang,
                f"{self.rate:.4f}",
                self.text,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TTSCache:
    """GCS-backed content-hash cache for TTS audio.

    Construct once per process (the GCS client is thread-safe). Pass the
    bucket name in via env var so different envs can point at different
    buckets without code changes.
    """

    def __init__(self, bucket_name: str, *, client: storage.Client | None = None):
        self.bucket_name = bucket_name
        # The client is created lazily on first call, so tests that don't
        # exercise the wire never touch ADC.
        self._client = client
        self._bucket: storage.Bucket | None = None

    @classmethod
    def from_env(cls) -> TTSCache | None:
        """Build from VOICE_TTS_CACHE_BUCKET, or None if unset.

        Returning None lets callers fall back to "no cache" (pure
        synthesize) in dev without a bucket. The route layer treats
        None-cache as miss-every-time + skip-write.
        """
        bucket = os.getenv("VOICE_TTS_CACHE_BUCKET")
        if not bucket:
            logger.info("VOICE_TTS_CACHE_BUCKET unset; TTS cache disabled")
            return None
        return cls(bucket)

    async def lookup(self, key: CacheKey) -> tuple[bytes, str] | None:
        """Fetch cached audio if present.

        Returns:
            `(audio_bytes, mime)` on hit, `None` on miss.
        """
        return await asyncio.to_thread(self._lookup_sync, key)

    async def write(self, key: CacheKey, audio: bytes, mime: str) -> None:
        """Persist newly-synthesized audio.

        Best-effort: if GCS is unreachable, log + continue. A failed cache
        write must not break the user-facing path.
        """
        try:
            await asyncio.to_thread(self._write_sync, key, audio, mime)
        except Exception as exc:
            logger.warning("TTS cache write failed (continuing without cache): %s", exc)

    # --- sync helpers (run inside asyncio.to_thread) ---

    def _lookup_sync(self, key: CacheKey) -> tuple[bytes, str] | None:
        blob = self._blob_for(key)
        try:
            audio = blob.download_as_bytes()
        except NotFound:
            return None
        # blob.metadata can be None if we never wrote a mime tag.
        mime = (blob.metadata or {}).get("mime", "audio/mpeg")
        return audio, mime

    def _write_sync(self, key: CacheKey, audio: bytes, mime: str) -> None:
        blob = self._blob_for(key)
        blob.metadata = {"mime": mime}
        blob.upload_from_string(audio, content_type=mime)

    def _blob_for(self, key: CacheKey) -> storage.Blob:
        h = key.hash()
        # We hash *before* writing, so both lookup and write hit the same
        # path. Extension is cosmetic; mime is read from object metadata on
        # a hit. Default to mp3 (our only current provider output).
        ext = _MIME_TO_EXT.get("audio/mpeg", "bin")
        path = f"{h[:2]}/{h}.{ext}"
        return self._lazy_bucket().blob(path)

    def _lazy_bucket(self) -> storage.Bucket:
        if self._bucket is None:
            if self._client is None:
                self._client = storage.Client()
            self._bucket = self._client.bucket(self.bucket_name)
        return self._bucket
