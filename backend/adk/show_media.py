"""``show_media``: put pictures, video and audio the agent can reach in front of the user.

Found live 2026-11-10: asked to show a photo from a project folder of photos, the agent answered
"I can't display images in this chat. My tools only return text". It was right.
Every piece existed on its own — authenticated routes for documents, bucket
objects and browser screenshots, and a workbench that fetches those routes with
the viewer's credentials (``frontend/src/lib/a2uiAuthImages.ts``). Nothing let
the agent say "show this one".

One tool, three kinds of source, every one served by an existing route that
re-checks the VIEWER's access when the bytes are fetched:

* a library or project document (by ref or by path). Images come from
  ``/api/documents/{id}/thumbnail`` (resized), video and audio from
  ``/api/documents/{id}/preview``. The tool checks the caller owns it, the
  project floor checks it is in the project, and the route checks again.
* a bucket object (``gs://bucket/object``): ``/api/buckets/{bucket}/thumbnail``
  or ``/preview``. The route applies the bucket ACLs. The project floor refuses
  it in a project chat unless it is a project document.
* an artefact in the caller's own store: a browser screenshot
  (``/api/browser/artefacts/<id>``) or media a tool made
  (``/api/media/<id>``, ``adk.media_results.save_user_media``, e.g. on-device
  image or audio generation). That route serves only the caller's namespace.

The media always lands in a **Media** tab of the workbench (a registered
result→A2UI mapping, Basic catalog ``Image`` / ``Video`` / ``AudioPlayer``; no
media-specific React). The result also hands the model one ready Markdown line
per item, so it can show one or two inline in its reply. The chat's
``InlineMedia`` fetches ``/api/...`` sources the same authenticated way and
plays video and audio by their type. URLs are built here, never by the model:
a guessed URL is broken media at best.

No pixels or samples enter the model's context, the session or the transcript,
only references. Letting the model LOOK at or LISTEN to media is a separate
decision (cost).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote

from google.adk.tools.tool_context import ToolContext

from adk.a2ui_result_render import BASIC_CATALOG_ID, WORKSPACE_SURFACE_ID, register
from tools.documents.media import media_kind

logger = logging.getLogger(__name__)

MEDIA_SURFACE = "media"
TOOL_NAME = "show_media"

# Display width asked of the thumbnail routes (they clamp to 64..1600). Big
# enough for a workbench card and the chat lightbox, small enough that a phone
# photo does not arrive as a 6 MB original.
DISPLAY_WIDTH = 1200
MAX_ITEMS = 24

_ARTEFACT_PREFIXES = ("/api/media/", "/api/browser/artefacts/")
# Same shape the artefact route accepts (tools/browser_artefact_routes.py).
_ARTEFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_GS_RE = re.compile(r"^gs://(?P<bucket>[a-z0-9][a-z0-9._-]{1,220})/(?P<object>.+)$")
_NOT_MEDIA = "not a picture, video or audio file"


def _markdown(caption: str, url: str) -> str:
    # Brackets and newlines would end the alt text early.
    safe = re.sub(r"[\[\]\r\n]+", " ", caption).strip() or "media"
    return f"![{safe}]({url})"


def _item(caption: str, url: str, source: str, kind: str) -> dict[str, str]:
    return {"caption": caption, "url": url, "source": source, "kind": kind, "markdown": _markdown(caption, url)}


def _clean_ref(value: str) -> str:
    v = value.strip()
    if v.lower().startswith("[ref:") and v.endswith("]"):
        v = v[5:-1].strip()
    return v.removeprefix("doc:").removesuffix(".json")


def _load_document(doc_id: str) -> dict[str, Any] | None:
    from db.firestore import get_document

    return get_document("parsed_documents", doc_id)


def _document_item(doc_id: str, caller_uid: str) -> tuple[dict[str, str] | None, str]:
    """``(item, "")`` for the caller's own media document, else ``(None, problem)``.

    "Not found" and "not yours" read the same, as in get_document_content: saying
    a document exists but belongs to someone else is itself a disclosure.
    """
    from tools.documents import paths
    from tools.documents.context import DocumentAccessError, assert_document_readable

    try:
        raw = _load_document(doc_id)
    except Exception as exc:
        logger.warning("show_media: could not read document %s: %s", doc_id, exc)
        return None, f"{doc_id}: could not be read ({exc})."
    if raw is None:
        return None, f"{doc_id}: no such document. Use list_documents to find it."
    try:
        assert_document_readable(raw, caller_uid, doc_id)
    except DocumentAccessError:
        return None, f"{doc_id}: no such document. Use list_documents to find it."
    filename = str(raw.get("originalFilename") or "")
    if raw.get("userId") != caller_uid:
        # Readable shared-library documents exist, but the media routes serve
        # only the caller's own, so offering one would draw a broken player.
        return None, f"{filename or doc_id}: a shared-library document, which cannot be shown here."
    kind = media_kind(filename) or media_kind(str(raw.get("sourceFormat") or ""))
    if kind is None:
        return None, f"{filename or doc_id}: {_NOT_MEDIA}. Open it with get_document_content."
    if not str(raw.get("sourceUrl") or "").startswith("gs://"):
        return None, f"{filename or doc_id}: its original file is not stored, so it cannot be shown."
    caption = paths.document_path(raw) or filename or doc_id
    ref = quote(doc_id, safe="")
    url = (
        f"/api/documents/{ref}/thumbnail?width={DISPLAY_WIDTH}" if kind == "image" else f"/api/documents/{ref}/preview"
    )
    return _item(caption, url, "document", kind), ""


def _bucket_item(gs_url: str) -> tuple[dict[str, str] | None, str]:
    m = _GS_RE.match(gs_url.strip())
    if not m:
        return None, f"{gs_url}: not a gs://bucket/object address."
    obj = m.group("object")
    kind = media_kind(obj)
    if kind is None:
        return None, f"{gs_url}: {_NOT_MEDIA}."
    base = f"/api/buckets/{quote(m.group('bucket'), safe='')}"
    url = (
        f"{base}/thumbnail?object={quote(obj, safe='')}&width={DISPLAY_WIDTH}"
        if kind == "image"
        else f"{base}/preview?object={quote(obj, safe='')}"
    )
    return _item(obj.rsplit("/", 1)[-1], url, "bucket", kind), ""


def _artefact_item(value: str) -> tuple[dict[str, str] | None, str]:
    text = value.strip()
    prefix = "/api/media/"
    for candidate in _ARTEFACT_PREFIXES:
        if text.startswith(candidate):
            prefix, text = candidate, text[len(candidate) :]
            break
    if not _ARTEFACT_ID_RE.match(text) or ".." in text:
        return None, f"{value}: not an artefact id or url (use the url the tool that made it returned)."
    kind = media_kind(text)
    if kind is None:
        return None, f"{value}: {_NOT_MEDIA}."
    source = "screenshot" if prefix == "/api/browser/artefacts/" else "artefact"
    return _item(text, f"{prefix}{text}", source, kind), ""


def _as_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [v for v in value if isinstance(v, str) and v.strip()]


async def show_media(
    doc_ids: list[str] | None = None,
    paths: list[str] | None = None,
    gs_urls: list[str] | None = None,
    artefacts: list[str] | None = None,
    title: str = "",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Show pictures, video or audio to the user: photos, drawings, scans,
    screenshots, recordings, clips, generated images or speech.

    Use this whenever the user wants to SEE, WATCH or HEAR something you can
    reach, instead of saying you cannot display it. Everything appears in a
    Media tab of the workbench, with players for video and audio. The result
    also gives one ready Markdown line per item: copy a line exactly into your
    reply to show that item inline too (good for one to three; point to the
    Media tab for more). Never write a media URL yourself and never show a ref
    to the user.

    Pass any mix of:

    Args:
        doc_ids: Refs of image, video or audio documents (png, jpg, gif, webp,
            mp4, mov, webm, mp3, wav, m4a, ogg, flac) from list_documents or a
            search result.
        paths: Library or project paths instead of refs, e.g. "pics/kitchen-01.jpg"
            or "media/walkthrough.mp4", as list_documents shows them.
        gs_urls: Media files in a storage bucket, as gs://bucket/path/clip.mp4
            (from list_bucket_documents).
        artefacts: Urls (or ids) of media a tool made for this user: a
            browser_screenshot url, or the url a generation tool returned.
        title: Optional heading for the Media tab, e.g. "West living room".

    Returns:
        ``items``: caption, kind (image, video, audio), source and ``markdown``
        for each item shown; ``problems``: one line for each one that could not
        be shown.
    """
    from adk.tools import _resolve_document_path
    from tools.documents.context import resolve_caller_uid

    caller_uid = resolve_caller_uid(tool_context)
    items: list[dict[str, str]] = []
    problems: list[str] = []

    requested = (
        [("doc", v) for v in _as_list(doc_ids)]
        + [("path", v) for v in _as_list(paths)]
        + [("gs", v) for v in _as_list(gs_urls)]
        + [("artefact", v) for v in _as_list(artefacts)]
    )
    if not requested:
        return {
            "status": "nothing_requested",
            "message": "Give doc_ids, paths, gs_urls or artefacts. Use list_documents to find media documents.",
        }
    if len(requested) > MAX_ITEMS:
        problems.append(f"Only the first {MAX_ITEMS} of {len(requested)} items are shown; ask for the rest after.")
        requested = requested[:MAX_ITEMS]

    for kind, value in requested:
        if kind in ("doc", "path") and not caller_uid:
            problems.append(f"{value}: could not identify the current user, so no document can be shown.")
            continue
        if kind == "doc":
            item, problem = await asyncio.to_thread(_document_item, _clean_ref(value), caller_uid)
        elif kind == "path":
            doc_id, problem = await _resolve_document_path(caller_uid, value, tool_context)
            item = None
            if doc_id:
                item, problem = await asyncio.to_thread(_document_item, doc_id, caller_uid)
        elif kind == "gs":
            item, problem = _bucket_item(value)
        else:
            item, problem = _artefact_item(value)
        if item is not None and item["url"] not in {i["url"] for i in items}:
            items.append(item)
        elif problem:
            problems.append(problem)

    result: dict[str, Any] = {
        "status": "shown" if items else "nothing_shown",
        "title": title.strip() or "Media",
        "items": items,
        "problems": problems,
    }
    if items:
        result["message"] = (
            f"{len(items)} item(s) are in the Media tab of the workbench. To show one inline as well, "
            "copy its `markdown` line exactly into your reply."
        )
    return result


# --- the Media tab -----------------------------------------------------------------


def media_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """The Media tab: a heading, then one card per item (Basic catalog)."""
    if not isinstance(typed_result, dict):
        return None
    items = [i for i in typed_result.get("items") or [] if isinstance(i, dict) and i.get("url")]
    if not items:
        return None

    components: list[dict[str, Any]] = []
    children: list[str] = []

    def add(component: dict[str, Any]) -> str:
        component["id"] = f"media-{len(components) + 1}"
        components.append(component)
        return component["id"]

    children.append(add({"component": "Text", "text": str(typed_result.get("title") or "Media"), "variant": "h2"}))
    for item in items:
        caption = str(item.get("caption") or "")
        kind = item.get("kind") or "image"
        if kind == "video":
            player = add({"component": "Video", "url": item["url"]})
        elif kind == "audio":
            player = add({"component": "AudioPlayer", "url": item["url"], "description": caption})
        else:
            # The Image's description is its alt text.
            player = add({"component": "Image", "url": item["url"], "description": caption, "fit": "contain"})
        label = add({"component": "Text", "text": caption, "variant": "body"})
        children.append(add({"component": "Card", "child": add({"component": "Column", "children": [player, label]})}))
    problems = [str(p) for p in typed_result.get("problems") or [] if p]
    if problems:
        children.append(add({"component": "Text", "text": "Not shown", "variant": "h3"}))
        children.append(add({"component": "Text", "text": "\n".join(f"- {p}" for p in problems), "variant": "body"}))
    components.append({"id": "root", "component": "Column", "children": children})
    return [
        {"version": "v0.9", "createSurface": {"surfaceId": WORKSPACE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": WORKSPACE_SURFACE_ID, "components": components}},
    ]


def _artifact_meta(typed_result: Any) -> dict[str, str]:
    count = len((typed_result or {}).get("items") or []) if isinstance(typed_result, dict) else 0
    return {
        "kind": "media",
        "title": "Media",
        "description": f"{count} item(s) the assistant showed" if count else "Media the assistant showed",
    }


register(
    media_to_a2ui,
    tool_names=[TOOL_NAME],
    name="show_media",
    surface=MEDIA_SURFACE,
    artifact_meta=_artifact_meta,
)

__all__ = ["MEDIA_SURFACE", "TOOL_NAME", "media_to_a2ui", "show_media"]
