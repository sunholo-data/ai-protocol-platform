"""Flash-tier session title generator.

Generates a ≤6-word human-readable title from the first few events of a
session using the model configured via `CHAT_TITLE_MODEL` env var.

Runs in the after_agent_callback — never on the critical path.
All errors return None (title is optional; the session record is not
affected by title generation failure).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PROMPT = (
    "Based on the conversation excerpt below, generate a short title "
    "(maximum 6 words, no quotes, no punctuation at the end).\n\n"
    "{excerpt}"
)


def generate_title_fast(events: list) -> str | None:
    """Generate a ≤6-word title from a list of ADK Event objects.

    Returns None on any error — callers treat None as "no title yet".
    """
    excerpt = _extract_excerpt(events)
    if not excerpt:
        return None

    model_ref = os.environ.get("CHAT_TITLE_MODEL", "lite")
    try:
        from google import genai
        from google.genai import types as genai_types

        from config.models import api_name_for, entry_for
        from config.thinking import ThinkDepth, thinking_config_for

        # This bare client (allowlisted exemption from the resilient layer —
        # cosmetic, fails soft, off the critical path) has no fallback chain to
        # pin a location for it, so it must do so itself: an entry's own
        # `location` (2026-08-13: the "eu" multi-region Gemini 3.x need) wins,
        # else `residency: global` pins "global", else the env default region.
        # CHAT_TITLE_MODEL may still be set to a raw literal not in the
        # registry — entry is None, location stays unset, same as before.
        model_name = api_name_for(model_ref)
        entry = entry_for(model_ref)
        location = entry.location if entry else None
        if location is None and entry is not None and entry.residency == "global":
            location = "global"
        client = genai.Client(vertexai=True, location=location) if location else genai.Client()
        response = client.models.generate_content(
            model=model_name,
            contents=_PROMPT.format(excerpt=excerpt[:1000]),
            config=genai_types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=32,
                # ThinkDepth.OFF: a ≤6-word title from a 1000-char excerpt is
                # mechanical work, so the thinking tokens the API default spends
                # here buy nothing. Live-checked 2026-07-21: 3/3 sensible titles
                # in 0.66-1.11s with OFF.
                # (An earlier worry that dynamic thinking + max_output_tokens=32
                # could exhaust the budget and yield an EMPTY title did NOT
                # reproduce — 4/4 produced a title both with and without
                # thinking. Recorded so nobody re-derives it.)
                thinking_config=thinking_config_for(ThinkDepth.OFF, model_name),
            ),
        )
        title = response.text.strip() if response.text else None
        if title:
            # Truncate aggressively in case the model ignores the instruction
            words = title.split()
            title = " ".join(words[:8])
        return title or None
    except Exception as exc:
        logger.warning("title generation failed (model=%s): %s", model_name, exc)
        return None


def _extract_excerpt(events: list) -> str:
    """Pull up to 4 user/model text turns from ADK Event objects."""
    lines: list[str] = []
    for event in events[:8]:
        try:
            role = getattr(event, "author", None) or ""
            if not role:
                continue
            content = getattr(event, "content", None)
            if content is None:
                continue
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    label = "User" if role == "user" else "Assistant"
                    lines.append(f"{label}: {text[:200]}")
                    break
        except Exception:
            continue
        if len(lines) >= 4:
            break
    return "\n".join(lines)
