"""A2UI → Discord embed projection tests (v6.21.0 M2).

The load-bearing property is **degradation**: a surface may be projected
imperfectly, but it must never silently vanish. Discord rejects an entire
API call when any embed limit is exceeded, so the limit tests are about
delivery, not tidiness.

Surface payloads here mirror the real wire shape produced by
`adk/a2ui_result_render.py` — flat components with id refs, root id
"root", and `{"path": "/key"}` data-model bindings.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from channels._a2ui_discord import (
    EMBED_FIELD_LIMIT,
    EMBED_FIELD_NAME_LIMIT,
    EMBED_FIELD_VALUE_LIMIT,
    EMBED_TITLE_LIMIT,
    EMBED_TOTAL_LIMIT,
    _embed_size,
    parse_surface,
    surface_to_embed,
    surface_to_text,
)


def _surface(components: list[dict[str, Any]], data_model: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a surface payload in the shape the emitter really produces."""
    messages: list[dict[str, Any]] = [
        {"version": "v0.9", "createSurface": {"surfaceId": "workspace", "catalogId": "basic"}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "workspace", "components": components}},
    ]
    if data_model is not None:
        messages.append({"version": "v0.9", "updateDataModel": {"surfaceId": "workspace", "value": data_model}})
    return {"surfaceId": "workspace", "messages": messages, "toolName": "t", "sourceId": "s"}


class TestParsing:
    def test_extracts_components_and_data_model(self) -> None:
        parsed = parse_surface(_surface([{"id": "root", "component": "Text", "text": "hi"}], {"k": "v"}))

        assert parsed is not None
        components, data_model = parsed
        assert "root" in components
        assert data_model == {"k": "v"}

    @pytest.mark.parametrize(
        "payload",
        [None, {}, "string", 42, {"messages": "not a list"}, {"messages": []}],
    )
    def test_unparseable_payloads_return_none(self, payload: Any) -> None:
        assert parse_surface(payload) is None

    def test_later_component_updates_merge(self) -> None:
        """Progressive fill: extract → extract → compare each push components."""
        payload = _surface([{"id": "root", "component": "Column", "children": ["a"]}])
        payload["messages"].append(
            {
                "version": "v0.9",
                "updateComponents": {
                    "surfaceId": "workspace",
                    "components": [{"id": "a", "component": "Text", "text": "late"}],
                },
            }
        )

        embed = surface_to_embed(payload)

        assert embed is not None
        assert "late" in embed["description"]


class TestEmbedProjection:
    def test_first_heading_becomes_title(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h", "b"]},
                    {"id": "h", "component": "Text", "text": "Contract comparison", "variant": "h2"},
                    {"id": "b", "component": "Text", "text": "Three clauses differ."},
                ]
            )
        )

        assert embed is not None
        assert embed["title"] == "Contract comparison"
        assert embed["description"] == "Three clauses differ."

    def test_later_headings_become_fields(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h1", "h2", "b2", "h3", "b3"]},
                    {"id": "h1", "component": "Text", "text": "Title", "variant": "h1"},
                    {"id": "h2", "component": "Text", "text": "Clause 4", "variant": "h3"},
                    {"id": "b2", "component": "Text", "text": "Indexation differs"},
                    {"id": "h3", "component": "Text", "text": "Clause 9", "variant": "h3"},
                    {"id": "b3", "component": "Text", "text": "Term length differs"},
                ]
            )
        )

        assert embed is not None
        names = [f["name"] for f in embed["fields"]]
        assert names == ["Clause 4", "Clause 9"]
        assert embed["fields"][0]["value"] == "Indexation differs"

    def test_card_and_list_containers_are_traversed(self) -> None:
        """Basic has no Table — tables are Columns of Rows; all must flatten."""
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["card"]},
                    {"id": "card", "component": "Card", "child": "list"},
                    {"id": "list", "component": "List", "children": ["r1", "r2"]},
                    {"id": "r1", "component": "Text", "text": "row one"},
                    {"id": "r2", "component": "Text", "text": "row two"},
                ]
            )
        )

        assert embed is not None
        assert "row one" in embed["description"]
        assert "row two" in embed["description"]

    def test_data_model_bindings_are_resolved(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["t"]},
                    {"id": "t", "component": "Text", "text": {"path": "/summary"}},
                ],
                {"summary": "bound value"},
            )
        )

        assert embed is not None
        assert "bound value" in embed["description"]

    def test_nested_and_missing_bindings(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["a", "b"]},
                    {"id": "a", "component": "Text", "text": {"path": "/deep/nested"}},
                    {"id": "b", "component": "Text", "text": {"path": "/does/not/exist"}},
                ],
                {"deep": {"nested": "found it"}},
            )
        )

        assert embed is not None
        assert "found it" in embed["description"]

    def test_image_fills_the_single_image_slot(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["i"]},
                    {"id": "i", "component": "Image", "url": "https://example.com/a.png"},
                ]
            )
        )

        assert embed is not None
        assert embed["image"] == {"url": "https://example.com/a.png"}

    def test_second_image_becomes_a_link_not_a_drop(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["i1", "i2"]},
                    {"id": "i1", "component": "Image", "url": "https://example.com/1.png"},
                    {
                        "id": "i2",
                        "component": "Image",
                        "url": "https://example.com/2.png",
                        "description": "second",
                    },
                ]
            )
        )

        assert embed is not None
        assert embed["image"]["url"].endswith("1.png")
        assert "2.png" in embed["description"]

    def test_empty_surface_returns_none(self) -> None:
        assert surface_to_embed(_surface([{"id": "root", "component": "Divider"}])) is None

    def test_missing_root_falls_back_to_first_component(self) -> None:
        embed = surface_to_embed(_surface([{"id": "x", "component": "Text", "text": "no root id"}]))

        assert embed is not None
        assert "no root id" in embed["description"]


class TestInteractiveComponents:
    def test_interactive_components_are_announced_not_dropped(self) -> None:
        """Read-only on channels — but the user must learn the control exists."""
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["btn"]},
                    {"id": "btn", "component": "Button", "child": "lbl", "action": "start_compare"},
                    {"id": "lbl", "component": "Text", "text": "Compare"},
                ]
            )
        )

        assert embed is not None
        assert "Button" in embed["description"]
        assert "workbench" in embed["description"].lower()

    def test_choice_picker_label_is_surfaced(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["cp"]},
                    {"id": "cp", "component": "ChoicePicker", "label": "Severity", "options": ["high"]},
                ]
            )
        )

        assert embed is not None
        assert "Severity" in embed["description"]


class TestDiscordLimits:
    def test_field_count_is_capped(self) -> None:
        components: list[dict[str, Any]] = [
            {"id": "root", "component": "Column", "children": [f"h{i}" for i in range(40)]}
        ]
        components += [{"id": f"h{i}", "component": "Text", "text": f"Section {i}", "variant": "h3"} for i in range(40)]

        embed = surface_to_embed(_surface(components))

        assert embed is not None
        assert len(embed["fields"]) <= EMBED_FIELD_LIMIT

    def test_title_is_clipped(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h"]},
                    {"id": "h", "component": "Text", "text": "T" * 500, "variant": "h1"},
                ]
            )
        )

        assert embed is not None
        assert len(embed["title"]) <= EMBED_TITLE_LIMIT

    def test_field_value_is_clipped(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h1", "h2", "b"]},
                    {"id": "h1", "component": "Text", "text": "Title", "variant": "h1"},
                    {"id": "h2", "component": "Text", "text": "Section", "variant": "h3"},
                    {"id": "b", "component": "Text", "text": "V" * 3000},
                ]
            )
        )

        assert embed is not None
        assert len(embed["fields"][0]["value"]) <= EMBED_FIELD_VALUE_LIMIT
        assert len(embed["fields"][0]["name"]) <= EMBED_FIELD_NAME_LIMIT

    def test_total_budget_is_enforced(self) -> None:
        """Discord rejects the whole call over 6000 chars — delivery, not tidiness."""
        components: list[dict[str, Any]] = [
            {"id": "root", "component": "Column", "children": [x for i in range(20) for x in (f"h{i}", f"b{i}")]}
        ]
        for i in range(20):
            components.append({"id": f"h{i}", "component": "Text", "text": f"Section {i}", "variant": "h3"})
            components.append({"id": f"b{i}", "component": "Text", "text": "L" * 900})

        embed = surface_to_embed(_surface(components))

        assert embed is not None
        assert _embed_size(embed) <= EMBED_TOTAL_LIMIT

    def test_dropped_sections_are_announced(self) -> None:
        """Silent truncation reads as 'that was everything' — it wasn't."""
        components: list[dict[str, Any]] = [
            {"id": "root", "component": "Column", "children": [x for i in range(20) for x in (f"h{i}", f"b{i}")]}
        ]
        for i in range(20):
            components.append({"id": f"h{i}", "component": "Text", "text": f"Section {i}", "variant": "h3"})
            components.append({"id": f"b{i}", "component": "Text", "text": "L" * 900})

        embed = surface_to_embed(_surface(components))

        assert embed is not None
        assert "footer" in embed
        assert "workbench" in embed["footer"]["text"].lower()

    def test_field_value_is_never_empty(self) -> None:
        """Discord rejects an empty field value outright."""
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h1", "h2"]},
                    {"id": "h1", "component": "Text", "text": "Title", "variant": "h1"},
                    {"id": "h2", "component": "Text", "text": "Empty section", "variant": "h3"},
                ]
            )
        )

        assert embed is not None
        assert embed["fields"][0]["value"]


class TestRobustness:
    def test_cyclic_children_terminate(self) -> None:
        """`children` are id refs, so a malformed surface can cycle."""
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["a"]},
                    {"id": "a", "component": "Column", "children": ["b"]},
                    {"id": "b", "component": "Column", "children": ["a", "t"]},
                    {"id": "t", "component": "Text", "text": "reached"},
                ]
            )
        )

        assert embed is not None
        assert "reached" in embed["description"]

    def test_dangling_child_ref_is_survivable(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["missing", "t"]},
                    {"id": "t", "component": "Text", "text": "still here"},
                ]
            )
        )

        assert embed is not None
        assert "still here" in embed["description"]

    def test_non_string_text_values_render(self) -> None:
        embed = surface_to_embed(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["a", "b", "c"]},
                    {"id": "a", "component": "Text", "text": 42},
                    {"id": "b", "component": "Text", "text": True},
                    {"id": "c", "component": "Text", "text": ["x", "y"]},
                ]
            )
        )

        assert embed is not None
        assert "42" in embed["description"]


class TestTextFallback:
    def test_text_fallback_includes_content(self) -> None:
        text = surface_to_text(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h", "b"]},
                    {"id": "h", "component": "Text", "text": "Heading", "variant": "h2"},
                    {"id": "b", "component": "Text", "text": "Body copy"},
                ]
            )
        )

        assert "Heading" in text
        assert "Body copy" in text

    @pytest.mark.parametrize("payload", [None, {}, {"messages": []}, "nonsense"])
    def test_text_fallback_never_empty(self, payload: Any) -> None:
        assert surface_to_text(payload).strip()


class TestDiscordSinkIntegration:
    """`_DiscordSink.render_surface` — native render, with honest failure."""

    def _sink(self) -> tuple[Any, Any]:
        from channels.discord import DiscordChannel, _DiscordSink

        adapter = DiscordChannel(public_key_hex="aa" * 32, token="t")
        channel = MagicMock()
        channel.send = AsyncMock()
        sink = _DiscordSink(adapter, channel)
        return sink, channel

    @pytest.mark.asyncio
    async def test_embed_is_sent_and_text_untouched(self) -> None:
        sink, channel = self._sink()

        result = await sink.render_surface(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h"]},
                    {"id": "h", "component": "Text", "text": "Rendered", "variant": "h2"},
                ]
            )
        )

        # None → rendered natively, reply text left alone.
        assert result is None
        channel.send.assert_awaited_once()
        assert "embed" in channel.send.await_args.kwargs

    @pytest.mark.asyncio
    async def test_unprojectable_surface_degrades_to_text(self) -> None:
        sink, channel = self._sink()

        result = await sink.render_surface({"messages": []})

        assert result is not None and result.strip()
        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_degrades_to_text(self) -> None:
        """A rejected embed must not lose the surface."""
        sink, channel = self._sink()
        channel.send = AsyncMock(side_effect=RuntimeError("400 Bad Request"))

        result = await sink.render_surface(
            _surface(
                [
                    {"id": "root", "component": "Column", "children": ["h"]},
                    {"id": "h", "component": "Text", "text": "Fallback me", "variant": "h2"},
                ]
            )
        )

        assert result is not None
        assert "Fallback me" in result
