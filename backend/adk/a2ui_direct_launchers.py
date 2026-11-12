"""Fixed no-model workbench entry points. Skill metadata selects registered handlers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LauncherContext:
    user: Any
    session_id: str
    skill_id: str


@dataclass
class LaunchResult:
    """Safe control envelope only. Confidential detail uses private direct actions."""

    surface_id: str
    messages: list[dict[str, Any]]
    artifact: dict[str, Any]
    message: str = "Opened."


@dataclass(frozen=True)
class _Launcher:
    handler: Callable[[LauncherContext], Awaitable[LaunchResult]]
    requires_tool: str


_registry: dict[str, _Launcher] = {}


def register_launcher(
    *, name: str, handler: Callable[[LauncherContext], Awaitable[LaunchResult]], requires_tool: str
) -> None:
    """Register a deterministic handler, never a browser-selected callable/tool."""
    _registry[name] = _Launcher(handler, requires_tool)


def lookup_launcher(name: str) -> _Launcher | None:
    return _registry.get(name)
