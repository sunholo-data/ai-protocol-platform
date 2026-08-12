"""Pluggable budget enforcement for the platform.

Sprint 2.12 (v6.2.0). Public surface:

- ``BudgetEnforcer`` — runtime-checkable Protocol forks implement.
- ``BudgetConsultation`` / ``BudgetDecision`` — wire shapes.
- ``BudgetExceededError`` — raised on hard block; carries the decision.
- ``register_budget_enforcer`` / ``get_registered_enforcer`` — registry.
- ``InMemoryBudgetEnforcer`` — reference impl for LOCAL_MODE + single-instance Cloud Run.
"""

from budget.enforcer import (
    BudgetConsultation,
    BudgetDecision,
    BudgetEnforcer,
    BudgetExceededError,
    clear_registered_enforcer,
    get_registered_enforcer,
    register_budget_enforcer,
)
from budget.in_memory_enforcer import InMemoryBudgetEnforcer

__all__ = [
    "BudgetConsultation",
    "BudgetDecision",
    "BudgetEnforcer",
    "BudgetExceededError",
    "InMemoryBudgetEnforcer",
    "clear_registered_enforcer",
    "get_registered_enforcer",
    "register_budget_enforcer",
]
