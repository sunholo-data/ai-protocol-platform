"""Voice provider HTTP routes - read-aloud (TTS) synthesize + config.

Three routes (mounted so the final paths are /api/voice/...):

  GET  /api/voice/config?skill_id=...  - return {tts:{provider,voice,language},
                                         capabilities} for this skill (or the
                                         platform default).
  POST /api/voice/tts/synthesize       - text -> audio/mpeg (cache-first).
  GET  /api/voice/voices               - curated voice list for a picker.

Auth: every route is gated by ``get_current_user`` (Firebase / group /
LOCAL_MODE), the same dependency the document routes use. A request with
no bearer returns 401.

Security: synthesized audio is returned as bytes over this authenticated
route. We NEVER produce or return a public GCS URL - the cache bucket is
read/written by the backend SA only.

Voice precedence (most specific wins):
  1. skill.persona.voice (SkillVoiceConfig)
  2. DEFAULT_VOICE (Aitana, es-ES-Chirp3-HD-Aoede) / env VOICE_TTS_PROVIDER

There is NO class layer in v6 (stripped from the AIPLA fork).

OTel span ``voice.synthesize`` attrs: voice.provider, voice.chars,
voice.lang, voice.cache_hit, voice.cost_estimate_usd.

Ported from the AIPLA fork (voice-provider-abstraction), v6.6.0 M2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from auth import User, get_current_user
from db.models import SkillVoiceConfig
from skills.skill_config import get_skill
from voice import DEFAULT_VOICE, get_tts
from voice.cache import CacheKey, TTSCache
from voice.cost import tts_cost_usd
from voice.voices import SUPPORTED_LANGS, get_voices_for_lang

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

_CurrentUser = Annotated[User, Depends(get_current_user)]


class _Sentinel:
    """Marker so we can tell "never tried to build" from "tried, got None"."""


_NOT_BUILT = _Sentinel()
# Lazily-built shared cache so the GCS client is constructed once per
# process. None when VOICE_TTS_CACHE_BUCKET is unset (dev without a
# bucket); routes treat that as miss-every-time + skip-write.
_cache_singleton: TTSCache | None | _Sentinel = _NOT_BUILT


def _get_cache() -> TTSCache | None:
    """Process-wide cache singleton. None means no cache configured."""
    global _cache_singleton
    if isinstance(_cache_singleton, _Sentinel):
        _cache_singleton = TTSCache.from_env()
    return _cache_singleton


def _reset_cache_for_tests() -> None:
    """Force the cache singleton to rebuild on next call. Test helper."""
    global _cache_singleton
    _cache_singleton = _NOT_BUILT


# --- voice resolution ---


@dataclass
class ResolvedVoice:
    """The voice a chat turn should speak in, resolved ONCE from the full chain.

    Used by BOTH ``GET /config`` (tells the frontend what to request) and
    ``POST /synthesize`` (actually picks the provider). One resolver means
    the two endpoints can never drift.

    ``provider`` is a registry name override (e.g. ``"gcp_wavenet"``) or
    None to fall back to the env default via ``get_tts``.
    """

    provider: str | None = None
    voice: str | None = None
    lang: str | None = None
    rate: float = 1.0
    # Whether read-aloud is enabled for this skill. The frontend hides the
    # speaker button when False. Defaults True (opt-out per skill).
    enabled: bool = True
    # Voice direction / "Style Instructions" for promptable (Gemini-TTS)
    # voices. Sourced from the skill persona; ignored by non-Gemini tiers.
    prompt: str | None = None


def _skill_voice(skill: object | None) -> SkillVoiceConfig | None:
    """Read ``skill.persona.voice`` defensively. None when absent."""
    if skill is None:
        return None
    persona = getattr(skill, "persona", None)
    if persona is None:
        return None
    return getattr(persona, "voice", None)


def resolve_voice(skill: object | None) -> ResolvedVoice:
    """Resolve the effective voice for a skill - the single source of truth.

    Precedence: skill.persona.voice fills any field it sets; the platform
    DEFAULT_VOICE (Aitana) fills every remaining gap so a skill with no
    persona voice still speaks rather than going silent.
    """
    rv = ResolvedVoice()
    sv = _skill_voice(skill)
    if sv is not None:
        rv.provider = sv.tts_provider
        rv.voice = sv.tts_voice
        rv.lang = sv.language
        rv.rate = sv.rate if sv.rate is not None else 1.0
        rv.enabled = sv.enabled
        rv.prompt = sv.voice_prompt
    # Fill remaining gaps from the platform default (Aitana).
    rv.provider = rv.provider or DEFAULT_VOICE["provider"]
    rv.voice = rv.voice or DEFAULT_VOICE["voice"]
    rv.lang = rv.lang or DEFAULT_VOICE["language"]
    return rv


# --- request / response models ---


class SynthesizeRequest(BaseModel):
    """Body for POST /api/voice/tts/synthesize."""

    text: str = Field(min_length=1, max_length=5000)
    lang: str | None = Field(default=None, max_length=16)
    voice: str | None = Field(default=None, max_length=64)
    skill_id: str | None = Field(default=None, alias="skillId", max_length=128)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# --- routes ---


def _resolve(ref: str):
    """Skill by canonical id, falling back to a friendly slug.

    Two-step (rather than resolve_skill_ref alone) so existing tests that
    patch ``voice_routes.get_skill`` keep working.
    """
    skill = get_skill(ref)
    if skill is not None:
        return skill
    from skills.skill_config import resolve_skill_ref

    return resolve_skill_ref(ref)


@router.get("/config")
async def get_config(user: _CurrentUser, skill_id: str | None = None) -> dict[str, Any]:
    """Return the voice config the frontend should use for `skill_id`.

    Response shape:
      {
        "tts": {"provider": str, "voice": str|null, "language": str,
                "capabilities": VoiceCapabilities}
      }

    When the skill has no persona voice (or no skill_id) this resolves the
    ONE platform default: Aitana (gcp_chirp3hd / es-ES-Chirp3-HD-Aoede / es).
    """
    # Alias-tolerant (CLAUDE.md #9). This one fails SILENTLY: an unresolved
    # ref just yields None, so the caller silently gets the platform default
    # voice instead of the skill's persona — a wrong result, not an error.
    skill = _resolve(skill_id) if skill_id else None
    rv = resolve_voice(skill)
    tts = get_tts(rv.provider)

    logger.info(
        "voice/config skill_id=%r skill_found=%s tts.provider=%s tts.voice=%s tts.lang=%s",
        skill_id,
        skill is not None,
        tts.name,
        rv.voice,
        rv.lang,
    )

    return {
        "tts": {
            "provider": tts.name,
            "voice": rv.voice,
            "language": rv.lang,
            "enabled": rv.enabled,
            "capabilities": tts.describe(),
        },
    }


@router.get("/voices")
async def list_voices(user: _CurrentUser, lang: str | None = None) -> dict[str, Any]:
    """Curated list of Cloud TTS voices for a voice picker.

    `lang` (BCP-47 short tag) filters to just that language. Omit to get
    every language's voices in one response.
    """
    if lang:
        return {
            "languages": [lang],
            "voices": {lang: get_voices_for_lang(lang)},
        }
    return {
        "languages": SUPPORTED_LANGS,
        "voices": {lang_key: get_voices_for_lang(lang_key) for lang_key in SUPPORTED_LANGS},
    }


@router.post("/tts/synthesize")
async def synthesize(body: SynthesizeRequest, user: _CurrentUser) -> Response:
    """Synthesize text to audio. Cache-first; provider on miss.

    Returns:
      - 200 audio/mpeg blob (provider mime) on success.
      - 200 JSON {"provider": "browser"} when config selects browser -
        the frontend then uses Web Speech locally.
      - 400 on bad input, 503 on provider failure.
    """
    skill = _resolve(body.skill_id) if body.skill_id else None
    rv = resolve_voice(skill)
    provider = get_tts(rv.provider)
    # The frontend sends the voice + lang it got from /config; trust it, but
    # fall back to the resolver's values so a direct API caller still gets
    # the persona / default voice.
    effective_voice = body.voice or rv.voice
    effective_lang = body.lang or rv.lang or DEFAULT_VOICE["language"]
    rate = rv.rate

    logger.info(
        "voice/synthesize skill_id=%r skill_found=%s provider=%s lang=%s voice=%s chars=%d",
        body.skill_id,
        skill is not None,
        provider.name,
        effective_lang,
        effective_voice,
        len(body.text),
    )

    with _tracer.start_as_current_span("voice.synthesize") as span:
        span.set_attribute("voice.provider", provider.name)
        span.set_attribute("voice.chars", len(body.text))
        span.set_attribute("voice.lang", effective_lang)

        # Browser path: signal to the FE, no synthesis.
        if provider.name == "browser":
            span.set_attribute("voice.cache_hit", False)
            span.set_attribute("voice.cost_estimate_usd", 0.0)
            return Response(
                content='{"provider":"browser"}',
                media_type="application/json",
                headers={"X-Voice-Provider": "browser"},
            )

        # Fold the style prompt into the cache key's voice slot so a
        # different voice direction produces a distinct cached entry.
        voice_for_key = effective_voice or "_default_"
        if rv.prompt:
            import hashlib

            voice_for_key = f"{voice_for_key}:p{hashlib.sha256(rv.prompt.encode()).hexdigest()[:8]}"
        key = CacheKey(
            provider=provider.name,
            voice=voice_for_key,
            lang=effective_lang,
            rate=rate,
            text=body.text,
        )

        cache = _get_cache()
        if cache is not None:
            hit = await cache.lookup(key)
            if hit is not None:
                audio, mime = hit
                span.set_attribute("voice.cache_hit", True)
                span.set_attribute("voice.cost_estimate_usd", 0.0)
                return _audio_response(audio, mime, provider.name, cache_hit=True, cost=0.0)

        span.set_attribute("voice.cache_hit", False)

        # Synthesize.
        try:
            audio, mime = await provider.synthesize(
                text=body.text,
                lang=effective_lang,
                voice=effective_voice,
                extras={"rate": rate, "prompt": rv.prompt},
            )
        except RuntimeError as exc:
            logger.warning("Voice synthesize failed: %s", exc)
            span.set_attribute("voice.error", str(exc))
            raise HTTPException(status_code=503, detail="voice provider unavailable") from exc
        except ValueError as exc:
            span.set_attribute("voice.error", str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cost = tts_cost_usd(provider.name, len(body.text))
        span.set_attribute("voice.cost_estimate_usd", cost)

        # Best-effort cache write.
        if cache is not None:
            await cache.write(key, audio, mime)

        return _audio_response(audio, mime, provider.name, cache_hit=False, cost=cost)


def _audio_response(audio: bytes, mime: str, provider_name: str, *, cache_hit: bool, cost: float) -> Response:
    return Response(
        content=audio,
        media_type=mime,
        headers={
            "X-Voice-Provider": provider_name,
            "X-Voice-Cache-Hit": "true" if cache_hit else "false",
            "X-Voice-Cost-Usd": f"{cost:.6f}",
        },
    )
