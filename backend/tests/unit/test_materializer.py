"""Unit tests for skill materializer — Firestore doc to ADK Skill conversion."""

import tempfile
from pathlib import Path

from google.adk.skills import load_skill_from_dir, models

from db.models import SkillConfig, SkillMetadata
from skills.skill_materializer import materialize_to_dir, skill_from_config


def _make_config(**overrides) -> SkillConfig:
    defaults = {
        "name": "test-skill",
        "description": "A test skill for unit tests.",
        "instructions": "You are a test assistant. Help with testing.",
        "skillMetadata": SkillMetadata(
            model="gemini-2.5-flash",
            tools=["ai_search"],
            toolConfigs={"ai_search": {"datastore": "ds-test"}},
        ),
        "references": {"guide.md": "# Guide\n\nSome reference content."},
    }
    defaults.update(overrides)
    return SkillConfig(**defaults)


# === skill_from_config ===


def test_skill_from_config_basic():
    config = _make_config()
    skill = skill_from_config(config)
    assert isinstance(skill, models.Skill)
    assert skill.name == "test-skill"
    assert skill.description == "A test skill for unit tests."
    assert "test assistant" in skill.instructions


def test_skill_from_config_metadata():
    config = _make_config(skillMetadata=SkillMetadata(model="gemini-2.5-pro", thinkingModel="gemini-2.5-pro"))
    skill = skill_from_config(config)
    assert skill.frontmatter.metadata["model"] == "gemini-2.5-pro"
    assert skill.frontmatter.metadata["thinkingModel"] == "gemini-2.5-pro"


def test_skill_from_config_references():
    config = _make_config(references={"a.md": "content a", "b.md": "content b"})
    skill = skill_from_config(config)
    assert skill.resources.get_reference("a.md") == "content a"
    assert skill.resources.list_references() == ["a.md", "b.md"]


def test_skill_from_config_no_optional_metadata():
    """Skills with no thinking model, no tools, no sub-skills should still work."""
    config = _make_config(skillMetadata=SkillMetadata(model="gemini-2.5-flash"))
    skill = skill_from_config(config)
    assert "thinkingModel" not in skill.frontmatter.metadata
    assert "tools" not in skill.frontmatter.metadata


def test_skill_from_config_empty_references():
    config = _make_config(references={})
    skill = skill_from_config(config)
    assert skill.resources.list_references() == []


# === materialize_to_dir ===


def test_materialize_creates_skill_md():
    config = _make_config()
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = materialize_to_dir(config, base_dir=Path(tmp))
        skill_md = (skill_dir / "SKILL.md").read_text()
        assert "name: test-skill" in skill_md
        assert "test assistant" in skill_md


def test_materialize_creates_references():
    config = _make_config(references={"guide.md": "# Guide"})
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = materialize_to_dir(config, base_dir=Path(tmp))
        assert (skill_dir / "references" / "guide.md").read_text() == "# Guide"


def test_materialize_no_references_dir_when_empty():
    config = _make_config(references={})
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = materialize_to_dir(config, base_dir=Path(tmp))
        assert not (skill_dir / "references").exists()


# === Round-trip: config → materialize → load_skill_from_dir ===


def test_round_trip_via_filesystem():
    config = _make_config()
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = materialize_to_dir(config, base_dir=Path(tmp))
        loaded = load_skill_from_dir(skill_dir)
        assert loaded.name == config.name
        assert loaded.description == config.description
        assert "test assistant" in loaded.instructions
        assert loaded.resources.get_reference("guide.md") is not None
