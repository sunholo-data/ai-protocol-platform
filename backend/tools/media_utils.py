"""Media utilities — GET /api/media/pdf-info.

Lightweight PDF page-count endpoint. Uses an HTTP Range request to read
the first 2 KB of a PDF (its header/trailer) without downloading the
full file. Ported from v5 backend/tools/pdf_utils.py.

Only GCS URLs are accepted (storage.googleapis.com /
storage.cloud.google.com) to prevent this endpoint from being used as
an open HTTP proxy.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import User, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

_ALLOWED_GCS_HOSTS = {
    "storage.googleapis.com",
    "storage.cloud.google.com",
}


class PDFInfoResponse(BaseModel):
    filename: str
    pages: int | None


def _is_allowed_url(url: str) -> bool:
    """Accept only https:// GCS URLs to prevent open-proxy abuse."""
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in _ALLOWED_GCS_HOSTS
    except Exception:
        return False


def _extract_filename(url: str) -> str:
    """Extract filename from the last path segment of a URL."""
    try:
        path = urlparse(url).path
        return path.rstrip("/").split("/")[-1] or "document.pdf"
    except Exception:
        return "document.pdf"


def count_pdf_pages_from_url(url: str) -> int | None:
    """Read the first 2 KB of a PDF via HTTP Range to extract page count.

    Looks for /N or /Count trailer entries — present in most PDFs.
    Returns None if the page count cannot be determined.
    """
    try:
        headers = {"Range": "bytes=0-2047"}
        response = httpx.get(url, headers=headers, timeout=5.0, follow_redirects=True)
        if response.status_code not in (200, 206):
            return None
        header_text = response.content.decode("latin-1", errors="ignore")
        match = re.search(r"/N\s+(\d+)", header_text)
        if match:
            return int(match.group(1))
        match = re.search(r"/Count\s+(\d+)", header_text)
        if match:
            return int(match.group(1))
        return None
    except Exception as exc:
        log.warning("PDF page count failed for %s: %s", url, exc)
        return None


@router.get("/pdf-info", response_model=PDFInfoResponse)
async def get_pdf_info(
    url: str = Query(..., description="GCS PDF URL to inspect"),
    _user: User = Depends(get_current_user),  # noqa: B008
) -> PDFInfoResponse:
    """Return filename and page count for a GCS-hosted PDF.

    Only GCS URLs accepted. Returns pages=null if count cannot be read
    from the PDF header — never returns 500 for unreadable PDFs.
    """
    if not _is_allowed_url(url):
        raise HTTPException(
            status_code=400,
            detail="Only GCS URLs (storage.googleapis.com) are accepted.",
        )

    filename = _extract_filename(url)
    pages = count_pdf_pages_from_url(url)
    return PDFInfoResponse(filename=filename, pages=pages)
