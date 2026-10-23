"""Dictation auth, spend bounds and opt-in settings; no model/network calls."""

import io
import wave
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from db.models import SkillConfig, SkillPersona, SkillVoiceConfig
from protocols import voice_routes


def wav(seconds=0.1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(seconds * 16000))
    return output.getvalue()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_current_user] = lambda: User(uid="voice-user", email="voice@example.com")
    return TestClient(app)


def provider():
    result = AsyncMock()
    result.name = "gemini"
    result.describe = lambda: {"tts": False, "stt": True, "streaming": False, "languages": []}
    result.transcribe.return_value = "A dictation transcript"
    return result


def test_auth_required():
    app = FastAPI()
    app.include_router(voice_routes.router)
    response = TestClient(app).post("/api/voice/stt/transcribe", files={"audio": ("clip.wav", wav(), "audio/wav")})
    assert response.status_code == 401


def test_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("VOICE_STT_PROVIDER", raising=False)
    response = client.post("/api/voice/stt/transcribe", files={"audio": ("clip.wav", wav(), "audio/wav")})
    assert response.status_code == 503


def test_valid_clip_passed_inline(client):
    stt = provider()
    clip = wav()
    with patch.object(voice_routes, "get_stt", return_value=stt):
        response = client.post(
            "/api/voice/stt/transcribe", files={"audio": ("clip.wav", clip, "audio/wav")}, data={"lang": "en"}
        )
    assert response.json() == {"text": "A dictation transcript"}
    stt.transcribe.assert_awaited_once_with(clip, "audio/wav", "en", None)


@pytest.mark.parametrize("clip", [b"", b"not audio", wav(62), wav()[:-4]])
def test_invalid_or_long_clip_never_calls_model(client, clip):
    stt = provider()
    with patch.object(voice_routes, "get_stt", return_value=stt):
        response = client.post("/api/voice/stt/transcribe", files={"audio": ("clip.wav", clip, "audio/wav")})
    assert response.status_code == 400
    stt.transcribe.assert_not_awaited()


def test_hidden_skill_cannot_enable_dictation(client):
    skill = SkillConfig(name="private", skillId="private", accessControl={"type": "private"}, ownerId="other")
    with patch.object(voice_routes, "_resolve", return_value=skill), patch.object(voice_routes, "get_stt") as stt:
        response = client.post(
            "/api/voice/stt/transcribe", files={"audio": ("clip.wav", wav(), "audio/wav")}, data={"skillId": "private"}
        )
    assert response.status_code == 404
    stt.assert_not_called()


def test_skill_input_opt_out_overrides_enabled_environment(client, monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "gemini")
    skill = SkillConfig(
        name="silent-input",
        skillId="s",
        ownerId="voice-user",
        persona=SkillPersona(voice=SkillVoiceConfig(voiceInputEnabled=False)),
    )
    with patch.object(voice_routes, "_resolve", return_value=skill):
        response = client.post(
            "/api/voice/stt/transcribe", files={"audio": ("clip.wav", wav(), "audio/wav")}, data={"skillId": "s"}
        )
    assert response.status_code == 503


def test_provider_error_is_sanitised(client):
    stt = provider()
    stt.transcribe.side_effect = RuntimeError("private upstream error")
    with patch.object(voice_routes, "get_stt", return_value=stt):
        response = client.post("/api/voice/stt/transcribe", files={"audio": ("clip.wav", wav(), "audio/wav")})
    assert response.status_code == 503
    assert "private upstream" not in response.text
