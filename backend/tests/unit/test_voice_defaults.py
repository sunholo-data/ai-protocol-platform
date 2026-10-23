"""Deployment voice defaults must reach both config and synthesis resolution."""

from db.models import SkillConfig, SkillPersona, SkillVoiceConfig
from protocols.voice_routes import resolve_voice
from voice import DEFAULT_VOICE


def test_deployment_defaults_replace_the_fallback_accent(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "gcp_chirp3hd")
    monkeypatch.setenv("VOICE_TTS_VOICE", "en-GB-Chirp3-HD-Aoede")
    monkeypatch.setenv("VOICE_TTS_LANGUAGE", "en-GB")
    resolved = resolve_voice(None)
    assert resolved.voice == "en-GB-Chirp3-HD-Aoede"
    assert resolved.lang == "en-GB"
    assert resolved.provider == "gcp_chirp3hd"


def test_skill_voice_wins_over_deployment_defaults(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "browser")
    monkeypatch.setenv("VOICE_TTS_VOICE", "en-GB-Chirp3-HD-Aoede")
    monkeypatch.setenv("VOICE_TTS_LANGUAGE", "en-GB")
    skill = SkillConfig(
        name="french-chat",
        skillId="french",
        persona=SkillPersona(
            voice=SkillVoiceConfig(ttsProvider="gcp_chirp3hd", ttsVoice="fr-FR-Chirp3-HD-Kore", language="fr-FR")
        ),
    )
    resolved = resolve_voice(skill)
    assert resolved.voice == "fr-FR-Chirp3-HD-Kore"
    assert resolved.lang == "fr-FR"
    assert resolved.provider == "gcp_chirp3hd"


def test_unconfigured_deployments_keep_the_existing_fallback(monkeypatch):
    for key in ("VOICE_TTS_PROVIDER", "VOICE_TTS_VOICE", "VOICE_TTS_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    resolved = resolve_voice(None)
    assert resolved.voice == DEFAULT_VOICE["voice"]
    assert resolved.lang == DEFAULT_VOICE["language"]
