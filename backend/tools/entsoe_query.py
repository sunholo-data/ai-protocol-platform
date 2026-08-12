"""entsoe_day_ahead_prices — typed BigQuery read of ENTSO-E hourly prices.

Targets an existing
ENTSO-E ingestion at `your-entsoe-project.entsoe.*`. Read-only, runs as
sa-platform@your-project-id-{env} which holds roles/bigquery.jobUser on the
Aitana project (job billing) + roles/bigquery.dataViewer on the ENTSO-E dataset.

The real dataset schema (verified 2026-07-17 against the live tables — the tool
originally assumed a single `day_ahead_prices` table that does NOT exist):

  * Zone-accurate markets store price as a PER-ZONE COLUMN in one wide table:
      data_zones_denmark_hourly : `Day-Ahead Price DK1`, `Day-Ahead Price DK2`
      data_zones_sweden_hourly  : `Day-Ahead Price SE1`..`SE4`
      data_zones_italy_hourly   : `Day-Ahead Price Nord/CNord/CSud/Sud/Sard`
  * Single-zone / country-aggregate markets use one `data_<country>` table with
    a single `day_ahead_price` column (data_france, data_germany, data_spain, …).
  * ALL tables key time on separate INTEGER columns: `year`, `month`, `day`,
    `hour` (hour 0-23, UTC) — there is no TIMESTAMP column. Some country tables
    (e.g. data_france) carry 4 sub-hourly (15-min) prices under the SAME `hour`
    with no minute column, so the query averages to one price per hour.

So this tool resolves a bidding-zone code to (table, price_column): a zone-level
table+column where one exists, else the country table's `day_ahead_price`. The
`{table, price_column}` are looked up from the hardcoded maps below (never from
raw user input), so interpolating them into SQL is safe; the date range is a
bound parameter.

Source attribution (Axiom #2): returns `source_uri` in the format
  `bq://your-entsoe-project.entsoe.data_zones_denmark_hourly?bidding_zone=DK1&start=…&end=…`
which a downstream A2UI line-chart card can render as a citation chip without
round-tripping through the agent.

v6.12.0 M5-ELICITATION-TRIGGER — the three params are OPTIONAL by signature on
purpose. Observed on deployed test, asking "can you query bigquery for prices?"
cost FIVE turns to collect three values the signature already declares, because
a REQUIRED-param declaration leaves the model only two legal moves: invent
values (the wrong-year incident) or interrogate the user in prose. Optional
params give it a third, better one — call the tool with what it has and let the
tool raise the form (`needs_input` elicitation envelope, the same shape
`map_ppa_obligations` returns for `needs_assumptions`). The submitted values are
read back AUTHORITATIVELY from the surface data model (`read_submitted_values`),
never transcribed by the model. See docs/design/v6.12.0/market-prices-workspace.md.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from google.adk.tools import ToolContext

from adk.elicitation import (
    ElicitationEnvelope,
    ElicitationField,
    make_elicitation_result,
    read_submitted_values,
)

log = logging.getLogger(__name__)

# Hard-coded to the configured data project. Forks override via env if they have
# their own ENTSO-E source.
_ENTSOE_PROJECT = os.environ.get("ENTSOE_PROJECT", "your-entsoe-project")
_ENTSOE_DATASET = os.environ.get("ENTSOE_DATASET", "entsoe")

# Sanity cap on row count returned to the agent. 7 days * 24 hours = 168 rows
# already covers typical demo queries; cap at 1000 so a "last 30 days" call
# doesn't blow up the response.
_MAX_ROWS = 1000

# Zone-accurate markets: each multi-zone market has ONE wide table whose price is
# a per-zone COLUMN. Keyed by the ENTSO-E zone code → (table, price column).
_ZONE_TABLES: dict[str, tuple[str, str]] = {
    # Denmark
    "DK1": ("data_zones_denmark_hourly", "Day-Ahead Price DK1"),
    "DK2": ("data_zones_denmark_hourly", "Day-Ahead Price DK2"),
    # Sweden
    "SE1": ("data_zones_sweden_hourly", "Day-Ahead Price SE1"),
    "SE2": ("data_zones_sweden_hourly", "Day-Ahead Price SE2"),
    "SE3": ("data_zones_sweden_hourly", "Day-Ahead Price SE3"),
    "SE4": ("data_zones_sweden_hourly", "Day-Ahead Price SE4"),
    # Italy — ENTSO-E IT_* codes map to the dataset's short zone labels.
    "IT_NORD": ("data_zones_italy_hourly", "Day-Ahead Price Nord"),
    "IT_CNOR": ("data_zones_italy_hourly", "Day-Ahead Price CNord"),
    "IT_CSUD": ("data_zones_italy_hourly", "Day-Ahead Price CSud"),
    "IT_SUD": ("data_zones_italy_hourly", "Day-Ahead Price Sud"),
    "IT_SARD": ("data_zones_italy_hourly", "Day-Ahead Price Sard"),
}

# Country fallback: single-zone / country-aggregate markets — one hourly row with
# a single `day_ahead_price`. Keyed by ENTSO-E country (or single-zone) code.
_COUNTRY_PRICE_COL = "day_ahead_price"
_COUNTRY_TABLES: dict[str, str] = {
    "AT": "data_austria",
    "BE": "data_belgium",
    "BG": "data_bulgaria",
    "CH": "data_switzerland",
    "DE": "data_germany",
    "DE_LU": "data_germany",  # Germany-Luxembourg bidding zone
    "DK": "data_denmark",
    "EE": "data_estonia",
    "ES": "data_spain",
    "FI": "data_finland",
    "FR": "data_france",
    "GR": "data_greece",
    "HU": "data_hungary",
    "IE": "data_ireland",
    "IT": "data_italy",
    "LT": "data_lithuania",
    "LV": "data_latvia",
    "NL": "data_netherlands",
    "PL": "data_poland",
    "PT": "data_portugal",
    "RO": "data_romania",
    "RS": "data_serbia",
    "SE": "data_sweden",
}


# Human market names for every supported code (CLAUDE.md #9 — friendly names, not
# raw ids). The elicitation picker offers "DK1 — West Denmark", not a bare code
# an analyst has to already know; `_normalise_zone` resolves the friendly form
# (or a bare market name) BACK to the code, never the reverse.
_ZONE_NAMES: dict[str, str] = {
    "DK1": "West Denmark",
    "DK2": "East Denmark",
    "SE1": "Sweden zone 1 (Luleå)",
    "SE2": "Sweden zone 2 (Sundsvall)",
    "SE3": "Sweden zone 3 (Stockholm)",
    "SE4": "Sweden zone 4 (Malmö)",
    "IT_NORD": "Italy North",
    "IT_CNOR": "Italy Centre-North",
    "IT_CSUD": "Italy Centre-South",
    "IT_SUD": "Italy South",
    "IT_SARD": "Italy Sardinia",
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "DE": "Germany",
    "DE_LU": "Germany-Luxembourg",
    "DK": "Denmark (all zones)",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GR": "Greece",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy (all zones)",
    "LT": "Lithuania",
    "LV": "Latvia",
    "NL": "Netherlands",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "RS": "Serbia",
    "SE": "Sweden (all zones)",
}

# The separator between the code and its market name in a picker option. An
# em dash, so it can never collide with the "-" inside a code like DE-LU.
_LABEL_SEP = " — "


def _zone_label(code: str) -> str:
    """``"DK1"`` → ``"DK1 — West Denmark"`` (the form's option string)."""
    name = _ZONE_NAMES.get(code)
    return f"{code}{_LABEL_SEP}{name}" if name else code


def _zone_options() -> list[str]:
    """Every supported market as a friendly picker option — zone-accurate
    markets first (the ones an analyst usually wants), then country-level."""
    return [_zone_label(c) for c in sorted(_ZONE_TABLES)] + [_zone_label(c) for c in sorted(_COUNTRY_TABLES)]


# Bare market name → code, so "France" / "west denmark" resolve without the code.
_NAME_TO_CODE: dict[str, str] = {name.lower(): code for code, name in _ZONE_NAMES.items()}


def _normalise_zone(bidding_zone: str) -> str:
    """Canonicalise a zone code for lookup: upper-case, dashes → underscores.

    Accepts the FRIENDLY forms too (CLAUDE.md #9 — resolve friendly→id, never
    require the raw id): the picker's ``"DK1 — West Denmark"`` option string, or
    a bare market name like ``"France"``.
    """
    text = str(bidding_zone or "").strip()
    if not text:
        return ""
    # "DK1 — West Denmark" → "DK1" (the picker's own option string comes back
    # verbatim in the submitted data model).
    head = text.split(_LABEL_SEP.strip())[0].strip()
    code = _NAME_TO_CODE.get(head.lower(), head)
    return code.strip().upper().replace("-", "_")


def _resolve_table(bidding_zone: str) -> tuple[str, str] | None:
    """Resolve a bidding-zone code to ``(table, price_column)``.

    Prefers the zone-accurate table+column where the market has one; falls back
    to the country table's single ``day_ahead_price``. Returns ``None`` for an
    unknown code so the caller can return a friendly "supported zones" error.
    """
    z = _normalise_zone(bidding_zone)
    if z in _ZONE_TABLES:
        return _ZONE_TABLES[z]
    if z in _COUNTRY_TABLES:
        return (_COUNTRY_TABLES[z], _COUNTRY_PRICE_COL)
    return None


def _supported_zones() -> str:
    return ", ".join(sorted(_ZONE_TABLES) + sorted(_COUNTRY_TABLES))


# The surface action the form's submit fires. A chat-placement form always drives
# a full agent turn (`ChatPlacementForms` mounts with `triggerOnAction`), so the
# name is descriptive rather than load-bearing — the re-run reads the submitted
# values straight off the surface via `read_submitted_values`.
RUN_PRICE_QUERY_ACTION = "run_price_query"

# The form's field names. They are the closed loop: each is BOTH the A2UI
# dataModel path the widget binds to AND the key read back on submit — and they
# match this tool's own parameter names, so the re-run maps 1:1.
PRICE_FORM_FIELDS = ("bidding_zone", "start_date", "end_date")

# Default window offered in the form: the last week, ending today.
_DEFAULT_WINDOW_DAYS = 7


def _default_range(now: datetime | None = None) -> tuple[str, str]:
    """Sensible ``(start, end)`` defaults derived from the GROUNDED current date.

    Computed per CALL (never at import, never from the model's training-era guess
    of the year) — the same discipline `adk.today_context` applies to the
    instruction. Asked for Danish prices "from start of year to now" on
    2026-07-17, the ungrounded agent queried 2024 and reported those figures as
    fact; a form pre-filled from a guessed year would ship the same defect with a
    nicer skin.
    """
    stamp = now or datetime.now(UTC)
    end = stamp.date()
    start = end - timedelta(days=_DEFAULT_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def _needs_input(
    bidding_zone: str,
    start_date: str,
    end_date: str,
    tool_context: ToolContext | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ask for the missing query parameters as an A2UI chat FORM, not in prose.

    Mirrors the `needs_assumptions` refusal `map_ppa_obligations` already returns
    (and which already renders): a `needs_input` elicitation envelope carried on
    the tool result, transformed by the generic
    `adk.a2ui_elicitation_render.elicitation_form_to_a2ui` and pushed to a
    `placement:"chat"` surface. Whatever the caller DID supply is prefilled, so
    the form asks only for what's genuinely missing.
    """
    start_default, end_default = _default_range(now)
    code = _normalise_zone(bidding_zone)
    fields = [
        ElicitationField(
            name="bidding_zone",
            type="select",
            label="Bidding zone",
            help="Which ENTSO-E market to price.",
            # Prefill only a zone we actually support — echoing an unknown code
            # back into the picker would offer the user an unselectable option.
            default=_zone_label(code) if _resolve_table(code) else None,
            options=_zone_options(),
            required=True,
        ),
        ElicitationField(
            name="start_date",
            type="date",
            label="Start date",
            help="First day to include (UTC, inclusive).",
            default=start_date.strip() or start_default,
            required=True,
        ),
        ElicitationField(
            name="end_date",
            type="date",
            label="End date",
            help="Last day to include (UTC, inclusive).",
            default=end_date.strip() or end_default,
            required=True,
        ),
    ]
    envelope = ElicitationEnvelope(
        kind="confirm_with_fields",
        action=RUN_PRICE_QUERY_ACTION,
        message="Which day-ahead prices should I pull? The dates default to the last week.",
        reason="I need a market and a date range before I can query the price data.",
        fields=fields,
        context={"tool": "entsoe_day_ahead_prices"},
    )
    return make_elicitation_result(envelope, tool_context=tool_context)


def _source_uri(table: str, bidding_zone: str, start_date: str, end_date: str) -> str:
    return (
        f"bq://{_ENTSOE_PROJECT}.{_ENTSOE_DATASET}.{table or _ENTSOE_DATASET}"
        f"?bidding_zone={bidding_zone}&start={start_date}&end={end_date}"
    )


async def entsoe_day_ahead_prices(
    bidding_zone: str = "",
    start_date: str = "",
    end_date: str = "",
    tool_context: ToolContext = None,
) -> dict[str, Any]:
    """Fetch hourly day-ahead prices for a bidding zone over a date range.

    Use when the user asks about electricity market prices, day-ahead
    settlement values, or wants to ground a PPA cost calculation in
    historical prices. Composes naturally with extract_ppa_clauses /
    compare_ppa_contracts results — "what would this price-formula
    difference cost at DK1 prices last month".

    CALL THIS TOOL EVEN WHEN YOU DON'T HAVE THE ZONE OR THE DATES. Pass whatever
    the user gave you and omit the rest: the tool shows the user a form asking
    for exactly the missing values, and the next turn picks their answer up
    automatically. Do NOT ask for the zone or dates in prose, and NEVER invent
    them — an interrogation costs the user four turns, and a guessed year is
    answered as fact. If the user names a relative range ("last month", "year to
    date"), resolve it against today's date and pass real ISO dates.

    Args:
        bidding_zone: ENTSO-E market. Either a code — zone-accurate: "DK1",
            "DK2", "SE1".."SE4", "IT_NORD"/"IT_CNOR"/"IT_CSUD"/"IT_SUD"/
            "IT_SARD"; country-level: "FR", "DE" (or "DE_LU"), "ES", "NL", "BE",
            "AT", "PT", "IE", "FI", "PL", "CH", … — or a market name ("France",
            "West Denmark"). Case-insensitive. Omit if the user hasn't said.
        start_date: ISO date `YYYY-MM-DD` (inclusive, UTC). Omit if unknown.
        end_date: ISO date `YYYY-MM-DD` (inclusive, UTC). Must be >= start.
            Omit if unknown.

    Returns:
        On success:
            {
              "rows": [{"ts": "2026-06-01T00:00:00+00:00", "price_eur_mwh": 42.5}, ...],
              "row_count": <int>,
              "source_uri": "bq://...",
              "bidding_zone": "DK1",
              "start_date": "...",
              "end_date": "..."
            }
        When a required value is missing:
            {"needs_input": true, "elicitation": {...}} — a form is now on the
            user's screen. Say briefly that you've asked for the details and
            STOP; do not re-ask in prose and do not call this tool again until
            the user submits.
        On error:
            {"error": "...", "source_uri": "bq://..."}
    """
    bidding_zone = (bidding_zone or "").strip()
    start_date = (start_date or "").strip()
    end_date = (end_date or "").strip()

    # A missing value may already have been answered: when the user submits the
    # form, the run re-enters here and the values are read AUTHORITATIVELY off
    # the surface's data model — never transcribed by the model (same closed loop
    # as the obligation assumptions form).
    if not (bidding_zone and start_date and end_date):
        submitted = read_submitted_values(tool_context, expected_fields=list(PRICE_FORM_FIELDS)) or {}
        bidding_zone = bidding_zone or str(submitted.get("bidding_zone") or "").strip()
        start_date = start_date or str(submitted.get("start_date") or "").strip()
        end_date = end_date or str(submitted.get("end_date") or "").strip()

    # Still short? Ask the user IN A FORM rather than dead-ending on an error the
    # agent would relay as another prose question (v6.12.0 M5).
    if not (bidding_zone and start_date and end_date):
        return _needs_input(bidding_zone, start_date, end_date, tool_context=tool_context)

    # Normalize the friendly form to the canonical code at the boundary
    # (CLAUDE.md #9) — the picker submits "DK1 — West Denmark", and neither the
    # source_uri citation nor the result the render reads should carry a label.
    if _resolve_table(bidding_zone) is not None:
        bidding_zone = _normalise_zone(bidding_zone)

    if start_date > end_date:
        return {
            "error": f"start_date ({start_date}) must be on or before end_date ({end_date}).",
            "source_uri": _source_uri("", bidding_zone, start_date, end_date),
        }

    resolved = _resolve_table(bidding_zone)
    if resolved is None:
        return {
            "error": (f"Unknown bidding zone {bidding_zone!r}. Supported zones/countries: {_supported_zones()}."),
            "source_uri": _source_uri("", bidding_zone, start_date, end_date),
        }
    table, price_col = resolved
    source_uri = _source_uri(table, bidding_zone, start_date, end_date)

    try:
        from google.cloud import bigquery
    except ImportError:
        return {
            "error": "google-cloud-bigquery is not installed in this environment.",
            "source_uri": source_uri,
        }

    # `table` and `price_col` come from the hardcoded maps above (never raw user
    # input), so backtick-interpolation is safe; the date range is parameterised.
    # Time is stored as separate INT columns — filter on DATE(year, month, day)
    # and skip NULL prices (the current, not-yet-settled day carries NULLs).
    # AVG + GROUP BY collapses to ONE price per hour: a no-op for the hourly zone
    # tables (1 row/hour), but essential for country tables like data_france that
    # store 4 sub-hourly (15-min) prices under the same `hour` with no minute
    # column — otherwise we'd emit 4 duplicate timestamps per hour.
    query = f"""
        SELECT year, month, day, hour, AVG(`{price_col}`) AS price
        FROM `{_ENTSOE_PROJECT}.{_ENTSOE_DATASET}.{table}`
        WHERE DATE(year, month, day) BETWEEN DATE(@start) AND DATE(@end)
          AND `{price_col}` IS NOT NULL
        GROUP BY year, month, day, hour
        ORDER BY year, month, day, hour
        LIMIT {_MAX_ROWS}
    """

    try:
        # Bill the job to the Aitana project (where sa-platform has jobUser),
        # not to your-entsoe-project where we only have dataViewer.
        # The table ref is fully-qualified so cross-project read still works.
        client = bigquery.Client()
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "STRING", start_date),
                bigquery.ScalarQueryParameter("end", "STRING", end_date),
            ]
        )
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        exc_str = str(exc)
        log.warning(
            "entsoe_day_ahead_prices: BQ query failed for zone=%s (%s) %s..%s: %s",
            bidding_zone,
            table,
            start_date,
            end_date,
            exc,
        )

        # Detect IAM / permission errors and return a user-readable fix.
        is_permission_error = (
            "403" in exc_str
            or "bigquery.jobs.create" in exc_str
            or "Permission denied" in exc_str
            or "Access Denied" in exc_str
        )
        if is_permission_error:
            billing_project = client.project or "your-project-id"
            return {
                "error": (
                    "The ENTSO-E price data is not accessible yet — the backend "
                    "service account is missing BigQuery permissions. An Aitana "
                    "administrator needs to run these two grants once:\n\n"
                    f"# 1. Allow job creation billed to the Aitana project:\n"
                    f"gcloud projects add-iam-policy-binding {billing_project} \\\n"
                    f"  --member='serviceAccount:sa-platform@{billing_project}.iam.gserviceaccount.com' \\\n"
                    f"  --role='roles/bigquery.jobUser'\n\n"
                    f"# 2. Allow reading the ENTSO-E data project:\n"
                    f"gcloud projects add-iam-policy-binding {_ENTSOE_PROJECT} \\\n"
                    f"  --member='serviceAccount:sa-platform@{billing_project}.iam.gserviceaccount.com' \\\n"
                    f"  --role='roles/bigquery.dataViewer'"
                ),
                "source_uri": source_uri,
                "error_type": "permission_denied",
            }

        # Any other failure — surface the BQ error verbatim so the operator can
        # adjust. Column/table drift shows up as "Not found" / "Name X not found".
        return {
            "error": f"BigQuery query failed: {exc}",
            "source_uri": source_uri,
            "hint": (
                "If the error mentions a missing table or column, the ENTSO-E "
                f"schema in {_ENTSOE_PROJECT} may have changed. This query reads "
                f"`{price_col}` from `{_ENTSOE_DATASET}.{table}`; inspect it with: "
                f"bq show {_ENTSOE_PROJECT}:{_ENTSOE_DATASET}.{table}"
            ),
        }

    serialised = [
        {
            # Reconstruct a UTC ISO timestamp from the split INT columns (hour 0-23).
            "ts": (
                f"{int(row['year']):04d}-{int(row['month']):02d}-{int(row['day']):02d}"
                f"T{int(row['hour']):02d}:00:00+00:00"
            ),
            "price_eur_mwh": float(row["price"]) if row["price"] is not None else None,
        }
        for row in rows
    ]

    return {
        "rows": serialised,
        "row_count": len(serialised),
        "source_uri": source_uri,
        "bidding_zone": bidding_zone,
        "start_date": start_date,
        "end_date": end_date,
    }
