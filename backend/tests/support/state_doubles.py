"""State double that honours ADK key-prefix scoping (AIPLA #37).

In ADK a state key's PREFIX sets its scope:

    app:   -> application-global (shared by every user and every session)
    user:  -> per user
    temp:  -> not persisted
    (none) -> session-scoped

A plain `dict` — which is what the callback fixtures use — has no concept of
any of that, so a mis-scoped key looks perfectly correct in tests.

What that hid, twice:

* The reporting fork stored a per-session turn counter under
  ``app:chat_session_turn_count``. It was one global odometer shared by every
  session. A teacher report showed ``turnCount: 259`` for an 18-second,
  2-message session; the values clustered in 246-262 across four different
  owners, climbing with wall-clock time. It also broke title generation (gated
  on ``turn_count == 2``, which a global counter is almost never at for a given
  session) and made the per-session "initialized" flag global.

* We hit the identical bug independently — issue #38, commit ``4999307``,
  2026-07-28, "six mis-scoped ADK state keys, incl. a cross-user RAG corpus".
  A month after they documented it. Neither CI could have caught it.

The keys are fixed. This double closes the blind spot that allowed them.
"""

from __future__ import annotations

from typing import Any

from google.adk.sessions.state import State


class ScopedState(dict):
    """A dict-shaped state that routes prefixed keys to shared stores.

    Two `ScopedState` instances constructed with the same `app_store` /
    `user_store` model two sessions in the same app: writes to ``app:``-prefixed
    keys are visible to both, session-scoped keys are not. That is the entire
    property a plain dict cannot express.

    Subclasses `dict` so it drops into fixtures that type-hint or duck-type a
    mapping, while intercepting the accessors that carry scope.
    """

    def __init__(
        self,
        *,
        app_store: dict[str, Any] | None = None,
        user_store: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        # Shared across every ScopedState that was handed the same dict —
        # this is what makes app:/user: scope observable.
        self.app_store = app_store if app_store is not None else {}
        self.user_store = user_store if user_store is not None else {}
        self._session_store: dict[str, Any] = {}

    def _store_for(self, key: str) -> dict[str, Any]:
        if key.startswith(State.APP_PREFIX):
            return self.app_store
        if key.startswith(State.USER_PREFIX):
            return self.user_store
        # temp: is non-persisted but still session-lifetime, so the session
        # store models it correctly for a single-turn test.
        return self._session_store

    def __getitem__(self, key: str) -> Any:
        return self._store_for(key)[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._store_for(key)[key] = value

    def __delitem__(self, key: str) -> None:
        del self._store_for(key)[key]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._store_for(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._store_for(key).get(key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self._store_for(key).setdefault(key, default)

    def pop(self, key: str, *args: Any) -> Any:
        return self._store_for(key).pop(key, *args)

    def update(self, other: Any = (), /, **kwargs: Any) -> None:  # type: ignore[override]
        items = other.items() if hasattr(other, "items") else other
        for key, value in items:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def keys(self):  # type: ignore[override]
        return {**self.app_store, **self.user_store, **self._session_store}.keys()

    def items(self):  # type: ignore[override]
        return {**self.app_store, **self.user_store, **self._session_store}.items()

    def values(self):  # type: ignore[override]
        return {**self.app_store, **self.user_store, **self._session_store}.values()

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.app_store) + len(self.user_store) + len(self._session_store)

    def __repr__(self) -> str:
        return f"ScopedState(session={self._session_store!r}, app={self.app_store!r}, user={self.user_store!r})"
