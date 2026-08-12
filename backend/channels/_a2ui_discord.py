"""A2UI v0.9 Basic catalog → Discord embed projection (v6.21.0 M2).

The first native `ChannelSink.render_surface` implementation. It proves the
sink boundary sits in the right place: everything here is Discord's embed
vocabulary and A2UI's component vocabulary, with no AG-UI event handling —
that lives in `_agui_render`.

Wire shape (see `adk/a2ui_result_render.py`, `adk/a2ui_sources_render.py`):

    {
      "surfaceId": "workspace",
      "messages": [
        {"version": "v0.9", "createSurface":    {"surfaceId": ..., "catalogId": ...}},
        {"version": "v0.9", "updateComponents": {"surfaceId": ..., "components": [...]}},
        {"version": "v0.9", "updateDataModel":  {"surfaceId": ..., "value": {...}}},
      ],
      "artifact": ..., "sourceId": ..., "toolName": ...,
    }

Two A2UI structural facts drive the whole projection:

  - **Components are flat with id refs.** `children` / `child` / `content` /
    `trigger` hold component-**id strings**, not nested objects, and the tree
    root is the component with `id == "root"`. So projecting means walking a
    graph, not a tree literal — and a malformed surface can contain cycles.
  - **Values may be data-model bindings.** `{"path": "/key"}` resolves against
    the `updateDataModel` value; literals are bare strings/numbers.

Scope is deliberately narrow (M2): Basic catalog, **read-only**. Interactive
components (`Button`, `ChoicePicker`, `TextField`, …) cannot round-trip to the
agent from a channel yet, so they are announced as text rather than silently
dropped — the user learns an interactive control exists and where to use it.

Anything unmappable degrades to readable text. A surface must never vanish.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Discord's documented embed limits. Exceeding any of them makes the whole
# API call fail, so the projection budgets against them rather than hoping.
EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 25
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FOOTER_LIMIT = 2048
EMBED_TOTAL_LIMIT = 6000

# Aitana brand accent for surface embeds (Discord wants an int, not "#rrggbb").
EMBED_COLOR = 0x4F46E5

# `Text.variant` values that read as headings rather than body copy.
_HEADING_VARIANTS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "title", "heading"})

# Components that carry an action back to the agent. Read-only on channels.
_INTERACTIVE_COMPONENTS = frozenset(
    {"Button", "ChoicePicker", "CheckBox", "TextField", "Slider", "DateTimeInput", "Modal"}
)

# Props holding child component-id refs, in the Basic catalog.
_CHILD_REF_PROPS = ("children", "child", "content", "trigger")

_INTERACTIVE_NOTE = "_Interactive controls are available in the workbench._"


def _truncate(text: str, limit: int) -> str:
    """Clip to `limit`, marking the cut so a user knows text is missing."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _resolve(value: Any, data_model: dict[str, Any]) -> Any:
    """Resolve an A2UI value that may be a `{"path": "/key"}` binding."""
    if isinstance(value, dict) and "path" in value:
        path = value.get("path")
        if not isinstance(path, str):
            return None
        cursor: Any = data_model
        for part in [p for p in path.split("/") if p]:
            if isinstance(cursor, dict):
                cursor = cursor.get(part)
            elif isinstance(cursor, list) and part.isdigit():
                index = int(part)
                cursor = cursor[index] if 0 <= index < len(cursor) else None
            else:
                return None
            if cursor is None:
                return None
        return cursor
    return value


def _as_text(value: Any) -> str:
    """Render a resolved A2UI value as flat text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(t for t in (_as_text(v) for v in value) if t)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    return str(value)


def parse_surface(payload: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]] | None:
    """Extract `(components_by_id, data_model)` from an A2UI surface payload.

    Returns None when the payload carries no components — a surface we
    cannot project at all, which the caller degrades to text.
    """
    if not isinstance(payload, dict):
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None

    components: dict[str, dict[str, Any]] = {}
    data_model: dict[str, Any] = {}

    for message in messages:
        if not isinstance(message, dict):
            continue

        update = message.get("updateComponents")
        if isinstance(update, dict):
            for component in update.get("components") or []:
                if isinstance(component, dict) and isinstance(component.get("id"), str):
                    components[component["id"]] = component

        model = message.get("updateDataModel")
        if isinstance(model, dict):
            value = model.get("value")
            if isinstance(value, dict):
                data_model.update(value)

    if not components:
        return None
    return components, data_model


class _Walker:
    """Flatten the component graph into ordered (kind, text) blocks.

    Tracks visited ids because `children` are id refs — a malformed or
    hand-authored surface can reference a component already on the stack,
    and an unguarded walk would recurse forever.
    """

    MAX_BLOCKS = 200

    def __init__(self, components: dict[str, dict[str, Any]], data_model: dict[str, Any]) -> None:
        self._components = components
        self._data_model = data_model
        self._visited: set[str] = set()
        self.blocks: list[tuple[str, str]] = []
        self.image_url: str | None = None
        self.has_interactive = False

    def walk(self, component_id: str) -> None:
        if component_id in self._visited or len(self.blocks) >= self.MAX_BLOCKS:
            return
        self._visited.add(component_id)

        component = self._components.get(component_id)
        if not isinstance(component, dict):
            return

        name = component.get("component")
        if name in _INTERACTIVE_COMPONENTS:
            self.has_interactive = True
            self._emit_interactive(name, component)
        elif name == "Text":
            self._emit_text(component)
        elif name == "Image":
            self._emit_image(component)
        elif name in ("AudioPlayer", "Video"):
            url = _as_text(_resolve(component.get("url"), self._data_model))
            if url:
                self.blocks.append(("body", f"[{str(name).lower()}]({url})"))
        elif name == "Divider":
            self.blocks.append(("divider", ""))
        elif name == "Icon":
            pass  # Decorative; Discord embeds have no inline icon slot.
        elif name == "Tabs":
            self._emit_tabs(component)

        self._walk_children(component)

    def _walk_children(self, component: dict[str, Any]) -> None:
        for prop in _CHILD_REF_PROPS:
            ref = component.get(prop)
            if isinstance(ref, str):
                self.walk(ref)
            elif isinstance(ref, list):
                for child_id in ref:
                    if isinstance(child_id, str):
                        self.walk(child_id)

    def _emit_text(self, component: dict[str, Any]) -> None:
        text = _as_text(_resolve(component.get("text"), self._data_model)).strip()
        if not text:
            return
        variant = component.get("variant")
        kind = "heading" if isinstance(variant, str) and variant.lower() in _HEADING_VARIANTS else "body"
        self.blocks.append((kind, text))

    def _emit_image(self, component: dict[str, Any]) -> None:
        url = _as_text(_resolve(component.get("url"), self._data_model)).strip()
        if not url:
            return
        # An embed has ONE image slot; later images become links so they
        # are still reachable rather than dropped.
        if self.image_url is None:
            self.image_url = url
            return
        description = _as_text(_resolve(component.get("description"), self._data_model)).strip()
        self.blocks.append(("body", f"[{description or 'image'}]({url})"))

    def _emit_interactive(self, name: Any, component: dict[str, Any]) -> None:
        label = ""
        for prop in ("label", "text"):
            label = _as_text(_resolve(component.get(prop), self._data_model)).strip()
            if label:
                break
        self.blocks.append(("body", f"`[{name}{': ' + label if label else ''}]`"))

    def _emit_tabs(self, component: dict[str, Any]) -> None:
        """`Tabs` has no Discord analogue — surface each tab title as a heading."""
        tabs = _resolve(component.get("tabs"), self._data_model)
        if not isinstance(tabs, list):
            return
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            title = _as_text(_resolve(tab.get("title") or tab.get("label"), self._data_model)).strip()
            if title:
                self.blocks.append(("heading", title))
            child = tab.get("child") or tab.get("content")
            if isinstance(child, str):
                self.walk(child)


def _root_id(components: dict[str, dict[str, Any]]) -> str | None:
    """The A2UI tree root, per spec `id == "root"`, with a tolerant fallback."""
    if "root" in components:
        return "root"
    return next(iter(components), None)


def surface_to_embed(payload: Any) -> dict[str, Any] | None:
    """Project an A2UI surface payload into a Discord embed dict.

    Returns None when the payload has no projectable components — callers
    fall back to `surface_to_text`.

    Layout: the first heading becomes the embed title, body text before any
    later heading becomes the description, and each subsequent heading opens
    a field whose value is the body text under it.
    """
    parsed = parse_surface(payload)
    if parsed is None:
        return None
    components, data_model = parsed

    root = _root_id(components)
    if root is None:
        return None

    walker = _Walker(components, data_model)
    walker.walk(root)
    if not walker.blocks and walker.image_url is None:
        return None

    title: str | None = None
    description_parts: list[str] = []
    fields: list[dict[str, Any]] = []
    current_field: dict[str, list[str]] | None = None

    for kind, text in walker.blocks:
        if kind == "divider":
            continue
        if kind == "heading":
            if title is None:
                title = text
                continue
            if current_field is not None:
                fields.append(_finish_field(current_field))
            current_field = {"name": [text], "value": []}
            continue
        # body
        if current_field is not None:
            current_field["value"].append(text)
        else:
            description_parts.append(text)

    if current_field is not None:
        fields.append(_finish_field(current_field))

    if walker.has_interactive:
        description_parts.append(_INTERACTIVE_NOTE)

    embed: dict[str, Any] = {"color": EMBED_COLOR}
    if title:
        embed["title"] = _truncate(title, EMBED_TITLE_LIMIT)
    if description_parts:
        embed["description"] = _truncate("\n".join(description_parts), EMBED_DESCRIPTION_LIMIT)
    if fields:
        embed["fields"] = fields[:EMBED_FIELD_LIMIT]
        if len(fields) > EMBED_FIELD_LIMIT:
            logger.info(
                "a2ui→discord: %d fields exceeded the %d-field embed limit; truncated",
                len(fields),
                EMBED_FIELD_LIMIT,
            )
    if walker.image_url:
        embed["image"] = {"url": walker.image_url}

    # Structure-only surfaces (a lone Divider, empty containers) produce
    # blocks but no content. An embed carrying just a colour renders as an
    # empty box, so hand back None and let the caller degrade to text.
    if not any(key in embed for key in ("title", "description", "fields", "image")):
        return None

    return _enforce_total_budget(embed)


def _finish_field(field: dict[str, list[str]]) -> dict[str, Any]:
    """Close an accumulating field, clipping name and value to Discord limits."""
    name = _truncate(" ".join(field["name"]).strip() or "​", EMBED_FIELD_NAME_LIMIT)
    # A field value may not be empty — U+200B keeps the heading visible.
    value = _truncate("\n".join(v for v in field["value"] if v).strip() or "​", EMBED_FIELD_VALUE_LIMIT)
    return {"name": name, "value": value, "inline": False}


def _embed_size(embed: dict[str, Any]) -> int:
    """Total characters Discord counts against the 6000-per-embed budget."""
    total = len(str(embed.get("title", ""))) + len(str(embed.get("description", "")))
    for field in embed.get("fields", []):
        total += len(str(field.get("name", ""))) + len(str(field.get("value", "")))
    footer = embed.get("footer")
    if isinstance(footer, dict):
        total += len(str(footer.get("text", "")))
    return total


def _enforce_total_budget(embed: dict[str, Any]) -> dict[str, Any]:
    """Drop trailing fields until the embed fits Discord's 6000-char budget.

    Dropping is announced in a footer — a silently shortened surface is the
    kind of quiet data loss CLAUDE.md #8 exists to prevent.
    """
    if _embed_size(embed) <= EMBED_TOTAL_LIMIT:
        return embed

    fields = list(embed.get("fields", []))
    dropped = 0
    while fields and _embed_size({**embed, "fields": fields}) > EMBED_TOTAL_LIMIT:
        fields.pop()
        dropped += 1

    embed["fields"] = fields
    if dropped:
        embed["footer"] = {
            "text": _truncate(
                f"{dropped} more section{'s' if dropped != 1 else ''} — open the workbench to see everything.",
                EMBED_FOOTER_LIMIT,
            )
        }

    # Footer text counts too; description is the last thing we can shrink.
    if _embed_size(embed) > EMBED_TOTAL_LIMIT and "description" in embed:
        overflow = _embed_size(embed) - EMBED_TOTAL_LIMIT
        embed["description"] = _truncate(embed["description"], max(1, len(embed["description"]) - overflow))

    return embed


def surface_to_text(payload: Any) -> str:
    """Flatten an A2UI surface to plain text — the always-available fallback.

    Used when the embed projection finds nothing to render, so a surface is
    never silently dropped.
    """
    parsed = parse_surface(payload)
    if parsed is None:
        return "\n[interactive content — open the workbench to view it]\n"

    components, data_model = parsed
    root = _root_id(components)
    if root is None:
        return "\n[interactive content — open the workbench to view it]\n"

    walker = _Walker(components, data_model)
    walker.walk(root)

    lines: list[str] = []
    for kind, text in walker.blocks:
        if kind == "heading":
            lines.append(f"**{text}**")
        elif kind == "body":
            lines.append(text)
    if walker.image_url:
        lines.append(walker.image_url)

    if not lines:
        return "\n[interactive content — open the workbench to view it]\n"
    return "\n" + "\n".join(lines) + "\n"


__all__ = [
    "EMBED_COLOR",
    "EMBED_FIELD_LIMIT",
    "EMBED_TOTAL_LIMIT",
    "parse_surface",
    "surface_to_embed",
    "surface_to_text",
]
