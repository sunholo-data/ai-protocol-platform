"""Tests for tools/entsoe_query.py (v6.4.0 ONE-DEMO M2 deferred unblock).

Covers:
  - Zone resolution: zone-accurate table+column, country fallback, unknown zone
  - Happy path: BQ returns rows → typed response with rows + source_uri
  - Empty range: BQ returns no rows → empty rows list, no exception
  - Missing args → a `needs_input` elicitation FORM (no BQ call) — v6.12.0 M5
  - Form submit → values read back off the surface state, then queried
  - Inverted date range → structured error (no BQ call)
  - BQ failure → structured error with `hint`

The real dataset keys time on year/month/day/hour INT columns and stores price
either as a per-zone column (`Day-Ahead Price DK1`) in a wide zone table or as a
single `day_ahead_price` in a `data_<country>` table.

Integration test against the real BQ table is gated on
`ENTSOE_INTEGRATION_TEST=1` env var so it doesn't run in default CI.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_bq_row(year: int, month: int, day: int, hour: int, price: float | None):
    """A fake BQ row keyed like the real query result (year/month/day/hour/price)."""
    data = {"year": year, "month": month, "day": day, "hour": hour, "price": price}
    row = MagicMock()
    row.__getitem__.side_effect = lambda key: data[key]
    return row


@pytest.fixture(autouse=True)
def _configured_entsoe_project(monkeypatch: pytest.MonkeyPatch):
    """Give the tool a data project, the way a deployed env does.

    `_ENTSOE_PROJECT` is read at IMPORT time, so setting the env var here would
    be too late — patch the module attribute. Deliberately a fake project: the
    real one is deployment config and naming it here would put it back into a
    shipped file (TEMPLATE-INVERT).

    The unconfigured path has its own test below.
    """
    from tools import entsoe_query

    monkeypatch.setattr(entsoe_query, "_ENTSOE_PROJECT", "test-entsoe-project")


def test_unconfigured_project_says_so_instead_of_querying(monkeypatch: pytest.MonkeyPatch):
    """No ENTSOE_PROJECT must name the missing knob, not emit `bq://.entsoe.…`.

    Regression guard for TEMPLATE-INVERT M4, where the identity scrub replaced
    the hardcoded project with a placeholder that no pipeline supplied. The
    symptom was a malformed BigQuery reference — or an empty result the agent
    would relay as "no prices found", which is worse.
    """
    import asyncio

    from tools import entsoe_query

    monkeypatch.setattr(entsoe_query, "_ENTSOE_PROJECT", "")

    with patch("google.cloud.bigquery.Client") as client:
        result = asyncio.run(
            entsoe_query.entsoe_day_ahead_prices(bidding_zone="DK1", start_date="2026-06-01", end_date="2026-06-02")
        )

    assert result.get("configured") is False
    assert "ENTSOE_PROJECT" in result["error"]
    client.assert_not_called()


# ---------------------------------------------------------------------------
# Zone resolution (pure)
# ---------------------------------------------------------------------------


def test_resolve_zone_accurate_table_and_column():
    from tools.entsoe_query import _resolve_table

    assert _resolve_table("DK1") == ("data_zones_denmark_hourly", "Day-Ahead Price DK1")
    assert _resolve_table("SE3") == ("data_zones_sweden_hourly", "Day-Ahead Price SE3")
    assert _resolve_table("IT_NORD") == ("data_zones_italy_hourly", "Day-Ahead Price Nord")


def test_resolve_country_fallback_and_case_insensitive():
    from tools.entsoe_query import _resolve_table

    assert _resolve_table("FR") == ("data_france", "day_ahead_price")
    assert _resolve_table("DE_LU") == ("data_germany", "day_ahead_price")
    assert _resolve_table("dk1") == ("data_zones_denmark_hourly", "Day-Ahead Price DK1")  # case-insensitive


def test_resolve_unknown_zone_is_none():
    from tools.entsoe_query import _resolve_table

    assert _resolve_table("ZZ9") is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_rows_with_source_uri_citation():
    from tools.entsoe_query import entsoe_day_ahead_prices

    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = [
        _make_bq_row(2026, 6, 1, 0, 42.5),
        _make_bq_row(2026, 6, 1, 1, 45.0),
    ]

    with patch("google.cloud.bigquery.Client", return_value=fake_client):
        result = await entsoe_day_ahead_prices("DK1", "2026-06-01", "2026-06-02")

    assert "error" not in result
    assert result["row_count"] == 2
    assert result["bidding_zone"] == "DK1"
    assert result["start_date"] == "2026-06-01"
    assert result["rows"][0]["price_eur_mwh"] == 42.5
    # ts is reconstructed from the split INT columns.
    assert result["rows"][0]["ts"] == "2026-06-01T00:00:00+00:00"
    assert result["rows"][1]["ts"] == "2026-06-01T01:00:00+00:00"
    # Source URI is the citation chip target — must reference the RESOLVED zone
    # table (DK1 → the Denmark zone table) and the query parameters.
    from tools import entsoe_query

    assert f"bq://{entsoe_query._ENTSOE_PROJECT}.entsoe.data_zones_denmark_hourly" in result["source_uri"]
    assert "bidding_zone=DK1" in result["source_uri"]
    # The query must read the per-zone column, not a bare price column.
    called_query = fake_client.query.call_args[0][0]
    assert "Day-Ahead Price DK1" in called_query


@pytest.mark.asyncio
async def test_country_zone_uses_country_table_and_column():
    from tools.entsoe_query import entsoe_day_ahead_prices

    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = [_make_bq_row(2026, 6, 1, 0, 60.0)]

    with patch("google.cloud.bigquery.Client", return_value=fake_client):
        result = await entsoe_day_ahead_prices("FR", "2026-06-01", "2026-06-02")

    assert "error" not in result
    assert "data_france" in result["source_uri"]
    called_query = fake_client.query.call_args[0][0]
    assert "day_ahead_price" in called_query and "data_france" in called_query


@pytest.mark.asyncio
async def test_empty_range_returns_empty_rows_not_error():
    from tools.entsoe_query import entsoe_day_ahead_prices

    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = []

    with patch("google.cloud.bigquery.Client", return_value=fake_client):
        result = await entsoe_day_ahead_prices("DK1", "2026-06-01", "2026-06-02")

    assert "error" not in result
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert "bq://" in result["source_uri"]


# ---------------------------------------------------------------------------
# Input validation — no BQ call burned on garbage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_args_returns_needs_input_form_without_bq_call():
    """v6.12.0 M5 — a missing param raises the FORM, not a prose interrogation.

    Replaces the old "missing args → error" assertion: an error string was
    relayed by the agent as yet another clarifying question (observed: 5 turns to
    collect 3 values). The tool now returns the same `needs_input` elicitation
    envelope shape `map_ppa_obligations` returns for `needs_assumptions`.
    """
    from tools.entsoe_query import entsoe_day_ahead_prices

    with patch("google.cloud.bigquery.Client") as mock_bq:
        result = await entsoe_day_ahead_prices("", "2026-06-01", "2026-06-02")

    assert result["needs_input"] is True
    assert result["placement"] == "chat"
    assert "error" not in result
    assert mock_bq.call_count == 0


@pytest.mark.asyncio
async def test_no_args_at_all_asks_for_all_three_fields():
    """The zero-arg call — "can you query bigquery for prices?" — is the whole
    point: the model may call the tool knowing nothing, and gets a form back."""
    from tools.entsoe_query import RUN_PRICE_QUERY_ACTION, entsoe_day_ahead_prices

    with patch("google.cloud.bigquery.Client") as mock_bq:
        result = await entsoe_day_ahead_prices()

    assert mock_bq.call_count == 0
    envelope = result["elicitation"]
    assert envelope["kind"] == "confirm_with_fields"
    assert envelope["action"] == RUN_PRICE_QUERY_ACTION
    fields = {f["name"]: f for f in envelope["fields"]}
    assert set(fields) == {"bidding_zone", "start_date", "end_date"}
    assert all(f["required"] for f in fields.values())
    assert fields["start_date"]["type"] == "date"
    assert fields["end_date"]["type"] == "date"


@pytest.mark.asyncio
async def test_form_offers_the_real_supported_zones_with_friendly_labels():
    """The picker offers the zones the tool actually supports (from the lookup
    maps — not an invented list), each as `CODE — Market name` (CLAUDE.md #9:
    never make a human pick a raw id)."""
    from tools.entsoe_query import _COUNTRY_TABLES, _ZONE_TABLES, entsoe_day_ahead_prices

    result = await entsoe_day_ahead_prices()
    zone_field = next(f for f in result["elicitation"]["fields"] if f["name"] == "bidding_zone")

    assert zone_field["type"] == "select"
    options = zone_field["options"]
    assert len(options) == len(_ZONE_TABLES) + len(_COUNTRY_TABLES)
    assert "DK1 — West Denmark" in options
    assert "FR — France" in options
    # Every option must resolve back to a real table (no dead choices).
    from tools.entsoe_query import _resolve_table

    assert all(_resolve_table(opt) is not None for opt in options)


@pytest.mark.asyncio
async def test_form_prefills_what_the_caller_did_supply():
    """Partial params: only the genuinely-missing values are asked for blind."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    result = await entsoe_day_ahead_prices("dk1", "2026-06-01", "")
    fields = {f["name"]: f for f in result["elicitation"]["fields"]}

    assert fields["bidding_zone"]["default"] == "DK1 — West Denmark"
    assert fields["start_date"]["default"] == "2026-06-01"
    assert fields["end_date"]["default"]  # falls back to the grounded default


@pytest.mark.asyncio
async def test_form_never_prefills_an_unsupported_zone():
    """An unknown zone must not be echoed into the picker as an unselectable
    option — the user would be stuck on a value they can't submit."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    result = await entsoe_day_ahead_prices("ATLANTIS", "", "")
    zone_field = next(f for f in result["elicitation"]["fields"] if f["name"] == "bidding_zone")

    assert zone_field["default"] is None


# ---------------------------------------------------------------------------
# Date defaults — derived from the GROUNDED current date, never a guessed year
# ---------------------------------------------------------------------------


def test_default_range_derives_from_the_grounded_date():
    from tools.entsoe_query import _default_range

    start, end = _default_range(datetime(2026, 3, 5, 11, 30, tzinfo=UTC))

    assert (start, end) == ("2026-02-26", "2026-03-05")


@pytest.mark.asyncio
async def test_form_date_defaults_track_today_not_a_hardcoded_year():
    """Regression guard for the wrong-year incident (asked for 2026 prices, the
    agent queried 2024 and reported the figures as fact). The form's defaults are
    computed per call from the real clock — so this assertion cannot be satisfied
    by a literal in the source."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    result = await entsoe_day_ahead_prices()
    fields = {f["name"]: f for f in result["elicitation"]["fields"]}
    today = datetime.now(UTC).date()

    assert fields["end_date"]["default"] == today.isoformat()
    assert fields["start_date"]["default"] == (today - timedelta(days=7)).isoformat()


# ---------------------------------------------------------------------------
# Form submit → the closed loop (values read from the surface, not the model)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submitted_form_values_are_read_from_surface_state_and_queried():
    """After the user submits, the run re-enters the tool with no args; the
    values come back AUTHORITATIVELY off the surface data model — never
    transcribed by the model."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    tool_context = SimpleNamespace(
        state={
            "a2ui_action_trigger": {"surfaceId": "elicit:run_price_query:1"},
            "a2ui_surface_state": {
                "elicit:run_price_query:1": {
                    "dataModel": {
                        "bidding_zone": "DK1 — West Denmark",
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-02",
                    }
                }
            },
        }
    )
    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = [_make_bq_row(2026, 6, 1, 0, 42.5)]

    with patch("google.cloud.bigquery.Client", return_value=fake_client):
        result = await entsoe_day_ahead_prices(tool_context=tool_context)

    assert "needs_input" not in result
    assert result["row_count"] == 1
    # The friendly picker label is normalized to the canonical code at the
    # boundary — neither the citation nor the result carries a label.
    assert result["bidding_zone"] == "DK1"
    assert "bidding_zone=DK1&" in result["source_uri"]
    assert result["start_date"] == "2026-06-01"


@pytest.mark.asyncio
async def test_friendly_zone_names_resolve_to_the_canonical_code():
    """Friendly-names rule: accept the alias on input, resolve to the id."""
    from tools.entsoe_query import _resolve_table

    assert _resolve_table("DK1 — West Denmark") == ("data_zones_denmark_hourly", "Day-Ahead Price DK1")
    assert _resolve_table("France") == ("data_france", "day_ahead_price")
    assert _resolve_table("west denmark") == ("data_zones_denmark_hourly", "Day-Ahead Price DK1")


@pytest.mark.asyncio
async def test_inverted_date_range_returns_error_without_bq_call():
    from tools.entsoe_query import entsoe_day_ahead_prices

    with patch("google.cloud.bigquery.Client") as mock_bq:
        result = await entsoe_day_ahead_prices("DK1", "2026-06-07", "2026-06-01")

    assert "error" in result
    assert "before" in result["error"].lower()
    assert mock_bq.call_count == 0


@pytest.mark.asyncio
async def test_unknown_zone_returns_error_without_bq_call():
    from tools.entsoe_query import entsoe_day_ahead_prices

    with patch("google.cloud.bigquery.Client") as mock_bq:
        result = await entsoe_day_ahead_prices("ATLANTIS", "2026-06-01", "2026-06-02")

    assert "error" in result
    assert "unknown bidding zone" in result["error"].lower()
    assert mock_bq.call_count == 0


# ---------------------------------------------------------------------------
# BQ failure — structured error with schema-hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bq_failure_returns_structured_error_with_hint():
    from tools.entsoe_query import entsoe_day_ahead_prices

    fake_client = MagicMock()
    fake_client.query.side_effect = RuntimeError("Not found: Table ... was not found")

    with patch("google.cloud.bigquery.Client", return_value=fake_client):
        result = await entsoe_day_ahead_prices("DK1", "2026-06-01", "2026-06-02")

    assert "error" in result
    assert "not found" in result["error"].lower()
    assert "hint" in result
    assert "table" in result["hint"].lower() or "column" in result["hint"].lower()
    # source_uri included even on failure, so the chat surface still has
    # something the user can click to debug in the BQ console.
    assert "bq://" in result["source_uri"]


# ---------------------------------------------------------------------------
# Integration — runs against live BQ if explicitly enabled
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("ENTSOE_INTEGRATION_TEST") != "1",
    reason="Live BQ integration test — set ENTSOE_INTEGRATION_TEST=1 to enable",
)
@pytest.mark.asyncio
async def test_live_bq_query_returns_dk1_prices():
    """Smoke test against the real ENTSO-E table. Confirms IAM + schema."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    result = await entsoe_day_ahead_prices("DK1", "2026-06-01", "2026-06-07")
    if "error" in result:
        pytest.fail(f"Live BQ call failed: {result['error']}\nHint: {result.get('hint', '')}")
    assert result["row_count"] > 0
    first = result["rows"][0]
    assert first["ts"] is not None
    assert isinstance(first["price_eur_mwh"], float)
