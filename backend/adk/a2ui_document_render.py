"""Generated document → A2UI result render (v6.32.0 document-creation).

Registers a result→A2UI mapping for `generate_document` on the proven 7.3 path
([a2ui_result_render](a2ui_result_render.py)), so a created document gets its own
workbench tab and a row in the Workspace/Home index.

Design: docs/design/v6.32.0/document-creation.md

## Registering `tool_names` is NOT optional here

Declaring `tool_names` is what marks a tool's output **offload-exempt**
(`is_render_payload_tool`). Without it a result over 50K is offloaded to an
artifact by `_handle_large_output`, the transform never sees it, no artifact is
registered, and the workbench tab never appears — the agent narrates "I've
created the document" while the tab stays on its empty state. See
`backend/adk/CLAUDE.md` trap 4. A generated document's result carries the source
markdown on the failure path and will exceed that threshold.

## The failure path renders too

A conversion that fails returns `status: "failed"` with a human-readable reason,
and this renders it as a visible card rather than returning `None`. That is
principle #8: a user who asked for a document must never be left with a silent
non-event. The success and failure surfaces are the same tab, so a retry updates
in place.
"""

from __future__ import annotations

import logging
from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID, register

logger = logging.getLogger(__name__)

# One tab per created document — keyed on the doc-id so regenerating the same
# document updates its tab in place, while a different document opens its own.
DOCUMENT_SURFACE_ID = "created_document"

_GENERATE_TOOLS = ["generate_document"]


def _as_dict(typed_result: Any) -> dict[str, Any] | None:
    if isinstance(typed_result, dict):
        return typed_result
    payload = getattr(typed_result, "model_dump", None)
    if callable(payload):
        try:
            return payload()
        except Exception:
            return None
    return None


def _size_label(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def document_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Build A2UI v0.9 Basic-catalog messages for a generated document."""
    result = _as_dict(typed_result)
    if not result:
        return None

    status = str(result.get("status") or "")
    if status not in ("created", "failed"):
        return None

    surface_id = _document_surface(typed_result)
    components: list[dict[str, Any]] = []
    seq = 0

    def _add(comp: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        comp["id"] = comp.get("id") or f"doc-{seq}"
        components.append(comp)
        return comp["id"]

    filename = str(result.get("filename") or "document")

    if status == "failed":
        rows = [
            _add({"component": "Text", "text": "Document not created", "variant": "h4"}),
            _add({"component": "Text", "text": str(result.get("error") or "Conversion failed.")}),
            _add({"component": "Text", "text": f"Intended file: {filename}"}),
        ]
    else:
        detail: list[str] = [f"Saved to your “{result.get('folder') or 'Created'}” folder."]
        template_used = str(result.get("templateUsed") or "")
        if template_used:
            carried = int(result.get("templatePartsCarried") or 0)
            if result.get("referenceDocApplied") and carried:
                detail.append(f"Styled from “{template_used}” ({carried} template parts carried).")
            elif result.get("referenceDocApplied"):
                detail.append(f"Styled from “{template_used}”.")
            else:
                detail.append(f"Template “{template_used}” was not applied.")
        size = _size_label(int(result.get("sizeBytes") or 0))
        if size:
            detail.append(f"{size} · {result.get('target') or 'docx'}")

        rows = [
            _add({"component": "Text", "text": filename, "variant": "h4"}),
            _add({"component": "Text", "text": " ".join(detail)}),
        ]

    col_id = _add({"component": "Column", "children": rows})
    card_id = _add({"component": "Card", "child": col_id})
    components.append({"id": "root", "component": "Column", "children": [card_id]})

    messages: list[dict[str, Any]] = [
        {"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}},
    ]
    # The data model carries the structured facts the workbench tab needs to
    # offer an authenticated download — never a public URL (CLAUDE.md security
    # rule): the frontend requests /api/proxy/... with the user's Bearer.
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": surface_id,
                "value": {
                    "document": {
                        "docId": result.get("docId") or "",
                        "filename": filename,
                        "folderId": result.get("folderId") or "",
                        "folder": result.get("folder") or "",
                        "target": result.get("target") or "",
                        "status": status,
                        "templateUsed": result.get("templateUsed") or "",
                        "referenceDocApplied": bool(result.get("referenceDocApplied")),
                        "templatePartsCarried": int(result.get("templatePartsCarried") or 0),
                        "blockCount": int(result.get("blockCount") or 0),
                        "sizeBytes": int(result.get("sizeBytes") or 0),
                        "error": result.get("error") or "",
                    }
                },
            },
        }
    )
    return messages


def _document_surface(typed_result: Any) -> str:
    """Per-document surfaceId — ``created_document:<docId>``.

    Stable, so regenerating a document updates its tab rather than opening a
    duplicate; distinct, so a second document gets its own tab and auto-focuses
    it. A failure has no doc-id and falls back to the base id.
    """
    result = _as_dict(typed_result) or {}
    doc_id = str(result.get("docId") or "")
    return f"{DOCUMENT_SURFACE_ID}:{doc_id}" if doc_id else DOCUMENT_SURFACE_ID


def _document_artifact(typed_result: Any, tool_context: Any = None) -> dict[str, Any] | None:
    """Workbench tab + Home-index metadata (7.5).

    Friendly by construction (CLAUDE.md #9): the tab is named after the FILE,
    never the doc-id.
    """
    result = _as_dict(typed_result) or {}
    status = str(result.get("status") or "")
    if status not in ("created", "failed"):
        return None
    filename = str(result.get("filename") or "Document")
    if status == "failed":
        return {"kind": "document", "title": filename, "description": "Document could not be created"}
    template_used = str(result.get("templateUsed") or "")
    description = f"Created in “{result.get('folder') or 'Created'}”"
    if template_used:
        description += f", styled from “{template_used}”"
    return {"kind": "document", "title": filename, "description": description}


register(
    document_to_a2ui,
    tool_names=_GENERATE_TOOLS,
    name="created-document",
    surface=_document_surface,
    artifact_meta=_document_artifact,
)
