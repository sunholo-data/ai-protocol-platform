"""Unit tests for the scoped-BigQuery result→A2UI mapping (v6.23.0 ONE-BQ).

The fixtures below are the REAL wire shapes, captured from Toolbox 1.7.0 through
a live MCP `tools/call` on 2026-08-07 — not shapes invented to match the code.
That matters more here than usual: an ADK `MCPTool` returns the MCP
`CallToolResult` envelope rather than the tool's payload, and a transform written
against the payload shape renders nothing, silently, forever. See
`adk/a2ui_bigquery_render.py`'s module docstring.

These are unit tests. Per `backend/adk/CLAUDE.md`, green here does NOT mean it
renders — that is M3's real-browser / real-stream check.
"""

from __future__ import annotations

import pytest

from adk.a2ui_bigquery_render import (
    BIGQUERY_QUERY_TOOLS,
    _axes,
    _bigquery_artifact,
    _bigquery_surface,
    _rows_from_mcp,
    _stats,
    bigquery_rows_to_a2ui,
)
from adk.callbacks import A2UI_TOOL_ARGS_STATE_KEY


class _Ctx:
    """Minimal tool_context double — the emitter stashes the call args in state."""

    def __init__(self, sql: str = ""):
        self.state = {A2UI_TOOL_ARGS_STATE_KEY: {"sql": sql}}


def marked(sql_body: str = "SELECT 1", title: str = "Monthly base load, Sweden 4") -> _Ctx:
    return _Ctx(f"-- chart: {title}\n{sql_body}")


# --- Captured live from Toolbox 1.7.0, 2026-08-07 ----------------------------

SUCCESS = {
    "content": [
        {"type": "text", "text": '{"year":2026,"month":8,"base_load":28.37}'},
        {"type": "text", "text": '{"year":2026,"month":9,"base_load":37.15}'},
    ]
}

# Note: NOT a JSON object — Toolbox returns a JSON-encoded STRING for this case.
EMPTY = {"content": [{"type": "text", "text": '"The query returned 0 rows."'}]}

ALLOWLIST_REJECTION = {
    "content": [
        {
            "type": "text",
            "text": "query accesses dataset 'your-entsoe-project.entsoe_mirror', which is not in the allowed list",
        }
    ],
    "isError": True,
}


class TestUnwrapping:
    def test_parses_one_row_per_content_item(self):
        """Toolbox emits one `content` item PER ROW, each a JSON string — not one
        JSON array. A transform assuming an array gets zero rows."""
        rows = _rows_from_mcp(SUCCESS)
        assert rows == [
            {"year": 2026, "month": 8, "base_load": 28.37},
            {"year": 2026, "month": 9, "base_load": 37.15},
        ]

    def test_error_envelope_yields_none(self):
        assert _rows_from_mcp(ALLOWLIST_REJECTION) is None

    def test_empty_result_yields_no_rows(self):
        """The empty payload parses to a STRING, so it must fall out as zero rows
        rather than being mistaken for one."""
        assert _rows_from_mcp(EMPTY) == []

    @pytest.mark.parametrize("payload", [None, "text", 42, {}, {"content": "notalist"}])
    def test_unrecognised_shapes_yield_none(self, payload):
        assert _rows_from_mcp(payload) is None


class TestTransform:
    def test_success_renders_the_three_a2ui_messages(self):
        messages = bigquery_rows_to_a2ui(SUCCESS, marked())
        assert messages is not None
        kinds = [next(k for k in m if k != "version") for m in messages]
        assert kinds == ["createSurface", "updateComponents", "updateDataModel"]

    def test_data_model_declares_the_series_envelope(self):
        """The declared envelope is what buys the chart + sortable table + CSV
        from the existing SeriesArtefactTab with zero frontend work. If these keys
        drift, the tab silently renders nothing."""
        messages = bigquery_rows_to_a2ui(SUCCESS, marked())
        value = messages[2]["updateDataModel"]["value"]
        assert value["kind"] == "series"
        # `year` + `month` compose into a synthesised `period` — neither is a
        # measure, and neither alone is a usable x (see TestAxes).
        assert value["x"]["key"] == "period"
        assert [y["key"] for y in value["y"]] == ["base_load"]
        assert value["rowCount"] == 2
        assert len(value["rows"]) == 2

    @pytest.mark.parametrize("payload", [ALLOWLIST_REJECTION, EMPTY, None, {"content": []}])
    def test_non_renderable_payloads_render_nothing(self, payload):
        """Returning None is deliberate, not a swallowed failure: the agent still
        gets the full error text as its tool result and explains it in prose,
        which beats an empty workbench tab (principle #8)."""
        assert bigquery_rows_to_a2ui(payload, marked()) is None

    def test_inner_surface_id_is_the_placeholder_everywhere(self):
        """TRAP 5: `render_for_emit` retargets the inner surfaceId to the resolved
        one, so every message must carry the SAME placeholder — a divergent
        hardcoded id splits the surface and nothing renders."""
        messages = bigquery_rows_to_a2ui(SUCCESS, marked())
        ids = {next(v for k, v in m.items() if k != "version")["surfaceId"] for m in messages}
        assert len(ids) == 1


SCHEMA_LOOKUP = {
    "content": [
        {"type": "text", "text": '{"column_name":"year","data_type":"INT64"}'},
        {"type": "text", "text": '{"column_name":"base_load","data_type":"FLOAT64"}'},
    ]
}
TABLE_LISTING = {
    "content": [
        {"type": "text", "text": '{"table_name":"PPA_sweden_4"}'},
        {"type": "text", "text": '{"table_name":"PPA_germany"}'},
    ]
}

CROSS_TABLE_SWEEP = {
    "content": [
        {
            "type": "text",
            "text": '{"table_name":"PPA_sweden_4","column_name":"year","data_type":"INT64"}',
        }
    ]
}


class TestDiscoveryResultsGetNoTab:
    """REGRESSION (2026-08-07, caught by the M3 real-stream check).

    Collapsing the toolset to 2 tools means INFORMATION_SCHEMA lookups run through
    the SAME tool as real queries. Before this filter, one question produced EIGHT
    workbench tabs, most of them "column_name, data_type · 27 rows" — burying the
    answer among its own scaffolding. Unit tests were green throughout.
    """

    @pytest.mark.parametrize("payload", [SCHEMA_LOOKUP, TABLE_LISTING, CROSS_TABLE_SWEEP])
    def test_information_schema_results_render_no_surface(self, payload):
        """CROSS_TABLE_SWEEP is the shape that defeated the first (exact-signature)
        version of this filter — the agent wrote it unprompted on a live run."""
        assert bigquery_rows_to_a2ui(payload, marked()) is None

    def test_a_real_answer_still_renders(self):
        """The filter must be narrow — an answer that happens to have few columns
        is not discovery."""
        assert bigquery_rows_to_a2ui(SUCCESS, marked()) is not None

    def test_an_answer_containing_a_named_column_still_renders(self):
        """`table_name` alongside real data is an ANSWER (e.g. row counts per
        table), not a bare listing — the signature match is on the exact column
        SET, not on the presence of a name column."""
        payload = {"content": [{"type": "text", "text": '{"table_name":"PPA_sweden_4","row_count":120}'}]}
        assert bigquery_rows_to_a2ui(payload, marked()) is not None


class TestAxes:
    """Each case here is a chart that rendered WRONGLY in the browser on
    2026-08-07. Unit tests were green for all of them — the axis derivation was
    self-consistent and produced nonsense."""

    def test_calendar_parts_are_never_measures(self):
        """`month` was drawn as a 1..12 sawtooth series next to the prices."""
        rows = [{"year": 2026, "month": 8, "base_load": 28.4}, {"year": 2026, "month": 9, "base_load": 37.2}]
        _x, y, _rows = _axes(rows)
        assert [c["key"] for c in y] == ["base_load"]

    def test_x_is_never_a_constant_column(self):
        """`year` was picked as x for a single year, stacking every point on one
        tick — the chart was a vertical line."""
        rows = [{"year": 2026, "month": m, "base_load": float(m)} for m in range(8, 13)]
        x, _y, _rows = _axes(rows)
        assert x["key"] != "year"
        assert len({r[x["key"]] for r in _rows}) == 5

    def test_multiple_calendar_parts_compose_into_a_sortable_period(self):
        """A multi-year monthly series has no single good x: `month` collapses the
        years, `year` collapses the months. Zero-padded so it sorts as a string."""
        rows = [
            {"year": 2026, "month": 9, "v": 1.0},
            {"year": 2026, "month": 10, "v": 2.0},
            {"year": 2027, "month": 1, "v": 3.0},
        ]
        x, y, out = _axes(rows)
        assert x["key"] == "period"
        assert [r["period"] for r in out] == ["2026-09", "2026-10", "2027-01"]
        assert sorted(r["period"] for r in out) == [r["period"] for r in out]
        assert [c["key"] for c in y] == ["v"]

    def test_small_integers_are_not_typed_as_time(self):
        """`month` holding 1..12 was typed `time`, so the tab rendered those
        integers as dates in 2000/2001."""
        rows = [{"month": 1, "v": 1.0}, {"month": 2, "v": 2.0}]
        x, _y, _rows = _axes(rows)
        assert x["key"] == "month"
        assert x["type"] == "category"

    def test_real_timestamps_are_typed_as_time(self):
        rows = [{"ts": "2026-01-01T00:00:00", "v": 1.0}, {"ts": "2026-01-02T00:00:00", "v": 2.0}]
        x, _y, _rows = _axes(rows)
        assert x["type"] == "time"

    def test_text_dimension_becomes_x(self):
        rows = [{"market": "sweden_4", "price": 31.2}, {"market": "germany", "price": 44.0}]
        x, y, _rows = _axes(rows)
        assert x["key"] == "market" and x["type"] == "category"
        assert [c["key"] for c in y] == ["price"]

    def test_booleans_are_not_treated_as_numeric(self):
        """bool subclasses int — charting an is_active flag would be nonsense."""
        rows = [{"name": "a", "is_active": True, "n": 3}, {"name": "b", "is_active": False, "n": 4}]
        _x, y, _rows = _axes(rows)
        assert [c["key"] for c in y] == ["n"]


ONE_ROW_PROBE = {"content": [{"type": "text", "text": '{"min_date":"2026-01-01","row_count":137}'}]}
NO_MEASURE = {
    "content": [
        {"type": "text", "text": '{"market":"sweden_4","product":"ppa"}'},
        {"type": "text", "text": '{"market":"germany","product":"ppa"}'},
    ]
}
CONSTANT_X = {
    "content": [
        {"type": "text", "text": '{"market":"sweden_4","price":1.0}'},
        {"type": "text", "text": '{"market":"sweden_4","price":2.0}'},
    ]
}


class TestTheAgentDecidesWhatIsShown:
    """Mark's call on 2026-08-07, after seeing SEVEN tabs for one question:
    *"we would prefer not having tabs for each query, just ones we think should
    visualise for the user — the content is available in the Activity."*

    A heuristic cannot know which of eleven queries was the answer, so the agent
    marks it, in the only channel Toolbox leaves open: a comment in the SQL.
    """

    @pytest.mark.parametrize("payload", [SUCCESS, ONE_ROW_PROBE, NO_MEASURE, CONSTANT_X])
    def test_unmarked_results_get_no_tab(self, payload):
        """Including a perfectly plottable one — silence is the default."""
        assert bigquery_rows_to_a2ui(payload, _Ctx("SELECT 1")) is None

    def test_missing_context_is_treated_as_unmarked(self):
        """Fail closed: no context (or an emitter predating the args stash) must
        not resurrect a tab per query."""
        assert bigquery_rows_to_a2ui(SUCCESS, None) is None

    def test_marked_result_gets_its_tab(self):
        assert bigquery_rows_to_a2ui(SUCCESS, marked()) is not None

    def test_marker_title_becomes_the_tab_title(self):
        """Fixes tabs reading "product_code, f0_, f1_ +1" — derived column names
        are not a description a human can navigate by."""
        meta = _bigquery_artifact(SUCCESS, marked(title="Monthly base load, Sweden 4"))
        assert meta["title"] == "Monthly base load, Sweden 4"

    def test_marker_is_case_and_space_tolerant(self):
        ctx = _Ctx("--Chart:  Spot prices\nSELECT 1")
        assert bigquery_rows_to_a2ui(SUCCESS, ctx) is not None
        assert _bigquery_artifact(SUCCESS, ctx)["title"] == "Spot prices"

    @pytest.mark.parametrize("payload", [ONE_ROW_PROBE, NO_MEASURE, CONSTANT_X])
    def test_marked_but_unplottable_renders_as_a_table_not_a_chart(self, payload):
        """The agent asked for it, so it is shown — but routed to the generic
        mount rather than drawing one of the meaningless charts from the
        screenshots (a vertical line, a 1..12 sawtooth)."""
        assert bigquery_rows_to_a2ui(payload, marked()) is not None
        assert _bigquery_artifact(payload, marked())["kind"] == "table"

    def test_marked_and_plottable_routes_to_the_chart_tab(self):
        assert _bigquery_artifact(SUCCESS, marked())["kind"] == "prices"


class TestStatTilesAndAxisFormatting:
    """Both visible in the browser on 2026-08-07 with the chart otherwise correct."""

    def test_stats_are_emitted_for_the_primary_measure(self):
        """SeriesArtefactTab renders Average/Low/High from `stats` and NEVER
        re-derives them client-side, so omitting the key left all three as "—"."""
        value = bigquery_rows_to_a2ui(SUCCESS, marked())[2]["updateDataModel"]["value"]
        assert value["stats"] == {"avg": pytest.approx(32.76), "min": 28.37, "max": 37.15}

    def test_stats_degrade_to_none_without_a_measure(self):
        rows = [{"a": "x"}, {"b": "y"}]
        assert _stats(rows, []) == {"avg": None, "min": None, "max": None}

    def test_composed_period_is_a_label_not_a_timestamp(self):
        """Typed `time`, the frontend parsed "2026-08" into a full timestamp: the
        axis read "2026-08-01 00:00:00 UTC" and the tooltip said "00:00"."""
        x, _y, _rows = _axes([{"year": 2026, "month": 8, "v": 1.0}, {"year": 2026, "month": 9, "v": 2.0}])
        assert x["key"] == "period"
        assert x["type"] == "category"


class TestSurfaceIdentity:
    def test_same_result_yields_the_same_surface(self):
        """Re-running an identical query updates that tab in place instead of
        opening a duplicate."""
        assert _bigquery_surface(SUCCESS) == _bigquery_surface(SUCCESS)

    def test_different_results_yield_different_surfaces(self):
        """A new question gets its own tab, so two answers sit side by side and
        the new one auto-focuses."""
        other = {"content": [{"type": "text", "text": '{"year":2027,"month":1,"base_load":50.0}'}]}
        assert _bigquery_surface(SUCCESS) != _bigquery_surface(other)

    def test_unrenderable_payload_degrades_to_the_base_id(self):
        assert _bigquery_surface(ALLOWLIST_REJECTION) == "bigquery_result"


class TestArtifactMeta:
    def test_numeric_result_routes_to_the_series_tab(self):
        assert _bigquery_artifact(SUCCESS)["kind"] == "prices"

    def test_non_numeric_result_falls_back_to_the_generic_mount(self):
        text_only = {"content": [{"type": "text", "text": '{"table_id":"PPA_sweden_4"}'}]}
        assert _bigquery_artifact(text_only)["kind"] == "table"

    def test_title_is_friendly_and_distinguishing(self):
        """Tabs are per-result, so several are open at once — CLAUDE.md #9 forbids
        a raw id and a constant 'Query result' would be useless in the index."""
        title = _bigquery_artifact(SUCCESS)["title"]
        assert "year" in title and "2 rows" in title
        assert "bigquery_result" not in title


class TestRegistration:
    def test_query_tools_are_registered_and_offload_exempt(self):
        """Declaring `tool_names` is what marks these render-payload, so a wide
        result is never swapped for an artifact pointer (TRAP 4) and the result
        stays client-visible for lower-trust sessions."""
        from adk.a2ui_result_render import is_render_payload_tool

        for tool in BIGQUERY_QUERY_TOOLS:
            assert is_render_payload_tool(tool), f"{tool} must be offload-exempt"

    def test_only_the_two_query_tools_exist(self):
        """The toolset is deliberately just the executor.

        Toolbox's dedicated `bigquery-list-table-ids` / `bigquery-get-table-info`
        were shipped in the first cut and removed: they resolve `dataset` against
        the SOURCE's project (the BILLING project), so they can never see a
        dataset in the customer's project — with either the bare or the qualified
        name. Discovery is INFORMATION_SCHEMA through the executor instead, which
        stays allowlist-gated. Pinned here so nobody re-adds them.
        """
        from adk.a2ui_result_render import is_render_payload_tool

        assert BIGQUERY_QUERY_TOOLS == ["bq_market_query", "bq_analysis_query"]
        for tool in ["bq_market_list_tables", "bq_market_table_schema"]:
            assert not is_render_payload_tool(tool)


# ── ONE-BQ-SHAPES: the analyst's own query shapes ────────────────────────────
# Every fixture below mirrors a query ONE's analyst actually keeps in BigQuery
# Studio (read via the Dataform API on 2026-08-11), with values from a real run
# against `your-entsoe-project.market_prices`. Each one broke the generic render in a
# different way before ONE-BQ-SHAPES.


def envelope(rows: list[dict]) -> dict:
    """The MCP wire shape: one `content` item per row, each a JSON STRING."""
    import json as _json

    return {"content": [{"type": "text", "text": _json.dumps(r)} for r in rows]}


def model(result: dict, ctx=None) -> dict:
    messages = bigquery_rows_to_a2ui(result, ctx or marked())
    assert messages is not None, "expected a rendered surface"
    return messages[2]["updateDataModel"]["value"]


# Her `captured_prices_market`, Poland — nine measures, three technologies x
# three scenario cases. Baseload/wind values are the real 2027-28 numbers.
CAPTURED_PRICES = [
    {
        "year": 2027,
        "baseload_price_basecase": 100.92,
        "baseload_price_lowcase": 88.14,
        "baseload_price_highcase": 115.30,
        "wind_on_captured_price_basecase": 94.691,
        "wind_on_captured_price_lowcase": 82.10,
        "wind_on_captured_price_highcase": 108.44,
        "solar_captured_price_basecase": 79.22,
        "solar_captured_price_lowcase": 68.90,
        "solar_captured_price_highcase": 91.05,
    },
    {
        "year": 2028,
        "baseload_price_basecase": 87.281,
        "baseload_price_lowcase": 76.02,
        "baseload_price_highcase": 99.71,
        "wind_on_captured_price_basecase": 80.115,
        "wind_on_captured_price_lowcase": 69.44,
        "wind_on_captured_price_highcase": 92.03,
        "solar_captured_price_basecase": 65.88,
        "solar_captured_price_lowcase": 57.31,
        "solar_captured_price_highcase": 75.60,
    },
]

# Her `captured_rates_entsoe`, Spain — prices (~70-80) and dimensionless rates
# (~0.85) in ONE result set.
CAPTURE_RATES = [
    {"year": 2023, "avg_price": 87.11, "pv_captured_price": 71.44, "pv_capture_rate": 0.820},
    {"year": 2024, "avg_price": 63.02, "pv_captured_price": 48.90, "pv_capture_rate": 0.776},
    {"year": 2025, "avg_price": 71.55, "pv_captured_price": 52.13, "pv_capture_rate": 0.729},
]

# Her `year_price_ratio` — a single dimensionless seasonal index around 1.0.
PRICE_RATIO = [
    {"month": 1, "monthly_price_ratio": 1.184},
    {"month": 6, "monthly_price_ratio": 0.792},
    {"month": 12, "monthly_price_ratio": 1.093},
]


class TestScenarioCasesBecomeBands:
    """`_basecase`/`_lowcase`/`_highcase` triples collapse to a line + a band."""

    def test_nine_measures_collapse_to_three_series(self):
        value = model(envelope(CAPTURED_PRICES))
        assert [d["key"] for d in value["y"]] == [
            "baseload_price_basecase",
            "wind_on_captured_price_basecase",
            "solar_captured_price_basecase",
        ]

    def test_the_palette_is_never_exceeded(self):
        # Nine lines from an eight-slot categorical palette meant series 9
        # silently reused slot 1's colour. Three cannot.
        assert len(model(envelope(CAPTURED_PRICES))["y"]) <= 8

    def test_each_series_declares_its_low_high_band(self):
        bands = model(envelope(CAPTURED_PRICES))["bands"]
        assert [b["key"] for b in bands] == [
            "baseload_price_basecase",
            "wind_on_captured_price_basecase",
            "solar_captured_price_basecase",
        ]
        assert bands[1] == {
            "key": "wind_on_captured_price_basecase",
            "lower": "wind_on_captured_price_lowcase",
            "upper": "wind_on_captured_price_highcase",
            "label": "wind_on_captured_price",
        }

    def test_the_legend_label_drops_the_case_suffix(self):
        labels = [d["label"] for d in model(envelope(CAPTURED_PRICES))["y"]]
        assert labels == ["baseload_price", "wind_on_captured_price", "solar_captured_price"]

    def test_band_edges_stay_in_the_rows_for_the_table_and_csv(self):
        # Collapsing is a CHART decision. The user must still be able to read and
        # export the low/high columns.
        assert "wind_on_captured_price_lowcase" in model(envelope(CAPTURED_PRICES))["rows"][0]

    def test_a_one_sided_band_collapses_onto_the_base_line(self):
        rows = [
            {"year": 2027, "p_basecase": 100.0, "p_lowcase": 88.0},
            {"year": 2028, "p_basecase": 87.0, "p_lowcase": 76.0},
        ]
        band = model(envelope(rows))["bands"][0]
        assert band["lower"] == "p_lowcase"
        assert band["upper"] == "p_basecase"

    def test_an_orphan_case_column_stays_an_ordinary_series(self):
        # A lone `_lowcase` with no basecase is not a band — it is just a series.
        rows = [{"year": 2027, "p_lowcase": 88.0}, {"year": 2028, "p_lowcase": 76.0}]
        value = model(envelope(rows))
        assert value["bands"] == []
        assert [d["key"] for d in value["y"]] == ["p_lowcase"]


class TestOneAxisIsKept:
    """Prices and dimensionless ratios never share a y axis."""

    def test_ratios_are_deferred_off_a_price_chart(self):
        value = model(envelope(CAPTURE_RATES))
        assert [d["key"] for d in value["y"]] == ["avg_price", "pv_captured_price"]
        assert [d["key"] for d in value["deferred"]] == ["pv_capture_rate"]

    def test_deferred_columns_are_declared_not_dropped(self):
        # NEVER SILENT (CLAUDE.md #8): the tab has to be able to say what it did
        # not chart, and the value must still be in the row for the table.
        value = model(envelope(CAPTURE_RATES))
        assert value["deferred"], "a dropped series with no declaration is a silent loss"
        assert "pv_capture_rate" in value["rows"][0]

    def test_similar_magnitudes_are_left_alone(self):
        # Two prices differing by ~20% must stay on one axis — splitting them
        # would cost the reader exactly the comparison they asked for.
        value = model(envelope(CAPTURED_PRICES))
        assert value["deferred"] == []


class TestRatioCharts:
    """An all-ratio chart reads as a percentage against 1.0."""

    def test_a_pure_ratio_result_is_formatted_as_percent(self):
        value = model(envelope(PRICE_RATIO))
        assert value["yFormat"] == "percent"
        assert value["reference"]["value"] == 1.0

    def test_a_price_chart_gets_no_percent_format(self):
        assert "yFormat" not in model(envelope(CAPTURED_PRICES))
        assert "reference" not in model(envelope(CAPTURED_PRICES))

    def test_market_profile_is_a_volume_not_a_ratio(self):
        # MarketData names its hourly PRODUCTION column `profile`. Matching on the name
        # alone would format MWh as a percentage.
        rows = [{"hour": h, "profile": 1200.0 + h} for h in range(6)]
        assert "yFormat" not in model(envelope(rows))


class TestUnconventionalResultsAreUnaffected:
    """A query using none of ONE's conventions renders exactly as before."""

    def test_plain_result_declares_empty_shape_fields(self):
        value = model(SUCCESS)
        assert value["bands"] == []
        assert value["deferred"] == []
        assert [d["key"] for d in value["y"]] == ["base_load"]
