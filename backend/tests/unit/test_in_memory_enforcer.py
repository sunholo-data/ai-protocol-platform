"""Unit tests for ``backend/budget/in_memory_enforcer.py``.

This file is the 8-gate matrix from sprint 2.12 M1. Each gate is its own
test function so failure messages name the gate that broke. The matrix:

    Gate 1 — allow under cap
    Gate 2 — warn at 80% soft threshold (< cap)
    Gate 3 — hard block at 100% (>= cap)
    Gate 4 — period rollover resets state
    Gate 5 — multi-identity isolation
    Gate 6 — replay dedup via invocation_id (60s window)
    Gate 7 — record updates state with realised cost
    Gate 8 — fail-loud-but-allow on missing cap (default-deny is opt-in)

Time injection uses the CLASS-attribute pattern from sprint 2.11's
``AnonymousGroupAuth.time_provider`` — see ``backend/auth/group_id_auth.py:178``.
Override via ``InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t)``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from budget.enforcer import BudgetConsultation
from budget.in_memory_enforcer import InMemoryBudgetEnforcer

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def real_clock() -> Iterator[None]:
    """Restore the real clock after each test that monkeypatched it.

    The class-attribute time_provider sticks across tests because it
    lives on the class; this fixture is autouse via composition with
    the per-test enforcer fixtures below.
    """
    original = InMemoryBudgetEnforcer.time_provider
    try:
        yield
    finally:
        InMemoryBudgetEnforcer.time_provider = original


@pytest.fixture
def frozen_at_2026_05_19(real_clock: None) -> float:
    """Freeze the clock at a known timestamp inside the May 2026 monthly window."""
    fixed = 1779537600.0  # 2026-05-19T08:00:00Z
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: fixed)
    return fixed


def _consultation(
    *,
    identity_value: str = "group:PHYS-7K2N",
    projected_cost_usd: float = 0.0,
    invocation_id: str = "inv-default",
    skill_id: str = "physics-tutor",
    model_id: str = "gemini-2.5-flash",
) -> BudgetConsultation:
    return BudgetConsultation(
        identity_value=identity_value,
        skill_id=skill_id,
        model_id=model_id,
        projected_cost_usd=projected_cost_usd,
        invocation_id=invocation_id,
    )


# ─── Gate 1 — allow under cap ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_1_allow_under_cap(frozen_at_2026_05_19: float):
    """consult with projected < cap * soft_threshold returns action='allow'.

    remaining_usd is populated; message is None.
    """
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    decision = await enforcer.consult(_consultation(projected_cost_usd=1.0))
    assert decision.action == "allow"
    assert decision.remaining_usd is not None
    assert decision.remaining_usd == pytest.approx(9.0)
    assert decision.message is None
    assert decision.retry_after_seconds is None


# ─── Gate 2 — warn at 80% soft threshold ─────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_2_warn_at_soft_threshold(frozen_at_2026_05_19: float):
    """consult that crosses cap * 0.8 but < cap returns action='warn' with non-empty message.

    Test isolated from Gate 3 — fresh enforcer, single-shot consult.
    """
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    # First call burns under-threshold cost, then second call tips into soft.
    await enforcer.consult(_consultation(projected_cost_usd=7.0, invocation_id="inv-1"))
    decision = await enforcer.consult(_consultation(projected_cost_usd=1.5, invocation_id="inv-2"))
    assert decision.action == "warn"
    assert decision.message is not None and decision.message != ""
    # Still under cap, so retry_after is not set.
    assert decision.retry_after_seconds is None


@pytest.mark.asyncio
async def test_gate_2_warn_not_triggered_below_soft_threshold(frozen_at_2026_05_19: float):
    """Below 80%: still 'allow', not 'warn'."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    await enforcer.consult(_consultation(projected_cost_usd=5.0, invocation_id="inv-1"))
    decision = await enforcer.consult(_consultation(projected_cost_usd=1.0, invocation_id="inv-2"))
    assert decision.action == "allow"


# ─── Gate 3 — hard block at 100% ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_3_block_at_cap(frozen_at_2026_05_19: float):
    """consult that crosses cap returns action='block' with retry_after set.

    Test isolated from Gate 2 — fresh enforcer.
    """
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    await enforcer.consult(_consultation(projected_cost_usd=9.0, invocation_id="inv-1"))
    decision = await enforcer.consult(_consultation(projected_cost_usd=2.0, invocation_id="inv-2"))
    assert decision.action == "block"
    assert decision.retry_after_seconds is not None
    assert decision.retry_after_seconds > 0
    assert decision.message is not None and decision.message != ""


@pytest.mark.asyncio
async def test_gate_3_block_remaining_clamped_to_zero(frozen_at_2026_05_19: float):
    """When blocking, remaining_usd is clamped to 0.0 (not negative)."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    await enforcer.consult(_consultation(projected_cost_usd=15.0, invocation_id="inv-1"))
    decision = await enforcer.consult(_consultation(projected_cost_usd=0.5, invocation_id="inv-2"))
    assert decision.action == "block"
    assert decision.remaining_usd == 0.0


# ─── Gate 4 — period rollover resets state ───────────────────────────────────


@pytest.mark.asyncio
async def test_gate_4_period_rollover_resets_state(real_clock: None):
    """Advance the clock past the period boundary — remaining_usd resets to full cap."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")

    # Burn the entire budget in May 2026.
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: 1779537600.0)  # 2026-05-19
    await enforcer.consult(_consultation(projected_cost_usd=10.0, invocation_id="inv-may"))
    decision_in_may = await enforcer.consult(_consultation(projected_cost_usd=1.0, invocation_id="inv-may-2"))
    assert decision_in_may.action == "block"

    # Jump to June 2026 — new monthly window.
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: 1782129600.0)  # 2026-06-18
    decision_in_june = await enforcer.consult(_consultation(projected_cost_usd=1.0, invocation_id="inv-jun"))
    assert decision_in_june.action == "allow"
    assert decision_in_june.remaining_usd == pytest.approx(9.0)


# ─── Gate 5 — multi-identity isolation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_5_multi_identity_isolation(frozen_at_2026_05_19: float):
    """Burning identity A's budget does not affect identity B."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    # Burn A's full budget.
    await enforcer.consult(_consultation(identity_value="group:A", projected_cost_usd=10.0, invocation_id="A-1"))
    decision_A_blocked = await enforcer.consult(
        _consultation(identity_value="group:A", projected_cost_usd=0.5, invocation_id="A-2")
    )
    assert decision_A_blocked.action == "block"
    # B should be untouched.
    decision_B = await enforcer.consult(
        _consultation(identity_value="group:B", projected_cost_usd=0.5, invocation_id="B-1")
    )
    assert decision_B.action == "allow"
    assert decision_B.remaining_usd == pytest.approx(9.5)


# ─── Gate 6 — replay dedup via invocation_id ─────────────────────────────────


@pytest.mark.asyncio
async def test_gate_6_replay_dedup_returns_cached_decision(frozen_at_2026_05_19: float):
    """Same (invocation_id, identity) within 60s returns the cached decision — no double-charge."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    first = await enforcer.consult(_consultation(projected_cost_usd=3.0, invocation_id="inv-dedup"))
    second = await enforcer.consult(_consultation(projected_cost_usd=3.0, invocation_id="inv-dedup"))
    # Same decision (cached); cumulative cost is 3.0, not 6.0 — assert by
    # firing a third unique-id consultation and reading remaining_usd.
    third = await enforcer.consult(_consultation(projected_cost_usd=0.0, invocation_id="inv-probe"))
    assert first.action == second.action == "allow"
    assert third.remaining_usd == pytest.approx(7.0)  # only one 3.0 charge


@pytest.mark.asyncio
async def test_gate_6_replay_window_expires_after_60s(real_clock: None):
    """After the 60s dedup window, the same invocation_id IS charged again.

    (Defensive — in practice the agent should mint a new invocation_id,
    but the cache must not be a permanent dedup store.)
    """
    enforcer = InMemoryBudgetEnforcer(
        default_cap_usd=10.0, soft_threshold=0.8, period="monthly", dedup_window_seconds=60.0
    )
    t0 = 1779537600.0  # 2026-05-19T08:00:00Z
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t0)
    await enforcer.consult(_consultation(projected_cost_usd=3.0, invocation_id="inv-stale"))

    # Advance past the 60s window.
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t0 + 120.0)
    await enforcer.consult(_consultation(projected_cost_usd=3.0, invocation_id="inv-stale"))

    probe = await enforcer.consult(_consultation(projected_cost_usd=0.0, invocation_id="inv-probe"))
    assert probe.remaining_usd == pytest.approx(4.0)  # 10 - 3 - 3


# ─── Gate 7 — record updates state with realised cost ────────────────────────


@pytest.mark.asyncio
async def test_gate_7_record_replaces_projection_with_actual(frozen_at_2026_05_19: float):
    """consult charges the projection; record reconciles to the actual.

    Pattern: projected over-estimates (worst-case). When the call finishes
    we know the real cost — record() reduces the held charge accordingly.
    """
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=10.0, soft_threshold=0.8, period="monthly")
    request = _consultation(projected_cost_usd=4.0, invocation_id="inv-rec")
    await enforcer.consult(request)
    # Actual cost came in at 1.0 — 3.0 should be released back to the budget.
    await enforcer.record(request, actual_cost_usd=1.0)
    probe = await enforcer.consult(_consultation(projected_cost_usd=0.0, invocation_id="inv-probe"))
    assert probe.remaining_usd == pytest.approx(9.0)  # 10 - 1


# ─── Gate 8 — fail-loud-but-allow on missing config ──────────────────────────


@pytest.mark.asyncio
async def test_gate_8_missing_cap_returns_allow_with_warn_log(frozen_at_2026_05_19: float, caplog):
    """No BUDGET_DEFAULT_CAP_USD set and no per-identity cap: default to 'allow'.

    Default-deny is opt-in — surprise denial would break every fork that
    forgets to configure. A WARN log records the unconfigured state.
    """
    import logging

    enforcer = InMemoryBudgetEnforcer(default_cap_usd=0.0, soft_threshold=0.8, period="monthly")
    with caplog.at_level(logging.WARNING, logger="budget"):
        decision = await enforcer.consult(_consultation(projected_cost_usd=1.0))
    assert decision.action == "allow"
    assert decision.remaining_usd is None  # not knowable without a cap
    # WARN log should mention 'cap' or 'unconfigured' or similar.
    assert any("cap" in rec.message.lower() or "unconfigured" in rec.message.lower() for rec in caplog.records)


# ─── time_provider class-attribute pattern ───────────────────────────────────


def test_time_provider_is_class_attribute_not_dataclass_field():
    """Override at the class level affects every instance — confirms the pattern."""
    sentinel = 9999.0
    original = InMemoryBudgetEnforcer.time_provider
    try:
        InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: sentinel)
        # New instance respects the class-level override.
        enforcer = InMemoryBudgetEnforcer(default_cap_usd=1.0)
        assert enforcer.time_provider() == sentinel
        # The dataclass field list must NOT include time_provider.
        from dataclasses import fields

        field_names = {f.name for f in fields(enforcer)}
        assert "time_provider" not in field_names
    finally:
        InMemoryBudgetEnforcer.time_provider = original


# ─── Period-key helpers ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_period_resets_each_day(real_clock: None):
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=2.0, soft_threshold=0.8, period="daily")
    # Day 1 (2026-05-19 08:00 UTC) — burn the budget.
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: 1779537600.0)
    await enforcer.consult(_consultation(projected_cost_usd=2.0, invocation_id="d1"))
    assert (await enforcer.consult(_consultation(projected_cost_usd=0.1, invocation_id="d1-probe"))).action == "block"
    # Day 2 (2026-05-20 08:00 UTC).
    InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: 1779537600.0 + 86400.0)
    assert (await enforcer.consult(_consultation(projected_cost_usd=0.1, invocation_id="d2"))).action == "allow"
