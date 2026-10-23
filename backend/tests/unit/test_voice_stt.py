"""The dictation provider uses the platform's resilient multimodal seam."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from voice.providers.gemini_stt import GeminiSTTProvider
from voice.registry import get_stt


@pytest.mark.asyncio
async def test_audio_and_neutral_prompt_use_resilient_registry_call():
    with patch("voice.providers.gemini_stt.generate_content_resilient", new_callable=AsyncMock) as call:
        call.return_value = SimpleNamespace(text=" Transcribed audio ")
        result = await GeminiSTTProvider("lite").transcribe(b"audio", "audio/wav", "en", None)
    assert result == "Transcribed audio"
    args = call.call_args.kwargs
    assert args["model_ref"] == "lite"
    assert args["prompt"][0].inline_data.data == b"audio"
    assert args["prompt"][0].inline_data.mime_type == "audio/wav"
    assert "physics" not in args["prompt"][1].lower()


def test_skill_and_environment_resolution(monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "gemini")
    monkeypatch.setenv("VOICE_STT_MODEL", "pro")
    assert get_stt().model_ref == "pro"
    assert get_stt("gemini", "lite").model_ref == "lite"
    assert not get_stt("disabled").describe()["stt"]
