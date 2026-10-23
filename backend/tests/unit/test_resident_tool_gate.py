"""`ask_assistant` is gated on the `resident-user` role.

WHY THIS TEST EXISTS

M12 defined the `resident-user` tag and M13/M14 built provisioning around it,
but nothing ever checked it on the path that actually reaches an agent:
`is_resident_user` was referenced only by the tag-GRANTING route. With
`RESIDENT_AGENT_PER_USER` off — its state on dev — `ask_assistant` resolves the
single shared instance for whoever calls it, and that instance mounts one
person's agent home. So an ungated tool on a skill any colleague could open was
a cross-user read of somebody's workspace.

The gate lives on `make_permission_enforcer` because `resolve_tools` never
receives caller identity, which is the reason M12 deferred it in the first
place. These tests pin the behaviour so a refactor of that seam cannot quietly
drop it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from adk.callbacks import make_permission_enforcer
from auth.admin_roles import RESIDENT_USER_TAG


def _tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _ctx() -> MagicMock:
    c = MagicMock()
    c.agent_name = "resident-agent"
    return c


def test_ask_assistant_blocked_without_the_role():
    """No tag: short-circuited with a readable reason, and the tool never runs."""
    enforcer = make_permission_enforcer("outsider@example.com", "example.com", frozenset())
    result = enforcer(_tool("ask_assistant"), {"message": "hi"}, _ctx())
    assert result is not None, "an ungated ask_assistant hands over the shared instance"
    assert result["eligible"] is False
    assert "resident-user" in result["error"]


def test_ask_assistant_blocked_when_tags_omitted_entirely():
    """FAIL CLOSED. A call site that forgets to pass tags must deny, not grant."""
    enforcer = make_permission_enforcer("outsider@example.com", "example.com")
    result = enforcer(_tool("ask_assistant"), {"message": "hi"}, _ctx())
    assert result is not None
    assert result["eligible"] is False


def test_ask_assistant_allowed_with_the_role(monkeypatch):
    """With the tag the gate defers to the ordinary email/domain policy."""
    monkeypatch.setattr("adk.callbacks.can_use_tool", lambda *a, **k: True)
    enforcer = make_permission_enforcer("holder@example.com", "example.com", frozenset({RESIDENT_USER_TAG}))
    assert enforcer(_tool("ask_assistant"), {"message": "hi"}, _ctx()) is None


def test_other_tools_are_untouched_by_the_role_gate(monkeypatch):
    """The gate is scoped to the role-gated set — it must not become a second
    permission system for every tool."""
    monkeypatch.setattr("adk.callbacks.can_use_tool", lambda *a, **k: True)
    enforcer = make_permission_enforcer("anyone@example.com", "example.com", frozenset())
    assert enforcer(_tool("google_search"), {"q": "x"}, _ctx()) is None


def test_create_agent_passes_group_tags_to_the_enforcer():
    """The gate is only real if the tags actually reach it. Asserting the wiring
    because an enforcer that always sees an empty set would deny everyone and
    look like a different bug."""
    import inspect

    from adk import agent as agent_mod

    src = inspect.getsource(agent_mod)
    assert "make_permission_enforcer(user.email, user.domain, user.group_tags)" in src
