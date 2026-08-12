"""Render a document's first page (or an image) to a PNG for clean previews.

Standardised thumbnail rendering used by the bucket/object and document
thumbnail routes (v6.6.0). An `<img>` of this PNG looks far better than an
`<iframe>` of a raw PDF (which shows the browser's PDF-viewer chrome) and gives
every document surface — example cards, file lists, hover previews — one
consistent look.

Supports PDFs (first page via pypdfium2) and raster images (resized via Pillow).
Deterministic, no LLM. A small shared LRU keeps repeat views instant.
"""

from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict

# Clamp the render width so a caller can't request an enormous raster.
_MIN_WIDTH = 64
_MAX_WIDTH = 1600

# Serialise heavy renders. The sidebar fires one thumbnail request per library
# doc, so a PPA library renders ~5 large PDFs AT ONCE — pypdfium + PIL on that
# many big PDFs concurrently can exhaust the container's memory. A native OOM is
# NOT catchable by `except`: the process dies (`exit(1)`), every in-flight
# request 502s — including the chat SSE stream, which the user sees as a SILENT
# FAILURE (the stream just cuts). Bounding concurrency caps peak memory; renders
# are cached so the brief queueing is one-off. Tunable via env for headroom.
_RENDER_SEM = threading.Semaphore(max(1, int(os.environ.get("THUMBNAIL_MAX_CONCURRENCY", "2") or "2")))
# PDFium is NOT thread-safe — upstream is explicit that all pdfium calls must
# be serialised (parallelise with processes, never threads). The semaphore
# above still allowed 2 concurrent pdfium renders, and two threads inside the
# native library can corrupt state and kill the interpreter with a fatal
# `exit(1)` — no Python exception to catch. That is what actually took the
# test-env sidecar down on 2026-07-21 (5-doc picker burst → container exit(1)
# → every in-flight request incl. chat SSE 502'd; issue #13): the semaphore
# capped memory but not native-concurrency. ALL pdfium work goes under this
# hard mutex; Pillow-only image renders stay governed by the semaphore alone.
_PDFIUM_LOCK = threading.Lock()
# Hard ceiling on the rendered raster's larger dimension — a pathological page
# aspect ratio (or a tiny page rendered at a big scale) could otherwise blow up
# height even with width clamped.
_MAX_RENDER_DIM = 2400

# Raster image formats we can thumbnail directly (Pillow resize).
IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"})
# All formats the thumbnailer can handle.
THUMBNAILABLE_EXTS = frozenset({"pdf"}) | IMAGE_EXTS


def normalise_ext(name_or_ext: str) -> str:
    """Return the lowercase extension (no dot) from a filename, format, or ext."""
    tail = name_or_ext.rsplit(".", 1)[-1] if "." in name_or_ext else name_or_ext
    return tail.lower().lstrip(".")


def is_thumbnailable(name_or_ext: str) -> bool:
    """True if `render_thumbnail_png` can render this filename / format / ext."""
    return normalise_ext(name_or_ext) in THUMBNAILABLE_EXTS


def _clamp_width(target_width: int) -> int:
    return max(_MIN_WIDTH, min(_MAX_WIDTH, int(target_width)))


def render_pdf_first_page_png(pdf_bytes: bytes, target_width: int = 600) -> bytes:
    """Render page 1 of ``pdf_bytes`` to a PNG ~``target_width`` px wide.

    Raises:
        ValueError: If the PDF is empty / unparseable / has no pages.
    """
    import pypdfium2 as pdfium
    from PIL import Image

    width = _clamp_width(target_width)
    # Hard serialise: every pdfium call (open, render, close) under the global
    # lock — see _PDFIUM_LOCK. Concurrent callers queue here; renders are
    # cached, so the wait is one-off per document.
    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(pdf_bytes)
        except Exception as exc:  # pypdfium raises PdfiumError on bad input
            raise ValueError(f"Could not open PDF: {exc}") from exc

        try:
            if len(pdf) == 0:
                raise ValueError("PDF has no pages")
            page = pdf[0]
            pw, ph = page.get_size()
            page_width = pw or width
            scale = width / page_width
            # Guard a pathological aspect ratio / tiny page: keep the larger rendered
            # dimension bounded so a single render can't balloon memory.
            if ph and scale * ph > _MAX_RENDER_DIM:
                scale = _MAX_RENDER_DIM / ph
            bitmap = page.render(scale=scale)
            # .convert() copies into PIL-owned storage — to_pil() alone may
            # share the pdfium bitmap's buffer, which dies with pdf.close().
            pil_image: Image.Image = bitmap.to_pil().convert("RGB")
        finally:
            pdf.close()
    # PNG encode is pure Pillow — safe outside the pdfium lock, keeps the
    # serialised section as short as possible.
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_image_thumbnail_png(image_bytes: bytes, target_width: int = 600) -> bytes:
    """Resize a raster image down to ``target_width`` and re-encode as PNG.

    Only ever scales down (small images are returned at their own size). Alpha
    is flattened onto white so previews look consistent on any surface.

    Raises:
        ValueError: If the bytes aren't a decodable image.
    """
    from PIL import Image, UnidentifiedImageError

    width = _clamp_width(target_width)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Could not open image: {exc}") from exc

    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    if img.width > width:
        height = round(img.height * width / img.width)
        img = img.resize((width, height))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_thumbnail_png(data: bytes, name_or_ext: str, target_width: int = 600) -> bytes:
    """Render a PDF (first page) or image to a PNG thumbnail.

    Args:
        data: The full file bytes.
        name_or_ext: Filename, source format, or bare extension — used to pick
            the renderer.
        target_width: Output width in px (clamped 64-1600).

    Raises:
        ValueError: Unsupported type / undecodable bytes.
    """
    ext = normalise_ext(name_or_ext)
    if ext not in THUMBNAILABLE_EXTS:
        raise ValueError(f"Unsupported thumbnail type: {ext!r}")
    # Cap concurrent heavy renders so a burst of library thumbnails can't OOM
    # (and crash) the whole container. Renders are cached, so callers wait once.
    with _RENDER_SEM:
        if ext == "pdf":
            return render_pdf_first_page_png(data, target_width)
        return render_image_thumbnail_png(data, target_width)


# --- shared LRU cache ---------------------------------------------------------
# Document libraries are static, so keying on a caller-supplied string (which
# encodes bucket/object/doc-id + width) is safe and repeat views are instant.
# Bounded so a large library can't grow the process unbounded.

_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_MAX = 256


def cache_get(key: str) -> bytes | None:
    png = _CACHE.get(key)
    if png is not None:
        _CACHE.move_to_end(key)
    return png


def cache_put(key: str, png: bytes) -> None:
    _CACHE[key] = png
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
