"""Explicitly disabled dictation provider, ported from AIPLA."""

from voice.base import VoiceCapabilities


class NullSTTProvider:
    name = "disabled"

    async def transcribe(self, audio: bytes, mime: str, lang: str, extras: dict | None) -> str:
        raise RuntimeError("Speech-to-text is disabled")

    def describe(self) -> VoiceCapabilities:
        return {"tts": False, "stt": False, "streaming": False, "languages": []}
