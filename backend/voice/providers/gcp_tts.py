"""Google Cloud Text-to-Speech provider.

Wraps `google-cloud-texttospeech` 2.x. One class serves every voice tier
(Standard, WaveNet, Neural2, Chirp3HD); the tier is encoded in the voice
name (`es-ES-Wavenet-C` vs `es-ES-Chirp3-HD-Aoede`), not in a separate
field.

Auth: Application Default Credentials. Locally:
    gcloud auth application-default login
On Cloud Run: the backend SA's identity is picked up automatically.

The Protocol parameter `lang` is BCP-47 short form (`"es"` / `"en"`).
This provider normalizes to the full form GCP expects (`"es-ES"` /
`"en-US"`) inside `_normalize_lang`. Callers who want a different region
pass the full form (`"es-ES"`); the normalizer is idempotent.

Ported from the AIPLA fork. v6 default voice is Aitana - a warm Spanish
female professional voice on the premium tier (es-ES-Chirp3-HD-Aoede).
"""

from __future__ import annotations

import asyncio
import logging
import os

from google.cloud import texttospeech

from voice.base import VoiceCapabilities

logger = logging.getLogger(__name__)


# Short BCP-47 -> full BCP-47 with region. Add entries as new languages
# come online. Anything not in the map is passed through unchanged, so
# callers can still use full forms directly.
_LANG_DEFAULTS = {
    "es": "es-ES",
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "it": "it-IT",
    "pt": "pt-PT",
}

# Default voice per (tier, full-BCP-47) when the caller doesn't supply an
# explicit `voice` arg. The registry can still override via
# SkillVoiceConfig.tts_voice. es-ES defaults to Aitana (Wavenet-C).
_DEFAULT_VOICE = {
    ("standard", "es-ES"): "es-ES-Standard-A",
    ("standard", "en-US"): "en-US-Standard-A",
    ("wavenet", "es-ES"): "es-ES-Wavenet-C",  # WaveNet-tier default (budget)
    ("wavenet", "en-US"): "en-US-Wavenet-C",
    ("neural2", "es-ES"): "es-ES-Neural2-A",
    ("neural2", "en-US"): "en-US-Neural2-F",
    ("chirp3hd", "es-ES"): "es-ES-Chirp3-HD-Aoede",
    ("chirp3hd", "en-US"): "en-US-Chirp3-HD-Aoede",
    # Gemini-TTS uses BARE voice names (no lang/tier prefix) + a model_name.
    ("gemini", "es-ES"): "Aoede",
    ("gemini", "en-US"): "Aoede",
}

# Gemini-TTS model that backs the promptable / "Style Instructions" voices.
# Override via env if Google ships a newer model id.
_GEMINI_TTS_MODEL = os.getenv("VOICE_GEMINI_TTS_MODEL", "gemini-2.5-flash-tts")


class GCPTTSProvider:
    """Cloud TTS provider. One instance per voice tier.

    The tier (`"standard"`, `"wavenet"`, `"neural2"`, `"chirp3hd"`) is
    chosen at construction time; the registry passes it from the
    provider name (`gcp_wavenet` -> tier=`"wavenet"`).
    """

    SUPPORTED_TIERS = frozenset({"standard", "wavenet", "neural2", "chirp3hd", "gemini"})

    def __init__(self, tier: str, *, client: texttospeech.TextToSpeechClient | None = None):
        if tier not in self.SUPPORTED_TIERS:
            raise ValueError(f"Unsupported GCP TTS tier {tier!r}. Known: {sorted(self.SUPPORTED_TIERS)}.")
        self.tier = tier
        self.name = f"gcp_{tier}"
        # Lazy client construction so unit tests can inject a mock and
        # production avoids unnecessary auth lookups at import time. We use
        # the SYNC client + asyncio.to_thread (rather than the async client)
        # because the async gRPC variant has known ADC quota-project quirks.
        # The sync client handles ADC correctly. Matches the pattern in
        # voice/cache.py and tools/documents/.
        self._client = client

    async def synthesize(
        self,
        text: str,
        lang: str,
        voice: str | None,
        extras: dict | None,
    ) -> tuple[bytes, str]:
        if not text:
            raise ValueError("synthesize: text must not be empty")
        # When the caller passes an explicit voice, the voice's BCP-47 prefix
        # (e.g. "es-ES" from "es-ES-Wavenet-C") IS the authoritative language
        # code. Cloud TTS 400s on a lang/voice mismatch, so prefer
        # voice-derived lang over the caller's lang when they differ. The
        # caller-lang still wins when no voice is supplied.
        is_gemini = self.tier == "gemini"
        # Gemini voices are BARE names (e.g. "Aoede") with no lang prefix, so
        # the caller's lang is authoritative; other tiers derive lang from
        # the voice.
        if voice and not is_gemini:
            derived = self._derive_lang_from_voice(voice)
            lang_full = derived or self._normalize_lang(lang)
        else:
            lang_full = self._normalize_lang(lang)
        voice_name = voice or self._default_voice(lang_full)
        # 1.0 is natural pace for Cloud TTS WaveNet/Neural2/Chirp3HD.
        rate = float((extras or {}).get("rate", 1.0))
        prompt = (extras or {}).get("prompt") or None

        if is_gemini:
            # Gemini-TTS: pass the style "prompt" (voice direction) + the model.
            # speaking_rate isn't part of the Gemini control surface (the prompt
            # steers pace/tone), so we omit it to avoid a 400.
            synthesis_input = (
                texttospeech.SynthesisInput(text=text, prompt=prompt)
                if prompt
                else texttospeech.SynthesisInput(text=text)
            )
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=lang_full,
                name=voice_name,
                model_name=_GEMINI_TTS_MODEL,
            )
            audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
        else:
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=lang_full,
                name=voice_name,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=rate,
            )

        client = self._lazy_client()
        try:
            response = await asyncio.to_thread(
                client.synthesize_speech,
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
        except Exception as exc:
            # Translate provider errors to RuntimeError per Protocol contract.
            # Route layer maps to 503.
            logger.warning(
                "Cloud TTS synthesis failed (tier=%s, voice=%s): %s",
                self.tier,
                voice_name,
                exc,
            )
            raise RuntimeError(f"Cloud TTS synthesis failed: {exc}") from exc

        return bytes(response.audio_content), "audio/mpeg"

    def describe(self) -> VoiceCapabilities:
        return VoiceCapabilities(
            tts=True,
            stt=False,
            streaming=False,
            # Languages we have a default voice for. Cloud TTS supports many
            # more; the frontend can still pass explicit voices for those.
            languages=sorted({lang for _, lang in _DEFAULT_VOICE}),
        )

    # --- helpers ---

    def _normalize_lang(self, lang: str) -> str:
        """Map short BCP-47 to the full form Cloud TTS expects."""
        return _LANG_DEFAULTS.get(lang, lang)

    def _derive_lang_from_voice(self, voice_name: str) -> str | None:
        """Extract the BCP-47 lang code from a Cloud TTS voice name.

        Cloud TTS voice names follow `<lang>-<REGION>-...` convention:
            es-ES-Chirp3-HD-Charon -> es-ES
            en-US-Wavenet-A        -> en-US

        Returns the two-part prefix on success, or None if the voice name
        doesn't follow the convention (caller falls back to normalising the
        supplied `lang` arg).
        """
        parts = voice_name.split("-")
        if len(parts) < 2:
            return None
        # First part is always a 2-letter lang, second is 2-letter region.
        if len(parts[0]) != 2 or len(parts[1]) != 2:
            return None
        return f"{parts[0]}-{parts[1]}"

    def _default_voice(self, lang_full: str) -> str:
        """Pick a sensible default voice for (tier, lang_full)."""
        try:
            return _DEFAULT_VOICE[(self.tier, lang_full)]
        except KeyError:
            raise ValueError(
                f"No default {self.tier} voice for {lang_full!r}. Pass an explicit `voice=` or extend _DEFAULT_VOICE."
            ) from None

    def _lazy_client(self) -> texttospeech.TextToSpeechClient:
        if self._client is None:
            endpoint = os.getenv("VOICE_TTS_ENDPOINT")
            if endpoint:
                from google.api_core.client_options import ClientOptions

                self._client = texttospeech.TextToSpeechClient(
                    client_options=ClientOptions(api_endpoint=endpoint),
                )
            else:
                self._client = texttospeech.TextToSpeechClient()
        return self._client
