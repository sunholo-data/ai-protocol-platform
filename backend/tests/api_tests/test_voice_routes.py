"""API tests for /api/voice/* (read-aloud / TTS routes, v6.6.0 M2).

Covers:
  - synthesize returns 401 without a bearer (auth-gated).
  - synthesize with a mocked auth user + mocked TTS provider returns
    audio/mpeg.
  - an identical second call is a cache hit (in-memory cache double) with
    cost 0.
  - /api/voice/config resolves the ONE platform default voice (Aitana,
    es-ES-Wavenet-C) when a skill has no persona voice.

The google.cloud.texttospeech client is never hit - we patch the
provider's synthesize at the registry boundary. Follows the
dependency_overrides + patch style used in test_doc_folders.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import protocols.voice_routes as voice_routes
from auth import User, get_current_user
from voice.cache import CacheKey

_USER = User(uid="user_a", email="alice@example.com", domain="example.com")


class _InMemoryCache:
    """Cache double matching TTSCache's async lookup/write contract.

    Records lookups + writes so tests can assert cache-hit-on-second-call
    without touching GCS.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, str]] = {}
        self.lookups = 0
        self.writes = 0

    async def lookup(self, key: CacheKey) -> tuple[bytes, str] | None:
        self.lookups += 1
        return self._store.get(key.hash())

    async def write(self, key: CacheKey, audio: bytes, mime: str) -> None:
        self.writes += 1
        self._store[key.hash()] = (audio, mime)


class _FakeTTSProvider:
    """Stand-in for GCPTTSProvider. Counts synth calls; never hits GCP."""

    name = "gcp_wavenet"

    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, text, lang, voice, extras):
        self.calls += 1
        return b"FAKE_MP3_BYTES", "audio/mpeg"

    def describe(self):
        return {"tts": True, "stt": False, "streaming": False, "languages": ["es-ES", "en-US"]}


@pytest.fixture()
def client_authed() -> TestClient:
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


@pytest.fixture()
def client_anon() -> TestClient:
    app = FastAPI()
    app.include_router(voice_routes.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache():
    voice_routes._reset_cache_for_tests()
    yield
    voice_routes._reset_cache_for_tests()


class TestAuth:
    def test_synthesize_requires_bearer(self, client_anon: TestClient):
        resp = client_anon.post(
            "/api/voice/tts/synthesize",
            json={"text": "hola", "lang": "es"},
        )
        assert resp.status_code == 401

    def test_config_requires_bearer(self, client_anon: TestClient):
        resp = client_anon.get("/api/voice/config")
        assert resp.status_code == 401


class TestSynthesize:
    def test_returns_audio_mpeg(self, client_authed: TestClient):
        provider = _FakeTTSProvider()
        cache = _InMemoryCache()
        with (
            patch.object(voice_routes, "get_tts", return_value=provider),
            patch.object(voice_routes, "_get_cache", return_value=cache),
        ):
            resp = client_authed.post(
                "/api/voice/tts/synthesize",
                json={"text": "Hola, soy Aitana", "lang": "es"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == b"FAKE_MP3_BYTES"
        assert resp.headers["X-Voice-Provider"] == "gcp_wavenet"
        assert resp.headers["X-Voice-Cache-Hit"] == "false"
        assert provider.calls == 1

    def test_identical_second_call_is_cache_hit_cost_zero(self, client_authed: TestClient):
        provider = _FakeTTSProvider()
        cache = _InMemoryCache()
        payload = {"text": "Hola, soy Aitana", "lang": "es"}
        with (
            patch.object(voice_routes, "get_tts", return_value=provider),
            patch.object(voice_routes, "_get_cache", return_value=cache),
        ):
            first = client_authed.post("/api/voice/tts/synthesize", json=payload)
            second = client_authed.post("/api/voice/tts/synthesize", json=payload)

        assert first.status_code == 200
        assert first.headers["X-Voice-Cache-Hit"] == "false"
        assert second.status_code == 200
        assert second.headers["X-Voice-Cache-Hit"] == "true"
        assert second.headers["X-Voice-Cost-Usd"] == f"{0.0:.6f}"
        # The provider was only invoked once; the second call came from cache.
        assert provider.calls == 1
        assert cache.writes == 1

    def test_provider_runtime_error_maps_to_503(self, client_authed: TestClient):
        class _Boom(_FakeTTSProvider):
            async def synthesize(self, text, lang, voice, extras):
                raise RuntimeError("api down")

        with (
            patch.object(voice_routes, "get_tts", return_value=_Boom()),
            patch.object(voice_routes, "_get_cache", return_value=None),
        ):
            resp = client_authed.post(
                "/api/voice/tts/synthesize",
                json={"text": "hola", "lang": "es"},
            )
        assert resp.status_code == 503


class TestConfig:
    def test_resolves_default_voice_when_no_skill(self, client_authed: TestClient):
        # No skill_id -> the ONE platform default (Aitana / es-ES-Chirp3-HD-Aoede).
        # Patch get_tts so the real GCP client is never constructed. `provider`
        # in the response is the (patched) provider's name; `voice` is the
        # resolved default from DEFAULT_VOICE.
        provider = _FakeTTSProvider()
        with patch.object(voice_routes, "get_tts", return_value=provider):
            resp = client_authed.get("/api/voice/config")
        assert resp.status_code == 200
        tts = resp.json()["tts"]
        assert tts["voice"] == "es-ES-Chirp3-HD-Aoede"
        assert tts["language"] == "es"
        # Read-aloud is on by default (opt-out per skill).
        assert tts["enabled"] is True

    def test_read_aloud_disabled_when_skill_opts_out(self, client_authed: TestClient):
        from db.models import SkillConfig, SkillPersona, SkillVoiceConfig

        skill = SkillConfig(
            name="silent-skill",
            skillId="s3",
            persona=SkillPersona(voice=SkillVoiceConfig(enabled=False)),
        )
        provider = _FakeTTSProvider()
        with (
            patch.object(voice_routes, "get_skill", return_value=skill),
            patch.object(voice_routes, "get_tts", return_value=provider),
        ):
            resp = client_authed.get("/api/voice/config?skill_id=s3")
        assert resp.status_code == 200
        assert resp.json()["tts"]["enabled"] is False

    def test_resolves_default_when_skill_has_no_persona_voice(self, client_authed: TestClient):
        from db.models import SkillConfig

        skill = SkillConfig(name="one-ppa-expert", skillId="s1")  # no persona voice
        provider = _FakeTTSProvider()
        with (
            patch.object(voice_routes, "get_skill", return_value=skill),
            patch.object(voice_routes, "get_tts", return_value=provider),
        ):
            resp = client_authed.get("/api/voice/config?skill_id=s1")
        assert resp.status_code == 200
        tts = resp.json()["tts"]
        assert tts["voice"] == "es-ES-Chirp3-HD-Aoede"
        assert tts["language"] == "es"

    def test_skill_persona_voice_overrides_default(self, client_authed: TestClient):
        from db.models import SkillConfig, SkillPersona, SkillVoiceConfig

        skill = SkillConfig(
            name="english-tutor",
            skillId="s2",
            persona=SkillPersona(
                voice=SkillVoiceConfig(ttsProvider="gcp_neural2", ttsVoice="en-US-Neural2-F", language="en")
            ),
        )
        provider = _FakeTTSProvider()
        provider.name = "gcp_neural2"
        with (
            patch.object(voice_routes, "get_skill", return_value=skill),
            patch.object(voice_routes, "get_tts", return_value=provider),
        ):
            resp = client_authed.get("/api/voice/config?skill_id=s2")
        assert resp.status_code == 200
        tts = resp.json()["tts"]
        assert tts["voice"] == "en-US-Neural2-F"
        assert tts["language"] == "en"


class TestVoicesList:
    def test_lists_curated_voices(self, client_authed: TestClient):
        resp = client_authed.get("/api/voice/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "es" in data["languages"]
        es_voices = data["voices"]["es"]
        # Picker is premium-only (Chirp3 HD), no budget Standard/WaveNet.
        assert any(v["name"] == "es-ES-Chirp3-HD-Kore" for v in es_voices)
        assert all(v["tier"] == "Chirp3 HD" for v in es_voices)

    def test_filters_by_lang(self, client_authed: TestClient):
        resp = client_authed.get("/api/voice/voices?lang=es")
        assert resp.status_code == 200
        data = resp.json()
        assert data["languages"] == ["es"]
