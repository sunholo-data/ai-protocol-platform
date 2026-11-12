"""MULTI-LANG M3: SKILL.md `locales:` → parse → validate → seed payload → SkillConfig.

The `xx` tag is the made-up test locale (sprint ruling 4): no real language is
implied upstream.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from admin.platform_seed import _parse_template, seed
from db.models import SkillConfig
from skills.skill_materializer import materialize_to_dir

SKILL_MD = """---
name: {name}
display_name: General Assistant
description: >
  Answers general questions.
initial_message: "Hi! How can I help you today?"
metadata:
  model: gemini-2.5-flash
{locales}---

Be helpful.
"""

XX_BLOCK = """locales:
  xx:
    display_name: Xeneral assistant
    initial_message: "Xi! Xow can I xelp?"
  pt_br:
    description: >
      Xescription.
"""

XX_PARSED = {
    "xx": {"display_name": "Xeneral assistant", "initial_message": "Xi! Xow can I xelp?"},
    "pt-BR": {"description": "Xescription."},
}


def _template(root, name="alpha", locales=XX_BLOCK):
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(SKILL_MD.format(name=name, locales=locales))
    return d / "SKILL.md"


def _config(name: str, **overrides) -> SkillConfig:
    return SkillConfig(
        **{
            "name": name,
            "description": "d",
            "skillId": f"platform-{name}",
            "ownerId": "aitana-platform",
            "accessControl": {"type": "public"},
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def _platform_owner_email(monkeypatch):
    monkeypatch.setenv("PLATFORM_OWNER_EMAIL", "platform@test.com")


# === parse ===


def test_parse_reads_and_normalises_the_block(tmp_path):
    parsed = _parse_template(_template(tmp_path))
    assert parsed["locales"] == XX_PARSED
    # The base fields are untouched, and the body (the instruction) is not localised.
    assert parsed["display_name"] == "General Assistant"
    assert parsed["instructions"] == "Be helpful."


def test_parse_without_a_block_is_empty(tmp_path):
    assert _parse_template(_template(tmp_path, locales=""))["locales"] == {}


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("locales:\n  xx:\n    instructions: Xe xelpful.\n", "unknown field 'instructions'"),
        ("locales:\n  english:\n    display_name: X\n", "not a language tag"),
        ("locales:\n  - xx\n", "must be a mapping"),
    ],
)
def test_parse_rejects_a_bad_block_naming_the_file(tmp_path, block, message):
    path = _template(tmp_path, locales=block)
    with pytest.raises(ValueError, match=message) as exc:
        _parse_template(path)
    assert str(path) in str(exc.value)


# === seed payloads ===


def test_seed_create_passes_locales(tmp_path):
    _template(tmp_path, "alpha")
    _template(tmp_path, "beta", locales="")
    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[]),
        patch("admin.platform_seed.skill_config.create_skill") as create,
    ):
        create.side_effect = lambda **kw: _config(kw["name"])
        summary = seed(templates_root=tmp_path)
    assert summary.created == 2
    by_name = {c.kwargs["name"]: c.kwargs for c in create.call_args_list}
    assert by_name["alpha"]["locales"] == XX_PARSED
    assert by_name["alpha"]["initialMessage"] == "Hi! How can I help you today?"
    assert "locales" not in by_name["beta"]
    # And the kwargs make a valid SkillConfig that keeps the block.
    assert SkillConfig(**by_name["alpha"], skillId="x").locales == XX_PARSED


def test_seed_refresh_pushes_locales_and_clears_removed_ones(tmp_path):
    _template(tmp_path, "alpha")
    _template(tmp_path, "beta", locales="")
    existing = [_config("alpha", slug="alpha"), _config("beta", slug="beta", locales={"xx": {"display_name": "Old"}})]
    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=existing),
        patch("admin.platform_seed.skill_config.update_skill") as update,
    ):
        summary = seed(templates_root=tmp_path)
    assert summary.refreshed == 2
    payloads = dict(c.args for c in update.call_args_list)
    assert payloads["platform-alpha"]["locales"] == XX_PARSED
    # Disk is the source of truth for a template skill: a block removed from
    # SKILL.md removes the live translations too.
    assert payloads["platform-beta"]["locales"] == {}


def test_seed_marks_a_template_with_a_bad_block_failed(tmp_path):
    _template(tmp_path, "alpha", locales="locales:\n  xx:\n    instructions: X\n")
    with (
        patch("admin.platform_seed.skill_config.list_skills", return_value=[]),
        patch("admin.platform_seed.skill_config.create_skill") as create,
    ):
        summary = seed(templates_root=tmp_path)
    assert summary.failed == ["alpha"]
    create.assert_not_called()


# === model + materialiser ===


def test_skill_config_validates_and_round_trips():
    cfg = _config("alpha", locales={"pt_br": {"display_name": "X"}, "xx": {"description": ""}})
    assert cfg.locales == {"pt-BR": {"display_name": "X"}}
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["locales"] == {"pt-BR": {"display_name": "X"}}
    assert SkillConfig.model_validate(dumped).locales == cfg.locales
    assert _config("beta").locales == {}
    with pytest.raises(ValidationError, match="unknown field"):
        _config("gamma", locales={"xx": {"bio": "x"}})


def test_materialised_skill_md_reseeds_with_its_locales(tmp_path):
    cfg = _config("alpha", locales=XX_PARSED, instructions="Be helpful.")
    skill_dir = materialize_to_dir(cfg, tmp_path)
    parsed = _parse_template(skill_dir / "SKILL.md")
    assert parsed["locales"] == XX_PARSED
    # The block lives in the front matter, never in the instruction body.
    assert parsed["instructions"] == "Be helpful."
