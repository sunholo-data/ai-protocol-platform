"""Localised content: the backend half of the one resolution rule.

Design: docs/design/v6.43.0/multi-language.md §1 and §5. The frontend half is
``frontend/src/i18n/locale.ts`` (``normaliseLocale``, ``localeChain``) and
``frontend/src/i18n/resolve.ts`` (``pickLocale``, ``resolveLocalized``,
``localizedField``). Both sides are checked against ONE table,
``backend/tests/fixtures/i18n_resolution.json``, so they can't drift.

Two shapes carry a second language:

    a map            {"en": "Projects", "da": "Projekter"}
    base + variants  display_name: "General Assistant"
                     locales: {"da": {"display_name": "Generel assistent"}}

A map resolves along the chain (locale, its language, deployment default, its
language, ``en``); base + variants tries only the person's locale and its
language, then the base (see ``localized_field``). Exported for forks' own
content, so the fallback rule is never re-implemented.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SOURCE_LOCALE = "en"

_TAG = re.compile(r"^[a-z]{2,3}(-([A-Z]{2}|\d{3}))?$")

# The text fields of a skill a PERSON reads, and how long each may be. The base
# `description` is capped at 1,024 by SkillConfig, so its translations are too;
# a display name is a label, and a greeting is a short paragraph or two.
SKILL_LOCALE_FIELDS: dict[str, int] = {
    "display_name": 128,
    "description": 1024,
    "initial_message": 4000,
}

# Enough for every language a deployment could plausibly offer, small enough
# that a pasted blob can't bloat a skill document.
MAX_SKILL_LOCALES = 50


def normalise_locale(tag: Any) -> str | None:
    """``"pt_br"``, ``"PT-br"`` → ``"pt-BR"``; anything that isn't a language[-region] tag → None.

    Mirrors ``normaliseLocale`` in ``frontend/src/i18n/locale.ts``.

    Args:
        tag: A candidate BCP 47 tag (any type; non-strings are rejected).

    Returns:
        The normalised tag, or None when it isn't a valid language[-region] tag.
    """
    if not isinstance(tag, str):
        return None
    parts = tag.strip().replace("_", "-").split("-")
    if len(parts) > 2 or not parts[0]:
        return None
    norm = f"{parts[0].lower()}-{parts[1].upper()}" if len(parts) == 2 else parts[0].lower()
    return norm if _TAG.match(norm) else None


def language_of(locale: str) -> str:
    """The language half of a tag: ``pt-BR`` → ``pt``."""
    return locale.split("-")[0]


def locale_chain(locale: str, default_locale: str = SOURCE_LOCALE) -> list[str]:
    """The order a map lookup tries: locale, its language, default, its language, ``en`` (deduped).

    Mirrors ``localeChain`` in ``frontend/src/i18n/locale.ts``.
    """
    out: list[str] = []
    for tag in (locale, language_of(locale), default_locale, language_of(default_locale), SOURCE_LOCALE):
        if tag and tag not in out:
            out.append(tag)
    return out


def _present(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def pick_locale(value: str | Mapping[str, Any] | None, locale: str, default_locale: str = SOURCE_LOCALE) -> str:
    """Resolve a plain string or a ``{locale: text}`` map for one person.

    A plain string returns itself. A map resolves along ``locale_chain``, then to
    any non-empty value it has, then to ``""`` — never to a key or an error.
    Mirrors ``resolveLocalized`` in ``frontend/src/i18n/resolve.ts``.

    Args:
        value: The localised value.
        locale: The person's locale (normalised).
        default_locale: The deployment's default locale.

    Returns:
        The text to show.
    """
    if isinstance(value, str):
        return value
    if not value:
        return ""
    for tag in locale_chain(locale, default_locale):
        if _present(value.get(tag)):
            return value[tag]
    return next((v for v in value.values() if _present(v)), "")


def localized_field(
    base: str | None,
    variants: Mapping[str, Mapping[str, Any] | None] | None,
    field: str,
    locale: str,
) -> str:
    """A base value plus per-locale variants (SKILL.md ``locales:``).

    The base is the author's own text in whatever language they wrote it, and
    nothing records which. So only the person's locale (then its language) is
    tried, and otherwise the base wins. Walking on to the deployment default
    would be wrong: someone who picked English on a Danish-default deployment
    would get a skill's ``da`` variant instead of its English base. Mirrors
    ``localizedField`` in ``frontend/src/i18n/resolve.ts``.

    Args:
        base: The source value.
        variants: ``{locale: {field: text}}``.
        field: Which field to read from a variant.
        locale: The person's locale (normalised).

    Returns:
        The variant for the locale or its language, else the base (``""`` when None).
    """
    if variants:
        for tag in (locale, language_of(locale)):
            entry = variants.get(tag)
            if entry and _present(entry.get(field)):
                return entry[field]
    return base or ""


def validate_locales_block(raw: Any) -> dict[str, dict[str, str]]:
    """Validate a skill's ``locales:`` block (SKILL.md front matter or the skills API).

    Strict on structure, lenient on emptiness:

    - **Raises** ``ValueError`` for anything that is a mistake rather than a gap:
      a block or entry that isn't a mapping, a tag that isn't a language[-region]
      tag, two spellings of the same tag (``pt_BR`` and ``pt-BR``), a field other
      than ``display_name`` / ``description`` / ``initial_message``, a value that
      isn't a string, a value over its length limit, or more than
      ``MAX_SKILL_LOCALES`` locales. A typo should fail the seed or the save
      loudly, not vanish.
    - **Drops** what only means "not translated": ``None`` or blank values, and
      a locale left with no fields. Values are stripped (YAML ``>`` adds a
      trailing newline). Tags are normalised (``da_dk`` → ``da-DK``).

    ``None`` is the same as an empty block.

    Args:
        raw: The block as parsed from YAML or JSON.

    Returns:
        ``{tag: {field: text}}`` with only filled fields.

    Raises:
        ValueError: On a structural mistake (see above), with a message naming it.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"locales must be a mapping of language tag to fields, not {type(raw).__name__}")
    if len(raw) > MAX_SKILL_LOCALES:
        raise ValueError(f"locales lists {len(raw)} languages; the limit is {MAX_SKILL_LOCALES}")
    allowed = ", ".join(SKILL_LOCALE_FIELDS)
    out: dict[str, dict[str, str]] = {}
    for raw_tag, entry in raw.items():
        tag = normalise_locale(raw_tag)
        if tag is None:
            raise ValueError(f"locales: {raw_tag!r} is not a language tag like 'da' or 'pt-BR'")
        if tag in out:
            raise ValueError(f"locales: {raw_tag!r} repeats the language {tag!r}")
        if entry is None:
            out[tag] = {}
            continue
        if not isinstance(entry, Mapping):
            raise ValueError(f"locales.{tag} must be a mapping of fields ({allowed})")
        fields: dict[str, str] = {}
        for field, value in entry.items():
            limit = SKILL_LOCALE_FIELDS.get(field) if isinstance(field, str) else None
            if limit is None:
                raise ValueError(f"locales.{tag}: unknown field {field!r}; allowed: {allowed}")
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"locales.{tag}.{field} must be text, not {type(value).__name__}")
            text = value.strip()
            if not text:
                continue
            if len(text) > limit:
                raise ValueError(f"locales.{tag}.{field} must be at most {limit:,} characters")
            fields[field] = text
        # An all-empty locale is the same as no locale. Record the tag anyway so
        # a later spelling of it is still caught as a repeat.
        out[tag] = fields
    return {tag: fields for tag, fields in out.items() if fields}
