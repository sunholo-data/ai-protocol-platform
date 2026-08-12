"""Unit tests for Pydantic data models — Agent Skills spec compliance."""

import pytest
from pydantic import ValidationError

from db.models import AccessControl, SkillConfig, SkillMetadata

_D = "A test skill."  # Valid default description for tests


# === SkillConfig defaults ===


def test_skill_config_defaults():
    skill = SkillConfig(name="test-skill", description=_D)
    assert skill.skill_metadata.model == "gemini-2.5-flash"
    assert skill.protocols.agui.enabled is True
    assert skill.access_control.type == "private"
    assert skill.instructions == ""
    assert skill.tags == []
    assert skill.featured is False
    assert skill.usage_count == 0


def test_skill_config_full():
    skill = SkillConfig(
        name="document-analyst",
        description="Analyze documents.",
        instructions="You are an expert.",
        skillMetadata=SkillMetadata(model="gemini-2.5-pro", tools=["ai_search"]),
        displayName="Document Analyst",
        tags=["extraction", "data"],
    )
    assert skill.name == "document-analyst"
    assert skill.display_name == "Document Analyst"
    assert skill.skill_metadata.tools == ["ai_search"]


# === Agent Skills spec: name validation ===


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "my-skill",
        "document-analyst",
        "skill123",
        "a-b-c-d",
        "x" * 64,
    ],
)
def test_valid_names(name):
    skill = SkillConfig(name=name, description=_D)
    assert skill.name == name


@pytest.mark.parametrize(
    "name,reason",
    [
        ("", "empty"),
        ("x" * 65, "too long"),
        ("My-Skill", "uppercase"),
        ("ALLCAPS", "uppercase"),
        ("-leading", "leading hyphen"),
        ("trailing-", "trailing hyphen"),
        ("double--hyphen", "consecutive hyphens"),
        ("has space", "space"),
        ("has_underscore", "underscore"),
        ("special!char", "special char"),
    ],
)
def test_invalid_names(name, reason):
    with pytest.raises(ValidationError):
        SkillConfig(name=name, description=_D)


# === Agent Skills spec: description validation ===


def test_description_empty_rejected():
    with pytest.raises(ValidationError):
        SkillConfig(name="test", description="")


def test_description_max_length():
    skill = SkillConfig(name="test", description="x" * 1024)
    assert len(skill.description) == 1024


def test_description_too_long():
    with pytest.raises(ValidationError):
        SkillConfig(name="test", description="x" * 1025)


# === Agent Skills spec: instructions validation ===


def test_instructions_max_length():
    skill = SkillConfig(name="test", description=_D, instructions="x" * 20_000)
    assert len(skill.instructions) == 20_000


def test_instructions_too_long():
    with pytest.raises(ValidationError):
        SkillConfig(name="test", description=_D, instructions="x" * 20_001)


# === Tags validation ===


def test_tags_max_count():
    skill = SkillConfig(name="test", description=_D, tags=["t"] * 10)
    assert len(skill.tags) == 10


def test_tags_too_many():
    with pytest.raises(ValidationError):
        SkillConfig(name="test", description=_D, tags=["t"] * 11)


def test_tag_too_long():
    with pytest.raises(ValidationError):
        SkillConfig(name="test", description=_D, tags=["x" * 51])


# === AccessControl ===


@pytest.mark.parametrize("access_type", ["private", "public", "domain", "specific"])
def test_valid_access_types(access_type):
    ac = AccessControl(type=access_type)
    assert ac.type == access_type


def test_invalid_access_type():
    with pytest.raises(ValidationError):
        AccessControl(type="invalid")


# === SkillMetadata ===


def test_skill_metadata_defaults():
    meta = SkillMetadata()
    assert meta.author == "aitana"
    assert meta.model == "gemini-2.5-flash"
    assert meta.thinking_model is None
    assert meta.tools == []
    assert meta.tool_configs == {}
    assert meta.sub_skills == []


def test_skill_metadata_alias_round_trip():
    meta = SkillMetadata(thinkingModel="gemini-2.5-pro", toolConfigs={"a": {"b": 1}}, subSkills=["other"])
    data = meta.model_dump(by_alias=True)
    assert data["thinkingModel"] == "gemini-2.5-pro"
    assert data["toolConfigs"] == {"a": {"b": 1}}
    restored = SkillMetadata.model_validate(data)
    assert restored.thinking_model == "gemini-2.5-pro"


# === Round-trip ===


def test_model_dump_round_trip():
    skill = SkillConfig(
        name="test-skill",
        description="Test.",
        instructions="Do things.",
        skillMetadata=SkillMetadata(model="gemini-2.5-pro", tools=["ai_search"]),
        displayName="Test Skill",
        tags=["test"],
    )
    data = skill.model_dump(by_alias=True)
    restored = SkillConfig.model_validate(data)
    assert restored.name == skill.name
    assert restored.skill_metadata.model == "gemini-2.5-pro"
    assert restored.display_name == "Test Skill"
    assert restored.skill_id == skill.skill_id
