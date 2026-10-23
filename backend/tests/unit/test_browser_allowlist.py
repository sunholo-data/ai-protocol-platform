"""The browser allowlist — the policy decision point.

These live in the BACKEND rather than the browser service on purpose. That
service holds zero project roles so it cannot read Firestore; an allowlist
implemented there could only fail closed, and on the first deployed revision it
did exactly that, denying every user. See tools/browser_allowlist.py.
"""

from __future__ import annotations

import pytest

from tools import browser_allowlist as mod
from tools.browser_allowlist import NotAllowed, assert_allowed, is_allowed


@pytest.fixture(autouse=True)
def _clear():
    mod.clear_cache()
    yield
    mod.clear_cache()


class TestMembership:
    def test_empty_config_denies(self):
        """Deny-by-default, which is also the fail-closed path when Firestore
        is unreachable."""
        with pytest.raises(NotAllowed):
            assert_allowed("someone@example.com", config={})

    def test_listed_user_allowed(self):
        assert_allowed("a@example.com", config={"users": ["a@example.com"]})

    def test_unlisted_user_denied(self):
        with pytest.raises(NotAllowed):
            assert_allowed("b@example.com", config={"users": ["a@example.com"]})

    def test_domain_grant_allows(self):
        assert_allowed("anyone@example.com", config={"domains": ["example.com"]})

    def test_domain_grant_does_not_leak(self):
        with pytest.raises(NotAllowed):
            assert_allowed("attacker@evil.com", config={"domains": ["example.com"]})

    def test_kill_switch_disables_everyone(self):
        cfg = {"users": ["a@example.com"], "enabled": False}
        with pytest.raises(NotAllowed, match="temporarily disabled"):
            assert_allowed("a@example.com", config=cfg)

    def test_empty_user_denied(self):
        with pytest.raises(NotAllowed):
            assert_allowed("", config={"domains": ["example.com"]})

    def test_refusal_message_is_fit_to_show_a_user(self):
        """CLAUDE.md #8 — a refusal the user can act on, not a 403."""
        with pytest.raises(NotAllowed) as exc:
            assert_allowed("x@example.com", config={"users": []})
        assert "administrator" in str(exc.value).lower()

    def test_is_allowed_mirrors_assert(self):
        cfg = {"users": ["a@example.com"]}
        assert is_allowed("a@example.com", config=cfg) is True
        assert is_allowed("b@example.com", config=cfg) is False


class TestFailClosed:
    def test_firestore_failure_denies_rather_than_opens(self):
        """The direction that matters: an outage must not open the browser."""
        import tools.browser_allowlist as m

        original = m._load
        m._load = lambda now=None: {}
        try:
            with pytest.raises(NotAllowed):
                assert_allowed("anyone@example.com")
        finally:
            m._load = original


class TestCallersAreGated:
    """Both paths to the browser check before dialling — the service cannot."""

    def test_browse_tool_imports_the_gate(self):
        import inspect

        import tools.browser as browser_tool

        source = inspect.getsource(browser_tool.browse)
        assert "assert_allowed" in source

    def test_mcp_registry_gates_on_config(self):
        import inspect

        from tools.mcp import registry

        source = inspect.getsource(registry._build_header_provider)
        assert "browser_allowlist" in source
        assert "assert_allowed" in source
