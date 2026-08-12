"""Unit tests for the per-skill persona + voice model.

v6.6.0 ONE-FORK-CONVERGENCE M1: promote the static per-skill `avatar` into a
first-class `SkillPersona` bundle (avatar + interaction style + voice). Legacy
records with only a top-level `avatar` must fold into `persona.avatar` on load.
"""

from __future__ import annotations

import pytest

from db.models import SkillConfig, SkillPersona, SkillVoiceConfig


class TestSkillVoiceConfig:
    def test_defaults(self):
        v = SkillVoiceConfig()
        assert v.rate == 1.0
        assert v.tts_provider is None

    def test_aitana_spanish_professional_voice(self):
        v = SkillVoiceConfig(tts_provider="gcp_wavenet", tts_voice="es-ES-Wavenet-C", language="es")
        assert v.tts_voice == "es-ES-Wavenet-C"
        assert v.language == "es"

    def test_rate_out_of_range_raises(self):
        with pytest.raises(ValueError):
            SkillVoiceConfig(rate=9.0)


class TestSkillPersona:
    def test_defaults(self):
        p = SkillPersona()
        assert p.interaction_style == "concise"
        assert p.avatar == ""

    def test_invalid_interaction_style_raises(self):
        with pytest.raises(ValueError):
            SkillPersona(interaction_style="sarcastic")

    def test_voice_nested(self):
        p = SkillPersona(voice=SkillVoiceConfig(tts_voice="es-ES-Wavenet-C"))
        assert p.voice is not None
        assert p.voice.tts_voice == "es-ES-Wavenet-C"


class TestLegacyAvatarFoldIn:
    def test_legacy_avatar_only_folds_into_persona(self):
        cfg = SkillConfig(name="legacy-skill", description="x", avatar="https://cdn/x.png")
        assert cfg.persona is not None
        assert cfg.persona.avatar == "https://cdn/x.png"

    def test_no_avatar_no_persona_stays_none(self):
        cfg = SkillConfig(name="bare-skill", description="x")
        assert cfg.persona is None

    def test_explicit_persona_not_overwritten(self):
        cfg = SkillConfig(
            name="rich-skill",
            description="x",
            avatar="https://cdn/legacy.png",
            persona=SkillPersona(avatar="https://cdn/new.png", interaction_style="rigorous"),
        )
        assert cfg.persona.avatar == "https://cdn/new.png"
        assert cfg.persona.interaction_style == "rigorous"

    def test_persona_round_trips_by_alias(self):
        cfg = SkillConfig(
            name="alias-skill",
            description="x",
            persona=SkillPersona(interaction_style="warm"),
        )
        dumped = cfg.model_dump(by_alias=True)
        assert dumped["persona"]["interactionStyle"] == "warm"
