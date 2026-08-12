"""Integration test: every curated voice id must exist in the live Cloud TTS API.

This is the verification gate for voice/voices.py. Chirp3-HD locale coverage
changes over time and is not fully documented, so we assert each curated
`<locale>-Chirp3-HD-<Persona>` id is actually returned by ListVoices for its
locale. Runs only where GCP credentials exist (skipped in fast CI).

Run: `uv run pytest tests/integration/test_curated_voices_live.py -m integration`
"""

from __future__ import annotations

import pytest

from voice.voices import CURATED_VOICES

pytestmark = pytest.mark.integration


def _locale_of(voice_id: str) -> str:
    # "fr-FR-Chirp3-HD-Kore" -> "fr-FR"
    return "-".join(voice_id.split("-", 2)[:2])


def test_all_curated_voice_ids_exist_upstream():
    from google.api_core import exceptions as gexc
    from google.auth import exceptions as auth_exc
    from google.cloud import texttospeech as tts

    # Group ids by locale so we call ListVoices once per locale.
    by_locale: dict[str, set[str]] = {}
    for entries in CURATED_VOICES.values():
        for v in entries:
            by_locale.setdefault(_locale_of(v["name"]), set()).add(v["name"])

    try:
        client = tts.TextToSpeechClient()
        available_by_locale = {
            locale: {v.name for v in client.list_voices(language_code=locale).voices} for locale in by_locale
        }
    except (auth_exc.GoogleAuthError, gexc.Unauthenticated, gexc.PermissionDenied) as exc:
        # No real creds / project (e.g. conftest's fake GOOGLE_CLOUD_PROJECT).
        # This gate is meaningful only against a real project.
        pytest.skip(f"live Cloud TTS not reachable with current credentials: {exc}")

    missing: list[str] = []
    for locale, wanted in by_locale.items():
        missing.extend(sorted(wanted - available_by_locale[locale]))

    assert not missing, f"curated voice ids not found in live Cloud TTS: {missing}"
