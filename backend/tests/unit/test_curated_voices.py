"""Unit tests for the curated premium voice catalog (voice/voices.py)."""

from __future__ import annotations

import re

from voice.voices import (
    CURATED_VOICES,
    PERSONA_NAMES,
    SUPPORTED_LANGS,
    get_voices_for_lang,
)

# Chirp3-HD id form: <lang>-<REGION>-Chirp3-HD-<Persona>
_ID_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-Chirp3-HD-[A-Za-z]+$")


def test_only_premium_chirp3hd_voices():
    """The picker must never offer budget tiers (Standard/WaveNet/Neural2)."""
    for lang, entries in CURATED_VOICES.items():
        assert entries, f"{lang} has no voices"
        for v in entries:
            assert v["tier"] == "Chirp3 HD", f"{v['name']} is not premium"
            assert v["provider"] == "gcp_chirp3hd"
            assert _ID_RE.match(v["name"]), f"bad voice id: {v['name']}"


def test_expected_european_languages_present():
    for lang in ("es", "en", "de", "fr", "it", "nl"):
        assert lang in SUPPORTED_LANGS


def test_portuguese_pt_absent():
    """European Portuguese has no Chirp3-HD voices upstream — must not be offered."""
    assert "pt" not in CURATED_VOICES


def test_same_personas_across_all_languages():
    """Personas are language-independent — every language offers the same set."""
    for lang in SUPPORTED_LANGS:
        personas = [v["name"].rsplit("-", 1)[-1] for v in get_voices_for_lang(lang)]
        assert personas == PERSONA_NAMES


def test_unknown_lang_returns_empty():
    assert get_voices_for_lang("xx") == []
