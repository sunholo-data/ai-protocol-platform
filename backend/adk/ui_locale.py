"""The person's interface language reaches the agent (multi-language.md §6).

The frontend sends the locale the person reads the interface in as
``forwardedProps.ui_locale`` on every run, unless the deployment is English-only
(one locale, ``en``) — so a Danish-only fork sends ``da`` too. This module validates it against the deployment's own
list and turns it into one line of instruction, so any skill answers in that
language unless the person writes in another one, or the skill's own
instructions fix the language.

Configuration: none needed. An English-only deployment never sends a value.
``UI_LOCALES`` is an OPTIONAL backend allow-list:

    UI_LOCALES=da,en   # only these tags are honoured

Whatever arrives must be a well-formed BCP 47 language[-region] tag (it goes
into an instruction, so it is never free text). Inert means byte-identical:
nothing is added to the instruction and the state key is "".

State: ``ui_locale`` (NOT ``temp:ui_locale``). Wire-supplied ``temp:`` keys are
stripped before the run (see the ``document_ids`` note in
skills/skill_processor.py), so it is a plain key that is written on EVERY turn,
"" when unused, so a previous turn's value is never carried. Tools that return
localised content read ``tool_context.state.get("ui_locale")``.

Like ``wrap_with_today``, the line is computed per REQUEST from state, never at
agent build. Agents are cached across turns and people (adk.agent_cache), so a
build-time value would leak one person's language into another's chat.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable

from google.adk.agents.readonly_context import ReadonlyContext

STATE_KEY = "ui_locale"
_ENV = "UI_LOCALES"
_TAG = re.compile(r"^[a-z]{2,3}(?:-(?:[A-Z]{2}|\d{3}))?$")

# English names for the languages forks are most likely to offer. The backend
# has no Intl, and the model understands "Danish (da)" better than a bare tag.
# A tag not listed here is passed as the tag alone, which models also handle.
_LANGUAGE_NAMES = {
    "da": "Danish",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "is": "Icelandic",
    "it": "Italian",
    "nb": "Norwegian Bokmål",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "sv": "Swedish",
    "uk": "Ukrainian",
}

_BaseInstruction = str | Callable[[ReadonlyContext], Awaitable[str]]


def normalise_tag(raw: object) -> str | None:
    """``"pt_br"`` → ``"pt-BR"``; anything that isn't a language[-region] tag → None.
    Mirrors frontend/src/i18n/locale.ts ``normaliseLocale``."""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().replace("_", "-").split("-")
    if len(parts) > 2 or not parts[0]:
        return None
    tag = f"{parts[0].lower()}-{parts[1].upper()}" if len(parts) == 2 else parts[0].lower()
    return tag if _TAG.match(tag) else None


def configured_locales() -> list[str]:
    """The optional allow-list (``UI_LOCALES``); [] when unset. Read per call so
    tests and a config change don't need a restart."""
    out: list[str] = []
    for part in os.environ.get(_ENV, "").split(","):
        tag = normalise_tag(part)
        if tag and tag not in out:
            out.append(tag)
    return out


def normalize(raw: object) -> str:
    """The locale to run in, or "" for "add nothing".

    "" when nothing was sent, when it isn't a well-formed tag, or — if the
    ``UI_LOCALES`` allow-list is set — when it isn't listed there. Never passed
    through raw: only a normalised tag reaches the instruction.
    """
    tag = normalise_tag(raw)
    if not tag:
        return ""
    allowed = configured_locales()
    return tag if not allowed or tag in allowed else ""


def language_label(tag: str) -> str:
    """``"da"`` → ``"Danish (da)"``; ``"pt-BR"`` → ``"Portuguese (pt-BR)"``; unknown → the tag."""
    name = _LANGUAGE_NAMES.get(tag.split("-")[0])
    return f"{name} ({tag})" if name else tag


_BLOCK_TEMPLATE = """
============================================================
The person reads this app in {label}. Unless your instructions above fix the
language you answer in, reply in {name} — and if the person writes in another
language or asks for one, use that instead.
============================================================
""".strip()


def render_instruction_with_locale(base_str: str, locale: str) -> str:
    """Append the language line to ``base_str``; unchanged when ``locale`` is ""."""
    if not locale:
        return base_str
    label = language_label(locale)
    name = label.split(" (")[0] if label != locale else f"the language with tag {locale}"
    return base_str + "\n\n" + _BLOCK_TEMPLATE.format(label=label, name=name)


def wrap_with_ui_locale(base: _BaseInstruction) -> Callable[[ReadonlyContext], Awaitable[str]]:
    """Return an ``InstructionProvider`` that adds the person's interface language.

    Re-validates the state value per request: state is written by
    skill_processor from an already-normalised value, but a session could carry
    an older value from before ``UI_LOCALES`` changed.
    """

    async def _provider(ctx: ReadonlyContext) -> str:
        base_str = await base(ctx) if callable(base) else base
        try:
            raw = ctx.state.get(STATE_KEY)
        except Exception:
            raw = None
        return render_instruction_with_locale(base_str, normalize(raw))

    return _provider


__all__ = [
    "STATE_KEY",
    "configured_locales",
    "language_label",
    "normalise_tag",
    "normalize",
    "render_instruction_with_locale",
    "wrap_with_ui_locale",
]
