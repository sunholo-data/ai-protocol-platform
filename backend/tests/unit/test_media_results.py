"""MCP image blocks become authenticated references, ahead of the offload.

Pins the correction recorded in `docs/design/v6.39.0/obscura-browser.md`
(Phase 2, amended 2026-09-04). Measured on a deployed environment the day this
was written: `browser_screenshot` returned 22,927 chars of base64 on a minimal
page — on the wire, in the browser, rendered by nothing — and was offloaded
outright on a content-heavy one. Both failures are the same missing conversion.

The ordering tests are the point of this file. A converter composed AFTER
`_handle_large_output` silently does nothing: the offload has already replaced
the image with a pointer string, there is no error, and nothing is logged. That
failure mode is invisible to every test that does not assert the order itself.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from adk.callbacks import _handle_large_output
from adk.mcp_observability import chain_mutators
from adk.media_results import _convert_media_results

# A 1x1 PNG — the smallest thing that is genuinely a PNG, so the width/height
# probe is exercised rather than stubbed.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_BASE64_ISH = "iVBORw0KGgo"  # PNG magic in base64 — cheap "did bytes leak?" probe


def _mk_tool_context() -> MagicMock:
    ctx = MagicMock()
    ctx.save_artifact = AsyncMock()
    ctx.invocation_id = "e-testinvocation"
    return ctx


def _image_result(png: bytes, mime: str = "image/png") -> dict:
    """The shape ADK produces: `CallToolResult.model_dump(mode="json")`."""
    return {
        "content": [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": mime}],
        "isError": False,
    }


def _tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


# --- the conversion itself ---


async def test_image_block_becomes_a_small_reference():
    ctx = _mk_tool_context()
    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
    )

    rendered = json.dumps(out)
    assert len(rendered) < 2048, "the reference must stay small — that is the whole point"
    assert _BASE64_ISH not in rendered, "image bytes must not survive into the tool result"
    assert "artefact_id" in rendered
    ctx.save_artifact.assert_awaited_once()


async def test_reference_carries_the_fields_the_transform_needs():
    ctx = _mk_tool_context()
    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
    )

    block = out["content"][0]
    assert block["type"] == "text"
    ref = json.loads(block["text"])
    assert ref["mime"] == "image/png"
    assert ref["width"] == 1 and ref["height"] == 1
    assert ref["bytes"] == len(_PNG_1X1)
    assert ref["url"] == f"/api/browser/artefacts/{ref['artefact_id']}"


async def test_a_large_image_still_produces_a_small_result():
    """The real-page case: 200 KB of PNG must never reach the offload."""
    ctx = _mk_tool_context()
    big = _PNG_1X1 + b"\x00" * 200_000
    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(big)
    )

    assert len(str(out)) < 2048
    assert len(str(out)) < 50_000, "must be under the offload threshold, not merely smaller"


async def test_browser_pdf_uses_the_same_path():
    ctx = _mk_tool_context()
    out = await _convert_media_results(
        tool=_tool("browser_pdf"),
        args={},
        tool_context=ctx,
        tool_response=_image_result(_PNG_1X1, mime="application/pdf"),
    )
    assert _BASE64_ISH not in json.dumps(out)
    ctx.save_artifact.assert_awaited_once()


async def test_non_image_results_pass_through_untouched():
    ctx = _mk_tool_context()
    resp = {"content": [{"type": "text", "text": "Navigated to https://example.com"}], "isError": False}
    out = await _convert_media_results(tool=_tool("browser_navigate"), args={}, tool_context=ctx, tool_response=resp)

    assert out is None or out == resp, "declining must not rewrite the response"
    ctx.save_artifact.assert_not_called()


async def test_a_result_that_is_not_a_dict_is_left_alone():
    ctx = _mk_tool_context()
    out = await _convert_media_results(tool=_tool("x"), args={}, tool_context=ctx, tool_response="just a string")
    assert out is None or out == "just a string"
    ctx.save_artifact.assert_not_called()


async def test_a_failed_artifact_save_does_not_lose_the_turn():
    """Never silent (CLAUDE.md #8) — but never a crashed turn either."""
    ctx = _mk_tool_context()
    ctx.save_artifact.side_effect = RuntimeError("bucket unavailable")

    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
    )

    rendered = json.dumps(out) if out is not None else ""
    assert _BASE64_ISH not in rendered, "a failed save must not fall back to leaking the bytes"
    assert "error" in rendered.lower(), "the model must be told the screenshot did not survive"


# --- the ordering, which is the bug ---


async def test_chain_runs_mutators_in_order():
    def first(*, tool, args, tool_context, tool_response):
        return {"seen": [*tool_response.get("seen", []), "first"]}

    def second(*, tool, args, tool_context, tool_response):
        return {"seen": [*tool_response.get("seen", []), "second"]}

    chained = chain_mutators(first, second)
    out = await chained(tool=_tool("x"), args={}, tool_context=_mk_tool_context(), tool_response={"seen": []})
    assert out["seen"] == ["first", "second"], "each mutator must see the previous one's rewrite"


async def test_chain_returns_none_when_nothing_rewrote():
    """ADK reads `is not None` as 'use this' — declining must stay declining."""

    def decline(*, tool, args, tool_context, tool_response):
        return None

    chained = chain_mutators(decline, decline)
    assert await chained(tool=_tool("x"), args={}, tool_context=_mk_tool_context(), tool_response={"a": 1}) is None


async def test_converter_before_offload_preserves_the_image_reference():
    """The correct order: convert, then offload sees a small result and declines."""
    ctx = _mk_tool_context()
    big = _PNG_1X1 + b"\x00" * 200_000
    chained = chain_mutators(_convert_media_results, _handle_large_output)

    out = await chained(tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(big))

    rendered = json.dumps(out) if isinstance(out, (dict, list)) else str(out)
    assert "artefact_id" in rendered, "the reference must survive the offload stage"
    assert "large response saved as artifact" not in rendered, "the offload must not have fired"


async def test_offload_before_converter_loses_the_image():
    """The ordering hazard, pinned on a tool the exemption does not cover.

    Composed the wrong way round, `_handle_large_output` replaces the image with
    a pointer string before the converter sees it — no exception, nothing
    logged, and a screenshot the user is never shown.

    This uses an UNREGISTERED tool name deliberately. `browser_screenshot` is
    now also offload-exempt, because `a2ui_browser_render` names it in
    `tool_names` (that is what registering a mapping does), so for that tool the
    two protections overlap and the wrong order would survive. The hazard is
    still live for any media-returning tool without a mapping — which is what a
    new MCP server's screenshot tool would be on the day it is added.
    """
    ctx = _mk_tool_context()
    big = _PNG_1X1 + b"\x00" * 200_000
    wrong = chain_mutators(_handle_large_output, _convert_media_results)

    out = await wrong(
        tool=_tool("some_unregistered_shot_tool"), args={}, tool_context=ctx, tool_response=_image_result(big)
    )

    rendered = str(out)
    assert "large response saved as artifact" in rendered
    assert "artefact_id" not in rendered, "this is precisely what the wrong order costs"


async def test_browser_screenshot_is_protected_twice_over():
    """Belt and braces, and worth knowing which is which.

    `browser_screenshot` survives the wrong composition order because
    registering its A2UI mapping ALSO marks it offload-exempt. Remove the
    mapping and only the ordering protects it; reorder the chain and only the
    exemption does. This asserts both are currently in force, so losing one is
    a visible change rather than a silent narrowing.
    """
    import adk.a2ui_browser_render  # noqa: F401 — registers the mapping
    from adk.a2ui_result_render import is_render_payload_tool

    assert is_render_payload_tool("browser_screenshot") is True

    ctx = _mk_tool_context()
    big = _PNG_1X1 + b"\x00" * 200_000
    wrong = chain_mutators(_handle_large_output, _convert_media_results)
    out = await wrong(tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(big))

    assert "artefact_id" in str(out), "the exemption should carry it even in the wrong order"


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp"])
async def test_common_image_mimes_are_all_converted(mime: str):
    ctx = _mk_tool_context()
    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1, mime=mime)
    )
    assert _BASE64_ISH not in json.dumps(out)


async def test_artefact_is_stored_user_namespaced_but_the_url_is_not():
    """The namespace is added server-side on write, and again on read.

    A caller who could choose the stored filename could name one outside their
    own namespace; keeping the prefix out of the URL means they cannot. The
    route re-adds it, so the only artefacts reachable are the caller's own.
    """
    ctx = _mk_tool_context()
    out = await _convert_media_results(
        tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
    )

    stored = ctx.save_artifact.await_args.kwargs["filename"]
    ref = json.loads(out["content"][0]["text"])

    assert stored.startswith("user:"), "screenshots are user-scoped, not session-scoped"
    assert not ref["artefact_id"].startswith("user:"), "the namespace must not leak into the URL"
    assert stored == f"user:{ref['artefact_id']}"


class _StatefulCtx:
    """A tool_context whose session state knows which page is open."""

    def __init__(self, url: str | None):
        self.invocation_id = "e-testinvocation"
        self.state = {"browser_pane_url": url} if url else {}
        self.save_artifact = AsyncMock()


class TestArtefactsAreNamedForTheModelNotTheBackend:
    """CLAUDE.md #9 — opaque ids are backend addressing, not a model surface.

    Reported 2026-09-05: a screenshot timed out, and the model then reached for
    an OLDER artefact and answered about a different website. It could not have
    known better — `load_artifacts` returned
    `browser_screenshot_e-<uuid>_<digest>.png` several times over, and the
    artefacts are user-namespaced so every past session's screenshots are in
    that list. The names have to say which site and when.
    """

    async def test_the_name_carries_the_site_and_a_timestamp(self):
        ctx = _StatefulCtx("https://www.example-news.test/section/article12345")
        out = await _convert_media_results(
            tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
        )
        ref = json.loads(out["content"][0]["text"])

        assert "example-news.test" in ref["artefact_id"], "the name must say WHICH site"
        assert "www." not in ref["artefact_id"], "www. is noise in a name"
        import re as _re

        assert _re.search(r"_\d{8}-\d{6}_", ref["artefact_id"]), "the name must say WHEN"

    async def test_the_envelope_tells_the_model_the_page_and_the_time(self):
        url = "https://example.com/some/path"
        ctx = _StatefulCtx(url)
        out = await _convert_media_results(
            tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
        )
        ref = json.loads(out["content"][0]["text"])

        assert ref["page_url"] == url
        assert ref["taken_at"], "a screenshot with no time is one the model cannot order"

    async def test_two_sites_produce_distinguishable_names(self):
        """The actual failure: two screenshots the model could not tell apart."""
        a = await _convert_media_results(
            tool=_tool("browser_screenshot"),
            args={},
            tool_context=_StatefulCtx("https://first-site.example/x"),
            tool_response=_image_result(_PNG_1X1),
        )
        b = await _convert_media_results(
            tool=_tool("browser_screenshot"),
            args={},
            tool_context=_StatefulCtx("https://second-site.example/y"),
            tool_response=_image_result(_PNG_1X1),
        )
        name_a = json.loads(a["content"][0]["text"])["artefact_id"]
        name_b = json.loads(b["content"][0]["text"])["artefact_id"]

        assert name_a != name_b
        assert "first-site" in name_a and "second-site" in name_b

    async def test_a_page_with_no_recorded_url_still_produces_an_artefact(self):
        """Degrade to a duller name, never to a lost screenshot."""
        ctx = _StatefulCtx(None)
        out = await _convert_media_results(
            tool=_tool("browser_screenshot"), args={}, tool_context=ctx, tool_response=_image_result(_PNG_1X1)
        )
        ref = json.loads(out["content"][0]["text"])
        assert ref["artefact_id"].endswith(".png")
        ctx.save_artifact.assert_awaited_once()
