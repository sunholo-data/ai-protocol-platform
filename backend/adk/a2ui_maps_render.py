"""Result → A2UI mapping for Google Maps Grounding Lite (v6.23.0 MAPS-GROUNDING).

Renders the Maps attribution links every Grounding Lite tool returns into the
existing Sources artefact tab.

**This mapping is a LICENCE CONDITION, not a presentation choice.** Grounding
Lite's terms require that Google Maps sources *immediately follow the generated
content they support* and be *viewable within one user interaction*, linking via
the URL the response supplies. Without this transform the agent would narrate
place names and drive times with no attribution anywhere on screen, which is a
breach — so a Maps-enabled skill must not ship without it. See
``docs/design/v6.23.0/maps-grounding.md`` M4.

Why it reuses ``kind: "sources"`` rather than adding a Maps tab: the payload's
``attribution`` object is ``{title, url}``, which is exactly the ``{title, uri}``
shape ``SourcesArtefactTab`` already renders as clean clickable links. Reusing it
discharges the obligation with zero frontend work and no bespoke React per tool
(CLAUDE.md #7). A map view would need a real map component — deliberately
deferred; see the design doc's rendering options.

**Attribution titles are passed through VERBATIM.** Grounding Lite returns
titles like ``"SolarCentric B.V. - Google Maps"``, and Google's attribution
guidelines forbid altering the "Google Maps" wording, capitalisation or
localisation. Stripping the suffix to make the list prettier would remove the
very attribution the licence requires. Do not "clean up" these strings.

Shapes captured live against the deployed dev key on 2026-08-12 — attribution
appears at a DIFFERENT depth per tool:

  search_places   {"places":  [{..., "attribution": {...}}], "summary": "..."}
  compute_routes  {"routes":  [{..., "attribution": {...}}]}
  lookup_weather  {"temperature": {...}, ..., "attribution": {...}}   # top level

Rather than encode three field paths (and guess at ``resolve_names`` /
``resolve_maps_urls``, which are undocumented as MCP tools and may change), the
extractor walks the payload for any ``attribution`` object. That is robust to
nesting changes and covers all five tools with one rule.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from adk.a2ui_result_render import register
from adk.a2ui_sources_render import sources_to_a2ui

logger = logging.getLogger(__name__)

# Every tool the Maps Grounding Lite MCP server exposes. The docs advertise the
# first three; a live tools/list on 2026-08-12 also returned resolve_names and
# resolve_maps_urls. All are listed so that (a) attribution renders whichever one
# the agent picks, and (b) every one of them is marked render-payload and so is
# never offloaded to an artifact by `_handle_large_output` — an offloaded result
# would strand the attribution render, i.e. silently turn a licence condition off.
MAPS_TOOLS = [
    "search_places",
    "compute_routes",
    "lookup_weather",
    "resolve_names",
    "resolve_maps_urls",
]

# Its own surface, NOT the web-search `web_sources` one: a turn may use both web
# search and Maps, and sharing a surfaceId would make whichever landed second
# overwrite the other's citations — dropping attribution that the licence
# requires be on screen.
MAPS_SOURCES_SURFACE_ID = "maps_sources"

# Depth cap for the recursive walk. Weather payloads nest several levels
# (forecast → intervals → conditions); this is well clear of that while making a
# pathological or cyclic structure impossible to hang on.
_MAX_DEPTH = 12

# Session-scoped accumulator of everything cited so far. UNPREFIXED on purpose:
# ADK's `app:` prefix is one global odometer shared by every session (issue #38),
# and `user:` would leak one chat's citations into another. Session scope is
# exactly right for "sources cited in this conversation".
_STATE_KEY = "maps_attribution_sources"

# Ceiling on the accumulated list. A long conversation could otherwise grow the
# Sources tab without bound and re-send the whole list on every Maps call.
_MAX_ACCUMULATED = 30


def _merge_with_session(fresh: list[dict[str, str]], tool_context: Any) -> list[dict[str, str]]:
    """Accumulate ``fresh`` onto the session's running citation list.

    WHY THIS EXISTS — found by a real delegated run on dev (2026-08-12), not by a
    unit test. One turn asked for a drive time AND the weather, so maps-assistant
    called ``compute_routes`` then ``lookup_weather``. Both render to the SAME
    ``maps_sources`` surface, so the second ``updateDataModel`` replaced the
    first: the weather citation appeared and **the route's Google Maps link
    silently vanished**, while the route answer stayed on screen. That is
    precisely the unattributed-output state the licence forbids, and nothing
    would have gone red.

    Accumulating (rather than giving each call its own surface) keeps one
    "Google Maps" tab per conversation instead of a tab per tool call.

    Fail-open: any state error falls back to ``fresh``, so a state bug degrades
    to the old replace-behaviour rather than dropping the render entirely.
    """
    if tool_context is None:
        return fresh
    state = getattr(tool_context, "state", None)
    if state is None:
        return fresh
    try:
        prior = state.get(_STATE_KEY) or []
        if not isinstance(prior, list):
            prior = []
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in [*prior, *fresh]:
            if not isinstance(item, dict):
                continue
            key = (item.get("uri") or "").strip() or (item.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append({"title": item.get("title") or "", "uri": item.get("uri") or ""})
        # Keep the MOST RECENT on overflow: a citation belongs with the answer
        # still on screen, and the oldest are furthest scrolled away.
        merged = merged[-_MAX_ACCUMULATED:]
        state[_STATE_KEY] = merged
        return merged
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("maps attribution: session merge failed (%s); using this call's sources only", exc)
        return fresh


def _payload_from_mcp(typed_result: Any) -> Any | None:
    """Unwrap the MCP ``CallToolResult`` envelope into the parsed JSON payload.

    Returns ``None`` for an error envelope or an unrecognised shape. Mirrors
    ``a2ui_bigquery_render._rows_from_mcp``, but Maps returns a single JSON
    object per call rather than one row-object per content item.
    """
    if not isinstance(typed_result, dict) or typed_result.get("isError"):
        return None
    content = typed_result.get("content")
    if not isinstance(content, list):
        return None

    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            # A non-JSON text item is a human-readable error notice (the server
            # returns those for a malformed argument), not a payload.
            continue
    return None


def _collect_attributions(node: Any, *, depth: int = 0) -> list[dict[str, str]]:
    """Walk ``node`` collecting every ``attribution: {title, url}`` it contains.

    Returns ``[{title, uri}]`` in encounter order, deduped on uri-else-title so a
    payload citing one place twice yields one link. Titles are NOT modified —
    see the module docstring.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def _walk(value: Any, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(value, list):
            for item in value:
                _walk(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        attribution = value.get("attribution")
        if isinstance(attribution, dict):
            title = (attribution.get("title") or "").strip()
            uri = (attribution.get("url") or "").strip()
            if title or uri:
                key = uri or title
                if key not in seen:
                    seen.add(key)
                    found.append({"title": title, "uri": uri})
        for key, child in value.items():
            # Don't descend into the attribution object itself — already handled,
            # and it holds no nested attributions.
            if key != "attribution":
                _walk(child, depth + 1)

    _walk(node, depth)
    return found


def maps_attribution_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Result→A2UI transform: Maps attribution links → Sources card.

    Returns ``None`` when the payload carries no attribution — an empty tab would
    be worse than none. Note that a ``None`` here means nothing renders, so if a
    future payload stops carrying ``attribution`` this fails CLOSED (no
    attribution shown) rather than open. That is the right failure direction for
    a licence condition only insofar as it is loud: the accompanying test asserts
    the real captured shapes still yield links, so a silent format change breaks
    CI rather than shipping an unattributed answer.
    """
    payload = _payload_from_mcp(typed_result)
    if payload is None:
        return None
    fresh = _collect_attributions(payload)
    if not fresh:
        return None
    # Accumulate — a second Maps call in the same turn must not erase the first
    # call's citation. See _merge_with_session.
    sources = _merge_with_session(fresh, tool_context)

    messages = sources_to_a2ui(sources, surface_id=MAPS_SOURCES_SURFACE_ID, title="Google Maps")
    # SourcesArtefactTab reads dataModel["/"]["sources"] for the clickable list;
    # the component tree above is the generic-mount fallback.
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {"surfaceId": MAPS_SOURCES_SURFACE_ID, "value": {"sources": sources}},
        }
    )
    return messages


def _maps_artifact(typed_result: Any, tool_context: Any = None) -> dict[str, Any] | None:
    """Workbench tab + Home-index metadata (7.5).

    ``kind: "sources"`` routes to ``SourcesArtefactTab`` — see the module
    docstring for why that tab rather than a Maps-specific one.
    """
    payload = _payload_from_mcp(typed_result)
    fresh = _collect_attributions(payload) if payload is not None else []
    if not fresh:
        return None
    # The count must match what the tab actually shows, which after a second
    # Maps call is the ACCUMULATED list, not just this call's. render_for_emit
    # runs the transform before resolve_artifact, so the merged list is already
    # in session state by now; re-reading it is what keeps the two in step. If
    # that ordering ever changes this degrades to under-counting, never to a
    # wrong render — the tab's contents come from the transform either way.
    count = len(fresh)
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is not None:
        try:
            accumulated = state.get(_STATE_KEY)
            if isinstance(accumulated, list) and accumulated:
                count = len(accumulated)
        except Exception:  # pragma: no cover - defensive
            pass
    return {
        "kind": "sources",
        "title": "Google Maps",
        "description": f"{count} Google Maps source{'' if count == 1 else 's'} cited in this conversation",
    }


register(
    maps_attribution_to_a2ui,
    tool_names=MAPS_TOOLS,
    name="maps-attribution",
    surface=MAPS_SOURCES_SURFACE_ID,
    artifact_meta=_maps_artifact,
)
