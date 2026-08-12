"""Unit tests for ``backend/adk/budget_config.py``.

``BudgetConfig`` mirrors the ``A2uiToolConfig`` pattern: a typed
Pydantic view over the loosely-typed ``tool_configs`` dict on
``SkillMetadata``. Sprint 2.12 M2 acceptance criterion 1.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adk.budget_config import BudgetConfig

# ─── from_tool_configs ───────────────────────────────────────────────────────


def test_missing_tool_configs_returns_none():
    """No tool_configs at all → None (skill exempt by absence)."""
    assert BudgetConfig.from_tool_configs(None) is None


def test_empty_tool_configs_returns_none():
    assert BudgetConfig.from_tool_configs({}) is None


def test_tool_configs_without_budget_key_returns_none():
    """tool_configs has other keys but no 'budget' — skill is exempt."""
    assert BudgetConfig.from_tool_configs({"a2ui": {"default_surface": "workspace"}}) is None


def test_tool_configs_with_budget_dict_returns_typed_config():
    cfg = BudgetConfig.from_tool_configs(
        {
            "budget": {
                "identity_key": "group_id",
                "cost_multiplier": 3.0,
                "exempt": False,
            }
        }
    )
    assert cfg is not None
    assert cfg.identity_key == "group_id"
    assert cfg.cost_multiplier == 3.0
    assert cfg.exempt is False


def test_budget_config_defaults():
    cfg = BudgetConfig.from_tool_configs({"budget": {"identity_key": "uid"}})
    assert cfg is not None
    assert cfg.identity_key == "uid"
    assert cfg.cost_multiplier == 1.0
    assert cfg.exempt is False


def test_exempt_skill_bypasses_gate_via_flag():
    cfg = BudgetConfig.from_tool_configs({"budget": {"identity_key": "uid", "exempt": True}})
    assert cfg is not None
    assert cfg.exempt is True


# ─── Validation ──────────────────────────────────────────────────────────────


def test_identity_key_is_required():
    """No identity_key → ValidationError. Forks must pick a User field explicitly."""
    with pytest.raises(ValidationError):
        BudgetConfig.from_tool_configs({"budget": {"cost_multiplier": 1.0}})


def test_cost_multiplier_must_be_positive():
    with pytest.raises(ValidationError):
        BudgetConfig.from_tool_configs({"budget": {"identity_key": "uid", "cost_multiplier": -1.0}})


def test_cost_multiplier_zero_is_rejected():
    """0.0 multiplier would silently disable budget tracking — that's the
    ``exempt: true`` flag's job. Reject 0.0 to force the explicit choice."""
    with pytest.raises(ValidationError):
        BudgetConfig.from_tool_configs({"budget": {"identity_key": "uid", "cost_multiplier": 0.0}})


def test_extra_keys_are_rejected():
    """Mirror A2uiToolConfig's strict shape — typos should fail loud."""
    with pytest.raises(ValidationError):
        BudgetConfig.from_tool_configs(
            {"budget": {"identity_key": "uid", "unkonwn_field": True}}  # codespell:ignore unkonwn
        )


def test_non_dict_budget_value_returns_none():
    """If tool_configs.budget is a string/list/whatever, treat as exempt
    (tolerant to misconfiguration on opt-in paths)."""
    assert BudgetConfig.from_tool_configs({"budget": "yes please"}) is None
    assert BudgetConfig.from_tool_configs({"budget": ["uid"]}) is None
