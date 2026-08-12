"""ENTSO-E prices → A2UI result render (v6.12.0).

Registers a result→A2UI mapping for ``entsoe_day_ahead_prices`` on the proven 7.3
path ([a2ui_result_render](a2ui_result_render.py)), which buys two things at once:

1. **Its own Workspace tab.** ``artifact_meta`` makes the price series a workbench
   artifact + a row in the Workspace/Home index — the same mechanism the clause /
   comparison / obligation results use.
2. **Offload exemption.** Declaring ``tool_names`` marks the tool render-payload,
   so ``_handle_large_output`` stops dumping a ~1000-row series into an artifact
   above the 50K threshold. That offload is what made the agent narrate a raw
   artifact id at the user ("the full dataset is available in the artifact
   entsoe_day_ahead_prices_response_e-…") instead of showing them the data — both
   a FRIENDLY-NAMES violation (CLAUDE.md #9) and a dead end.

The component tree here is a clean, readable SUMMARY (Basic v0.9 has no chart and
no table). The full hourly series rides in the surface's ``updateDataModel`` so a
richer, explorable/chartable tab can render from it without another round-trip —
same split as the Clauses / Sources tabs.

Pure functions — server-side, unit-testable, CLI-previewable.
"""

from __future__ import annotations

import re
from typing import Any

from adk.a2ui_elicitation_render import register_elicitation_for
from adk.a2ui_result_render import BASIC_CATALOG_ID, register

# Base id + the transform's placeholder surfaceId. The registered ``surface=`` is
# a CALLABLE (``_entsoe_surface``) that derives a PER-QUERY id from the query
# identity, so DK1 and DK2 sit side by side as two tabs instead of one tab
# silently updating in place (v6.12.0 Open Question #4). ``render_for_emit``
# retargets the messages' inner surfaceId to the resolved id, so this literal is
# only ever a placeholder — never hardcode a divergent id in the messages
# (backend/adk/CLAUDE.md TRAP 5).
ENTSOE_SURFACE_ID = "entsoe_prices"

ENTSOE_TOOL = "entsoe_day_ahead_prices"

# v6.12.0 M5 — the tool asks for missing params with a `needs_input` elicitation
# envelope instead of interrogating the user in prose. Registered FIRST (first
# matching mapping wins) so that payload renders as the shared chat form; the
# success transform below declines it anyway (no `rows`), and a decline STOPS the
# search rather than falling through to the next mapping.
register_elicitation_for(ENTSOE_TOOL)


def _stats(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """avg / min / max over the non-null prices. Empty → all None."""
    prices = [r.get("price_eur_mwh") for r in rows or []]
    prices = [float(p) for p in prices if isinstance(p, int | float)]
    if not prices:
        return {"avg": None, "min": None, "max": None}
    return {"avg": sum(prices) / len(prices), "min": min(prices), "max": max(prices)}


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f} EUR/MWh"


def entsoe_prices_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Transform an ``entsoe_day_ahead_prices`` result into A2UI messages.

    Returns ``None`` for an error payload or a shape we don't recognise, so the
    emitter renders nothing rather than an empty/confusing tab.
    """
    if not isinstance(typed_result, dict) or typed_result.get("error"):
        return None
    rows = typed_result.get("rows")
    if not isinstance(rows, list) or not rows:
        return None

    zone = str(typed_result.get("bidding_zone") or "").strip() or "—"
    start = str(typed_result.get("start_date") or "").strip()
    end = str(typed_result.get("end_date") or "").strip()
    stats = _stats(rows)

    components: list[dict[str, Any]] = []
    seq = 0

    def _add(comp: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        comp["id"] = comp.get("id") or f"px-{seq}"
        components.append(comp)
        return comp["id"]

    title = f"{zone} day-ahead prices"
    range_label = f"{start} → {end}" if start and end else ""
    heading_id = _add({"component": "Text", "text": title, "variant": "h4"})
    lines = [f"{len(rows)} hourly prices"]
    if range_label:
        lines.append(range_label)
    lines += [
        f"Average {_fmt(stats['avg'])}",
        f"Low {_fmt(stats['min'])}",
        f"High {_fmt(stats['max'])}",
    ]
    line_ids = [_add({"component": "Text", "text": t}) for t in lines]
    list_id = _add({"component": "List", "children": line_ids})
    col_id = _add({"component": "Column", "children": [heading_id, list_id]})
    card_id = _add({"component": "Card", "child": col_id})
    components.append({"id": "root", "component": "Column", "children": [card_id]})

    return [
        {"version": "v0.9", "createSurface": {"surfaceId": ENTSOE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": ENTSOE_SURFACE_ID, "components": components}},
        # The full series for a rich tab to chart. Kept separate from the
        # component tree so the summary stays legible in any generic render.
        {
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": ENTSOE_SURFACE_ID,
                "value": {
                    # Declared series envelope (v6.12.0 M1) — any dataset-shaped tool
                    # can emit this shape so a future chart tab needs no per-tool
                    # knowledge. `y` is a list so the same surface later charts
                    # load/solar/wind columns from the same tables with no new
                    # component. See docs/design/v6.12.0/market-prices-workspace.md
                    # ("The shape: a declared series surface").
                    "kind": "series",
                    "title": title,
                    "x": {"key": "ts", "label": "Time", "type": "time"},
                    "y": [{"key": "price_eur_mwh", "label": "Price", "unit": "EUR/MWh"}],
                    "rows": rows,
                    "stats": stats,
                    "sourceUri": typed_result.get("source_uri"),
                    # Kept for the citation chip's rendered date range (unchanged).
                    "biddingZone": zone,
                    "startDate": start,
                    "endDate": end,
                    "rowCount": len(rows),
                },
            },
        },
    ]


# Anything outside [a-z0-9_.-] collapses to "_" so a zone code (IT_NORD,
# DK1) or an ISO date survives intact while spaces/slashes/quotes can't produce
# a surfaceId the client can't key on.
_UNSAFE = re.compile(r"[^a-z0-9_.-]+")


def _slug(value: Any) -> str:
    """Lowercase, safe surfaceId fragment. Empty/None → ""."""
    return _UNSAFE.sub("_", str(value or "").strip().lower()).strip("_")


def _query_identity(typed_result: Any) -> tuple[str, str, str]:
    """(zone, start, end) as raw strings — the identity of one price query."""
    if not isinstance(typed_result, dict):
        return "", "", ""
    return (
        str(typed_result.get("bidding_zone") or "").strip(),
        str(typed_result.get("start_date") or "").strip(),
        str(typed_result.get("end_date") or "").strip(),
    )


def _entsoe_surface(typed_result: Any) -> str:
    """Per-query surfaceId — ``entsoe_prices:dk1:2026-06-01:2026-06-07``.

    STABLE for the same query (re-running the identical query updates that tab in
    place — no duplicate tabs), distinct across zones and date ranges (DK1 vs DK2
    compare side by side, and a new query auto-focuses its own tab because the
    surface is new).

    Degrades rather than crashes: no zone → the base id (a date range alone is
    not an identity); zone but no dates → ``entsoe_prices:dk1``.
    """
    zone, start, end = _query_identity(typed_result)
    zone_slug = _slug(zone)
    if not zone_slug:
        return ENTSOE_SURFACE_ID
    parts = [ENTSOE_SURFACE_ID, zone_slug]
    start_slug, end_slug = _slug(start), _slug(end)
    if start_slug and end_slug:
        parts += [start_slug, end_slug]
    return ":".join(parts)


def _entsoe_artifact(typed_result: Any) -> dict[str, Any]:
    """Workbench tab + Home-index metadata (7.5) for one price query.

    The title must DISTINGUISH the tabs now that each query gets its own surface
    — "DK1 prices" twice is useless in the Workspace/Home index. Friendly names
    only (CLAUDE.md #9): zone code + rendered date range, never the surfaceId.
    """
    zone, start, end = _query_identity(typed_result)
    title = f"{zone} prices" if zone else "Market prices"
    if start and end:
        title = f"{title} · {start} → {end}"
    return {
        # ChatShell maps the workbench tab on kind — must stay "prices".
        "kind": "prices",
        "title": title,
        "description": "ENTSO-E day-ahead prices from BigQuery",
    }


register(
    entsoe_prices_to_a2ui,
    tool_names=[ENTSOE_TOOL],
    name="entsoe-prices",
    surface=_entsoe_surface,
    artifact_meta=_entsoe_artifact,
)
