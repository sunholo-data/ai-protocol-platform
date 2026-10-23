"""MCP image blocks become authenticated artefact references, before the offload.

An MCP tool that renders something — `browser_screenshot`, `browser_pdf` —
returns an `ImageContent` block, and ADK's `McpTool._run_async_impl` does
`response.model_dump(exclude_none=True, mode="json")`, so the base64 payload
becomes the tool result verbatim. Measured on test 2026-09-04: 22,927 chars of
base64 on a minimal page, reaching the browser and rendered by nothing; and on
a content-heavy news page a result over the 50K threshold, offloaded to an
artifact before any result→A2UI transform could see it. Either way the agent
narrates having taken a screenshot nobody is shown.

This module replaces each image block with a small typed reference and puts the
bytes in the private artefact bucket, where they are reachable only through
`GET /api/browser/artefacts/{id}` and its uid re-check. Three things fall out of
that one decision:

  * the result stays a few hundred bytes, so it is never offloaded — the size
    question does not arise on any page;
  * no page imagery enters model context, the session store, or the transcript;
  * a screenshot of a customer's logged-in page is a derived artefact of
    confidential content, and CLAUDE.md's security rule names exactly that. It
    never reaches an unauthenticated URL because it never leaves this process
    as bytes.

**Not in the browser service.** The obvious home is `services/browser/gateway.py`,
which sees the block first — but that service runs as a deliberately powerless
SA (`roles = []`) with no bucket permissions at all, which is load-bearing for
the design's security score. It keeps returning bytes over its HTTP response;
the backend, which already holds artefact-write permission, does the conversion.

Design: `docs/design/v6.39.0/obscura-browser.md`, Phase 2 as amended 2026-09-04.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Artefacts are addressed through the backend route, never a bucket URL.
ARTEFACT_URL_PREFIX = "/api/browser/artefacts"

# ADK routes a `user:`-prefixed filename to `{app}/{user_id}/user/{filename}`
# instead of the session-scoped path (GcsArtifactService._get_blob_prefix). Two
# reasons that is the right namespace here, and the second is the load-bearing
# one:
#
#   * a screenshot outlives the turn that took it — a user reopening a chat
#     should still see it, and session-scoping would strand it;
#   * the serving route then needs NO session id, so its lookup is keyed
#     entirely on identity taken from the caller's token. The prefix is added
#     server-side on both write and read, so a caller cannot name a filename
#     outside their own namespace. Cross-user access is structurally impossible
#     rather than merely refused.
ARTEFACT_NAMESPACE = "user:"

# MCP content blocks we convert. Anything else passes through untouched.
_MEDIA_BLOCK_TYPES = {"image"}

# Where the browser pane records the page it is on. Read here so a screenshot
# can be NAMED after the page it shows. Owned by adk.a2ui_browser_render; this
# is a deliberate one-way read of a published key, not shared mutable state.
_PANE_URL_STATE_KEY = "browser_pane_url"

_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _probe_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Best-effort pixel dimensions, for the pane's layout.

    Never raises: a screenshot that cannot be measured is still a screenshot
    worth showing, and losing the whole conversion over an unreadable header
    would trade a real feature for a cosmetic one.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception:  # pragma: no cover - defensive; Pillow handles the real formats
        return None, None


def _page_label(tool_context: ToolContext) -> tuple[str, str]:
    """``(host_for_the_filename, full_url)`` for the page currently open.

    Screenshots used to be named ``browser_screenshot_<invocation>_<digest>``,
    which is an opaque id in the one place CLAUDE.md #9 says never to put one:
    a surface an AI reads. `load_artifacts` returned a list of those, the model
    could not tell which was which, and on 2026-09-05 — after a screenshot
    timed out — it reached for an older artefact and answered about a DIFFERENT
    website. A name it can read is the fix.

    Best-effort: an unnamed page still gets a usable artefact, just a duller
    name. Never raises.
    """
    try:
        state = getattr(tool_context, "state", None)
        raw = state.get(_PANE_URL_STATE_KEY) if state is not None else None
        # A test double's `state` is a mock whose .get() returns a mock, and a
        # session that has not navigated yet has nothing here. Both are "no
        # page", not an error — the whole function is decoration on the name.
        url = raw if isinstance(raw, str) else ""
        if not url:
            return "page", ""
        host = urlparse(url).hostname or "page"
        if host.startswith("www."):
            host = host[4:]
        return _UNSAFE_IN_FILENAME.sub("-", host) or "page", url
    except Exception:  # pragma: no cover - defensive
        return "page", ""


def _extension_for(mime: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "application/pdf": "pdf",
    }.get(mime, "bin")


async def _save_one(
    block: dict[str, Any],
    tool_name: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Save one media block's bytes; return the block that replaces it."""
    raw_b64 = block.get("data") or ""
    mime = block.get("mimeType") or "application/octet-stream"

    try:
        data = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.warning("media_results: %s returned an undecodable %s block: %s", tool_name, mime, exc)
        return _error_block(f"{tool_name} returned an image that could not be decoded ({exc}).")

    # The digest keys the artefact, so a genuinely repeated screenshot dedupes
    # while two different ones stay distinguishable — the same reasoning as the
    # offload path's filename, and for the same transcript-readability reason.
    digest = hashlib.sha256(data).hexdigest()[:8]
    host, page_url = _page_label(tool_context)
    taken_at = datetime.now(UTC)
    stamp = taken_at.strftime("%Y%m%d-%H%M%S")
    # Readable by design: WHAT it is, WHICH site, WHEN, and a digest to keep it
    # unique. `browser_screenshot_energiwatch.dk_20260905-070538_a1b2c3d4.png`
    # tells a model everything it needs to pick the right one; the old
    # `browser_screenshot_e-<uuid>_<digest>.png` told it nothing.
    artefact_id = f"{tool_name}_{host}_{stamp}_{digest}.{_extension_for(mime)}"
    # Stored user-namespaced; the URL carries the bare id and the route re-adds
    # the prefix, so the namespace is never caller-controlled.
    stored_name = f"{ARTEFACT_NAMESPACE}{artefact_id}"

    from google.genai import types as genai_types

    try:
        await tool_context.save_artifact(
            filename=stored_name,
            artifact=genai_types.Part.from_bytes(data=data, mime_type=mime),
        )
    except Exception as exc:
        # Never fall back to returning the bytes: that is the leak this module
        # exists to close, and a degraded turn is better than a leaked one.
        logger.warning("media_results: save_artifact failed for %s: %s", stored_name, exc)
        return _error_block(f"{tool_name} captured an image but it could not be stored ({exc}).")

    width, height = _probe_dimensions(data)
    reference = {
        "artefact_id": artefact_id,
        "url": f"{ARTEFACT_URL_PREFIX}/{artefact_id}",
        "mime": mime,
        "width": width,
        "height": height,
        "bytes": len(data),
        # The model reads this envelope. Telling it WHICH page and WHEN is what
        # stops it citing a screenshot of somewhere else later in the session.
        "page_url": page_url,
        "taken_at": taken_at.isoformat(timespec="seconds"),
    }
    logger.info("media_results: %s -> %s (%d bytes)", tool_name, artefact_id, len(data))
    return {"type": "text", "text": json.dumps(reference)}


def _error_block(message: str) -> dict[str, Any]:
    """A visible failure, never a silent one (CLAUDE.md #8)."""
    return {"type": "text", "text": json.dumps({"error": message})}


async def _convert_media_results(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> Any:
    """after_tool_callback — swap media blocks for references. MUST run before
    ``_handle_large_output``; see ``adk.mcp_observability.chain_mutators``.

    Returns ``None`` when there is nothing to convert, so ADK's "is not None
    means use this" contract leaves the response untouched.
    """
    if not isinstance(tool_response, dict):
        return None

    content = tool_response.get("content")
    if not isinstance(content, list):
        return None

    tool_name = getattr(tool, "name", "tool")
    converted: list[Any] = []
    changed = False

    for block in content:
        if isinstance(block, dict) and block.get("type") in _MEDIA_BLOCK_TYPES and block.get("data"):
            converted.append(await _save_one(block, tool_name, tool_context))
            changed = True
        else:
            converted.append(block)

    if not changed:
        return None

    return {**tool_response, "content": converted}


__all__ = ["ARTEFACT_NAMESPACE", "ARTEFACT_URL_PREFIX", "_convert_media_results"]
