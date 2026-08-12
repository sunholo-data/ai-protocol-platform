"""Result → A2UI mapping registry (Model B — tool-results-as-a2ui / 7.3).

A tool returns only its typed JSON (unchanged, model-visible). A registered
*transform* maps that typed result into A2UI v0.9 surface messages, which are
pushed to the ``workspace`` surface via an out-of-band AG-UI CUSTOM event
(never entering the model's context — see
``adk.callbacks.make_a2ui_result_emitter`` +
``observability.timing.LatencyTracker.emit_a2ui_surface``).

Design (docs/design/v6.7.0/tool-results-as-a2ui.md, Model B):

  * ``register(transform, tool_names=..., result_matcher=...)`` adds a mapping
    keyed on tool name and/or result shape. First matching mapping wins.
  * ``render_for(tool_name, typed_result)`` runs the first matching transform,
    returning A2UI v0.9 messages (``list[dict]``) or ``None``.
  * ``is_render_payload_tool(tool_name)`` drives the large-output offload
    exemption — declaring ``tool_names`` on a mapping marks those tools' output
    as a UI payload, so ``_handle_large_output`` never strands the render by
    offloading it to an artifact. This retires the hardcoded
    ``_RENDER_PAYLOAD_TOOLS`` set that used to live in ``callbacks.py``.

Transforms are pure ``typed_result -> list[A2uiMessage] | None`` functions:
server-side, unit-testable, CLI-previewable (``aiplatform a2ui render``). They
are the ONLY place a tool's result becomes UI — no bespoke React per tool.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# One A2UI v0.9 wire message, e.g.
#   {"version": "v0.9", "createSurface": {"surfaceId": ..., "catalogId": ...}}
#   {"version": "v0.9", "updateComponents": {"surfaceId": ..., "components": [...]}}
#   {"version": "v0.9", "updateDataModel": {"surfaceId": ..., "path": ..., "value": ...}}
A2uiMessage = dict[str, Any]

# A transform: ``(typed_result, tool_context=None) -> list[A2uiMessage] | None``.
# ``typed_result`` is the unwrapped dict/list; ``tool_context`` is the ADK
# ToolContext (or None outside a tool run — e.g. the CLI preview) so a transform
# can read session state to accumulate across tool calls (e.g. tab-per-document
# for repeated extractions). Return None to decline (emit nothing).
Transform = Callable[..., "list[A2uiMessage] | None"]

# Optional predicate over the typed result, for shape-based matching (used
# when one tool name can produce two different result shapes).
ResultMatcher = Callable[[Any], bool]

# The canonical v0.9 Basic catalog id — every createSurface references it.
BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json"

# Tool results render onto the session-scoped workspace surface by default.
WORKSPACE_SURFACE_ID = "workspace"


# A surface strategy: a literal surfaceId, or a callable ``typed_result ->
# surfaceId`` for per-entity artifacts (e.g. ``ppa_clauses:{doc_id}``).
SurfaceStrategy = "str | Callable[[Any], str]"
# Artifact metadata builder: ``typed_result -> {kind, title, description}`` (or
# None). Drives the workbench tab title + the Workspace index (7.5).
ArtifactMeta = Callable[[Any], "dict[str, Any] | None"]


@dataclass(frozen=True)
class RenderResult:
    """What the emitter needs to push one artifact: where + what + metadata."""

    surface_id: str
    messages: list[A2uiMessage]
    artifact: dict[str, Any] | None


@dataclass(frozen=True)
class _Mapping:
    transform: Transform
    tool_names: frozenset[str]
    result_matcher: ResultMatcher | None
    name: str
    surface: Any = WORKSPACE_SURFACE_ID  # str | Callable[[Any], str]
    artifact_meta: ArtifactMeta | None = None

    def matches(self, tool_name: str, typed_result: Any) -> bool:
        if self.tool_names and tool_name not in self.tool_names:
            return False
        if self.result_matcher is not None:
            try:
                return bool(self.result_matcher(typed_result))
            except Exception as exc:  # a bad matcher must never break the loop
                logger.warning("a2ui result matcher %r raised (suppressed): %s", self.name, exc)
                return False
        return True

    def resolve_surface(self, typed_result: Any) -> str:
        """The target surfaceId — a literal, or a callable of the result. Falls
        back to the workspace surface if a callable raises / returns empty."""
        strategy = self.surface
        if callable(strategy):
            try:
                return strategy(typed_result) or WORKSPACE_SURFACE_ID
            except Exception as exc:
                logger.warning("a2ui surface strategy %r raised (suppressed): %s", self.name, exc)
                return WORKSPACE_SURFACE_ID
        return strategy or WORKSPACE_SURFACE_ID

    def resolve_artifact(self, typed_result: Any, tool_context: Any = None) -> dict[str, Any] | None:
        """Artifact metadata for this result, or None. Fail-safe.

        ``artifact_meta`` may take ``(typed_result)`` or ``(typed_result,
        tool_context)``. The arity is detected here rather than pushed onto every
        existing mapping, because only a result whose meaning depends on the
        REQUEST needs the context (v6.23.0: a BigQuery result is just rows, and
        the tab title comes from a marker in the SQL that produced them).
        """
        if self.artifact_meta is None:
            return None
        try:
            wants_context = False
            try:
                wants_context = len(inspect.signature(self.artifact_meta).parameters) >= 2
            except (TypeError, ValueError):  # builtins / C callables have no signature
                wants_context = False
            meta = self.artifact_meta(typed_result, tool_context) if wants_context else self.artifact_meta(typed_result)
            return meta if isinstance(meta, dict) else None
        except Exception as exc:
            logger.warning("a2ui artifact_meta %r raised (suppressed): %s", self.name, exc)
            return None


_registry: list[_Mapping] = []


def register(
    transform: Transform,
    *,
    tool_names: Iterable[str] | None = None,
    result_matcher: ResultMatcher | None = None,
    name: str | None = None,
    surface: Any = WORKSPACE_SURFACE_ID,
    artifact_meta: ArtifactMeta | None = None,
) -> str:
    """Register a result→A2UI transform. First matching mapping wins.

    A mapping matches a ``(tool_name, typed_result)`` pair when:
      * ``tool_names`` is empty OR ``tool_name`` is in it, AND
      * ``result_matcher`` is None OR ``result_matcher(typed_result)`` is truthy.

    Declaring ``tool_names`` also marks those tools as render-payload tools
    (never offloaded — see :func:`is_render_payload_tool`).

    Args:
        transform: Pure ``(typed_result, tool_context=None) -> messages | None``.
        tool_names: Tool names this mapping applies to (also the offload marker).
        result_matcher: Optional shape predicate for finer matching.
        name: Human/CLI-facing mapping name (defaults to ``transform.__name__``).
        surface: Target surface — a literal surfaceId or a callable
            ``typed_result -> surfaceId`` (per-entity artifacts, 7.5). Default
            ``workspace`` preserves 7.3 single-surface behaviour.
        artifact_meta: Optional ``typed_result -> {kind,title,description}`` for
            the workbench tab title + Workspace index (7.5).

    Returns:
        The registered mapping name (for the CLI + logging).
    """
    names = frozenset(tool_names or ())
    mapping_name = name or getattr(transform, "__name__", "mapping")
    _registry.append(
        _Mapping(
            transform=transform,
            tool_names=names,
            result_matcher=result_matcher,
            name=mapping_name,
            surface=surface,
            artifact_meta=artifact_meta,
        )
    )
    return mapping_name


# v0.9 message keys whose payload carries a `surfaceId` that must match the
# surface the emission targets (else the client processor creates the wrong
# surface and the artifact tab never appears).
_SURFACE_ID_KEYS = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")


def _retarget_surface(messages: list[A2uiMessage], surface_id: str) -> list[A2uiMessage]:
    """Rewrite each message's inner ``surfaceId`` to ``surface_id``.

    Transforms build their A2UI with a placeholder surfaceId (``workspace``);
    per-artifact routing (7.5) sends them to a different surface, so the two must
    be reconciled — the client keys the SurfaceModel on the message's surfaceId,
    not the CUSTOM-event envelope's. Returns new message dicts (no mutation of
    the transform's output)."""
    out: list[A2uiMessage] = []
    for msg in messages:
        new_msg = dict(msg)
        for key in _SURFACE_ID_KEYS:
            inner = new_msg.get(key)
            if isinstance(inner, dict) and "surfaceId" in inner:
                new_msg[key] = {**inner, "surfaceId": surface_id}
        out.append(new_msg)
    return out


def render_for_emit(tool_name: str, typed_result: Any, tool_context: Any = None) -> RenderResult | None:
    """Run the first matching transform and resolve its target surface + artifact
    metadata (7.5). Returns a :class:`RenderResult` or ``None``.

    ``tool_context`` is forwarded to the transform (state access). Fail-open: a
    transform that raises is logged and treated as ``None`` so a render bug never
    breaks the chat turn. The messages are retargeted to the resolved surface so
    the client builds the SurfaceModel under the artifact id (not the transform's
    ``workspace`` placeholder).
    """
    for mapping in _registry:
        if not mapping.matches(tool_name, typed_result):
            continue
        try:
            messages = mapping.transform(typed_result, tool_context)
        except Exception as exc:
            logger.warning("a2ui transform %r raised (suppressed): %s", mapping.name, exc)
            return None
        if not messages:
            return None
        surface_id = mapping.resolve_surface(typed_result)
        return RenderResult(
            surface_id=surface_id,
            messages=_retarget_surface(list(messages), surface_id),
            artifact=mapping.resolve_artifact(typed_result, tool_context),
        )
    return None


def render_for(tool_name: str, typed_result: Any, tool_context: Any = None) -> list[A2uiMessage] | None:
    """Run the first matching transform; return just its A2UI messages (or None).

    Thin wrapper over :func:`render_for_emit` for the CLI preview + callers that
    only need the messages (not the surface/artifact routing).
    """
    result = render_for_emit(tool_name, typed_result, tool_context)
    return result.messages if result else None


def render_by_name(mapping_name: str, typed_result: Any) -> list[A2uiMessage] | None:
    """Run a specific mapping by name, ignoring its matchers.

    Used by ``aiplatform a2ui render <mapping> --result <file.json>`` (M3) to
    preview a transform headlessly. Raises KeyError if the name is unknown.
    """
    for mapping in _registry:
        if mapping.name == mapping_name:
            messages = mapping.transform(typed_result, None)
            return list(messages) if messages else None
    raise KeyError(mapping_name)


def is_render_payload_tool(tool_name: str) -> bool:
    """True when any registered mapping claims ``tool_name``.

    This is the large-output offload exemption marker: a tool whose output feeds
    a UI mapping must never be replaced by an artifact pointer (that strands the
    render). Registry-driven — retires the hardcoded ``_RENDER_PAYLOAD_TOOLS``.
    """
    return any(tool_name in m.tool_names for m in _registry)


def tool_produces_artifact(tool_name: str) -> bool:
    """True when a registered mapping for ``tool_name`` carries artifact metadata.

    A tool whose result→A2UI mapping declares ``artifact_meta`` renders as a full
    workbench Result tab (7.5), so it is tier ``artifact`` for the curated
    workbench (6.11). Static signal (``artifact_meta is not None``) — does not
    depend on a specific result. Drives [`notability.tool_tier`](notability.py).
    """
    return any(tool_name in m.tool_names and m.artifact_meta is not None for m in _registry)


def registered_mapping_names() -> list[str]:
    """All registered mapping names, in registration order (for the CLI)."""
    return [m.name for m in _registry]


def clear_registry() -> None:
    """Drop all registrations. Test hook only — never called in production."""
    _registry.clear()
