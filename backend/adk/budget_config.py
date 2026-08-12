"""Skill-level budget config: typed view over ``tool_configs.budget``.

Sprint 2.12 (v6.2.0) M2. Mirrors ``A2uiToolConfig.from_tool_configs``
shape so the parsing pattern is uniform across all per-tool config
blocks.

Skills opt in by declaring:

    tool_configs:
      budget:
        identity_key: group_id   # which User field the enforcer keys on
        cost_multiplier: 1.0     # default 1.0; >1 to scale expensive skills
        exempt: false            # default false; true bypasses the gate

Skills without a ``budget`` block are exempt by absence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class BudgetConfig(BaseModel):
    """Per-skill budget gate config. Opt-in via ``tool_configs.budget``."""

    identity_key: str = Field(
        ...,
        description=(
            "Which ``User`` field the budget enforcer keys on. "
            "Examples: ``group_id`` for cohorts / classrooms, "
            "``uid`` for per-user budgets, ``domain`` for org-level. "
            "Required — there's no sensible default."
        ),
    )
    cost_multiplier: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Scale the projected cost before the enforcer is consulted. "
            "Use >1.0 for expensive skills (e.g. code-grader = 3x). "
            "0.0 is rejected: zero-cost is what ``exempt: true`` is for. "
            "Negative values are rejected."
        ),
    )
    exempt: bool = Field(
        default=False,
        description=(
            "Bypass the gate entirely. The enforcer is not consulted "
            "and no log line is emitted. Use for system tools that "
            "must never be budget-gated (e.g. auth checks)."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _identity_key_not_empty(self) -> BudgetConfig:
        if not self.identity_key.strip():
            raise ValueError("identity_key must be a non-empty string")
        return self

    @classmethod
    def from_tool_configs(cls, tool_configs: dict[str, Any] | None) -> BudgetConfig | None:
        """Build the typed config from a raw ``tool_configs`` dict.

        - ``None`` / empty / missing ``budget`` key → ``None`` (skill exempt by absence)
        - ``budget`` present and a dict → validate (raises ValidationError on bad shape)
        - ``budget`` present but not a dict → ``None`` (tolerant: opt-in path,
          misconfiguration falls back to safe behaviour)
        """
        if not tool_configs:
            return None
        raw = tool_configs.get("budget")
        if raw is None or not isinstance(raw, dict):
            return None
        return cls.model_validate(raw)
