"""In-memory reference implementation of ``BudgetEnforcer``.

Sprint 2.12 (v6.2.0). Stores held cost per ``(identity_value,
period_key)`` in a plain dict. Suitable for LOCAL_MODE smoke and
single-instance Cloud Run deployments. Multi-instance forks need a
shared store (Firestore, Redis, etc.) — see the howto's appendix
``FirestoreBudgetEnforcer`` sketch.

Clock injection follows the sprint-2.11 ``AnonymousGroupAuth.time_provider``
pattern: declared on the class AFTER ``@dataclass`` decoration so
tests can override via
``InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t)``.
A dataclass-field clock would be assigned at __init__ time and
shadow later overrides.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from budget.enforcer import BudgetConsultation, BudgetDecision

logger = logging.getLogger("budget")


@dataclass
class InMemoryBudgetEnforcer:
    """Reference impl. NOT for multi-instance deployments.

    Each instance owns its state. Restart loses history. Forks needing
    persistence implement the ``BudgetEnforcer`` Protocol with their
    own backend.
    """

    default_cap_usd: float = 0.0
    """0.0 means 'unconfigured' — consult returns allow with a WARN log
    (default-deny is opt-in; surprise denial breaks forks that forgot
    to configure)."""

    soft_threshold: float = 0.8
    """Fraction of cap that flips the decision from allow to warn."""

    period: str = "monthly"
    """One of 'daily', 'weekly', 'monthly'. Drives the period_key
    computation that resets spend at the boundary."""

    dedup_window_seconds: float = 60.0
    """How long to cache a consult decision keyed by (invocation_id,
    identity_value). Within this window, replays return the cached
    decision without double-charging."""

    _spend: dict[tuple[str, str], float] = field(default_factory=dict)
    """{(identity_value, period_key): cumulative_usd}"""

    _dedup_cache: dict[tuple[str, str], tuple[BudgetDecision, float]] = field(default_factory=dict)
    """{(invocation_id, identity_value): (decision, decided_at_ts)}"""

    _charged_per_inv: dict[tuple[str, str], tuple[float, str]] = field(default_factory=dict)
    """{(invocation_id, identity_value): (held_usd, period_key)}.
    Tracked so record() can reconcile projection with actual."""

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_env(cls) -> InMemoryBudgetEnforcer:
        """Factory: read caps from environment variables.

        - ``BUDGET_DEFAULT_CAP_USD`` (default ``0.0`` = unconfigured)
        - ``BUDGET_SOFT_THRESHOLD`` (default ``0.8``)
        - ``BUDGET_PERIOD`` (default ``monthly``; accepts ``daily``/``weekly``/``monthly``)
        """
        return cls(
            default_cap_usd=float(os.getenv("BUDGET_DEFAULT_CAP_USD", "0.0")),
            soft_threshold=float(os.getenv("BUDGET_SOFT_THRESHOLD", "0.8")),
            period=os.getenv("BUDGET_PERIOD", "monthly"),
        )

    # ─── Public API (Protocol contract) ──────────────────────────────────

    async def consult(self, request: BudgetConsultation) -> BudgetDecision:
        async with self._lock:
            now = self.time_provider()
            self._prune_dedup(now)

            cache_key = (request.invocation_id, request.identity_value)
            cached = self._dedup_cache.get(cache_key)
            if cached is not None:
                cached_decision, _ = cached
                return cached_decision

            decision = self._decide(request, now)

            # Charge the projection regardless of action. Block means "the
            # model call won't happen", but the cap accounts demand, not
            # just spend — otherwise a single over-cap request leaves the
            # budget untouched and retry-storms can game the gate. record()
            # later reconciles to the actual cost (or to zero on block by
            # virtue of no record being called).
            period_key = self._period_key(now)
            spend_key = (request.identity_value, period_key)
            self._spend[spend_key] = self._spend.get(spend_key, 0.0) + request.projected_cost_usd
            self._charged_per_inv[cache_key] = (request.projected_cost_usd, period_key)

            self._dedup_cache[cache_key] = (decision, now)
            return decision

    async def record(self, request: BudgetConsultation, actual_cost_usd: float) -> None:
        """Reconcile the held projection with the realised cost.

        If actual < projection, release the difference back to the
        budget. If actual > projection (rare — model used more than
        max_output_tokens?), charge the extra. If consult resulted in
        ``block`` (no charge held), do nothing.
        """
        async with self._lock:
            cache_key = (request.invocation_id, request.identity_value)
            held = self._charged_per_inv.pop(cache_key, None)
            if held is None:
                # Either blocked (never charged) or unknown invocation.
                return
            projected, period_key = held
            delta = actual_cost_usd - projected  # may be negative (refund)
            spend_key = (request.identity_value, period_key)
            self._spend[spend_key] = max(0.0, self._spend.get(spend_key, 0.0) + delta)

    # ─── Internal helpers ────────────────────────────────────────────────

    def _decide(self, request: BudgetConsultation, now: float) -> BudgetDecision:
        period_key = self._period_key(now)
        period_end = self._period_end_iso(now)
        spend_key = (request.identity_value, period_key)
        prior = self._spend.get(spend_key, 0.0)
        projected_total = prior + request.projected_cost_usd

        if self.default_cap_usd <= 0.0:
            # Unconfigured — fail-loud-but-allow.
            logger.warning(
                "budget.cap_unconfigured",
                extra={
                    "identity_value": request.identity_value,
                    "skill_id": request.skill_id,
                    "projected_cost_usd": request.projected_cost_usd,
                },
            )
            return BudgetDecision(
                action="allow",
                remaining_usd=None,
                period_end=period_end,
                message=None,
                retry_after_seconds=None,
            )

        cap = self.default_cap_usd
        soft = cap * self.soft_threshold

        if projected_total >= cap:
            retry_after = int(self._seconds_until_period_end(now))
            return BudgetDecision(
                action="block",
                remaining_usd=0.0,
                period_end=period_end,
                message=(
                    f"Budget exhausted for {request.identity_value} this {self.period} period. Resets at {period_end}."
                ),
                retry_after_seconds=retry_after,
            )

        if projected_total >= soft:
            remaining = max(0.0, cap - projected_total)
            return BudgetDecision(
                action="warn",
                remaining_usd=remaining,
                period_end=period_end,
                message=(
                    f"{request.identity_value} has used "
                    f"{int(projected_total / cap * 100)}% of this {self.period} budget."
                ),
                retry_after_seconds=None,
            )

        remaining = cap - projected_total
        return BudgetDecision(
            action="allow",
            remaining_usd=remaining,
            period_end=period_end,
            message=None,
            retry_after_seconds=None,
        )

    def _period_key(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        if self.period == "daily":
            return dt.strftime("%Y-%m-%d")
        if self.period == "weekly":
            iso_year, iso_week, _ = dt.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        return dt.strftime("%Y-%m")

    def _next_period_start(self, ts: float) -> datetime:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        if self.period == "daily":
            tomorrow = (dt + timedelta(days=1)).date()
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)
        if self.period == "weekly":
            days_until_monday = (7 - dt.weekday()) % 7 or 7
            target = (dt + timedelta(days=days_until_monday)).date()
            return datetime(target.year, target.month, target.day, tzinfo=UTC)
        # monthly
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        first_of_next = (dt.replace(day=last_day) + timedelta(days=1)).date()
        return datetime(first_of_next.year, first_of_next.month, first_of_next.day, tzinfo=UTC)

    def _period_end_iso(self, ts: float) -> str:
        return self._next_period_start(ts).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _seconds_until_period_end(self, ts: float) -> float:
        return max(0.0, self._next_period_start(ts).timestamp() - ts)

    def _prune_dedup(self, now: float) -> None:
        """Drop dedup cache entries older than ``dedup_window_seconds``."""
        cutoff = now - self.dedup_window_seconds
        stale = [k for k, (_, ts) in self._dedup_cache.items() if ts < cutoff]
        for k in stale:
            self._dedup_cache.pop(k, None)


# Class-level clock injection point. Lives on the CLASS (not as a
# dataclass field) so tests can override via
# ``InMemoryBudgetEnforcer.time_provider = staticmethod(lambda: t)`` —
# the override sticks because instance lookup falls through to the
# class attribute. A dataclass field would be bound at __init__ time
# and shadow later overrides. Same pattern as
# ``AnonymousGroupAuth.time_provider`` (sprint 2.11).
InMemoryBudgetEnforcer.time_provider = staticmethod(time.time)
