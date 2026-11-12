"""``look_at_media``: let the MODEL see a picture and hear or watch a clip.

``show_media`` puts media in front of the user; the model only ever got a link,
and a library photo reached it as the text description written at import. Asked
"what's in stue-west-03-fireplace-wide.jpg?", it read the filename and said a
fireplace. The photo is of a hallway.

ADK already has the mechanism: ``load_artifacts`` attaches an artifact's bytes
to the ONE model request that follows its call, never to the session, so the
pixels are not stored in the transcript or re-sent every turn. This tool does
the same for the media the agent can reach:

* **Pictures** (library/project documents by ref or path, the caller's own
  artefacts: screenshots, generated images) are read with the same access rule
  as get_document_content, shrunk to what a model uses
  (``tools.documents.media_bytes``), saved as session artifacts
  ``look:<digest>.jpg``, and attached by :meth:`LookAtMediaTool.process_llm_request`
  inside the tool's result in the request right after the call. Every current Claude, Gemini and GPT
  model reads images, so this works whichever model the agent runs on. The
  model then writes down what it saw; later turns keep its words, not the
  pixels (call again to look again).
* **Audio and video** are listened to / watched by Gemini through the approved
  resilient seam (``generate_content_resilient``) and come back as a
  transcript plus a description, so a Claude agent, which cannot take audio or
  video, gets them too.

Bucket objects are not read here: their ACLs are checked per viewer at the
route, and this tool has no viewer access context. ``show_media`` still shows them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

TOOL_NAME = "look_at_media"
ARTIFACT_PREFIX = "look:"
MAX_IMAGES = 6
MAX_CLIPS = 3

_AV_PROMPT = (
    "You are the ears and eyes of an assistant that cannot hear or watch this file. "
    "Report what is in it so the assistant can answer the user from your report alone.\n"
    "1. Transcript: the speech, verbatim, with a new line at each change of speaker "
    "(label speakers Speaker 1, Speaker 2, … unless they name themselves). "
    "Write (no speech) if there is none.\n"
    "2. Description: what can be seen (video: setting, people, actions, on-screen text, "
    "in order) and heard besides speech (music, sounds). Keep it factual; do not guess names.\n"
)


def _tier() -> str:
    return os.environ.get("MEDIA_UNDERSTANDING_TIER", "pro").strip() or "pro"


def _as_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [v for v in value if isinstance(v, str) and v.strip()]


async def _understand_av(data: bytes, mime: str, caption: str, question: str) -> str:
    """Transcript + description of a clip, from Gemini via generate_content_resilient."""
    from google.genai import types

    from tools.resilient_genai import generate_content_resilient

    ask = _AV_PROMPT + (
        f"\nThe user also asks: {question.strip()}\nAnswer that last, under 3. Answer.\n" if question.strip() else ""
    )
    response = await generate_content_resilient(
        prompt=[types.Part.from_bytes(data=data, mime_type=mime), ask],
        model_ref=_tier(),
        config={"max_output_tokens": 8192, "thinking_config": {"thinking_level": "LOW"}},
        progress_label=f"Listening to {caption}…",
        label="look-at-media",
    )
    return (getattr(response, "text", None) or "").strip() or "(Gemini returned nothing for this file.)"


async def _document_target(doc_id: str, caller_uid: str) -> tuple[str, str, bytes, str]:
    """``(caption, kind, data, mime)`` for a document, or raise with a reason."""
    from adk.show_media import _load_document
    from tools.documents import paths
    from tools.documents.media_bytes import read_document_media

    raw = await asyncio.to_thread(_load_document, doc_id)
    if raw is None:
        raise LookupError(f"{doc_id}: no such document. Use list_documents to find it.")
    kind, data, mime = await asyncio.to_thread(read_document_media, raw, doc_id, caller_uid)
    caption = await asyncio.to_thread(paths.document_path, raw) or str(raw.get("originalFilename") or doc_id)
    return caption, kind, data, mime


async def _artefact_target(value: str, tool_context: ToolContext) -> tuple[str, str, bytes, str]:
    from adk.media_results import ARTEFACT_NAMESPACE
    from adk.show_media import _artefact_item
    from tools.documents.media_bytes import MediaUnavailable, prepare_image_for_model

    item, problem = _artefact_item(value)
    if item is None:
        raise LookupError(problem)
    artefact_id = item["url"].rsplit("/", 1)[-1]
    part = await tool_context.load_artifact(filename=f"{ARTEFACT_NAMESPACE}{artefact_id}")
    blob = getattr(part, "inline_data", None) if part is not None else None
    if blob is None or not blob.data:
        raise LookupError(f"{value}: no such artefact for this user.")
    if item["kind"] == "image":
        try:
            data, mime = await asyncio.to_thread(prepare_image_for_model, blob.data)
        except MediaUnavailable as exc:
            raise LookupError(f"{artefact_id}: {exc}.") from exc
        return artefact_id, "image", data, mime
    return artefact_id, item["kind"], blob.data, blob.mime_type or "application/octet-stream"


async def look_at_media(
    doc_ids: list[str] | None = None,
    paths: list[str] | None = None,
    artefacts: list[str] | None = None,
    question: str = "",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Look at pictures, or listen to and watch audio and video, YOURSELF.

    Use this whenever the answer depends on what is actually IN a picture,
    recording or clip: "what's in this photo?", "which room is this?", "is the
    wall damaged?", "what does she say in the video?", or to check a generated
    image before you show it. A filename or an import-time description is not
    the picture: look before you describe one, and say what you saw.

    Pictures are attached to this tool's result for you to see directly (up to
    6 per call). Audio and video come back as a transcript and a description
    (up to 3 per call). Only you see them; to show the user, call show_media.
    What you see is not kept for later turns: note what matters in your reply,
    or call this again.

    Args:
        doc_ids: Refs of image, video or audio documents from list_documents or search.
        paths: Library or project paths instead of refs, e.g. "pics/kitchen-01.jpg".
        artefacts: Urls (or ids) of media a tool made for this user: a
            browser_screenshot url, or the url a generation tool returned.
        question: Optional. What to look or listen for; answered in an audio
            or video report.

    Returns:
        ``items``: for each, its caption and kind, and for audio/video the
        ``report``; ``problems``: one line per item that could not be read.
    """
    from adk.show_media import _clean_ref
    from adk.tools import _resolve_document_path
    from tools.documents.context import DocumentAccessError, resolve_caller_uid
    from tools.documents.media_bytes import MediaUnavailable

    caller_uid = resolve_caller_uid(tool_context)
    requested = (
        [("doc", v) for v in _as_list(doc_ids)]
        + [("path", v) for v in _as_list(paths)]
        + [("artefact", v) for v in _as_list(artefacts)]
    )
    if not requested:
        return {
            "status": "nothing_requested",
            "message": "Give doc_ids, paths or artefacts. Use list_documents to find media documents.",
        }

    items: list[dict[str, Any]] = []
    problems: list[str] = []
    images = clips = 0
    seen: set[str] = set()
    for source, value in requested:
        try:
            if source == "artefact":
                caption, kind, data, mime = await _artefact_target(value, tool_context)
            else:
                if not caller_uid:
                    raise LookupError(f"{value}: could not identify the current user.")
                doc_id = _clean_ref(value)
                if source == "path":
                    doc_id, problem = await _resolve_document_path(caller_uid, value, tool_context)
                    if problem:
                        raise LookupError(problem)
                caption, kind, data, mime = await _document_target(doc_id, caller_uid)
        except DocumentAccessError:
            problems.append(f"{value}: no such document. Use list_documents to find it.")
            continue
        except MediaUnavailable as exc:
            problems.append(f"{value}: {exc}.")
            continue
        except LookupError as exc:
            problems.append(str(exc))
            continue
        except Exception as exc:
            logger.warning("look_at_media: could not read %s: %s", value, exc)
            problems.append(f"{value}: could not be read ({exc}).")
            continue

        digest = hashlib.sha256(data).hexdigest()[:12]
        if digest in seen:
            continue
        seen.add(digest)
        if kind == "image":
            if images >= MAX_IMAGES:
                problems.append(f"{caption}: only {MAX_IMAGES} pictures per call; look at the rest in another call.")
                continue
            from google.genai import types

            name = f"{ARTIFACT_PREFIX}{digest}.jpg"
            await tool_context.save_artifact(filename=name, artifact=types.Part.from_bytes(data=data, mime_type=mime))
            items.append({"caption": caption, "kind": "image", "attached": name})
            images += 1
        else:
            if clips >= MAX_CLIPS:
                problems.append(f"{caption}: only {MAX_CLIPS} clips per call; ask about the rest in another call.")
                continue
            try:
                report = await _understand_av(data, mime, caption, question)
            except Exception as exc:
                logger.warning("look_at_media: Gemini could not read %s: %s", caption, exc)
                problems.append(f"{caption}: could not be listened to ({exc}).")
                continue
            items.append({"caption": caption, "kind": kind, "report": report})
            clips += 1

    result: dict[str, Any] = {
        "status": "looked" if items else "nothing_read",
        "items": items,
        "problems": problems,
    }
    if images:
        result["message"] = (
            f"{images} picture(s) are attached to this result, in the order of `items`. "
            "Describe what you actually see; the pictures are not kept for later turns."
        )
    return result


def _response_dict(function_response: Any) -> dict[str, Any]:
    response = getattr(function_response, "response", None)
    if isinstance(response, dict) and isinstance(response.get("result"), str):
        try:
            response = json.loads(response["result"])
        except ValueError:
            return {}
    return response if isinstance(response, dict) else {}


class LookAtMediaTool(FunctionTool):
    """``look_at_media`` plus the step that puts the pictures in front of the model."""

    def __init__(self) -> None:
        super().__init__(look_at_media)

    async def process_llm_request(self, *, tool_context: ToolContext, llm_request: Any) -> None:
        await super().process_llm_request(tool_context=tool_context, llm_request=llm_request)
        await attach_looked_at_media(tool_context, llm_request)


async def attach_looked_at_media(tool_context: ToolContext, llm_request: Any) -> int:
    """Attach the pictures a ``look_at_media`` call just saved to THIS request only.

    Only when the trailing content holds this tool's function_response (the
    model call right after the tool ran). The pictures go INSIDE that
    function_response (``FunctionResponse.parts``, in the order of its
    ``items``), not beside it: measured 2026-11-10, Gemini 3.8 rejects picture
    parts placed next to a function_response in the same turn ("Requests ending
    with a model turn are not supported" — the shape ADK's own load_artifacts
    uses), and accepts them inside it. ADK's LiteLLM adapter turns these parts
    into an image message right after the tool result for Claude and GPT.

    The request's contents are shallow copies of the session's events
    (``_copy_content_for_request``), so the Part is REPLACED with one holding a
    copied FunctionResponse; nothing reaches the session. Returns how many
    pictures were attached.
    """
    from google.genai import types

    contents = getattr(llm_request, "contents", None) or []
    if not contents or not getattr(contents[-1], "parts", None):
        return 0
    last = contents[-1]
    total = 0
    for index, part in enumerate(list(last.parts)):
        fr = getattr(part, "function_response", None)
        if fr is None or getattr(fr, "name", None) != TOOL_NAME:
            continue
        pictures: list[types.FunctionResponsePart] = []
        for item in _response_dict(fr).get("items") or []:
            name = item.get("attached") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.startswith(ARTIFACT_PREFIX):
                continue
            try:
                artifact = await tool_context.load_artifact(filename=name)
            except Exception as exc:
                logger.warning("look_at_media: could not load %s: %s", name, exc)
                continue
            blob = getattr(artifact, "inline_data", None) if artifact is not None else None
            if blob is None or not blob.data:
                logger.warning("look_at_media: artifact %s is missing", name)
                continue
            pictures.append(types.FunctionResponsePart.from_bytes(data=blob.data, mime_type=blob.mime_type))
        if pictures:
            copied = fr.model_copy(update={"parts": [*(fr.parts or []), *pictures]})
            last.parts[index] = part.model_copy(update={"function_response": copied})
            total += len(pictures)
    if total:
        logger.info("look_at_media: attached %d picture(s) to the model request", total)
    return total


__all__ = ["ARTIFACT_PREFIX", "TOOL_NAME", "LookAtMediaTool", "attach_looked_at_media", "look_at_media"]
