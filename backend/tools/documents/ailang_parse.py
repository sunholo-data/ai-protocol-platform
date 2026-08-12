"""AILANG Parse client — deterministic Office/structured-text extraction.

Routes supported formats (docx, pptx, xlsx, odt, odp, ods, epub, eml, mbox,
html, md, csv) through the AILANG Parse API which performs deterministic
XML-level parsing in <1s per file — no LLM tokens consumed.

Unsupported formats (PDF, images) return None; the caller should fall back
to Gemini multimodal extraction.

Output formats:
  "blocks"           — List[dict] (ailang_parse Block objects as dicts), default
  "markdown"         — str (server-rendered markdown + metadata preamble)
  "markdown+metadata"— str (markdown with YAML frontmatter, verbose)
  "a2ui"             — str (JSON component tree for A2UIViewer)
"""

from __future__ import annotations

import asyncio
import dataclasses as _dc
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from google.cloud import storage

log = logging.getLogger(__name__)

try:
    from ailang_parse import AuthError, DocParse, DocParseError, QuotaError
    from ailang_parse.types import ParseResult

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SDK_AVAILABLE = False
    log.warning("ailang-parse SDK not installed — AILANG extraction disabled")


# --- Static extension set, refreshed from live API on first client init ---

DETERMINISTIC_EXTENSIONS = {
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
}

# All extensions AILANG Parse can handle — includes non-deterministic (PDF,
# images) where the SDK runs AI extraction under the hood. Used by
# parse_gcs_file to decide whether to attempt a parse at all. Bug fix
# 2026-06-11: the prior gate was DETERMINISTIC_EXTENSIONS, which made every
# PDF return None → status="pending_ai_extraction" with empty blocks → no
# document content ever reached the agent or workbench. PDFs are core to
# the ONE demo (5 example PPAs, all PDFs).
PARSEABLE_EXTENSIONS = set(DETERMINISTIC_EXTENSIONS) | {".pdf", ".jpg", ".jpeg", ".png", ".tex"}

_formats_refreshed = False


def _refresh_deterministic_extensions(client: DocParse) -> None:
    global DETERMINISTIC_EXTENSIONS, PARSEABLE_EXTENSIONS, _formats_refreshed
    if _formats_refreshed:
        return
    try:
        fmts = client.formats()
        live_det: set[str] = set()
        live_all: set[str] = set()
        for ext in fmts.parse:
            ext_dot = ext if ext.startswith(".") else f".{ext}"
            live_all.add(ext_dot.lower())
            if fmts.is_deterministic(ext):
                live_det.add(ext_dot.lower())
        if live_det:
            DETERMINISTIC_EXTENSIONS = live_det
            if ".html" in DETERMINISTIC_EXTENSIONS:
                DETERMINISTIC_EXTENSIONS.add(".htm")
        if live_all:
            PARSEABLE_EXTENSIONS = live_all
            if ".html" in PARSEABLE_EXTENSIONS:
                PARSEABLE_EXTENSIONS.add(".htm")
            if ".jpg" in PARSEABLE_EXTENSIONS:
                PARSEABLE_EXTENSIONS.add(".jpeg")
        _formats_refreshed = True
    except Exception as exc:
        log.debug("AILANG Parse: could not refresh formats: %s", exc)
        _formats_refreshed = True


# --- Simple TTL cache (replaces v5 tool_cache) ---

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = Lock()
_CACHE_TTL = 3600  # 1 hour


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del _cache[key]
            return None
        return value


def _cache_set(key: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


# --- ParseOutcome ---


@dataclass
class ParseOutcome:
    """Result of an AILANG Parse attempt.

    Exactly one of content or error is set:
      content: parsed content — str for markdown modes, list[dict] for blocks/a2ui
      error:   human-readable failure message to surface to the LLM
    """

    content: str | list | None = None
    output_format: str = "blocks"
    error: str | None = None
    error_code: str | None = None  # "auth" | "quota" | "api" | "download" | "empty" | "unknown"

    @property
    def ok(self) -> bool:
        return self.content is not None and self.error is None

    @property
    def markdown(self) -> str | None:
        if isinstance(self.content, str):
            return self.content
        return None

    @property
    def blocks(self) -> list | None:
        if isinstance(self.content, list):
            return self.content
        return None


# --- Client singleton ---

_client_singleton: DocParse | None = None


def _get_client() -> DocParse | None:
    global _client_singleton
    if not _SDK_AVAILABLE:
        return None
    if _client_singleton is not None:
        return _client_singleton
    api_key = os.environ.get("DOCPARSE_API_KEY", "").strip()
    if not api_key:
        log.info("DOCPARSE_API_KEY not set — AILANG Parse extraction disabled")
        return None
    _client_singleton = DocParse(api_key=api_key, timeout=60)
    log.info("AILANG Parse client initialised")
    _refresh_deterministic_extensions(_client_singleton)
    return _client_singleton


# --- Public helpers ---


def is_supported(gs_url: str) -> bool:
    """Return True if the file extension is handled deterministically by AILANG Parse.

    Keeps the historical "deterministic-only" semantics for callers (e.g.
    org_documents tool routing) that specifically want to know "can this be
    parsed cheaply, with zero AI credits". For "can this be parsed at all"
    use ``is_parseable`` — that covers PDFs and images via AILANG's
    AI extraction path.
    """
    if not gs_url:
        return False
    ext = PurePosixPath(urlparse(gs_url).path).suffix.lower()
    return ext in DETERMINISTIC_EXTENSIONS


def is_parseable(gs_url: str) -> bool:
    """Return True if AILANG Parse can produce a Block ADT for this file.

    Wider net than ``is_supported`` — includes non-deterministic formats
    (PDF, images, .tex) where the SDK drives AI extraction under the hood.
    Slower (~5-30s for a 30-page PDF) and burns AI credits, but a real
    parse with real blocks. Used by ``parse_gcs_file`` to decide whether
    to attempt the parse at all.
    """
    if not gs_url:
        return False
    ext = PurePosixPath(urlparse(gs_url).path).suffix.lower()
    return ext in PARSEABLE_EXTENSIONS


# --- GCS helpers ---


def _generate_signed_url(gs_url: str) -> str | None:
    """Generate a 15-min signed HTTPS URL for a GCS object.

    Tries direct signing first (works on Cloud Run with SA credentials),
    then falls back to SA impersonation (works locally with IAM grant).
    """
    import datetime

    if not gs_url.startswith("gs://"):
        return None
    without_scheme = gs_url[len("gs://") :]
    bucket_name, _, blob_path = without_scheme.partition("/")
    if not bucket_name or not blob_path:
        return None

    expiration = datetime.timedelta(minutes=15)

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.generate_signed_url(expiration=expiration, method="GET")
    except Exception as exc:
        log.debug("AILANG Parse: direct signing failed for %s: %s", gs_url, exc)

    try:
        from google.auth import default, impersonated_credentials

        source_credentials, project = default()
        sa_email = os.environ.get(
            "AILANG_SIGN_SA",
            f"sa-emissary@{project}.iam.gserviceaccount.com",
        )
        target_credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=sa_email,
            target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
            lifetime=300,
        )
        client = storage.Client(project=project, credentials=target_credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        signed = blob.generate_signed_url(expiration=expiration, method="GET")
        log.info("AILANG Parse: signed URL via SA impersonation for %s", gs_url)
        return signed
    except Exception as exc:
        log.debug("AILANG Parse: SA impersonation signing also failed: %s", exc)
        return None


def _download_gcs_to_tempfile(gs_url: str) -> str | None:
    """Download a gs:// URI to a local temp file. Returns temp path or None."""
    if not gs_url.startswith("gs://"):
        return None
    without_scheme = gs_url[len("gs://") :]
    bucket_name, _, blob_path = without_scheme.partition("/")
    if not bucket_name or not blob_path:
        return None
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    # Prefer user-facing filename from blob metadata over GCS object basename
    original_name: str | None = None
    try:
        blob.reload()
        meta = blob.metadata or {}
        for key in ("originalName", "original_name", "filename", "name"):
            if meta.get(key):
                original_name = meta[key]
                break
    except Exception as exc:
        log.debug("AILANG Parse: could not reload blob metadata: %s", exc)
    if not original_name:
        original_name = PurePosixPath(blob_path).name or "document"
    original_name = original_name.replace("/", "_").replace("\\", "_").strip() or "document"
    tmp_dir = tempfile.mkdtemp(prefix="ailang_parse_")
    tmp_path = os.path.join(tmp_dir, original_name)
    blob.download_to_filename(tmp_path)
    return tmp_path


# --- Sync parse calls (run inside asyncio.to_thread) ---


def _flatten_block(d: dict) -> dict:
    """Recursively fix nested arrays that Firestore rejects.

    Firestore does not allow arrays whose elements are themselves arrays.
    Block.rows is List[List[Cell]] — wrap each inner row list in a dict.
    Block.children is List[Block] — recurse so any nested rows are fixed too.
    """
    if d.get("rows"):
        d["rows"] = [{"cells": row} for row in d["rows"]]
    if d.get("children"):
        d["children"] = [_flatten_block(c) for c in d["children"]]
    return d


def _extract_content(result: ParseResult, output_format: str) -> str | list | None:
    """Extract content from ParseResult based on requested output format."""
    if output_format in ("markdown", "markdown+metadata"):
        return result.markdown or None
    if output_format == "blocks":
        if not result.blocks:
            return None
        return [_flatten_block(_dc.asdict(b)) for b in result.blocks]
    return result.markdown or None


def _make_error_outcome(exc: Exception, output_format: str) -> ParseOutcome:
    if isinstance(exc, AuthError):
        return ParseOutcome(
            error=(f"AILANG Parse rejected the API key: {exc}. Action: rotate DOCPARSE_API_KEY in Secret Manager."),
            error_code="auth",
            output_format=output_format,
        )
    if isinstance(exc, QuotaError):
        return ParseOutcome(
            error=f"AILANG Parse quota exceeded: {exc}. Monthly limit reached.",
            error_code="quota",
            output_format=output_format,
        )
    if isinstance(exc, DocParseError):
        return ParseOutcome(
            error=f"AILANG Parse API error: {exc}.",
            error_code="api",
            output_format=output_format,
        )
    return ParseOutcome(
        error=f"AILANG Parse unexpected error: {type(exc).__name__}: {exc}.",
        error_code="unknown",
        output_format=output_format,
    )


def _parse_url_sync(signed_url: str, output_format: str) -> ParseOutcome:
    client = _get_client()
    if client is None:
        return ParseOutcome(
            error="AILANG Parse client not initialised.", error_code="auth", output_format=output_format
        )
    try:
        result: ParseResult = client.parse_url(signed_url, output_format=output_format)
    except Exception as exc:
        return _make_error_outcome(exc, output_format)
    content = _extract_content(result, output_format)
    if not content:
        return ParseOutcome(
            error="AILANG Parse returned empty content.", error_code="empty", output_format=output_format
        )
    return ParseOutcome(content=content, output_format=output_format)


def _parse_file_sync(tmp_path: str, output_format: str) -> ParseOutcome:
    client = _get_client()
    if client is None:
        return ParseOutcome(
            error="AILANG Parse client not initialised.", error_code="auth", output_format=output_format
        )
    try:
        result: ParseResult = client.parse_file(tmp_path, output_format=output_format)
    except Exception as exc:
        return _make_error_outcome(exc, output_format)
    content = _extract_content(result, output_format)
    if not content:
        return ParseOutcome(
            error="AILANG Parse returned empty content.", error_code="empty", output_format=output_format
        )
    return ParseOutcome(content=content, output_format=output_format)


# --- Public async API ---


async def parse_gcs_file(gs_url: str, output_format: str = "blocks") -> ParseOutcome | None:
    """Parse an Office/text file on GCS via AILANG Parse.

    Returns:
      ParseOutcome(content=...) on success
      ParseOutcome(error=...) when AILANG accepted the format but failed
      None when the file extension is not supported (caller should use Gemini fallback)
    """
    if not is_parseable(gs_url):
        ext = PurePosixPath(urlparse(gs_url).path).suffix.lower()
        log.info("AILANG Parse: skipping %s (extension %r not parseable)", gs_url, ext)
        return None
    if _get_client() is None:
        log.info("AILANG Parse: client disabled, skipping %s", gs_url)
        return None

    cache_key = f"ailang_parse:{output_format}:{gs_url}"
    cached = _cache_get(cache_key)
    if cached is not None:
        log.info("AILANG Parse cache hit: %s", gs_url)
        return ParseOutcome(content=cached, output_format=output_format)

    # Strategy 1: signed URL (works on Cloud Run, no download needed)
    signed_url = await asyncio.to_thread(_generate_signed_url, gs_url)
    if signed_url:
        log.info("AILANG Parse: parsing via signed URL for %s", gs_url)
        outcome = await asyncio.to_thread(_parse_url_sync, signed_url, output_format)
        if outcome.ok:
            _cache_set(cache_key, outcome.content)
            return outcome
        # Auth/quota errors are definitive — the download path will fail the same way.
        if outcome.error_code in ("auth", "quota"):
            return outcome
        # api/unknown errors from parse_url may be server-side URL-handling bugs;
        # fall through to the download strategy rather than giving up immediately.
        log.warning(
            "AILANG Parse URL path failed for %s: code=%s error=%s — falling back to download",
            gs_url,
            outcome.error_code,
            outcome.error,
        )

    # Strategy 2: download + upload (local dev, or when signed URL parse fails)
    log.info("AILANG Parse: signed URL unavailable for %s, falling back to download", gs_url)
    tmp_path: str | None = None
    try:
        tmp_path = await asyncio.to_thread(_download_gcs_to_tempfile, gs_url)
        if tmp_path is None:
            return ParseOutcome(
                error=f"Could not download {gs_url} from GCS.",
                error_code="download",
                output_format=output_format,
            )
        outcome = await asyncio.to_thread(_parse_file_sync, tmp_path, output_format)
        if outcome.ok:
            _cache_set(cache_key, outcome.content)
        return outcome
    except Exception as exc:
        return ParseOutcome(
            error=f"AILANG Parse download/parse pipeline failed: {type(exc).__name__}: {exc}",
            error_code="unknown",
            output_format=output_format,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
