"""Voice provider registry (TTS only).

Resolution chain (highest precedence first):
    1. SkillVoiceConfig.tts_provider (via the resolver in voice_routes)
    2. Env var VOICE_TTS_PROVIDER
    3. Default ("gcp_chirp3hd" - the v6 Aitana premium default voice tier)

Mirrors `backend/adk/agent.py` model resolution - explicit string
dispatch, no clever registration decorators, easy to grep for "which
providers exist". When a new provider lands in `voice/providers/`, add
one branch to `_build_tts`.

STT was dropped in the v6 M2 port - read-aloud only.
"""

from __future__ import annotations

import logging
import os

from voice.base import TTSProvider

logger = logging.getLogger(__name__)


# The v6 default voice tier is premium Chirp3-HD (Aitana =
# es-ES-Chirp3-HD-Aoede). The fork defaulted to "browser"; v6 synthesizes
# server-side so the default is a real Cloud TTS tier.
_TTS_DEFAULT = "gcp_chirp3hd"


def get_tts(provider_override: str | None = None) -> TTSProvider:
    """Resolve the TTS provider for this request.

    Args:
        provider_override: Optional. A registry name (e.g. `"gcp_wavenet"`,
            `"browser"`) resolved by the route layer from the skill's
            persona voice. When set it wins; otherwise falls through to
            env / default.

    Returns:
        A `TTSProvider` instance.

    Raises:
        ValueError: if a configured provider name is unknown.
    """
    name = _resolve_name(
        override=provider_override,
        env_var="VOICE_TTS_PROVIDER",
        default=_TTS_DEFAULT,
    )
    return _build_tts(name)


# --- helpers ---


def _resolve_name(override: str | None, env_var: str, default: str) -> str:
    """Pick provider name from override > env > default."""
    if override:
        return override
    env = os.getenv(env_var)
    if env:
        return env
    return default


def _build_tts(name: str) -> TTSProvider:
    """String dispatch for TTS providers.

    Add new branches here as providers land in `voice/providers/`.
    """
    if name == "browser":
        # Imported lazily to keep tests free of GCP client construction.
        from voice.providers.browser import BrowserTTSProvider

        return BrowserTTSProvider()
    if name.startswith("gcp_"):
        from voice.providers.gcp_tts import GCPTTSProvider

        return GCPTTSProvider(tier=name.removeprefix("gcp_"))
    raise ValueError(f"Unknown TTS provider {name!r}. Known: browser, gcp_<tier>.")
