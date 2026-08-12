"""Unit tests for the PDF first-page PNG renderer (v6.6.0)."""

from __future__ import annotations

import io

import pytest

from tools.documents.thumbnail import (
    is_thumbnailable,
    render_image_thumbnail_png,
    render_pdf_first_page_png,
    render_thumbnail_png,
)


def _png_image(w: int = 800, h: int = 600) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "steelblue").save(buf, "PNG")
    return buf.getvalue()


def _one_page_pdf(w: int = 420, h: int = 594) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PDF")
    return buf.getvalue()


class TestRenderFirstPage:
    def test_renders_png_magic_bytes(self):
        png = render_pdf_first_page_png(_one_page_pdf(), target_width=300)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_output_width_matches_target(self):
        from PIL import Image

        png = render_pdf_first_page_png(_one_page_pdf(w=400, h=600), target_width=500)
        img = Image.open(io.BytesIO(png))
        assert img.width == 500
        # Aspect ratio preserved (600/400 * 500 = 750).
        assert abs(img.height - 750) <= 2

    def test_width_is_clamped(self):
        from PIL import Image

        png = render_pdf_first_page_png(_one_page_pdf(), target_width=100_000)
        img = Image.open(io.BytesIO(png))
        assert img.width <= 1600

    def test_bad_bytes_raise_valueerror(self):
        with pytest.raises(ValueError):
            render_pdf_first_page_png(b"not a pdf", target_width=300)


class TestRenderImage:
    def test_resizes_image_to_target_width(self):
        from PIL import Image

        png = render_image_thumbnail_png(_png_image(800, 600), target_width=400)
        img = Image.open(io.BytesIO(png))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert img.width == 400
        assert abs(img.height - 300) <= 2  # aspect preserved

    def test_small_image_not_upscaled(self):
        from PIL import Image

        png = render_image_thumbnail_png(_png_image(120, 90), target_width=600)
        assert Image.open(io.BytesIO(png)).width == 120

    def test_bad_image_bytes_raise(self):
        with pytest.raises(ValueError):
            render_image_thumbnail_png(b"not an image")


class TestDispatcher:
    def test_pdf_by_filename(self):
        assert render_thumbnail_png(_one_page_pdf(), "contract.pdf", 200)[:4] == b"\x89PNG"

    def test_image_by_filename(self):
        assert render_thumbnail_png(_png_image(), "logo.PNG", 200)[:4] == b"\x89PNG"

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            render_thumbnail_png(b"x", "notes.txt", 200)


class TestIsThumbnailable:
    @pytest.mark.parametrize("name", ["a.pdf", "b.PNG", "c.jpg", "d.jpeg", "gs://x/y.webp", "pdf"])
    def test_supported(self, name):
        assert is_thumbnailable(name) is True

    @pytest.mark.parametrize("name", ["notes.txt", "sheet.xlsx", "a.docx", "novalue"])
    def test_unsupported(self, name):
        assert is_thumbnailable(name) is False


class TestConcurrentRenderBurst:
    """Issue #13 regression: a picker/library burst fires ~5 PDF renders at
    once. PDFium is not thread-safe — before the _PDFIUM_LOCK hard mutex, two
    threads inside the native library could kill the interpreter with a fatal
    exit(1) (took the test-env sidecar down 2026-07-21, 502ing every in-flight
    request including chat SSE). This can't deterministically reproduce a
    native crash, but it pins the contract: a 5-way concurrent burst completes
    with every render returning a valid PNG and no cross-thread corruption."""

    def test_five_way_pdf_burst_all_succeed(self):
        import threading

        pdf = _one_page_pdf()
        results: list[bytes | None] = [None] * 5
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                results[i] = render_thumbnail_png(pdf, "contract.pdf", target_width=600)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, f"burst renders raised: {errors}"
        assert all(r is not None and r.startswith(b"\x89PNG") for r in results)

    def test_pdfium_calls_are_serialised(self):
        """The pdfium section must run strictly one-at-a-time even when the
        semaphore admits 2 renders — assert via lock contention."""
        from tools.documents import thumbnail as t

        assert t._PDFIUM_LOCK is not None
        acquired = t._PDFIUM_LOCK.acquire(blocking=False)
        assert acquired, "lock should be free outside a render"
        try:
            import threading

            done = threading.Event()
            blocked: list[bool] = []

            def try_render() -> None:
                # With the lock held by the test, a PDF render must NOT enter
                # pdfium; it should still be waiting when we check.
                blocked.append(True)
                render_pdf_first_page_png(_one_page_pdf(), target_width=200)
                done.set()

            th = threading.Thread(target=try_render, daemon=True)
            th.start()
            assert not done.wait(timeout=0.5), "render entered pdfium despite held lock"
        finally:
            t._PDFIUM_LOCK.release()
        assert done.wait(timeout=10), "render never completed after lock release"
