"""``speak``: the agent says something out loud, through the platform's voice layer.

Read-aloud already turns a reply into audio (``protocols/voice_routes.py``),
through a provider chosen per skill, by env, or by default (``voice.registry``:
Google Cloud TTS tiers, plus whatever a deployment adds in a private
``voice.py`` — e.g. a host-local model). ``speak`` puts the same layer in the
agent's hands, for a recording that should exist as a thing: a voice-over, a
spoken summary, the same line in two voices to compare.

The audio is saved to the CALLER's own artefact store
(``adk.media_results.save_user_media``, served by ``/api/media/<id>``) and
drawn in the Media tab by the same mapping as ``show_media``, so it plays in
the workbench and, when the agent copies its Markdown line, inline in the
reply. Nothing is public and nothing enters the model's context but the
reference.

Opt-in: a skill lists ``speak`` in its tools; it is not on every agent.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from google.adk.tools.tool_context import ToolContext

from adk.a2ui_result_render import register
from adk.show_media import MEDIA_SURFACE, _artifact_meta, _markdown, media_to_a2ui

logger = logging.getLogger(__name__)

TOOL_NAME = "speak"
MAX_CHARS = 5000


def _defaults() -> tuple[str, str, str]:
    """``(provider, voice, lang)`` the deployment speaks in when nothing is chosen."""
    from voice import DEFAULT_VOICE

    return (
        os.getenv("VOICE_TTS_PROVIDER") or DEFAULT_VOICE["provider"],
        os.getenv("VOICE_TTS_VOICE") or DEFAULT_VOICE["voice"],
        os.getenv("VOICE_TTS_LANGUAGE") or DEFAULT_VOICE["language"],
    )


async def speak(
    text: str,
    source: str = "",
    voice: str = "",
    style: str = "",
    language: str = "",
    title: str = "",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Say something out loud: turn text into a recording the user can play.

    Use it when the user wants to HEAR something (a voice-over, a spoken
    summary, how a sentence sounds in a given voice), not for every reply:
    replies already have a read-aloud button. The recording appears in the
    Media tab; copy the result's ``markdown`` line into your reply to play it
    inline. Write the text exactly as it should be spoken.

    Args:
        text: What to say (up to 5000 characters).
        source: Optional. Which speech engine, by name: leave empty for this
            deployment's usual voice. Built in: "gcp_chirp3hd", "gcp_gemini",
            "gcp_wavenet". A deployment may add its own (the result lists the
            ones available when a name is unknown).
        voice: Optional. A voice of that engine, e.g. "en-GB-Chirp3-HD-Aoede",
            or a saved voice's name on an engine that has saved voices.
        style: Optional. How to say it, for engines that take direction
            ("warm and slow", "excited"); others ignore it.
        language: Optional. BCP-47 language, e.g. "en-GB" or "da-DK".
        title: Optional. A short name for the recording.

    Returns:
        ``items``: the recording (kind audio, its url and ``markdown``), or
        ``problems`` saying why nothing was made.
    """
    from adk.media_results import save_user_media
    from voice import get_tts

    text = (text or "").strip()
    if not text:
        return {"status": "nothing_said", "items": [], "problems": ["Give the text to say."]}
    if len(text) > MAX_CHARS:
        return {
            "status": "nothing_said",
            "items": [],
            "problems": [f"The text is {len(text)} characters; split it into parts of at most {MAX_CHARS}."],
        }
    default_provider, default_voice, default_lang = _defaults()
    provider_name = source.strip() or default_provider
    # The deployment's default voice belongs to the default engine: never send
    # a Google voice name to another engine.
    chosen_voice = voice.strip() or (default_voice if provider_name == default_provider else "") or None
    lang = language.strip() or default_lang
    if provider_name == "browser":
        return {
            "status": "nothing_said",
            "items": [],
            "problems": ["The browser engine speaks only in the reader's browser; choose another source."],
        }
    try:
        provider = get_tts(provider_name)
    except ValueError as exc:
        return {"status": "nothing_said", "items": [], "problems": [str(exc)]}
    extras = {"prompt": style.strip()} if style.strip() else None
    try:
        audio, mime = await provider.synthesize(text, lang, chosen_voice, extras)
    except (ValueError, RuntimeError, NotImplementedError) as exc:
        logger.warning("speak: %s failed: %s", provider_name, exc)
        return {"status": "nothing_said", "items": [], "problems": [f"{provider_name} could not speak this: {exc}"]}
    if not audio:
        return {"status": "nothing_said", "items": [], "problems": [f"{provider_name} returned no audio."]}

    label = re.sub(r"\s+", " ", title.strip() or text)[:48]
    try:
        ref = await save_user_media(tool_context, audio, mime, label=label)
    except Exception as exc:
        logger.warning("speak: could not store the recording: %s", exc)
        return {"status": "nothing_said", "items": [], "problems": [f"The recording could not be stored ({exc})."]}
    spoken_by = getattr(provider, "name", provider_name)
    caption = f"{title.strip() or label} ({spoken_by}{', ' + chosen_voice if chosen_voice else ''})"
    item = {
        "caption": caption,
        "url": ref["url"],
        "source": "artefact",
        "kind": "audio",
        "markdown": _markdown(caption, ref["url"]),
    }
    return {
        "status": "spoken",
        "title": title.strip() or "Speech",
        "items": [item],
        "problems": [],
        "message": "The recording is in the Media tab. Copy its `markdown` line into your reply to play it inline.",
    }


# The same Media tab as show_media: a speak result has the same shape.
register(
    media_to_a2ui,
    tool_names=[TOOL_NAME],
    name="speak",
    surface=MEDIA_SURFACE,
    artifact_meta=_artifact_meta,
)

__all__ = ["TOOL_NAME", "speak"]
