"""Direct surface actions — a click that does a deterministic thing, no model.

Two-way workbench, mode C (the two-way workbench design
§4.1). Beside the result→A2UI mappings (:mod:`adk.a2ui_result_render`): a
mapping puts a surface on the workbench; a direct action is what a Button on
that surface does when the outcome needs no judgement — "Mark read", "Open",
"Re-sync". It runs on the server, instantly, costs no model turn, and cannot
be talked out of by a message on the board that says "don't".

A surface Button opts in by naming its action ``direct:<name>``; the frontend
routes those to ``POST /api/sessions/{sid}/surface-action-direct``
(:mod:`protocols.a2ui_surface_direct_routes`), which runs the surface-action
gates and then the handler registered here for the surface's artifact
``kind`` and ``<name>``.

**Never trust the client.** A handler receives the server's STASHED copy of
the surface (what the emitter actually rendered) and must act only on things
that surface showed: :meth:`DirectActionContext.shown` gives the item list from
the stash, and a handler refuses anything not in it.

The handler returns an :class:`ActionResult`: a one-line ``message`` the user
sees (NEVER SILENT), optional A2UI ``messages`` that patch the surface (applied
live by the browser AND appended to the stash so a reload shows them), and a
``record`` line the AI reads on its next turn ("Mark marked #3, #5 read").

For confidential review details, return ``private_surfaces`` descriptors instead:
``{"surface_id": "private:<control id>:review", "messages": [...], "artifact": {...}}``.
These separate display-only surfaces live only in the current browser session.
They are not stashed, written to history, or exported on a later model turn.
A private response also suppresses ``record`` even when ``tell_ai=True``.
Keep status/message and ordinary control patches nonconfidential.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

DIRECT_PREFIX = "direct:"


@dataclass
class DirectActionContext:
    """What a handler gets: the caller, the stashed surface, the click."""

    user: Any
    session_id: str
    skill_id: str
    surface_id: str
    stash: dict[str, Any]
    action_context: dict[str, Any]
    # The ADK session state (read-only copy) — e.g. a tool's ref map.
    state: dict[str, Any] = field(default_factory=dict)

    def data_model(self) -> dict[str, Any]:
        """Replay root replacements and JSON-pointer patches without mutating the stash."""
        value: dict[str, Any] = {}
        for msg in self.stash.get("messages") or []:
            upd = msg.get("updateDataModel") if isinstance(msg, dict) else None
            if not isinstance(upd, dict):
                continue
            path = upd.get("path") or "/"
            if path == "/":
                if isinstance(upd.get("value"), dict):
                    value = deepcopy(upd["value"])
                continue
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
            target: Any = value
            try:
                for part in parts[:-1]:
                    target = target[int(part)] if isinstance(target, list) else target[part]
                key = int(parts[-1]) if isinstance(target, list) else parts[-1]
                if isinstance(target, (dict, list)):
                    target[key] = deepcopy(upd.get("value"))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return value

    def shown(self, key: str = "items") -> list[dict[str, Any]]:
        """The item list the surface showed (``data_model()[key]``)."""
        items = self.data_model().get(key)
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


@dataclass
class ActionResult:
    """A handler's outcome. ``status`` is ok | partial | error."""

    status: str
    message: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    record: str = ""
    # Browser-only detail surfaces. Never added to ADK state, stash, or history.
    # IDs must be private:<control surface id>:<detail name>. Keep status/message
    # and ordinary messages nonconfidential; controls belong to the stashed board.
    private_surfaces: list[dict[str, Any]] = field(default_factory=list)


Handler = Callable[[DirectActionContext], Awaitable[ActionResult]]


@dataclass(frozen=True)
class _Action:
    surface_kind: str
    name: str
    handler: Handler
    requires_tool: str | None
    tell_ai: bool
    automatic: bool = False
    confirm_in_chat: bool = False


_registry: dict[tuple[str, str], _Action] = {}


def register_action(
    *,
    surface_kind: str,
    name: str,
    handler: Handler,
    requires_tool: str | None = None,
    tell_ai: bool = True,
    automatic: bool = False,
    confirm_in_chat: bool = False,
) -> None:
    """Register ``handler`` for ``direct:<name>`` clicks on surfaces of ``surface_kind``.

    Args:
        surface_kind: The artifact ``kind`` the surface's mapping declared.
        name: The action name WITHOUT the ``direct:`` prefix.
        handler: ``async (DirectActionContext) -> ActionResult``.
        requires_tool: A tool the session's skill must declare for the click
            to run — a direct action never grants more than the skill has.
        tell_ai: Record the outcome where the next turn's instructions read it.
        automatic: Opt in a read-only status handler to background refresh.
            Never enable for approvals, cancellations or other business writes.
        confirm_in_chat: Confirm a newly recorded manual result in the chat.
            Requires ActionResult.record; never runs on automatic status reads.
    """
    _registry[(surface_kind, name)] = _Action(
        surface_kind, name, handler, requires_tool, tell_ai, automatic, confirm_in_chat
    )


def lookup(surface_kind: str, action_name: str) -> _Action | None:
    """The registered action for a click, or None. Accepts the prefixed name."""
    name = action_name[len(DIRECT_PREFIX) :] if action_name.startswith(DIRECT_PREFIX) else action_name
    return _registry.get((surface_kind, name))


def clear_registry() -> None:
    """Test hook only."""
    _registry.clear()


__all__ = [
    "DIRECT_PREFIX",
    "ActionResult",
    "DirectActionContext",
    "clear_registry",
    "lookup",
    "register_action",
]
