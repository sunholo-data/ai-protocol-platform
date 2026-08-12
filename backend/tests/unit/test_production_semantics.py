"""Production semantics that the default test doubles hide (v6.19.0).

Two failure modes have reached production because CI models the world more
permissively than the real services do:

* **#35 session ownership** — `InMemorySessionService` returns ``None`` for a
  non-owner; Vertex *raises*. Silent-vs-loud is the bug: a ``None`` is read as
  "no such session", so a new one is created and then collides on the reused
  threadId. (Narrower than the original report, which said the in-memory double
  leaks the session outright — it does not, in this ADK version.)
* **#37 state scoping** — dict-based state fixtures ignore ADK key prefixes, so
  an `app:`-prefixed per-session counter looks correct in tests and is a single
  global odometer in production.

The doubles under test here exist to make both catchable in `make test-fast`.
Each test is written to fail against the pre-fix behaviour — a double that
cannot fail is a double that asserts nothing.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.state import State

from tests.support.session_doubles import OwnershipEnforcingSessionService
from tests.support.state_doubles import ScopedState

APP = "aitana_platform"


class TestSessionOwnership:
    @pytest.mark.asyncio
    async def test_owner_can_read_its_own_session(self):
        svc = OwnershipEnforcingSessionService()
        await svc.create_session(app_name=APP, user_id="owner", session_id="s1")

        assert await svc.get_session(app_name=APP, user_id="owner", session_id="s1") is not None

    @pytest.mark.asyncio
    async def test_a_different_uid_is_rejected(self):
        """Vertex raises on a non-owner read; so must the double."""
        svc = OwnershipEnforcingSessionService()
        await svc.create_session(app_name=APP, user_id="owner", session_id="s1")

        with pytest.raises(ValueError, match="does not belong to user"):
            await svc.get_session(app_name=APP, user_id="stranger", session_id="s1")

    @pytest.mark.asyncio
    async def test_default_double_is_SILENT_where_vertex_is_LOUD(self):
        """The real gap, which is narrower than first reported — and still matters.

        This ADK version's `InMemorySessionService` does not leak the session to
        a stranger; it returns ``None``. But Vertex *raises*. That difference is
        the whole bug: `ag_ui_adk`'s SessionManager treats ``None`` as "no such
        session" and creates a new one, which then collides on the reused
        threadId with "already exists" and kills the run. A silent ``None`` and
        an explicit error are not interchangeable.
        """
        permissive = InMemorySessionService()
        await permissive.create_session(app_name=APP, user_id="owner", session_id="s1")

        # Silent: indistinguishable from a session that never existed.
        assert await permissive.get_session(app_name=APP, user_id="stranger", session_id="s1") is None

        strict = OwnershipEnforcingSessionService()
        await strict.create_session(app_name=APP, user_id="owner", session_id="s1")
        with pytest.raises(ValueError):
            await strict.get_session(app_name=APP, user_id="stranger", session_id="s1")

    @pytest.mark.asyncio
    async def test_unknown_session_is_still_none_not_an_error(self):
        """Missing != not-yours. Conflating them would mask real 404s."""
        svc = OwnershipEnforcingSessionService()

        assert await svc.get_session(app_name=APP, user_id="anyone", session_id="nope") is None


class TestStateScoping:
    def test_session_scoped_keys_are_independent(self):
        """Two sessions, same app: unprefixed keys must not be shared."""
        app_store: dict = {}
        s1 = ScopedState(app_store=app_store)
        s2 = ScopedState(app_store=app_store)

        s1["chat_session_turn_count"] = 5

        assert s2.get("chat_session_turn_count") is None

    def test_app_prefixed_keys_ARE_shared(self):
        """The property that made the counter a global odometer."""
        app_store: dict = {}
        s1 = ScopedState(app_store=app_store)
        s2 = ScopedState(app_store=app_store)

        s1[f"{State.APP_PREFIX}counter"] = 5

        assert s2[f"{State.APP_PREFIX}counter"] == 5

    def test_interleaved_counters_stay_independent(self):
        """The exact production symptom, reproduced.

        Two sessions each taking two turns must both end at 2. Under the
        pre-fix `app:` prefix the shared odometer reaches 4.
        """
        app_store: dict = {}
        s1 = ScopedState(app_store=app_store)
        s2 = ScopedState(app_store=app_store)
        key = "chat_session_turn_count"

        for state in (s1, s2, s1, s2):
            state[key] = state.get(key, 0) + 1

        assert s1[key] == 2
        assert s2[key] == 2

    def test_interleaved_counters_collide_when_app_prefixed(self):
        """Control: prove the double actually detects the mis-scoping."""
        app_store: dict = {}
        s1 = ScopedState(app_store=app_store)
        s2 = ScopedState(app_store=app_store)
        key = f"{State.APP_PREFIX}chat_session_turn_count"

        for state in (s1, s2, s1, s2):
            state[key] = state.get(key, 0) + 1

        assert s1[key] == 4, "an app:-prefixed counter is one global odometer"

    def test_user_prefixed_keys_are_shared_per_user_store(self):
        user_store: dict = {}
        s1 = ScopedState(user_store=user_store)
        s2 = ScopedState(user_store=user_store)

        s1[f"{State.USER_PREFIX}pref"] = "dark"

        assert s2[f"{State.USER_PREFIX}pref"] == "dark"

    def test_a_plain_dict_does_NOT_catch_this(self):
        """Why the fixtures had to change: a dict reports the bug as correct."""
        plain: dict = {}
        key = f"{State.APP_PREFIX}chat_session_turn_count"
        plain[key] = plain.get(key, 0) + 1

        assert plain[key] == 1, "a dict has no scope concept — the bug looks fine"

    def test_contains_and_pop_respect_scope(self):
        app_store: dict = {}
        s1 = ScopedState(app_store=app_store)
        s2 = ScopedState(app_store=app_store)

        s1["local"] = 1
        s1[f"{State.APP_PREFIX}global"] = 2

        assert "local" in s1 and "local" not in s2
        assert f"{State.APP_PREFIX}global" in s2

        s1.pop(f"{State.APP_PREFIX}global")
        assert f"{State.APP_PREFIX}global" not in s2


def test_callback_state_keys_are_never_app_scoped():
    """Static tripwire — this would have caught issue #38 directly.

    Flags ``app:`` only. ``user:`` is deliberately allowed: commit ``4999307``
    moved four keys FROM ``app:`` TO ``user:`` precisely because ``app:`` is
    shared by every session of every user (a cross-user RAG-corpus leak), while
    ``user:`` is the correct scope for "survives across THIS user's sessions".
    Flagging ``user:`` too would fight that fix.
    """
    from adk import callbacks

    source = Path(inspect.getfile(callbacks)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(State.APP_PREFIX)
    ]

    assert not offenders, (
        f"callback module uses app:-scoped state keys {offenders}. "
        "`app:` is shared by EVERY session of EVERY user — a per-session "
        "counter under it becomes one global odometer, and per-user data "
        "under it leaks across tenants (issue #38). Use `user:` or no prefix."
    )
