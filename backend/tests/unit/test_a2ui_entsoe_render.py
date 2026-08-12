"""ENTSO-E prices → A2UI mapping (v6.12.0).

Registering this mapping does two jobs at once (see the module docstring):
its own Workspace tab, AND offload exemption — without it the ~1000-row series
blew the 50K threshold, got dumped to an artifact, and the agent narrated a raw
artifact id at the user instead of showing the data.
"""

from __future__ import annotations

from typing import Any

from adk.a2ui_entsoe_render import (
    ENTSOE_SURFACE_ID,
    ENTSOE_TOOL,
    _entsoe_artifact,
    _entsoe_surface,
    entsoe_prices_to_a2ui,
)


def _result(rows: list[dict[str, Any]] | None = None, **over: Any) -> dict[str, Any]:
    base = {
        "rows": rows
        if rows is not None
        else [
            {"ts": "2026-06-01T00:00:00+00:00", "price_eur_mwh": 100.0},
            {"ts": "2026-06-01T01:00:00+00:00", "price_eur_mwh": 50.0},
            {"ts": "2026-06-01T02:00:00+00:00", "price_eur_mwh": -10.0},
        ],
        "row_count": 3,
        "bidding_zone": "DK1",
        "start_date": "2026-06-01",
        "end_date": "2026-06-02",
        "source_uri": "bq://your-entsoe-project.entsoe.data_zones_denmark_hourly?bidding_zone=DK1",
    }
    base.update(over)
    return base


def test_renders_summary_card_with_stats():
    msgs = entsoe_prices_to_a2ui(_result())
    assert msgs is not None
    text = str(msgs)
    assert "DK1 day-ahead prices" in text
    assert "3 hourly prices" in text
    # avg of 100, 50, -10 = 46.67; low -10; high 100
    assert "46.67" in text and "-10.00" in text and "100.00" in text


def test_carries_the_full_series_in_the_data_model():
    """The rich/chartable tab renders from the data model, not the component tree."""
    msgs = entsoe_prices_to_a2ui(_result())
    dm = next(m["updateDataModel"] for m in msgs if "updateDataModel" in m)
    assert dm["surfaceId"] == ENTSOE_SURFACE_ID
    value = dm["value"]
    assert value["rowCount"] == 3
    assert len(value["rows"]) == 3
    assert value["biddingZone"] == "DK1"
    assert value["sourceUri"].startswith("bq://")
    assert value["stats"]["max"] == 100.0


def test_data_model_is_a_declared_series_envelope():
    """v6.12.0 M1: the dataModel is a generic series shape, not ENTSO-E-specific.

    Any dataset-shaped tool (load/solar/wind) can emit this same envelope so a
    future chart tab needs no per-tool knowledge — see docs/design/v6.12.0/
    market-prices-workspace.md ("The shape: a declared series surface").
    """
    msgs = entsoe_prices_to_a2ui(_result())
    dm = next(m["updateDataModel"] for m in msgs if "updateDataModel" in m)
    value = dm["value"]

    assert value["kind"] == "series"
    assert value["title"] == "DK1 day-ahead prices"

    assert value["x"] == {"key": "ts", "label": "Time", "type": "time"}

    assert isinstance(value["y"], list), "y must be a list — the surface later charts load/solar/wind"
    assert value["y"] == [{"key": "price_eur_mwh", "label": "Price", "unit": "EUR/MWh"}]

    # Unchanged fields the citation chip / rich tab still need.
    assert value["rows"] == _result()["rows"]
    assert value["stats"]["avg"] is not None
    assert value["sourceUri"].startswith("bq://")
    assert value["biddingZone"] == "DK1"
    assert value["startDate"] == "2026-06-01"
    assert value["endDate"] == "2026-06-02"
    assert value["rowCount"] == 3


def test_surface_is_created_with_the_basic_catalog():
    msgs = entsoe_prices_to_a2ui(_result())
    create = next(m["createSurface"] for m in msgs if "createSurface" in m)
    assert create["surfaceId"] == ENTSOE_SURFACE_ID


def test_error_payload_renders_nothing():
    assert entsoe_prices_to_a2ui({"error": "permission denied"}) is None


def test_empty_rows_render_nothing():
    assert entsoe_prices_to_a2ui(_result(rows=[])) is None
    assert entsoe_prices_to_a2ui("not a dict") is None


def test_null_prices_do_not_crash_stats():
    """The current, unsettled day carries NULL prices."""
    msgs = entsoe_prices_to_a2ui(_result(rows=[{"ts": "2026-07-17T23:00:00+00:00", "price_eur_mwh": None}]))
    assert msgs is not None
    dm = next(m["updateDataModel"] for m in msgs if "updateDataModel" in m)
    assert dm["value"]["stats"]["avg"] is None


def test_registered_for_the_tool_so_it_gets_a_tab_and_offload_exemption():
    from adk.a2ui_result_render import render_for_emit

    out = render_for_emit(ENTSOE_TOOL, _result())
    assert out is not None, "entsoe must be a registered mapping (tab + offload-exempt)"


# ---------------------------------------------------------------------------
# v6.12.0 M5 — the tool's `needs_input` refusal must render as the CHAT form
# ---------------------------------------------------------------------------


def test_needs_input_refusal_renders_the_shared_chat_form():
    """The recurring trap: the registry gates on TOOL NAME, so a tool that starts
    returning an elicitation envelope renders NOTHING until the shared form
    transform is registered for its name (the success transform declines the
    payload, and a decline stops the search — it does not fall through).
    """
    import asyncio

    from adk.a2ui_result_render import render_for_emit
    from tools.entsoe_query import entsoe_day_ahead_prices

    refusal = asyncio.run(entsoe_day_ahead_prices())
    out = render_for_emit(ENTSOE_TOOL, refusal)

    assert out is not None, "a needs_input refusal must render (else the form never appears)"
    # placement:"chat" routes it to the transcript (ChatPlacementForms), whose
    # mount drives the submit as a full agent turn — not a workbench tab.
    assert out.artifact["placement"] == "chat"
    assert out.artifact["elicitationKind"] == "confirm_with_fields"
    assert out.surface_id.startswith("elicit:run_price_query")
    # The seeded data model is the read-back contract: every field's bound path
    # must exist, or the submitted values never come back.
    dm = next(m["updateDataModel"] for m in out.messages if "updateDataModel" in m)
    assert set(dm["value"]) == {"bidding_zone", "start_date", "end_date"}


def test_success_payload_still_wins_over_the_elicitation_mapping():
    """Registration order matters (first match wins): a normal result must still
    render the prices tab, not the form."""
    from adk.a2ui_result_render import render_for_emit

    out = render_for_emit(ENTSOE_TOOL, _result())

    assert out.surface_id.startswith(f"{ENTSOE_SURFACE_ID}:")
    assert out.artifact["kind"] == "prices"


# ---------------------------------------------------------------------------
# Open Question #4 — tabs are PER-QUERY so DK1 and DK2 compare side by side
# ---------------------------------------------------------------------------


def test_surface_id_is_derived_from_the_query_identity():
    assert _entsoe_surface(_result()) == "entsoe_prices:dk1:2026-06-01:2026-06-02"


def test_same_query_is_stable_so_a_re_run_updates_its_tab_in_place():
    """Not "the latest query wins" and not a duplicate tab: identical query in,
    identical surfaceId out."""
    assert _entsoe_surface(_result()) == _entsoe_surface(_result())
    # A different row payload for the SAME query (e.g. the day settled) must not
    # spawn a second tab — identity is zone + range, not the data.
    assert _entsoe_surface(_result(rows=[{"ts": "x", "price_eur_mwh": 1.0}])) == _entsoe_surface(_result())


def test_different_zone_gets_its_own_tab():
    assert _entsoe_surface(_result(bidding_zone="DK2")) != _entsoe_surface(_result(bidding_zone="DK1"))


def test_different_date_range_gets_its_own_tab():
    assert _entsoe_surface(_result(end_date="2026-06-07")) != _entsoe_surface(_result())
    assert _entsoe_surface(_result(start_date="2026-05-01")) != _entsoe_surface(_result())


def test_surface_id_is_safe_and_preserves_zone_codes_and_dates():
    """Multi-word zone codes must survive; nothing unsafe may reach the id."""
    assert _entsoe_surface(_result(bidding_zone="IT_NORD")) == "entsoe_prices:it_nord:2026-06-01:2026-06-02"

    sid = _entsoe_surface(_result(bidding_zone="DE LU / AT"))
    assert " " not in sid and "/" not in sid
    assert sid == "entsoe_prices:de_lu_at:2026-06-01:2026-06-02"


def test_missing_zone_or_dates_falls_back_without_crashing():
    # No zone → the base id (a date range alone is not a query identity).
    assert _entsoe_surface(_result(bidding_zone="")) == ENTSOE_SURFACE_ID
    assert _entsoe_surface(_result(bidding_zone=None)) == ENTSOE_SURFACE_ID
    # Zone but no dates → still zone-scoped.
    assert _entsoe_surface(_result(start_date="", end_date="")) == "entsoe_prices:dk1"
    assert _entsoe_surface(_result(end_date=None)) == "entsoe_prices:dk1"
    # Junk payloads must never break the emit.
    assert _entsoe_surface("not a dict") == ENTSOE_SURFACE_ID
    assert _entsoe_surface(None) == ENTSOE_SURFACE_ID
    assert _entsoe_surface({}) == ENTSOE_SURFACE_ID


def test_artifact_title_distinguishes_zone_and_range():
    """Per-query tabs are only useful if the Workspace/Home index can tell them
    apart — and the title must be friendly, never a raw id (CLAUDE.md #9)."""
    meta = _entsoe_artifact(_result(end_date="2026-06-07"))
    assert meta["kind"] == "prices", "ChatShell maps the workbench tab on kind"
    assert meta["title"] == "DK1 prices · 2026-06-01 → 2026-06-07"
    assert "entsoe_prices:" not in meta["title"]

    other = _entsoe_artifact(_result(bidding_zone="DK2", end_date="2026-06-07"))
    assert other["title"] != meta["title"]


def test_artifact_title_degrades_to_a_friendly_label():
    assert _entsoe_artifact(_result(start_date="", end_date=""))["title"] == "DK1 prices"
    # Zone-less results share the base surface, but the range still labels them.
    assert _entsoe_artifact(_result(bidding_zone=""))["title"] == "Market prices · 2026-06-01 → 2026-06-02"
    assert _entsoe_artifact(_result(bidding_zone="", start_date="", end_date=""))["title"] == "Market prices"
    assert _entsoe_artifact("not a dict")["title"] == "Market prices"


def test_messages_are_retargeted_to_the_per_query_surface():
    """TRAP 5: the transform builds with the placeholder id; render_for_emit
    retargets every inner surfaceId to the resolved per-query surface. If they
    diverge the client keys the SurfaceModel on the wrong id and no tab appears.
    """
    from adk.a2ui_result_render import render_for_emit

    out = render_for_emit(ENTSOE_TOOL, _result())

    assert out.surface_id == "entsoe_prices:dk1:2026-06-01:2026-06-02"
    for msg in out.messages:
        for key in ("createSurface", "updateComponents", "updateDataModel"):
            if key in msg:
                assert msg[key]["surfaceId"] == out.surface_id


def test_offload_exemption_survives_the_per_query_surface():
    """The ~1000-row series must never be offloaded to an artifact — that strands
    the render and makes the agent narrate a raw artifact id at the user."""
    from adk.a2ui_result_render import is_render_payload_tool

    assert is_render_payload_tool(ENTSOE_TOOL) is True
