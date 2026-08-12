"""Curated list of TTS voices for the skill voice picker.

Single source of truth for the GET /api/voice/voices endpoint.

Design (v6.6.0): the picker offers ONLY premium **Chirp3-HD** voices — the
best Cloud TTS tier ($30/M chars), not the budget Standard/WaveNet tiers. The
key simplification is that Chirp3-HD voices are a small set of named *personas*
(Aoede, Kore, Charon, Puck, …) that are the SAME identity across every locale
Google supports. You pick a persona + a language; that persona speaks the
chosen language. So `es-ES-Chirp3-HD-Kore` and `fr-FR-Chirp3-HD-Kore` are the
same "Kore" voice speaking Spanish vs French — there is no per-language
casting to do.

Voice IDs use Cloud TTS's canonical form `<locale>-Chirp3-HD-<Persona>`. The
`gcp_tts` provider derives the language_code from that prefix, so the ID is
authoritative regardless of the short lang tag the caller passes.

Locale coverage below is VERIFIED against the live ListVoices API
(tests/integration/test_curated_voices_live.py). European Portuguese (pt-PT)
is intentionally absent: Google ships no Chirp3-HD voices for it.

The platform default read-aloud voice (Aitana, es-ES-Chirp3-HD-Aoede) lives in
voice/__init__.py — it's the fallback when a skill hasn't chosen a voice.
"""

from typing import TypedDict


class VoiceEntry(TypedDict):
    """A single voice a skill author can pick.

    `provider` is the registry name (`gcp_chirp3hd`) so the frontend doesn't
    have to map tier->provider itself. `tier` is the human-facing label.
    """

    name: str
    provider: str
    tier: str
    gender: str
    label: str


PREMIUM_TIER_LABEL = "Chirp3 HD"
PREMIUM_PROVIDER = "gcp_chirp3hd"

# The curated persona set — deliberately small. Each is (name, gender, blurb).
# All are verified present in every locale in _LOCALES below.
_PERSONAS: list[tuple[str, str, str]] = [
    ("Aoede", "F", "warm"),
    ("Kore", "F", "bright"),
    ("Charon", "M", "deep"),
    ("Puck", "M", "upbeat"),
]

# European locales with Chirp3-HD support (verified via live ListVoices).
# Key = short BCP-47 tag the frontend uses; value = full locale for the voice ID.
_LOCALES: dict[str, str] = {
    "es": "es-ES",
    "en": "en-GB",
    "de": "de-DE",
    "fr": "fr-FR",
    "it": "it-IT",
    "nl": "nl-NL",
    "da": "da-DK",
}


def _entry(locale: str, persona: str, gender: str, blurb: str) -> VoiceEntry:
    who = "female" if gender == "F" else "male"
    return {
        "name": f"{locale}-Chirp3-HD-{persona}",
        "provider": PREMIUM_PROVIDER,
        "tier": PREMIUM_TIER_LABEL,
        "gender": gender,
        "label": f"{persona} — {blurb} ({who})",
    }


# Curated premium voices, keyed by short lang tag. Same personas in every
# language (that's the point — personas are language-independent).
CURATED_VOICES: dict[str, list[VoiceEntry]] = {
    short: [_entry(locale, name, gender, blurb) for name, gender, blurb in _PERSONAS]
    for short, locale in _LOCALES.items()
}


SUPPORTED_LANGS: list[str] = sorted(CURATED_VOICES.keys())

# The persona names the picker offers, in order — handy for tests and any
# persona-first UI that groups by voice rather than language.
PERSONA_NAMES: list[str] = [name for name, _, _ in _PERSONAS]


def get_voices_for_lang(lang: str) -> list[VoiceEntry]:
    """Look up the curated voice list for a BCP-47 short tag.

    Falls back to the empty list for unknown langs so the frontend
    dropdown gracefully shows "no voices" rather than 500-ing.
    """
    return CURATED_VOICES.get(lang, [])
