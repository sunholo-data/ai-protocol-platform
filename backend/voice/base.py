"""Voice provider Protocols.

Defines the narrow interface that every TTS provider implements. Two
methods total. Provider-specific quirks pass through the opaque
`extras: dict` parameter so the interface stays stable when we add
Gemini-TTS prompting, Studio prosody, etc.

Mirrors the model-selection pattern in `adk/agent.py`: an env /
skill-config / default chain picks the implementation, and the rest of
the codebase only sees the Protocol.

Ported from the AIPLA fork (voice-provider-abstraction). STT was
intentionally dropped in v6 M2 — read-aloud (TTS) only.
"""

from typing import Protocol, TypedDict, runtime_checkable


class VoiceCapabilities(TypedDict):
    """What a provider can do. Returned by `.describe()`.

    Frontend uses this (via `/api/voice/config`) to decide whether to
    render the read-aloud button. Backend uses it to refuse calls a
    provider can't serve before hitting the wire.
    """

    tts: bool
    stt: bool
    streaming: bool  # bidi audio (Gemini Live et al.); none of our v1 providers
    languages: list[str]  # BCP-47 tags the provider explicitly supports


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech provider. Implementations live in `voice/providers/`."""

    name: str
    """Registry key. Convention: lowercase, underscores. e.g. `"gcp_wavenet"`, `"browser"`."""

    async def synthesize(
        self,
        text: str,
        lang: str,
        voice: str | None,
        extras: dict | None,
    ) -> tuple[bytes, str]:
        """Synthesize `text` to audio bytes.

        Args:
            text: Plain text. The provider is responsible for any SSML
                escaping if needed; callers pass raw text.
            lang: BCP-47 language tag. Short form `"es"` / `"en"` is
                acceptable; providers normalize to their preferred form
                (e.g. `"es"` -> `"es-ES"` for GCP).
            voice: Provider-specific voice name (e.g. `"es-ES-Wavenet-C"`).
                `None` means "pick a sensible default for `lang`".
            extras: Opaque provider-specific config. e.g.
                `{"rate": 0.9}` for rate override, `{"prompt": ...}` for a
                Gemini-TTS style direction. Providers must ignore unknown keys.

        Returns:
            `(audio_bytes, mime_type)` - e.g. `(b"\\xff\\xfb...", "audio/mpeg")`.

        Raises:
            ValueError: invalid `lang`, voice unknown to the provider, etc.
            RuntimeError: provider-side failure (API down, quota, etc.).
                The route layer translates to 503.
        """
        ...

    def describe(self) -> VoiceCapabilities:
        """Report what this provider can do. Pure; no I/O."""
        ...
