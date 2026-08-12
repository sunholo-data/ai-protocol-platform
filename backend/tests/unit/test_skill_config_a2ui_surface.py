"""Unit tests for the A2UI surface fields on SkillMetadata.tool_configs.a2ui.

MULTI-SURFACE-A2UI M1 — adds optional `default_surface` + `default_update_mode`
to the A2UI tool config so skills can declare a target surface ("workspace",
"sidebar", "modal", or a fork-defined custom id) once instead of repeating
themselves on every tool call.

Backwards-compat contract:
  - `tool_configs.a2ui` may be absent → defaults to no surface override.
  - `tool_configs.a2ui = {}` → defaults to no surface override.
  - `default_surface=None` produces a payload that is byte-identical to the
    pre-M1 path (the wrapper-toolset omits the surface keys entirely).

Server-side rule:
  - `default_update_mode="patch"` requires a persistent surface (i.e. NOT
    None and NOT "chat"). The chat surface is turn-scoped; "patch" against
    it is meaningless and the frontend cannot honour it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adk.a2ui import A2uiToolConfig
from db.models import SkillConfig, SkillMetadata

# === A2uiToolConfig — direct construction ===


def test_a2ui_tool_config_defaults_empty():
    """No fields set → no surface override, no patch mode, fully back-compat."""
    cfg = A2uiToolConfig()
    assert cfg.default_surface is None
    assert cfg.default_update_mode == "replace"


def test_a2ui_tool_config_accepts_workspace_surface():
    cfg = A2uiToolConfig(default_surface="workspace")
    assert cfg.default_surface == "workspace"
    assert cfg.default_update_mode == "replace"


def test_a2ui_tool_config_accepts_sidebar_surface():
    assert A2uiToolConfig(default_surface="sidebar").default_surface == "sidebar"


def test_a2ui_tool_config_accepts_modal_surface():
    assert A2uiToolConfig(default_surface="modal").default_surface == "modal"


def test_a2ui_tool_config_accepts_chat_surface():
    """Explicit `chat` is allowed — it documents the default behaviour."""
    assert A2uiToolConfig(default_surface="chat").default_surface == "chat"


def test_a2ui_tool_config_accepts_fork_custom_surface():
    """Forks (AIPLA, a tutoring fork) may declare custom surface ids."""
    cfg = A2uiToolConfig(default_surface="aipla:teacher-grid")
    assert cfg.default_surface == "aipla:teacher-grid"


def test_a2ui_tool_config_accepts_patch_with_workspace():
    cfg = A2uiToolConfig(default_surface="workspace", default_update_mode="patch")
    assert cfg.default_update_mode == "patch"


def test_a2ui_tool_config_accepts_patch_with_sidebar():
    cfg = A2uiToolConfig(default_surface="sidebar", default_update_mode="patch")
    assert cfg.default_update_mode == "patch"


def test_a2ui_tool_config_rejects_patch_with_no_surface():
    """`patch` needs a persistent surface. None is the same as the turn-scoped chat."""
    with pytest.raises(ValidationError) as excinfo:
        A2uiToolConfig(default_surface=None, default_update_mode="patch")
    assert "patch" in str(excinfo.value).lower()


def test_a2ui_tool_config_rejects_patch_with_chat_surface():
    """`chat` is turn-scoped; you cannot patch into a tree that resets per turn."""
    with pytest.raises(ValidationError) as excinfo:
        A2uiToolConfig(default_surface="chat", default_update_mode="patch")
    assert "patch" in str(excinfo.value).lower()
    assert "chat" in str(excinfo.value).lower()


def test_a2ui_tool_config_rejects_unknown_update_mode():
    with pytest.raises(ValidationError):
        A2uiToolConfig(default_update_mode="merge")  # type: ignore[arg-type]


# === SkillMetadata.tool_configs.a2ui — round-trip via from_tool_configs ===


def test_a2ui_tool_config_from_empty_tool_configs():
    """Skills with no `a2ui` key in tool_configs get the empty default."""
    cfg = A2uiToolConfig.from_tool_configs({})
    assert cfg.default_surface is None
    assert cfg.default_update_mode == "replace"


def test_a2ui_tool_config_from_tool_configs_with_other_keys_only():
    """Skills with mcp/ai_search config but no a2ui still get the empty default."""
    cfg = A2uiToolConfig.from_tool_configs({"mcp": {"servers": ["ext-apps-map"]}})
    assert cfg.default_surface is None


def test_a2ui_tool_config_from_tool_configs_with_a2ui_workspace():
    cfg = A2uiToolConfig.from_tool_configs({"a2ui": {"default_surface": "workspace"}})
    assert cfg.default_surface == "workspace"


def test_a2ui_tool_config_from_tool_configs_with_a2ui_patch():
    cfg = A2uiToolConfig.from_tool_configs({"a2ui": {"default_surface": "workspace", "default_update_mode": "patch"}})
    assert cfg.default_surface == "workspace"
    assert cfg.default_update_mode == "patch"


def test_a2ui_tool_config_from_tool_configs_invalid_combo_raises():
    """Even when sourced from a raw dict, the validation rule applies."""
    with pytest.raises(ValidationError):
        A2uiToolConfig.from_tool_configs({"a2ui": {"default_update_mode": "patch"}})


# === enabled flag (tool-opt-out sprint) ===


def test_a2ui_tool_config_enabled_defaults_to_true():
    """Default behaviour: A2UI toolset is attached (backwards compat)."""
    assert A2uiToolConfig().enabled is True


def test_a2ui_tool_config_enabled_false_accepted():
    """Chat-only skills can disable A2UI via enabled=False."""
    cfg = A2uiToolConfig(enabled=False)
    assert cfg.enabled is False


def test_a2ui_tool_config_from_tool_configs_enabled_false():
    """SKILL.md with a2ui.enabled: false parses correctly."""
    cfg = A2uiToolConfig.from_tool_configs({"a2ui": {"enabled": False}})
    assert cfg.enabled is False


def test_a2ui_tool_config_enabled_false_skips_patch_validation():
    """enabled=False + patch + no surface should NOT raise — the surface
    validation is irrelevant when A2UI is disabled entirely."""
    cfg = A2uiToolConfig(enabled=False, default_update_mode="patch")
    assert cfg.enabled is False  # no ValidationError


def test_a2ui_tool_config_from_tool_configs_enabled_false_with_surface():
    """enabled=False can coexist with surface fields (they're ignored at runtime)."""
    cfg = A2uiToolConfig.from_tool_configs({"a2ui": {"enabled": False, "default_surface": "workspace"}})
    assert cfg.enabled is False
    assert cfg.default_surface == "workspace"


def test_a2ui_tool_config_from_tool_configs_handles_non_dict_a2ui():
    """Defensive: `a2ui` set to a non-dict (mis-shaped config) should not crash;
    falls back to the empty default rather than raising."""
    cfg = A2uiToolConfig.from_tool_configs({"a2ui": None})
    assert cfg.default_surface is None


# === Backwards compatibility — SkillConfig loads cleanly without the field ===


def test_skill_config_loads_without_a2ui_section():
    """Pre-M1 skills (no `a2ui` in tool_configs) must load unchanged."""
    skill = SkillConfig(
        name="legacy-skill",
        description="A skill that pre-dates M1.",
        skillMetadata=SkillMetadata(
            tools=["list_documents"],
            toolConfigs={"ai_search": {"datastore": "ds-docs"}},
        ),
    )
    # No exception. The a2ui sub-config defaults to the empty surface.
    derived = A2uiToolConfig.from_tool_configs(skill.skill_metadata.tool_configs)
    assert derived.default_surface is None


def test_skill_config_loads_with_a2ui_workspace_surface():
    """New M1 skills with `default_surface` round-trip through SkillConfig."""
    skill = SkillConfig(
        name="dashboard-skill",
        description="Renders a workspace dashboard.",
        skillMetadata=SkillMetadata(
            tools=["list_documents"],
            toolConfigs={
                "a2ui": {"default_surface": "workspace"},
                "mcp": {"servers": ["ext-apps-map"]},
            },
        ),
    )
    derived = A2uiToolConfig.from_tool_configs(skill.skill_metadata.tool_configs)
    assert derived.default_surface == "workspace"


def test_skill_config_round_trip_preserves_a2ui_section():
    """model_dump → model_validate keeps the a2ui section intact."""
    skill = SkillConfig(
        name="dashboard-skill",
        description="Renders a workspace dashboard.",
        skillMetadata=SkillMetadata(
            toolConfigs={
                "a2ui": {"default_surface": "workspace", "default_update_mode": "patch"},
            },
        ),
    )
    data = skill.model_dump(by_alias=True)
    restored = SkillConfig.model_validate(data)
    a2ui_dict = restored.skill_metadata.tool_configs.get("a2ui")
    assert a2ui_dict == {"default_surface": "workspace", "default_update_mode": "patch"}


def test_skill_config_with_invalid_a2ui_combo_via_factory_raises():
    """The factory entrypoint surfaces the validation error to the caller."""
    skill = SkillConfig(
        name="bad-skill",
        description="A skill with an illegal patch/no-surface combo.",
        skillMetadata=SkillMetadata(
            toolConfigs={"a2ui": {"default_update_mode": "patch"}},
        ),
    )
    with pytest.raises(ValidationError):
        A2uiToolConfig.from_tool_configs(skill.skill_metadata.tool_configs)


# === allow_action_triggered_runs (ACTION-TRIGGER M1) ===


def test_a2ui_tool_config_allow_action_triggered_runs_defaults_false():
    """Deny-by-default — distinct trust grant from surface-context writes."""
    cfg = A2uiToolConfig()
    assert cfg.allow_action_triggered_runs is False


def test_a2ui_tool_config_allow_action_triggered_runs_round_trips():
    """Skills can opt in via tool_configs.a2ui.allow_action_triggered_runs: true."""
    cfg = A2uiToolConfig.from_tool_configs(
        {"a2ui": {"allow_action_triggered_runs": True}},
    )
    assert cfg.allow_action_triggered_runs is True
    # The two opt-ins are independent — opting into action-triggered runs
    # does NOT implicitly grant surface-context writes.
    assert cfg.allow_surface_context_writes is False
