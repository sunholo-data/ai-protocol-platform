"""Unit tests for ``backend/budget/enforcer.py`` — Protocol, types, registry.

These cover the *interface* layer only — the in-memory reference impl
is tested separately in ``test_in_memory_enforcer.py``.

The headline acceptance criterion (sprint 2.12 M1 criterion 1) is that
``BudgetEnforcer`` is a ``typing.Protocol`` decorated with
``@runtime_checkable``: forks plug duck-typed impls (no inheritance)
and ``isinstance(impl, BudgetEnforcer)`` must return True.
"""

from __future__ import annotations

import pytest

from budget.enforcer import (
    BudgetConsultation,
    BudgetDecision,
    BudgetEnforcer,
    BudgetExceededError,
    clear_registered_enforcer,
    get_registered_enforcer,
    register_budget_enforcer,
)

# ─── BudgetConsultation / BudgetDecision shape ───────────────────────────────


def test_consultation_is_frozen_dataclass():
    """Consultation is a frozen dataclass — immutable after construction."""
    c = BudgetConsultation(
        identity_value="group:PHYS-7K2N",
        skill_id="physics-tutor",
        model_id="gemini-2.5-flash",
        projected_cost_usd=0.0042,
        invocation_id="inv-abc",
    )
    with pytest.raises((AttributeError, TypeError)):
        c.projected_cost_usd = 9.99  # type: ignore[misc]


def test_decision_is_frozen_dataclass():
    d = BudgetDecision(
        action="allow",
        remaining_usd=4.20,
        period_end="2026-06-01T00:00:00Z",
        message=None,
        retry_after_seconds=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        d.action = "block"  # type: ignore[misc]


def test_decision_action_literal_accepts_three_values():
    """Action is Literal['allow', 'warn', 'block']. Verified by constructing."""
    for action in ("allow", "warn", "block"):
        d = BudgetDecision(
            action=action,  # type: ignore[arg-type]
            remaining_usd=None,
            period_end=None,
            message=None,
            retry_after_seconds=None,
        )
        assert d.action == action


# ─── Protocol shape / runtime_checkable ──────────────────────────────────────


def test_budget_enforcer_is_runtime_checkable_protocol():
    """Acceptance criterion 1: isinstance() check works against duck-typed impls.

    A fork should be able to write a plain class with just the two async
    methods and pass isinstance against the Protocol.
    """

    class AdHocEnforcer:
        """No inheritance from BudgetEnforcer — pure duck typing."""

        async def consult(self, request):
            return BudgetDecision(
                action="allow",
                remaining_usd=None,
                period_end=None,
                message=None,
                retry_after_seconds=None,
            )

        async def record(self, request, actual_cost_usd):
            return None

    assert isinstance(AdHocEnforcer(), BudgetEnforcer)


def test_budget_enforcer_rejects_classes_missing_consult():
    class MissingConsult:
        async def record(self, request, actual_cost_usd):
            return None

    assert not isinstance(MissingConsult(), BudgetEnforcer)


def test_budget_enforcer_rejects_classes_missing_record():
    class MissingRecord:
        async def consult(self, request):
            return BudgetDecision(
                action="allow",
                remaining_usd=None,
                period_end=None,
                message=None,
                retry_after_seconds=None,
            )

    assert not isinstance(MissingRecord(), BudgetEnforcer)


# ─── Registry ────────────────────────────────────────────────────────────────


def test_registry_returns_none_when_no_enforcer_registered():
    clear_registered_enforcer()
    assert get_registered_enforcer() is None


def test_register_and_retrieve_enforcer():
    class StubEnforcer:
        async def consult(self, request):
            return BudgetDecision(
                action="allow",
                remaining_usd=None,
                period_end=None,
                message=None,
                retry_after_seconds=None,
            )

        async def record(self, request, actual_cost_usd):
            return None

    impl = StubEnforcer()
    register_budget_enforcer(impl)
    try:
        assert get_registered_enforcer() is impl
    finally:
        clear_registered_enforcer()


def test_register_replaces_previous_enforcer():
    class StubA:
        async def consult(self, request):
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    class StubB:
        async def consult(self, request):
            return BudgetDecision(
                action="block", remaining_usd=0.0, period_end=None, message="nope", retry_after_seconds=60
            )

        async def record(self, request, actual_cost_usd):
            return None

    register_budget_enforcer(StubA())
    register_budget_enforcer(StubB())
    try:
        assert isinstance(get_registered_enforcer(), StubB)
    finally:
        clear_registered_enforcer()


def test_register_rejects_non_enforcer():
    """Registering something that doesn't satisfy the Protocol fails loud."""

    class NotAnEnforcer:
        def consult(self, request):  # sync, not async — wrong shape
            return None

    with pytest.raises(TypeError, match="BudgetEnforcer"):
        register_budget_enforcer(NotAnEnforcer())  # type: ignore[arg-type]


# ─── BudgetExceededError ─────────────────────────────────────────────────────


def test_budget_exceeded_error_carries_decision():
    """The exception carries the BudgetDecision so the AG-UI translator
    can pull message + retry_after off it without re-consulting."""
    d = BudgetDecision(
        action="block",
        remaining_usd=0.0,
        period_end="2026-06-01T00:00:00Z",
        message="Cohort PHYS-7K2N is over its monthly budget.",
        retry_after_seconds=86400,
    )
    err = BudgetExceededError(d)
    assert err.decision is d
    assert "over" in str(err)
