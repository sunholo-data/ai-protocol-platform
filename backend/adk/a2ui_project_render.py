"""Project change → A2UI chat card (projects.md §6.7).

``create_project``, ``add_to_project`` and ``remove_from_project`` return a
structured result; this registers the mapping that turns it into a small card in
the chat — the outcome, the documents added or removed, anything not found or
ambiguous, any error, and an "Open project" button. Principle #7 (tool results
render as A2UI, never bespoke React) and #8 (an error is a visible card, never
only prose).

``placement: "chat"`` in the artifact metadata is what puts it inline in the
conversation (``ChatPlacementForms``) rather than in a workbench tab. Each call's
result carries its own ``cardId``, so a later change never rewrites an earlier
card. Declaring ``tool_names`` also marks the tools offload-exempt
(backend/adk/CLAUDE.md trap 4).
"""

from __future__ import annotations

from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID, register
from adk.project_tools import WRITE_TOOL_NAMES

PROJECT_CARD_SURFACE_ID = "project_card"

_TITLES = {
    ("create", "created"): "Project created",
    ("add", "updated"): "Project updated",
    ("remove", "updated"): "Project updated",
    ("create", "error"): "Project not created",
    ("add", "error"): "Nothing added",
    ("remove", "error"): "Nothing removed",
}


def _as_dict(typed_result: Any) -> dict[str, Any] | None:
    if isinstance(typed_result, dict) and typed_result.get("kind") == "project_change":
        return typed_result
    return None


def _join(items: list[Any]) -> str:
    return ", ".join(str(i) for i in items if i)


def project_change_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Build A2UI v0.9 Basic-catalog messages for a project change."""
    result = _as_dict(typed_result)
    if result is None:
        return None
    action, status = str(result.get("action") or ""), str(result.get("status") or "")
    surface_id = _project_surface(typed_result)
    components: list[dict[str, Any]] = []
    seq = 0

    def _add(comp: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        comp["id"] = comp.get("id") or f"pc-{seq}"
        components.append(comp)
        return comp["id"]

    def _text(text: str, variant: str | None = None) -> str:
        comp: dict[str, Any] = {"component": "Text", "text": text}
        if variant:
            comp["variant"] = variant
        return _add(comp)

    project_name = str((result.get("project") or {}).get("name") or "")
    title = _TITLES.get((action, status), "Nothing changed" if status == "needs_choice" else "Project")
    rows = [_text(f"{title}: {project_name}" if project_name else title, "h4")]

    if result.get("added"):
        rows.append(_text(f"Added: {_join(result['added'])}"))
    if result.get("already"):
        rows.append(_text(f"Already in the project: {_join(result['already'])}"))
    if result.get("removed"):
        rows.append(_text(f"Removed: {_join(result['removed'])} — still in your documents."))
    if result.get("notInProject"):
        rows.append(_text(f"Not in this project: {_join(result['notInProject'])}"))
    if result.get("chatAdded"):
        rows.append(_text("This chat is now part of the project."))
    if result.get("chatNotLinked"):
        rows.append(_text("This chat could not be linked to the project yet; send a message and try again."))
    for amb in result.get("ambiguous") or []:
        rows.append(_text(f"“{amb.get('name')}” could be: {_join(amb.get('candidates') or [])}"))
    if result.get("notFound"):
        rows.append(_text(f"Not found in your documents: {_join(result['notFound'])}"))
    for failed in result.get("failed") or []:
        label = failed.get("filename") or "A document"
        rows.append(_text(f"Could not add {label}: {failed.get('reason') or 'unknown error'}"))
    if result.get("error"):
        rows.append(_text(str(result["error"])))
    if status == "needs_choice":
        rows.append(_text("Nothing was changed. Say which one you mean."))

    if result.get("url") and status != "error":
        label_id = _text("Open project")
        rows.append(
            _add(
                {
                    "component": "Button",
                    "child": label_id,
                    "variant": "primary",
                    "action": {"functionCall": {"call": "openUrl", "args": {"url": str(result["url"])}}},
                }
            )
        )

    col_id = _add({"component": "Column", "children": rows})
    card_id = _add({"component": "Card", "child": col_id})
    components.append({"id": "root", "component": "Column", "children": [card_id]})
    return [
        {"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}},
    ]


def _project_surface(typed_result: Any) -> str:
    result = _as_dict(typed_result) or {}
    card = str(result.get("cardId") or "")
    return f"{PROJECT_CARD_SURFACE_ID}:{card}" if card else PROJECT_CARD_SURFACE_ID


def _project_artifact(typed_result: Any, tool_context: Any = None) -> dict[str, Any] | None:
    """Friendly by construction (#9): titled by the project NAME, never its id."""
    result = _as_dict(typed_result)
    if result is None:
        return None
    name = str((result.get("project") or {}).get("name") or "")
    action, status = str(result.get("action") or ""), str(result.get("status") or "")
    title = _TITLES.get((action, status), "Project")
    return {
        "kind": "project",
        "title": f"{title}: {name}" if name else title,
        "description": str(result.get("error") or ""),
        "placement": "chat",
    }


register(
    project_change_to_a2ui,
    tool_names=list(WRITE_TOOL_NAMES),
    name="project-change",
    surface=_project_surface,
    artifact_meta=_project_artifact,
)
