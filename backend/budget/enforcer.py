"""Budget enforcement Protocol + types + registry.

Sprint 2.12 (v6.2.0) — the platform-level budget interface. Forks
plug a ``BudgetEnforcer`` implementation; the platform consults it
before every LLM call and records the realised cost afterwards.

Design contract: ``docs/design/v6.2.0/budget-enforcement.md``.

The Protocol is ``@runtime_checkable`` so fork impls don't need to
inherit — duck typing with the two ``async`` methods is enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# ─── Wire shapes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetConsultation:
    """The platform's question to the enforcer: 'may I spend X for Y on Z?'.

    ``identity_value`` is opaque to the platform — forks decide its
    shape (e.g. ``"group:PHYS-7K2N"``, ``"org:acme-co"``,
    ``"user:abc123"``). The skill config's ``identity_key`` says which
    ``User`` field to read.

    ``invocation_id`` is the ADK invocation id (one per agent turn).
    The reference impl uses it to dedupe replay-storms within a short
    window — a retried turn shouldn't double-charge the budget.
    """

    identity_value: str
    skill_id: str
    model_id: str
    projected_cost_usd: float
    invocation_id: str


@dataclass(frozen=True)
class BudgetDecision:
    """The enforcer's answer.

    ``remaining_usd`` is ``None`` when the enforcer can't quantify
    remaining (e.g. no cap configured, or running a cost-tracking-only
    enforcer). UI should hide the gauge in that case.

    ``period_end`` is advisory (ISO 8601 timestamp) — gives the UI a
    countdown target. May be ``None`` if the impl doesn't have a
    fixed period.

    ``retry_after_seconds`` is set on ``block`` to give the frontend
    a countdown to display. The frontend renders it verbatim.
    """

    action: Literal["allow", "warn", "block"]
    remaining_usd: float | None
    period_end: str | None
    message: str | None
    retry_after_seconds: int | None


# ─── Protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class BudgetEnforcer(Protocol):
    """Consulted before every LLM call to decide allow / warn / block.

    Forks implement this with their own backend (Firestore, BigQuery,
    Redis, etc.). The reference in-memory impl in
    ``budget.in_memory_enforcer`` is suitable for LOCAL_MODE and
    single-instance Cloud Run deployments.

    Both methods are async — fork impls typically do I/O.
    """

    async def consult(self, request: BudgetConsultation) -> BudgetDecision:
        """Return ``allow`` / ``warn`` / ``block`` for the projected spend."""
        ...

    async def record(self, request: BudgetConsultation, actual_cost_usd: float) -> None:
        """Called after the model call completes with the realised cost.

        Forks reconcile the held projection with the actual usage —
        e.g. release the over-estimated portion back to the budget.
        """
        ...


# ─── Exception ───────────────────────────────────────────────────────────────


class BudgetExceededError(Exception):
    """Raised from the before_model callback when the enforcer says ``block``.

    Carries the ``BudgetDecision`` so the AG-UI translator can extract
    ``message`` + ``retry_after_seconds`` without re-consulting the
    enforcer.
    """

    def __init__(self, decision: BudgetDecision) -> None:
        self.decision = decision
        message = decision.message or "Budget exceeded."
        super().__init__(message)


# ─── Registry ────────────────────────────────────────────────────────────────


_registered: BudgetEnforcer | None = None


def register_budget_enforcer(impl: BudgetEnforcer) -> None:
    """Register the process-wide enforcer.

    Forks call this once at startup. Calling twice replaces the
    previous registration (no warning — late registration is a valid
    pattern for test fixtures).
    """
    if not isinstance(impl, BudgetEnforcer):
        raise TypeError(
            "register_budget_enforcer requires a BudgetEnforcer "
            "(needs async consult() and async record()); got "
            f"{type(impl).__name__}"
        )
    global _registered
    _registered = impl


def get_registered_enforcer() -> BudgetEnforcer | None:
    """Return the registered enforcer, or ``None`` if forks haven't plugged one.

    The ADK before_model_callback short-circuits to no-op when this
    returns ``None`` — platforms without budget concerns pay zero
    overhead.
    """
    return _registered


def clear_registered_enforcer() -> None:
    """Drop the registered enforcer. Used by tests; not for production code."""
    global _registered
    _registered = None
