"""``generate_image``: the agent makes or edits pictures with the deployment's image engines.

The image twin of ``speak``. Engines sit behind ``imagegen`` (a registry like
``voice``): a deployment's own (a private package's ``imagegen.py``, e.g. a
local GPU model) or, later, a built-in cloud one. Whichever runs, the pictures
are saved to the CALLER's own artefact store (``save_user_media``, served by
``/api/media/<id>``) and drawn in the Media tab by ``show_media``'s mapping;
the agent can look at them with ``look_at_media`` before it says what it made.

Slow engines are normal here (a shared GPU queues behind a lock): the tool
waits a bounded time, reporting progress, and otherwise returns the job so the
agent can call again with ``job=`` instead of starting over.

An edit's input picture is read with the same access rule and helpers as
``look_at_media``: the caller's documents (by ``doc_id`` — which the project
floor checks — or ``path``) or the caller's artefacts (a screenshot, an
earlier generated picture).

Opt-in: a skill lists ``generate_image`` in its tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from google.adk.tools.tool_context import ToolContext

from adk.a2ui_result_render import register
from adk.show_media import MEDIA_SURFACE, _artifact_meta, _markdown, media_to_a2ui

logger = logging.getLogger(__name__)

TOOL_NAME = "generate_image"
MAX_COUNT = 4
_JOB_RE = re.compile(r"^(?P<engine>[a-z0-9_-]{1,40}):(?P<job>[A-Za-z0-9_.:-]{1,200})$")


_WAITED_KEY = "temp:generate_image_waited"


def _waited_this_turn(tool_context: Any) -> set[str]:
    """Job tokens this invocation already waited on (``temp:`` state lasts one turn)."""
    try:
        return set(tool_context.state.get(_WAITED_KEY) or [])
    except Exception:
        return set()


def _remember_waited(tool_context: Any, waited: set[str]) -> None:
    try:
        tool_context.state[_WAITED_KEY] = sorted(waited)
    except Exception:  # pragma: no cover - a context without state just loses the guard
        logger.debug("generate_image: could not record the waited job", exc_info=True)


def _pending(token: str, detail: str, *, again: bool) -> dict[str, Any]:
    return {
        "status": "pending",
        "job": token,
        "detail": detail,
        "items": [],
        "problems": [],
        "message": (
            ("You already waited for this job in this reply. " if again else "")
            + f"The picture is queued ({detail or 'waiting'}). Tell the user it is on its way and END your reply now; "
            f'do not call generate_image again in this reply. In a later turn, generate_image(job="{token}") '
            "collects it. Do not start a new job for the same picture."
        ),
    }


def _progress(label: str) -> None:
    from tools.resilient_genai import emit_stage_progress

    try:
        emit_stage_progress(label)
    except Exception:  # pragma: no cover - progress is decoration
        logger.debug("generate_image: progress label not emitted", exc_info=True)


async def _input_picture(doc_id: str, path: str, artefact: str, tool_context: ToolContext) -> tuple[bytes, str]:
    """The picture to edit, as ``(bytes, mime)``. Raises LookupError with a reason."""
    from adk.look_at_media import _artefact_target, _document_target
    from adk.show_media import _clean_ref
    from adk.tools import _resolve_document_path
    from tools.documents.context import DocumentAccessError, resolve_caller_uid
    from tools.documents.media_bytes import MediaUnavailable

    if artefact.strip():
        _caption, kind, data, mime = await _artefact_target(artefact, tool_context)
    else:
        uid = resolve_caller_uid(tool_context)
        if not uid:
            raise LookupError("Could not identify the current user, so no document can be read.")
        ref = _clean_ref(doc_id)
        if not ref:
            ref, problem = await _resolve_document_path(uid, path, tool_context)
            if problem:
                raise LookupError(problem)
        try:
            _caption, kind, data, mime = await _document_target(ref, uid)
        except DocumentAccessError as exc:
            raise LookupError(f"{doc_id or path}: no such document. Use list_documents to find it.") from exc
        except MediaUnavailable as exc:
            raise LookupError(f"{doc_id or path}: {exc}.") from exc
    if kind != "image":
        raise LookupError("Only a picture can be edited.")
    return data, mime


async def generate_image(
    prompt: str = "",
    source: str = "",
    model: str = "",
    count: int = 1,
    width: int = 0,
    height: int = 0,
    doc_id: str = "",
    path: str = "",
    artefact: str = "",
    mode: str = "",
    strength: float = 0.0,
    job: str = "",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Make pictures from a description, or edit or vary an existing picture.

    Describe the picture in plain words: subject, setting, style, lighting,
    framing. To change a picture, give it (doc_id, path or artefact) and say
    what to change ("remove the coat rack, keep everything else"). Pictures
    appear in the Media tab; copy a result's ``markdown`` line into your reply
    to show it inline, and use look_at_media first if you need to check it.

    Some engines run on a shared GPU and queue. If the result says the job is
    still running, tell the user it is queued and END your reply: do not call
    this again in the same reply. In a later turn (the user asks for it, or
    says "check"), call it with only ``job=`` set to collect it. Never start
    the same picture twice.

    Args:
        prompt: What to make, or for an edit what to change.
        source: Optional. Which image engine; empty for the deployment's usual
            one. An unknown name lists the available engines.
        model: Optional. A model of that engine (the result names the default).
        count: How many pictures (1-4).
        width: Optional width in pixels; 0 for the engine's default.
        height: Optional height in pixels; 0 for the engine's default.
        doc_id: The picture to edit, as a document ref (from list_documents).
        path: The picture to edit, as a library/project path, instead of doc_id.
        artefact: The picture to edit, as a /api/media/... or screenshot url.
        mode: "edit" (change it as described) or "vary" (variations of it);
            empty means edit when a picture is given, else generate.
        strength: For edit/vary, how far to move from the input (0.05-1.0);
            0 for the engine's default.
        job: Only in a LATER turn, to collect a job a previous call returned
            as still running.

    Returns:
        ``items``: each picture (kind image, url, ``markdown``); ``status``
        "pending" with ``job`` when it is still running; ``problems`` otherwise.
    """
    from adk.media_results import save_user_media
    from imagegen import ImageRequest, get_image_provider

    def fail(problem: str) -> dict[str, Any]:
        return {"status": "nothing_made", "items": [], "problems": [problem]}

    waited = _waited_this_turn(tool_context)
    try:
        if job.strip():
            m = _JOB_RE.match(job.strip())
            if not m:
                return fail("That job reference is not one this tool returned.")
            if job.strip() in waited:
                # Measured live 2026-11-11: told to "collect it", the model
                # called again at once, and each call waited the whole budget
                # (4 min) while the GPU stayed busy. Once per reply is enough.
                return _pending(job.strip(), "still queued", again=True)
            provider = get_image_provider(m.group("engine"))
            result = await provider.resume(m.group("job"), _progress)
            prompt_used = prompt.strip() or "Generated picture"
        else:
            if not prompt.strip():
                return fail("Describe the picture to make, or the change to make.")
            provider = get_image_provider(source)
            inputs: list[tuple[bytes, str]] = []
            if doc_id.strip() or path.strip() or artefact.strip():
                inputs.append(await _input_picture(doc_id, path, artefact, tool_context))
            chosen_mode = mode.strip() or ("edit" if inputs else "generate")
            if chosen_mode not in ("generate", "edit", "vary"):
                return fail(f"Unknown mode {chosen_mode!r}: use generate, edit or vary.")
            if chosen_mode != "generate" and not inputs:
                return fail(f"'{chosen_mode}' needs a picture: give doc_id, path or artefact.")
            request = ImageRequest(
                prompt=prompt.strip(),
                mode=chosen_mode,  # type: ignore[arg-type]
                inputs=inputs,
                count=max(1, min(MAX_COUNT, int(count or 1))),
                width=int(width) or None,
                height=int(height) or None,
                model=model.strip() or None,
                strength=float(strength) or None,
            )
            result = await provider.generate(request, _progress)
            prompt_used = request.prompt
    except LookupError as exc:
        return fail(str(exc))
    except ValueError as exc:
        return fail(str(exc))
    except RuntimeError as exc:
        logger.warning("generate_image: engine failed: %s", exc)
        return fail(f"The image engine failed: {exc}")

    if result.status == "pending":
        token = f"{provider.name}:{result.job}"
        waited.add(token)
        _remember_waited(tool_context, waited)
        return _pending(token, result.detail, again=False)

    label = re.sub(r"\s+", " ", prompt_used)[:48]
    items: list[dict[str, str]] = []
    problems: list[str] = []
    for index, image in enumerate(result.images, start=1):
        try:
            ref = await save_user_media(tool_context, image.data, image.mime, label=label)
        except Exception as exc:
            logger.warning("generate_image: could not store picture %d: %s", index, exc)
            problems.append(f"Picture {index} could not be stored ({exc}).")
            continue
        caption = f"{label} ({result.model or provider.name}" + (f", seed {image.seed})" if image.seed else ")")
        items.append(
            {
                "caption": caption,
                "url": ref["url"],
                "source": "artefact",
                "kind": "image",
                "markdown": _markdown(caption, ref["url"]),
            }
        )
    if not items:
        return {"status": "nothing_made", "items": [], "problems": problems or ["The engine returned no pictures."]}
    return {
        "status": "made",
        "title": label,
        "items": items,
        "problems": problems,
        "message": (
            f"{len(items)} picture(s) are in the Media tab. Copy a `markdown` line into your reply to show one "
            "inline; to check what was made, look_at_media(artefacts=[url])."
        ),
    }


register(
    media_to_a2ui,
    tool_names=[TOOL_NAME],
    name="generate_image",
    surface=MEDIA_SURFACE,
    artifact_meta=_artifact_meta,
)

__all__ = ["TOOL_NAME", "generate_image"]
