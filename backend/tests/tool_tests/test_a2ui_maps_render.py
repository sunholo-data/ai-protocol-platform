"""Tests for adk/a2ui_maps_render.py — Maps Grounding Lite attribution rendering.

The payload fixtures below are REAL responses captured against the deployed dev
key on 2026-08-12, trimmed for size. They matter more than usual: attribution is
a licence condition, and the failure mode this file guards is a silent one — if
Grounding Lite changes its payload shape, the transform returns None, nothing
renders, and the agent happily narrates place names with no attribution on
screen. Nothing else would go red. These tests are the tripwire.
"""

from __future__ import annotations

import json

import pytest


def _mcp(payload: dict) -> dict:
    """Wrap a payload in the MCP CallToolResult envelope the toolset returns."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# --- Real captured shapes ----------------------------------------------------

SEARCH_PLACES = {
    "places": [
        {
            "place": "places/ChIJOzRV_QhvxkcR-qMSDQkZudI",
            "id": "ChIJOzRV_QhvxkcR-qMSDQkZudI",
            "location": {"latitude": 52.0799777, "longitude": 5.0595214},
            "googleMapsLinks": {
                "placeUrl": "https://www.google.com/maps/place//data=!4m2!3m1!1s0x47c66f08fd55343b",
                "directionsUrl": "https://www.google.com/maps/dir//''/data=!4m7",
            },
            "attribution": {
                "title": "SolarCentric B.V. - Google Maps",
                "url": "https://www.google.com/maps/place//data=!4m2!3m1!1s0x47c66f08fd55343b",
            },
        },
        {
            "id": "ChIJsecond",
            "location": {"latitude": 52.08, "longitude": 5.06},
            "attribution": {
                "title": "Lumion Energy - Google Maps",
                "url": "https://www.google.com/maps/place//data=!4m2!3m1!1s0xsecond",
            },
        },
    ],
    "summary": "There are several solar energy companies near Utrecht [0][1].",
}

COMPUTE_ROUTES = {
    "routes": [
        {
            "distanceMeters": 39219,
            "duration": "2325s",
            "attribution": {
                "title": "Utrecht Centraal, Netherlands to Amsterdam Zuid, Netherlands - Google Maps",
                "url": "https://www.google.com/maps/dir/Utrecht%20Centraal/Amsterdam%20Zuid",
            },
        }
    ]
}

# Weather carries attribution at the TOP level, not nested in a list — the exact
# reason the extractor walks the payload instead of encoding per-tool paths.
LOOKUP_WEATHER = {
    "temperature": {"degrees": 17.7, "unit": "CELSIUS"},
    "weatherCondition": {"description": {"text": "Partly sunny"}, "type": "PARTLY_CLOUDY"},
    "returnedLocation": {"latitude": 52.09, "longitude": 5.12},
    "attribution": {
        "title": "Utrecht, Netherlands weather - Google Maps",
        "url": "https://www.google.com/maps/place/Utrecht",
    },
}


class TestAttributionExtraction:
    @pytest.mark.parametrize(
        "payload,expected_count",
        [(SEARCH_PLACES, 2), (COMPUTE_ROUTES, 1), (LOOKUP_WEATHER, 1)],
        ids=["search_places", "compute_routes", "lookup_weather"],
    )
    def test_every_tool_shape_yields_attribution(self, payload, expected_count):
        # The core licence guard: each live shape must produce links. If a
        # payload format changes, this fails rather than silently rendering none.
        from adk.a2ui_maps_render import _collect_attributions

        sources = _collect_attributions(payload)
        assert len(sources) == expected_count
        assert all(s["uri"] for s in sources)
        assert all(s["title"] for s in sources)

    def test_titles_are_passed_through_verbatim(self):
        # Google's attribution guidelines forbid altering the "Google Maps"
        # wording. A future "tidy up the titles" refactor must break here.
        from adk.a2ui_maps_render import _collect_attributions

        titles = [s["title"] for s in _collect_attributions(SEARCH_PLACES)]
        assert titles == ["SolarCentric B.V. - Google Maps", "Lumion Energy - Google Maps"]

    def test_dedupes_repeated_places(self):
        from adk.a2ui_maps_render import _collect_attributions

        dup = {"places": [SEARCH_PLACES["places"][0], SEARCH_PLACES["places"][0]]}
        assert len(_collect_attributions(dup)) == 1

    def test_deeply_nested_attribution_is_found(self):
        from adk.a2ui_maps_render import _collect_attributions

        nested = {"a": {"b": {"c": [{"d": {"attribution": {"title": "T", "url": "u"}}}]}}}
        assert _collect_attributions(nested) == [{"title": "T", "uri": "u"}]

    def test_pathological_depth_terminates(self):
        from adk.a2ui_maps_render import _collect_attributions

        node: dict = {"attribution": {"title": "deep", "url": "u"}}
        for _ in range(200):
            node = {"child": node}
        # Must not recurse without bound; the too-deep attribution is simply
        # not found rather than blowing the stack.
        assert _collect_attributions(node) == []


class TestTransform:
    def test_renders_sources_surface_with_datamodel(self):
        from adk.a2ui_maps_render import MAPS_SOURCES_SURFACE_ID, maps_attribution_to_a2ui

        messages = maps_attribution_to_a2ui(_mcp(SEARCH_PLACES))

        assert messages
        assert all(m["version"] == "v0.9" for m in messages)
        assert any("createSurface" in m for m in messages)
        # SourcesArtefactTab reads dataModel["/"]["sources"] — without this the
        # tab renders its empty state and no link is clickable.
        data = [m for m in messages if "updateDataModel" in m]
        assert len(data) == 1
        sources = data[0]["updateDataModel"]["value"]["sources"]
        assert [s["uri"] for s in sources] == [
            SEARCH_PLACES["places"][0]["attribution"]["url"],
            SEARCH_PLACES["places"][1]["attribution"]["url"],
        ]
        for m in messages:
            for key in ("createSurface", "updateComponents", "updateDataModel"):
                if key in m:
                    assert m[key]["surfaceId"] == MAPS_SOURCES_SURFACE_ID

    def test_does_not_share_the_web_search_sources_surface(self):
        # A turn can use web search AND maps; a shared surfaceId would let one
        # overwrite the other's citations and drop required attribution.
        from adk.a2ui_maps_render import MAPS_SOURCES_SURFACE_ID
        from adk.a2ui_sources_render import WEB_SOURCES_SURFACE_ID

        assert MAPS_SOURCES_SURFACE_ID != WEB_SOURCES_SURFACE_ID

    def test_error_envelope_renders_nothing(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        assert maps_attribution_to_a2ui({"isError": True, "content": []}) is None

    def test_non_json_text_item_renders_nothing(self):
        # The server returns a plain-text notice for a malformed argument.
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        envelope = {"content": [{"type": "text", "text": "Invalid value at 'origin'"}]}
        assert maps_attribution_to_a2ui(envelope) is None

    def test_payload_without_attribution_renders_nothing(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        assert maps_attribution_to_a2ui(_mcp({"places": [{"id": "x"}]})) is None


class _Ctx:
    """Minimal ToolContext stand-in: the transform only touches ``.state``."""

    def __init__(self, state=None):
        self.state = {} if state is None else state


class TestAccumulationAcrossCalls:
    """Regression for a bug a REAL delegated run surfaced (dev, 2026-08-12) and
    no unit test had: one turn asking for a drive time and the weather made two
    Maps calls, both rendering to the same surface, so the second
    updateDataModel replaced the first and the ROUTE's Google Maps link silently
    disappeared while its answer stayed on screen — an unattributed output, with
    nothing going red.
    """

    def test_second_call_keeps_the_first_calls_citation(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        ctx = _Ctx()
        maps_attribution_to_a2ui(_mcp(COMPUTE_ROUTES), ctx)
        messages = maps_attribution_to_a2ui(_mcp(LOOKUP_WEATHER), ctx)

        sources = next(m for m in messages if "updateDataModel" in m)["updateDataModel"]["value"]["sources"]
        titles = [s["title"] for s in sources]
        assert COMPUTE_ROUTES["routes"][0]["attribution"]["title"] in titles, "route citation was dropped"
        assert LOOKUP_WEATHER["attribution"]["title"] in titles
        assert len(sources) == 2

    def test_repeating_the_same_call_does_not_duplicate(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        ctx = _Ctx()
        maps_attribution_to_a2ui(_mcp(SEARCH_PLACES), ctx)
        messages = maps_attribution_to_a2ui(_mcp(SEARCH_PLACES), ctx)

        sources = next(m for m in messages if "updateDataModel" in m)["updateDataModel"]["value"]["sources"]
        assert len(sources) == 2

    def test_accumulation_is_capped_keeping_most_recent(self):
        from adk.a2ui_maps_render import _MAX_ACCUMULATED, maps_attribution_to_a2ui

        ctx = _Ctx()
        for i in range(_MAX_ACCUMULATED + 5):
            payload = {"places": [{"attribution": {"title": f"P{i}", "url": f"https://maps.test/{i}"}}]}
            messages = maps_attribution_to_a2ui(_mcp(payload), ctx)
        sources = next(m for m in messages if "updateDataModel" in m)["updateDataModel"]["value"]["sources"]
        assert len(sources) == _MAX_ACCUMULATED
        # Newest survives, oldest evicted — a citation belongs with the answer
        # still on screen.
        assert sources[-1]["title"] == f"P{_MAX_ACCUMULATED + 4}"
        assert all(s["title"] != "P0" for s in sources)

    def test_sessions_do_not_share_citations(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        a, b = _Ctx(), _Ctx()
        maps_attribution_to_a2ui(_mcp(SEARCH_PLACES), a)
        messages = maps_attribution_to_a2ui(_mcp(COMPUTE_ROUTES), b)

        sources = next(m for m in messages if "updateDataModel" in m)["updateDataModel"]["value"]["sources"]
        assert len(sources) == 1, "one chat's citations leaked into another"

    def test_broken_state_degrades_to_this_calls_sources(self):
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        class Exploding(dict):
            def get(self, *_a, **_k):
                raise RuntimeError("state unavailable")

        messages = maps_attribution_to_a2ui(_mcp(SEARCH_PLACES), _Ctx(Exploding()))
        sources = next(m for m in messages if "updateDataModel" in m)["updateDataModel"]["value"]["sources"]
        # Fail-open: still renders this call's attribution rather than nothing.
        assert len(sources) == 2

    def test_no_context_still_renders(self):
        # The CLI preview path passes no tool_context.
        from adk.a2ui_maps_render import maps_attribution_to_a2ui

        assert maps_attribution_to_a2ui(_mcp(SEARCH_PLACES)) is not None


class TestArtifactAndRegistration:
    def test_artifact_uses_the_sources_tab_kind(self):
        from adk.a2ui_maps_render import _maps_artifact

        meta = _maps_artifact(_mcp(SEARCH_PLACES))
        assert meta["kind"] == "sources"
        assert "2 Google Maps sources" in meta["description"]

    def test_artifact_singular_wording(self):
        from adk.a2ui_maps_render import _maps_artifact

        assert "1 Google Maps source " in _maps_artifact(_mcp(COMPUTE_ROUTES))["description"]

    def test_artifact_none_when_no_attribution(self):
        from adk.a2ui_maps_render import _maps_artifact

        assert _maps_artifact(_mcp({"places": []})) is None

    def test_all_maps_tools_are_offload_exempt(self):
        # A >50K result offloaded to an artifact would strand the render — i.e.
        # silently switch the licence-required attribution off.
        import adk.a2ui_maps_render as maps  # registers the mapping on import
        from adk.a2ui_result_render import is_render_payload_tool

        for tool in maps.MAPS_TOOLS:
            assert is_render_payload_tool(tool), f"{tool} must never be offloaded"

    def test_mapping_is_registered_for_search_places(self):
        import adk.a2ui_maps_render  # noqa: F401
        from adk.a2ui_result_render import render_for_emit

        result = render_for_emit("search_places", _mcp(SEARCH_PLACES))
        assert result is not None
        assert result.artifact["kind"] == "sources"

    def test_agent_module_imports_the_mapping(self):
        # The registration is an import side-effect; if adk/agent.py stops
        # importing it, attribution silently stops rendering in the app.
        import adk.agent  # noqa: F401
        from adk.a2ui_result_render import registered_mapping_names

        assert "maps-attribution" in registered_mapping_names()
