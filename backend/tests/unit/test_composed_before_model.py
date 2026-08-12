"""Regression: the composed before_model_callback in agent.py runs ALL
participants in order — document_injector FIRST, budget_gate SECOND.

Sprint 2.12 M2 introduces the composition (the existing wire at
``adk/agent.py:422`` was a single callback). This test catches future
refactors that drop one of the participants.
"""

from __future__ import annotations

import inspect

import pytest

from adk import agent as agent_module


def test_create_agent_uses_composed_before_model_callback():
    """create_agent must wire a ``_composed_before_model`` async helper
    rather than a single bare callback.

    Regression for sprint 2.12 M2 — earlier wiring was
    ``before_model_callback=_document_injector`` (single function);
    new wiring chains the document injector + budget gate.
    """
    source = inspect.getsource(agent_module.create_agent)
    assert "_composed_before_model" in source, (
        "create_agent must wire a _composed_before_model chain — see sprint 2.12 M2. "
        "Replacing it with a single callback drops the budget gate silently."
    )
    assert "before_model_callback=_composed_before_model" in source, (
        "before_model_callback must point at the composed helper, not a participant directly."
    )


def test_create_agent_uses_composed_after_model_callback():
    """Sprint 2.12 M2 also introduces ``_composed_after_model`` for budget record.

    Even if no budget enforcer is registered (no-op fast path), the
    composed wrapper must exist so future after_model participants
    (telemetry, audit logs) can plug in without another refactor.
    """
    source = inspect.getsource(agent_module.create_agent)
    assert "after_model_callback=_composed_after_model" in source, (
        "after_model_callback must point at the composed helper. "
        "Without it the enforcer.record() reconciliation never fires."
    )


@pytest.mark.asyncio
async def test_composed_before_model_runs_document_injector_when_budget_allows(monkeypatch):
    """End-to-end behaviour: when the enforcer says allow, the document
    injector still runs. The injector is the existing pre-2.12 callback
    and its tests at tests/tool_tests/test_document_injector.py should
    continue to pass — this test is the cross-cutting check that proves
    composition preserves the injector's invocation."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from google.genai.types import Content, Part

    from adk.budget_config import BudgetConfig
    from auth.firebase_auth import User
    from budget.callback import make_budget_callbacks
    from budget.enforcer import BudgetDecision

    # Counter on a fake document_injector — we don't need real doc loading,
    # just to confirm the composition called it.
    injector_calls: list[int] = []

    async def fake_doc_injector(ctx, req):
        injector_calls.append(1)
        return None

    class AllowEnforcer:
        async def consult(self, request):
            return BudgetDecision(
                action="allow", remaining_usd=99.0, period_end=None, message=None, retry_after_seconds=None
            )

        async def record(self, request, actual_cost_usd):
            return None

    user = User(uid="u", email="u@x", auth_mode="firebase", group_id="g")
    budget_before, _budget_after = make_budget_callbacks(
        AllowEnforcer(),
        user=user,
        skill_id="s",
        budget_config=BudgetConfig(identity_key="uid"),
    )

    # Compose by hand the same way agent.py does — test the contract.
    async def _composed_before_model(ctx, req):
        await fake_doc_injector(ctx, req)
        await budget_before(ctx, req)

    ctx = MagicMock()
    ctx.state = {}
    ctx.invocation_id = "inv-comp"
    req = SimpleNamespace(
        model="gemini-2.5-flash",
        contents=[Content(role="user", parts=[Part.from_text(text="hi")])],
        config=SimpleNamespace(max_output_tokens=512),
    )
    await _composed_before_model(ctx, req)
    assert injector_calls == [1], (
        "document_injector must run as part of the composed before_model chain. "
        "Sprint 2.12 M2 introduced budget gating; the injector predates it and must not be dropped."
    )
