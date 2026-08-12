"""Integration tests for ``backend/budget/callback.py``.

Sprint 2.12 M2 — the ADK before/after model callback pair. Verifies:

- allow path: model call proceeds
- warn path: model call proceeds + state['budget:warn_message'] is set
- block path: BudgetExceededError raised + model never invoked
- exempt skill bypasses entirely (no enforcer call)
- cost_multiplier scales projection before consult
- record reconciles projection with actual usage_metadata
- no-enforcer-registered → no-op
- identity_unresolved → skip with WARN log (fail-open)

Uses the in-memory reference enforcer + mocked LlmRequest/Response
(SimpleNamespace) per the existing test_document_injector pattern.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from adk.budget_config import BudgetConfig
from auth.firebase_auth import User
from budget import (
    BudgetConsultation,
    BudgetDecision,
    BudgetExceededError,
    InMemoryBudgetEnforcer,
    clear_registered_enforcer,
)
from budget.callback import make_budget_callbacks

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry_and_clock():
    """Each test starts with no enforcer registered and the real clock."""
    clear_registered_enforcer()
    original = InMemoryBudgetEnforcer.time_provider
    try:
        yield
    finally:
        clear_registered_enforcer()
        InMemoryBudgetEnforcer.time_provider = original


def _make_user(group_id: str = "group:PHYS-7K2N", uid: str = "anon-PHYS-abc") -> User:
    return User(uid=uid, group_id=group_id, auth_mode="anonymous_group_id")


def _make_ctx(invocation_id: str = "inv-test") -> MagicMock:
    ctx = MagicMock()
    ctx.state = {}
    ctx.invocation_id = invocation_id
    return ctx


def _make_request(
    model: str = "gemini-2.5-flash",
    user_text: str = "Hello — explain photosynthesis.",
    max_output_tokens: int = 4096,
) -> SimpleNamespace:
    from google.genai.types import Content, Part

    config = SimpleNamespace(max_output_tokens=max_output_tokens)
    return SimpleNamespace(
        model=model,
        contents=[Content(role="user", parts=[Part.from_text(text=user_text)])],
        config=config,
    )


def _make_response(prompt_tokens: int = 100, candidates_tokens: int = 200) -> SimpleNamespace:
    usage_metadata = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidates_tokens,
    )
    return SimpleNamespace(usage_metadata=usage_metadata)


# ─── Acceptance criteria — gate paths ────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_path_returns_none_and_proceeds():
    """consult returns 'allow' → callback returns None → ADK proceeds with the call."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=100.0)
    before, _after = make_budget_callbacks(
        enforcer,
        user=_make_user(),
        skill_id="physics-tutor",
        budget_config=BudgetConfig(identity_key="group_id"),
    )
    ctx = _make_ctx()
    req = _make_request()
    result = await before(ctx, req)
    assert result is None
    # No state pollution on allow path beyond the active-consultation marker.
    assert "budget:warn_message" not in ctx.state


@pytest.mark.asyncio
async def test_warn_path_sets_warn_message_in_state():
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=0.001)  # tiny — first call warns

    # Pre-charge so the next consult tips into warn-territory.
    pre_consult = BudgetConsultation(
        identity_value="group:PHYS-7K2N",
        skill_id="seed",
        model_id="gemini-2.5-flash",
        projected_cost_usd=0.0009,  # 90% of cap
        invocation_id="seed",
    )
    await enforcer.consult(pre_consult)

    before, _after = make_budget_callbacks(
        enforcer,
        user=_make_user(),
        skill_id="physics-tutor",
        budget_config=BudgetConfig(identity_key="group_id"),
    )
    ctx = _make_ctx(invocation_id="inv-warn")
    req = _make_request(max_output_tokens=10)  # small enough to not block

    await before(ctx, req)
    assert ctx.state.get("budget:warn_message"), "warn must set state for the after_agent prefix injection"


@pytest.mark.asyncio
async def test_block_path_raises_budget_exceeded_error():
    """consult returns 'block' → BudgetExceededError raised BEFORE model invocation."""

    class BlockingEnforcer:
        async def consult(self, request):
            return BudgetDecision(
                action="block",
                remaining_usd=0.0,
                period_end="2026-06-01T00:00:00Z",
                message="Over cap.",
                retry_after_seconds=3600,
            )

        async def record(self, request, actual_cost_usd):
            return None

    before, _after = make_budget_callbacks(
        BlockingEnforcer(),
        user=_make_user(),
        skill_id="physics-tutor",
        budget_config=BudgetConfig(identity_key="group_id"),
    )
    ctx = _make_ctx()
    req = _make_request()
    with pytest.raises(BudgetExceededError) as excinfo:
        await before(ctx, req)
    assert excinfo.value.decision.retry_after_seconds == 3600


@pytest.mark.asyncio
async def test_exempt_skill_bypasses_consult():
    """tool_configs.budget.exempt=true → enforcer.consult never called."""

    consult_calls = []

    class SpyEnforcer:
        async def consult(self, request):
            consult_calls.append(request)
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    before, _after = make_budget_callbacks(
        SpyEnforcer(),
        user=_make_user(),
        skill_id="system-tool",
        budget_config=BudgetConfig(identity_key="uid", exempt=True),
    )
    ctx = _make_ctx()
    req = _make_request()
    result = await before(ctx, req)
    assert result is None
    assert consult_calls == [], "exempt skills must not consult"


@pytest.mark.asyncio
async def test_no_budget_config_bypasses_consult():
    """budget_config=None (skill has no tool_configs.budget) → no consult."""

    consult_calls = []

    class SpyEnforcer:
        async def consult(self, request):
            consult_calls.append(request)
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    before, _after = make_budget_callbacks(
        SpyEnforcer(),
        user=_make_user(),
        skill_id="legacy-skill",
        budget_config=None,
    )
    ctx = _make_ctx()
    req = _make_request()
    await before(ctx, req)
    assert consult_calls == [], "legacy skills without tool_configs.budget must not consult"


# ─── Cost multiplier ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_multiplier_scales_projection():
    """cost_multiplier=3.0 → enforcer sees 3x the raw projected cost."""

    captured: list[BudgetConsultation] = []

    class SpyEnforcer:
        async def consult(self, request):
            captured.append(request)
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    # Same setup twice, just changing the multiplier — assert the
    # second consult's projection is 3x the first.
    before_1x, _ = make_budget_callbacks(
        SpyEnforcer(),
        user=_make_user(),
        skill_id="cheap-skill",
        budget_config=BudgetConfig(identity_key="group_id", cost_multiplier=1.0),
    )
    before_3x, _ = make_budget_callbacks(
        SpyEnforcer(),
        user=_make_user(),
        skill_id="expensive-skill",
        budget_config=BudgetConfig(identity_key="group_id", cost_multiplier=3.0),
    )
    await before_1x(_make_ctx("inv-1x"), _make_request())
    await before_3x(_make_ctx("inv-3x"), _make_request())
    assert len(captured) == 2
    assert captured[1].projected_cost_usd == pytest.approx(captured[0].projected_cost_usd * 3.0)


# ─── Record reconciliation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_reconciles_with_actual_cost():
    """After model call, record() called with actual cost from llm_response.usage_metadata."""
    enforcer = InMemoryBudgetEnforcer(default_cap_usd=100.0)
    before, after = make_budget_callbacks(
        enforcer,
        user=_make_user(),
        skill_id="physics-tutor",
        budget_config=BudgetConfig(identity_key="group_id", cost_multiplier=1.0),
    )
    ctx = _make_ctx(invocation_id="inv-rec")
    req = _make_request(max_output_tokens=4096)
    await before(ctx, req)
    # Projected cost is held. After the call lands with real usage:
    resp = _make_response(prompt_tokens=50, candidates_tokens=100)
    await after(ctx, resp)
    # Probe — most of the projection should have been released back.
    probe = await enforcer.consult(
        BudgetConsultation(
            identity_value="group:PHYS-7K2N",
            skill_id="probe",
            model_id="gemini-2.5-flash",
            projected_cost_usd=0.0,
            invocation_id="inv-probe",
        )
    )
    # We don't know the exact projected (depends on input-token estimate),
    # but remaining must reflect a small realised cost, not the full
    # worst-case projection.
    assert probe.remaining_usd is not None
    assert probe.remaining_usd > 99.0  # nearly the whole cap is still available


# ─── No-enforcer-registered + identity edge cases ────────────────────────────


@pytest.mark.asyncio
async def test_identity_unresolved_logs_warn_and_skips(caplog):
    """identity_key='group_id' but User.group_id is empty → skip with WARN log."""

    consult_calls = []

    class SpyEnforcer:
        async def consult(self, request):
            consult_calls.append(request)
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    # User has empty group_id (Firebase-auth user, not anonymous-group).
    user_without_group = User(uid="firebase-user", email="t@example.com", auth_mode="firebase", group_id="")

    before, _after = make_budget_callbacks(
        SpyEnforcer(),
        user=user_without_group,
        skill_id="physics-tutor",
        budget_config=BudgetConfig(identity_key="group_id"),
    )
    with caplog.at_level(logging.WARNING, logger="budget"):
        await before(_make_ctx(), _make_request())
    assert consult_calls == [], "consult must be skipped when identity is unresolved"
    assert any("identity_unresolved" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_identity_falls_back_to_uid_when_group_id_missing():
    """A skill configured with identity_key='uid' uses the uid field directly.

    Confirms the resolver reads arbitrary User fields, not just group_id.
    """
    captured: list[BudgetConsultation] = []

    class SpyEnforcer:
        async def consult(self, request):
            captured.append(request)
            return BudgetDecision(
                action="allow", remaining_usd=None, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    user = User(uid="alice@org", email="alice@org", auth_mode="firebase")
    before, _after = make_budget_callbacks(
        SpyEnforcer(),
        user=user,
        skill_id="some-skill",
        budget_config=BudgetConfig(identity_key="uid"),
    )
    await before(_make_ctx(), _make_request())
    assert len(captured) == 1
    assert captured[0].identity_value == "alice@org"
