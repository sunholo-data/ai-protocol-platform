"""Bounded project text matches → the existing Sources workbench surface."""

from __future__ import annotations

from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID, register
from adk.a2ui_sources_render import sources_to_a2ui

SURFACE = "project_text_matches"


def project_text_matches_to_a2ui(result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    if not isinstance(result, dict):
        return None
    sources = []
    for row in result.get("results") or []:
        location = f"page {row['page']}, " if row.get("page") is not None else ""
        location += f"block {row['block_id']}"
        sources.append({"title": f"{row['filename']} — {location}", "uri": "", "snippet": row["snippet"]})
    messages = sources_to_a2ui(sources, surface_id=SURFACE, title="Project text matches")
    notice = result.get("notice") or result.get("message") or "No matching passages."
    if not messages:
        messages = [
            {"version": "v0.9", "createSurface": {"surfaceId": SURFACE, "catalogId": BASIC_CATALOG_ID}},
            {
                "version": "v0.9",
                "updateComponents": {
                    "surfaceId": SURFACE,
                    "components": [
                        {"id": "root", "component": "Column", "children": ["notice"]},
                        {"id": "notice", "component": "Text", "text": str(notice)},
                    ],
                },
            },
        ]
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": SURFACE,
                "value": {
                    "sources": sources,
                    "notice": str(notice),
                },
            },
        }
    )
    return messages


register(
    project_text_matches_to_a2ui,
    tool_names=["grep_project_documents"],
    surface=SURFACE,
    artifact_meta=lambda result, tool_context=None: {
        "kind": "sources",
        "title": "Project text matches",
        "description": str(
            (result or {}).get("notice") or f"{len((result or {}).get('results') or [])} matching passages"
        ),
        **({"placement": "chat"} if not (result or {}).get("results") else {}),
    },
)
