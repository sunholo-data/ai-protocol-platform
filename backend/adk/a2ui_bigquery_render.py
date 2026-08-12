"""Scoped ad-hoc BigQuery results → A2UI result render (v6.23.0 ONE-BQ).

Registers a result→A2UI mapping for the ``one-bigquery`` toolset's two query
tools on the proven 7.3 path ([a2ui_result_render](a2ui_result_render.py)).

Design: docs/design/v6.23.0/one-bigquery.md

## Why this reuses the `prices` tab instead of adding a component

`ChatShell` dispatches artifacts on `kind`, and its `"prices"` branch says so
explicitly: *"The tab holds no ENTSO-E knowledge — any dataset-shaped tool that
declares `x`/`y` reuses it, so this branch is keyed on the artifact `kind`, never
on the tool name."* Declaring the same **series envelope** as
``a2ui_entsoe_render`` therefore buys a chart + sortable table + CSV export with
**zero** frontend work. Writing a BigQuery-specific React tab would violate
CLAUDE.md principle #7 for no gain.

A result with no numeric column can't be charted, so it falls back to
``kind: "table"`` and renders through the generic ``A2UISurfaceMount`` from the
same Basic component tree. Both paths build the same tree — only the tab differs.

## THE MCP WIRE SHAPE — a third hazard the playbook didn't cover

`backend/adk/CLAUDE.md` trap 4 documents two wire hazards (the double-wrapped
``{"result": …}`` envelope and the >50K offload). MCP tools add a third, and it
is the reason a naive transform silently renders nothing.

An ADK ``MCPTool`` returns ``CallToolResult.model_dump(exclude_none=True)``, so
what reaches the transform is **not** the tool's payload — it is the MCP envelope
around it. Captured live from Toolbox 1.7.0 on 2026-08-07:

```
success  {"content": [{"type": "text", "text": "{\"year\":2026,\"base_load\":28.37}"},
                      {"type": "text", "text": "{\"year\":2026,\"base_load\":37.15}"}]}
error    {"content": [{"type": "text", "text": "query accesses dataset '…', which is
                      not in the allowed list"}], "isError": true}
empty    {"content": [{"type": "text", "text": "\"The query returned 0 rows.\""}]}
```

Three things to note, each of which would otherwise bite:

1. **One `content` item PER ROW**, each a JSON *string* — not one JSON array.
2. **The empty result is a JSON-encoded STRING**, not an object, so "parse each
   item and keep the dicts" naturally yields zero rows without a special case.
3. ``_coerce_typed_result`` passes this dict through untouched: its keys are
   ``{"content"}``, not ``{"result"}``, so no envelope is peeled. The transform
   owns the unwrapping.

## Error and empty paths render NOTHING, on purpose

Returning ``None`` for ``isError`` and for zero rows is not a silent failure —
it is principle #8 working. The agent still receives the full error text as the
tool result and explains it in prose ("that dataset isn't in the allowed list"),
which is a better surface for a refusal than an empty workbench tab. What would
violate #8 is swallowing the error *without* the agent seeing it; that does not
happen here.

Pure functions — server-side, unit-testable, CLI-previewable via
``aiplatform a2ui render bigquery-rows --result <file.json>``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID, register
from adk.callbacks import A2UI_TOOL_ARGS_STATE_KEY

# Base id + the transform's placeholder surfaceId. The registered ``surface=`` is
# a CALLABLE deriving a per-RESULT id, so two different questions sit side by side
# as two tabs. ``render_for_emit`` retargets the messages' inner surfaceId to the
# resolved id — never hardcode a divergent id in the messages (CLAUDE.md TRAP 5).
BIGQUERY_SURFACE_ID = "bigquery_result"

# The whole toolset — one executor per BigQuery region. Toolbox's dedicated
# list-tables/get-table-info tools are NOT used (they resolve the dataset against
# the BILLING project and so cannot see a cross-project dataset), so schema
# discovery runs through these same two tools. That is why the transform filters
# discovery results out by column signature — see `_DISCOVERY_SIGNATURES`.
BIGQUERY_QUERY_TOOLS = ["bq_market_query", "bq_analysis_query"]

# Columns whose name suggests a time axis, so the chart labels them sensibly.
# Best-effort only — `type` is optional in the series envelope and the tab
# tolerates its absence (SeriesArtefactTab reads axes from the declaration).
_TIME_HINTS = re.compile(r"(^|_)(ts|time|timestamp|date|datetime|year|month|day|hour)($|_)", re.IGNORECASE)


def _rows_from_mcp(typed_result: Any) -> list[dict[str, Any]] | None:
    """Unwrap the MCP ``CallToolResult`` envelope into row dicts.

    Returns ``None`` for an error envelope or an unrecognised shape, and an empty
    list when the query genuinely returned no rows. See the module docstring for
    the three captured shapes.
    """
    if not isinstance(typed_result, dict) or typed_result.get("isError"):
        return None
    content = typed_result.get("content")
    if not isinstance(content, list):
        return None

    rows: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            # A non-JSON text item is a human-readable notice, not a row.
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


# Column signatures of an INFORMATION_SCHEMA discovery query. Collapsing the
# toolset to 2 tools (see the tools.yaml note) means schema lookups run through
# the SAME tool as real queries — so without this, every `SELECT column_name...`
# opens its own workbench tab. Observed live on 2026-08-07: one question produced
# EIGHT tabs, most of them "column_name, data_type · 27 rows".
#
# A tab is for an ANSWER. Discovery is scaffolding the user did not ask for, and
# burying the result among it is the same failure the separate discovery tools
# were meant to avoid. Matched on the column set rather than by sniffing the SQL,
# because the MCP envelope does not carry the request.
# An exact-signature list was tried first and is too brittle: the agent wrote
# `SELECT table_name, column_name, data_type` (a cross-table schema sweep), which
# no fixed signature anticipated. The vocabulary test below generalises — a result
# is discovery when EVERY column is drawn from the INFORMATION_SCHEMA vocabulary.
# It cannot false-positive on a real answer, because an answer necessarily
# carries at least one domain column (a price, a count, a date) that is not in
# this set.
_INFORMATION_SCHEMA_COLUMNS = frozenset(
    {
        "table_catalog",
        "table_schema",
        "table_name",
        "table_type",
        "column_name",
        "ordinal_position",
        "data_type",
        "is_nullable",
        "is_partitioning_column",
        "clustering_ordinal_position",
        "is_generated",
        "is_stored",
        "is_hidden",
        "is_updatable",
        "is_system_defined",
        "column_default",
        "collation_name",
        "ddl",
        "creation_time",
    }
)


def _is_discovery_result(columns: list[str]) -> bool:
    """True when every column comes from the INFORMATION_SCHEMA vocabulary.

    Requires at least one column so an empty result doesn't count as discovery
    (it is already filtered earlier, but the predicate should stand alone).
    """
    return bool(columns) and all(c in _INFORMATION_SCHEMA_COLUMNS for c in columns)


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    """Column names in first-seen order across all rows.

    Union rather than ``rows[0].keys()`` because BigQuery omits nothing but a
    transform shouldn't assume every row is uniform.
    """
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _numeric_columns(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    """Columns whose non-null values are all numeric (and not bool)."""
    numeric: list[str] = []
    for col in columns:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if values and all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
            numeric.append(col)
    return numeric


def _distinct(rows: list[dict[str, Any]], col: str) -> int:
    return len({str(r.get(col)) for r in rows})


def _looks_temporal(rows: list[dict[str, Any]], col: str) -> bool:
    """True when the VALUES are real timestamps, not just a date-ish NAME.

    Load-bearing. `month` holding 1..12 is an ordinal label, not a time axis —
    typing it ``time`` made the chart render those integers as dates in 2000/2001
    (observed in the browser, 2026-08-07). Epoch-scale ints and ISO-ish strings
    are temporal; small integers are not.
    """
    values = [r.get(col) for r in rows if r.get(col) is not None]
    if not values:
        return False
    if all(isinstance(v, str) for v in values):
        return any(("-" in str(v) or ":" in str(v)) for v in values)
    return all(isinstance(v, int | float) and abs(v) > 100_000_000 for v in values)


def _period_parts(dimensions: list[str]) -> list[str]:
    """Time dimensions present, ordered coarse -> fine."""
    order = ["year", "quarter", "month", "week", "day", "hour", "minute"]
    return [c for c in order if c in dimensions]


# ── ONE's low / base / high case convention (v6.23.0 ONE-BQ-SHAPES) ──────────
# ONE's analyst names scenario columns `<measure>_basecase` / `_lowcase` /
# `_highcase` — verified 2026-08-11 against her four saved queries and the
# materialised `year_captured_prices_*` tables. A captured-price question
# therefore returns NINE measures (three technologies x three cases), and the
# generic axis logic drew nine lines from an EIGHT-slot categorical palette, so
# the ninth silently reused slot 1's colour.
#
# Three lines with a shaded low-high band is both inside the palette and the
# CORRECT reading: the band IS the uncertainty around one forecast, not three
# independent series. Declared in the envelope, so SeriesArtefactTab draws it
# with no BigQuery knowledge — the same contract as `x` / `y`.
_CASE_SUFFIX = re.compile(r"^(?P<stem>.+)_(?P<case>basecase|lowcase|highcase)$", re.IGNORECASE)


def _bands(measures: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Collapse case triples into (charted measures, band declarations).

    A stem needs its basecase plus at least one of low/high to become a band;
    an incomplete or unsuffixed measure stays an ordinary series, so a query
    that does not use the convention is completely unaffected.
    """
    stems: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for measure in measures:
        match = _CASE_SUFFIX.match(measure)
        if not match:
            continue
        stem = match.group("stem")
        if stem not in stems:
            stems[stem] = {}
            order.append(stem)
        stems[stem][match.group("case").lower()] = measure

    bands: list[dict[str, str]] = []
    consumed: set[str] = set()
    for stem in order:
        cases = stems[stem]
        base, lower, upper = cases.get("basecase"), cases.get("lowcase"), cases.get("highcase")
        if not base or not (lower or upper):
            continue
        consumed.update(cases.values())
        # A one-sided band (only low, or only high) collapses that edge onto the
        # base line rather than being dropped — half an interval is still real.
        bands.append({"key": base, "lower": lower or base, "upper": upper or base, "label": stem})

    charted = [m for m in measures if m not in consumed or any(b["key"] == m for b in bands)]
    return charted, bands


# ── One axis, always (data-viz non-negotiable) ───────────────────────────────
# A dual-scale chart is the single most common charting error, and this dataset
# invites it: ONE's capture-rate queries return prices (~80 EUR/MWh) and rates
# (~0.85) side by side, so the generic logic put both on one axis and the rate
# lines collapsed flat onto the floor. We chart the dominant group and DECLARE
# the rest, so the tab can say what it left out rather than dropping it silently
# (CLAUDE.md #8).
_RATIO_NAME = re.compile(r"(^|_)(rate|ratio|factor|share|pct|percent)($|_)", re.IGNORECASE)

# A gap this large between neighbouring magnitudes means one axis cannot show
# both. Deliberately generous: a merely "tall" series (10x) still shares an axis
# fine, and splitting it would cost the reader the comparison.
_SCALE_GAP = 20.0


def _median_magnitude(rows: list[dict[str, Any]], col: str) -> float:
    """Median |value| over the non-zero numerics, or 0.0 when there are none."""
    values = sorted(
        abs(float(r[col]))
        for r in rows
        if isinstance(r.get(col), int | float) and not isinstance(r.get(col), bool) and r[col]
    )
    return values[len(values) // 2] if values else 0.0


def _split_by_scale(rows: list[dict[str, Any]], measures: list[str]) -> tuple[list[str], list[str]]:
    """Partition measures into (charted, deferred) so one y axis stays honest."""
    if len(measures) < 2:
        return list(measures), []
    scales = {m: _median_magnitude(rows, m) for m in measures}
    ranked = sorted((m for m in measures if scales[m] > 0), key=lambda m: scales[m])
    if len(ranked) < 2:
        return list(measures), []

    gap, at = max((scales[ranked[i + 1]] / scales[ranked[i]], i) for i in range(len(ranked) - 1))
    if gap < _SCALE_GAP:
        return list(measures), []

    lower, upper = ranked[: at + 1], ranked[at + 1 :]
    # Keep the bigger group; on a tie keep the larger-magnitude one, which is
    # nearly always the headline answer (prices) rather than its derived ratio.
    keep = set(upper if len(upper) >= len(lower) else lower)
    # An all-null/all-zero measure plots nothing either way — leave it charted
    # rather than raising a "not shown" notice the user cannot act on.
    keep.update(m for m in measures if scales[m] <= 0)
    charted = [m for m in measures if m in keep]
    return charted, [m for m in measures if m not in keep]


def _is_ratio(rows: list[dict[str, Any]], col: str) -> bool:
    """A dimensionless ratio — needs BOTH the name hint and a plausible scale.

    The name alone is not enough here: MarketData calls its hourly PRODUCTION column
    `profile`, and that is a volume, not a ratio.
    """
    return bool(_RATIO_NAME.search(col)) and 0 < _median_magnitude(rows, col) <= 5.0


def _axes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive ``x``, ``y`` and possibly-rewritten rows for the series envelope.

    Three rules, each fixing a chart that rendered wrongly in the browser on
    2026-08-07 (screenshots on the ONE-BQ sprint):

    1. **A calendar part is a DIMENSION, never a measure.** `year`/`month`/`day`
       were being plotted as series alongside the real value, so a `month` column
       drew a 1..12 sawtooth next to the prices. Measures are numeric columns that
       are NOT calendar parts.
    2. **x must VARY.** With `year, month, base_load` for one year, `year` was
       picked as x and every point stacked on a single tick — a vertical line.
       x is now the dimension with the most distinct values.
    3. **Multiple calendar parts compose.** A 137-row `year, month, value` series
       has no single good x: `month` alone collapses the years together, `year`
       alone collapses the months. A synthesised zero-padded ``period``
       ("2026-08") sorts chronologically as a string and plots correctly.

    Returns ``(x, y, rows)`` — rows may carry an added ``period`` column.

    This is the GENERIC derivation and knows nothing about ONE's conventions;
    ``_chart_spec`` layers the domain shapes (bands, scale split) on top.
    """
    columns = _columns(rows)
    numeric = _numeric_columns(rows, columns)
    calendar = [c for c in columns if _TIME_HINTS.search(c or "")]
    dimensions = [c for c in columns if c not in numeric or c in calendar]
    measures = [c for c in numeric if c not in calendar]

    parts = _period_parts(dimensions)
    if len(parts) >= 2:
        rows = [dict(r) for r in rows]
        for row in rows:
            row["period"] = "-".join(str(row.get(p, "")).zfill(2) for p in parts)
        x_key = "period"
    elif dimensions:
        x_key = max(dimensions, key=lambda c: _distinct(rows, c))
    else:
        x_key = columns[0] if columns else ""

    # A composed `period` ("2026-08") is a LABEL, even though it contains a dash.
    # Typed `time` the frontend parsed it into a full timestamp and the axis read
    # "2026-08-01 00:00:00 UTC" with a "00:00" tooltip (browser, 2026-08-07).
    x_type = "category" if x_key == "period" else ("time" if _looks_temporal(rows, x_key) else "category")
    x = {"key": x_key, "label": " · ".join(parts) if x_key == "period" else (x_key or "—"), "type": x_type}
    y = [{"key": c, "label": c} for c in measures if c != x_key]
    return x, y, rows


def _chart_spec(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``_axes`` plus ONE's domain shapes — bands, one-axis split, ratio format.

    Every addition is OPTIONAL and additive: a result that uses none of ONE's
    conventions yields empty ``bands``/``deferred`` and no ``yFormat`` or
    ``reference``, so it renders exactly as it did before ONE-BQ-SHAPES.
    """
    x, y, rows = _axes(rows)

    # Order matters: collapse the case triples FIRST, so the scale split sees
    # three series rather than nine and cannot be skewed by the low/high edges.
    measures, bands = _bands([d["key"] for d in y])
    charted, deferred = _split_by_scale(rows, measures)
    band_decls = [b for b in bands if b["key"] in charted]

    labels = {b["key"]: b["label"] for b in band_decls}
    spec: dict[str, Any] = {
        "x": x,
        "y": [{"key": c, "label": labels.get(c, c)} for c in charted],
        "rows": rows,
        "bands": band_decls,
        "deferred": [{"key": c, "label": c} for c in deferred],
    }

    # A chart of nothing but ratios reads far better as a percentage against a
    # 1.0 reference — that is the whole point of a capture rate or a seasonal
    # index, and both of ONE's ratio queries produce exactly this shape.
    if charted and all(_is_ratio(rows, c) for c in charted):
        spec["yFormat"] = "percent"
        spec["reference"] = {"value": 1.0, "label": "Baseload average"}
    return spec


# The agent's opt-in marker, written into the SQL it sends. Toolbox fixes the
# parameters of `bigquery-execute-sql` to `sql` alone, so there is no extra
# argument to add — but a leading SQL COMMENT is a channel we control end to end,
# BigQuery ignores it, and the agent already authors the whole statement.
#
# Why agent-chosen and not inferred: a heuristic cannot know which of eleven
# queries was the ANSWER. Mark's call on 2026-08-07 after seeing seven tabs for
# one question — *"just ones we think should visualise for the user."* The
# marker also carries a human TITLE, which fixes tabs reading
# "product_code, f0_, f1_ +1" — derived column names are not a description.
_CHART_MARKER = re.compile(r"--\s*chart\s*:\s*(?P<title>[^\r\n]*)", re.IGNORECASE)


def _chart_request(tool_context: Any) -> tuple[bool, str]:
    """``(agent asked to visualise, title)`` read from the originating SQL.

    The MCP envelope carries no request, so the args are read from the state key
    the A2UI emitter stashes them under. Absent context (unit tests calling the
    transform directly, an emitter that predates the stash) → not requested.
    """
    try:
        state = getattr(tool_context, "state", None)
        args = (state or {}).get(A2UI_TOOL_ARGS_STATE_KEY) or {}
        sql = args.get("sql")
    except Exception:
        return False, ""
    if not isinstance(sql, str):
        return False, ""
    match = _CHART_MARKER.search(sql)
    if not match:
        return False, ""
    return True, (match.group("title") or "").strip()


def _is_worth_a_tab(rows: list[dict[str, Any]], x: dict[str, Any], y: list[dict[str, Any]]) -> bool:
    """Should this result get its own workbench tab?

    A tab is a claim that something is worth LOOKING at. The agent runs many
    queries per question — 11 on one live run — and a tab for each buried the
    answer among probes (`min_date, max_date, row_count · 1 row`) and drew charts
    that could not mean anything. The user's verdict on 2026-08-07: *"we would
    prefer not having tabs for each query, just ones we think should visualise for
    the user — the content is available in the Activity."*

    Nothing is lost by declining: the full result stays in the Activity tab, and
    the agent still narrates the number. So the bar is "is this PLOTTABLE":

      * at least 2 rows      — a scalar answer belongs in prose, not a chart
      * at least 1 measure   — nothing to plot on y
      * at least 2 distinct x — every point on one tick is a vertical line, which
                               is exactly the chart the browser showed us
    """
    if len(rows) < 2 or not y:
        return False
    return _distinct(rows, x.get("key", "")) >= 2


def bigquery_rows_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Transform a scoped BigQuery query result into A2UI messages.

    Returns ``None`` for an error envelope, an unrecognised shape, or zero rows,
    so the emitter renders nothing rather than an empty or misleading tab. The
    agent explains those cases in prose — see the module docstring.
    """
    rows = _rows_from_mcp(typed_result)
    if not rows:
        return None

    columns = _columns(rows)
    if _is_discovery_result(columns):
        return None

    requested, marker_title = _chart_request(tool_context)
    if not requested:
        # The agent did not ask for this one to be shown. Not a failure: the full
        # result is still in the Activity tab and the agent narrates the number.
        return None

    spec = _chart_spec(rows)
    x, y, rows = spec["x"], spec["y"], spec["rows"]
    title = marker_title or _title(rows, columns)

    components: list[dict[str, Any]] = []
    seq = 0

    def _add(comp: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        comp["id"] = comp.get("id") or f"bq-{seq}"
        components.append(comp)
        return comp["id"]

    heading_id = _add({"component": "Text", "text": title, "variant": "h4"})
    lines = [
        f"{len(rows):,} row{'s' if len(rows) != 1 else ''}",
        f"Columns: {', '.join(columns) if columns else '—'}",
    ]
    line_ids = [_add({"component": "Text", "text": t}) for t in lines]
    list_id = _add({"component": "List", "children": line_ids})
    col_id = _add({"component": "Column", "children": [heading_id, list_id]})
    card_id = _add({"component": "Card", "child": col_id})
    components.append({"id": "root", "component": "Column", "children": [card_id]})

    return [
        {"version": "v0.9", "createSurface": {"surfaceId": BIGQUERY_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": BIGQUERY_SURFACE_ID, "components": components}},
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": BIGQUERY_SURFACE_ID,
                "value": {
                    # The DECLARED SERIES envelope (v6.12.0 M1). Emitting exactly
                    # this shape is what lets SeriesArtefactTab chart/table/export
                    # this result with no BigQuery-specific frontend code.
                    "kind": "series",
                    "title": title,
                    "x": x,
                    "y": y,
                    "rows": rows,
                    "columns": columns,
                    "rowCount": len(rows),
                    # ONE-BQ-SHAPES. All four are OPTIONAL and additive: a query
                    # that uses none of ONE's conventions emits empty lists and
                    # no format/reference, and renders exactly as it did before.
                    "bands": spec["bands"],
                    "deferred": spec["deferred"],
                    **({"yFormat": spec["yFormat"]} if "yFormat" in spec else {}),
                    **({"reference": spec["reference"]} if "reference" in spec else {}),
                    # SeriesArtefactTab renders the Average/Low/High tiles from
                    # THIS key and deliberately never re-derives them client-side
                    # ("Stats come from the server and are NEVER re-derived").
                    # Omitting it left all three reading "—" (browser, 2026-08-07).
                    "stats": _stats(rows, y),
                },
            },
        },
    ]


def _stats(rows: list[dict[str, Any]], y: list[dict[str, Any]]) -> dict[str, float | None]:
    """avg / min / max over the PRIMARY measure, for the tab's stat tiles.

    First measure only: with several series there is no single meaningful
    "Average", and three tiles cannot show one per series. Non-numeric or absent
    values yield None, which the tab renders as "—".
    """
    if not y:
        return {"avg": None, "min": None, "max": None}
    key = y[0]["key"]
    values = [r.get(key) for r in rows]
    values = [float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
    if not values:
        return {"avg": None, "min": None, "max": None}
    return {"avg": sum(values) / len(values), "min": min(values), "max": max(values)}


def _title(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Friendly, distinguishing tab title (CLAUDE.md #9 — never a raw id).

    Tabs are keyed per result, so several can be open at once; "Query result"
    three times would be useless in the Workspace index. The column list is the
    most informative thing available without the SQL (the MCP envelope does not
    carry the request).
    """
    if not columns:
        return "Query result"
    shown = ", ".join(columns[:3])
    if len(columns) > 3:
        shown += f" +{len(columns) - 3}"
    return f"{shown} · {len(rows):,} row{'s' if len(rows) != 1 else ''}"


def _bigquery_surface(typed_result: Any) -> str:
    """Per-RESULT surfaceId — ``bigquery_result:<digest>``.

    ENTSO-E derives its per-tab identity from the query parameters, but the MCP
    envelope carries no request — only the result — so the identity has to come
    from the payload. A digest of the rows is STABLE (re-running the same query
    updates that tab in place rather than opening a duplicate) and DISTINCT across
    different answers (a new question gets its own tab and auto-focuses it).

    Degrades to the base id for an unrenderable payload; the transform returns
    ``None`` for those anyway, so the id is never used.
    """
    rows = _rows_from_mcp(typed_result)
    if not rows:
        return BIGQUERY_SURFACE_ID
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{BIGQUERY_SURFACE_ID}:{digest}"


def _bigquery_artifact(typed_result: Any, tool_context: Any = None) -> dict[str, Any]:
    """Workbench tab + Home-index metadata (7.5).

    ``kind`` decides the tab: ``"prices"`` routes to SeriesArtefactTab (chart +
    sortable table + CSV) and requires at least one numeric column to plot;
    anything else falls back to the generic A2UISurfaceMount, which renders the
    same Basic component tree as a readable summary.
    """
    rows = _rows_from_mcp(typed_result) or []
    columns = _columns(rows)
    spec = _chart_spec(rows) if rows else {"x": {}, "y": [], "rows": [], "bands": [], "deferred": []}
    x, y, rows = spec["x"], spec["y"], spec["rows"]
    _requested, marker_title = _chart_request(tool_context)
    # A marked result the axes cannot plot (one row, no measure, a constant x)
    # still gets its tab — the agent asked for it — but as `table`, which routes
    # to the generic mount instead of drawing a chart that cannot mean anything.
    return {
        "kind": "prices" if _is_worth_a_tab(rows, x, y) else "table",
        "title": marker_title or _title(rows, columns),
        "description": "Query result from ONE's BigQuery warehouse",
    }


register(
    bigquery_rows_to_a2ui,
    tool_names=BIGQUERY_QUERY_TOOLS,
    name="bigquery-rows",
    surface=_bigquery_surface,
    artifact_meta=_bigquery_artifact,
)
