"""Voice provider abstraction - read-aloud (TTS) behind swappable backends.

Public surface:
    `TTSProvider` - Protocol class.
    `VoiceCapabilities` - what a provider supports.
    `get_tts(provider_override=None)` - resolve an implementation from
        override > env > default.
    `DEFAULT_VOICE` - the v6 platform default (Aitana, es-ES-Chirp3-HD-Aoede).

Ported from the AIPLA fork; STT / recording / class layers stripped.
"""

from voice.base import TTSProvider, VoiceCapabilities
from voice.registry import get_tts

# The ONE / v6 platform default voice: Aitana, a warm Spanish female
# professional voice on the premium Chirp3-HD tier (the best quality).
# Used as the fallback when a skill has no persona voice. Note Chirp3-HD is
# ~7.5x the per-character cost of WaveNet; the cache in voice/cache.py keeps
# repeat playbacks free.
DEFAULT_VOICE = {
    "provider": "gcp_chirp3hd",
    "voice": "es-ES-Chirp3-HD-Aoede",
    "language": "es",
    "rate": 1.0,
}

__all__ = [
    "DEFAULT_VOICE",
    "TTSProvider",
    "VoiceCapabilities",
    "get_tts",
]
