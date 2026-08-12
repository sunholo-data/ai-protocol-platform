"""Unit tests for ``backend/protocols/artefact_review.py``.

Sprint 2.13 M3. Mirrors the shape of sprint 2.12's
``test_budget_enforcer.py``: Protocol shape + registry + types only.
The mcp_proxy interception is tested separately in
``test_mcp_proxy_artefact_review.py``.

The headline acceptance criterion: ``ArtefactReviewer`` is a
``typing.Protocol`` with ``@runtime_checkable`` so forks plug
duck-typed impls.
"""

from __future__ import annotations

import pytest

from protocols.artefact_review import (
    ArtefactDecision,
    ArtefactReview,
    ArtefactReviewer,
    BlockedArtefactError,
    clear_registered_artefact_reviewer,
    get_registered_artefact_reviewer,
    register_artefact_reviewer,
)

# ─── Wire shape ──────────────────────────────────────────────────────────────


def test_review_is_frozen_dataclass():
    """ArtefactReview is frozen — immutable after construction."""
    r = ArtefactReview(
        tool_name="physics_sim_builder",
        server_id="demo-mcp-server",
        resource_uri="ui://render/abc",
        html="<html><body>hi</body></html>",
        csp=None,
        structured_content={"v": 1},
        invocation_id="inv-1",
    )
    with pytest.raises((AttributeError, TypeError)):
        r.html = "tampered"  # type: ignore[misc]


def test_decision_is_frozen_dataclass():
    d = ArtefactDecision(
        action="approve",
        message=None,
        reason_code=None,
        appeal_url=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        d.action = "block"  # type: ignore[misc]


def test_decision_action_literal_accepts_three_values():
    for action in ("approve", "warn", "block"):
        d = ArtefactDecision(
            action=action,  # type: ignore[arg-type]
            message=None,
            reason_code=None,
            appeal_url=None,
        )
        assert d.action == action


def test_review_field_names_mirror_typescript_camelcase_via_snake_case():
    """Python uses snake_case; TS uses camelCase. Same fields."""
    r = ArtefactReview(
        tool_name="t",
        server_id="s",
        resource_uri="ui://x",
        html="<html/>",
        csp="default-src 'self'",
        structured_content=None,
        invocation_id="inv",
    )
    # Field set check — adding/removing a field requires a parallel TS change.
    from dataclasses import fields

    assert {f.name for f in fields(r)} == {
        "tool_name",
        "server_id",
        "resource_uri",
        "html",
        "csp",
        "structured_content",
        "invocation_id",
    }


# ─── Protocol shape / runtime_checkable ──────────────────────────────────────


def test_artefact_reviewer_is_runtime_checkable_protocol():
    """isinstance() works against duck-typed impls — no inheritance needed."""

    class AdHocReviewer:
        async def review(self, request):
            return ArtefactDecision(action="approve", message=None, reason_code=None, appeal_url=None)

    assert isinstance(AdHocReviewer(), ArtefactReviewer)


def test_artefact_reviewer_rejects_class_without_review_method():
    class NoReview:
        pass

    assert not isinstance(NoReview(), ArtefactReviewer)


# ─── Registry ────────────────────────────────────────────────────────────────


def test_registry_returns_none_when_unregistered():
    clear_registered_artefact_reviewer()
    assert get_registered_artefact_reviewer() is None


def test_register_and_retrieve_reviewer():
    class StubReviewer:
        async def review(self, request):
            return ArtefactDecision(action="approve", message=None, reason_code=None, appeal_url=None)

    impl = StubReviewer()
    register_artefact_reviewer(impl)
    try:
        assert get_registered_artefact_reviewer() is impl
    finally:
        clear_registered_artefact_reviewer()


def test_register_replaces_previous_reviewer():
    class A:
        async def review(self, request):
            return ArtefactDecision(action="approve", message=None, reason_code=None, appeal_url=None)

    class B:
        async def review(self, request):
            return ArtefactDecision(action="block", message="no", reason_code="TEST", appeal_url=None)

    register_artefact_reviewer(A())
    register_artefact_reviewer(B())
    try:
        assert isinstance(get_registered_artefact_reviewer(), B)
    finally:
        clear_registered_artefact_reviewer()


def test_register_rejects_non_reviewer():
    """Fork misconfiguration must fail loud at startup."""

    class NotAReviewer:
        def review(self, request):  # sync — wrong shape
            return None

    with pytest.raises(TypeError, match="ArtefactReviewer"):
        register_artefact_reviewer(NotAReviewer())  # type: ignore[arg-type]


# ─── BlockedArtefactError ────────────────────────────────────────────────────


def test_blocked_artefact_error_carries_decision():
    d = ArtefactDecision(
        action="block",
        message="Contains forbidden <script> tag",
        reason_code="FORBIDDEN_TAG",
        appeal_url=None,
    )
    err = BlockedArtefactError(d)
    assert err.decision is d
    assert "Contains forbidden" in str(err)
