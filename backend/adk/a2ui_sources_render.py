"""Sources → A2UI result render (v6.11.0 workbench-home-and-curated-activity).

Web / enterprise search sub-agents return free TEXT, but their grounding chunks
are propagated to the parent as ``tool_context.state['temp:_adk_grounding_metadata']``
(``AgentTool(propagate_grounding_metadata=True)`` — see agent_tool.py). This
module registers a result→A2UI mapping on the PROVEN 7.3 path
([a2ui_result_render](a2ui_result_render.py)) so a search answer gets its own
**Sources** workbench tab + a row in the Workspace/Home index — the same
mechanism clause/comparison/obligation results use. No bespoke emission, no
sub-agent tracker calls (those hit the known "digest never renders" trap because
the search sub-agent runs in a separate Runner without the per-request tracker).

Pure functions — server-side, unit-testable, CLI-previewable.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import unquote

from adk.a2ui_result_render import BASIC_CATALOG_ID, register

logger = logging.getLogger(__name__)

# The search-sources artifact surface — one "Sources" tab, latest answer's
# sources win (stable id, like ``ppa_comparison``).
WEB_SOURCES_SURFACE_ID = "web_sources"

# ADK stashes the sub-agent's grounding metadata here for the parent turn.
_GROUNDING_STATE_KEY = "temp:_adk_grounding_metadata"

# v6.15.0 search-sources-openable-documents: enterprise (Vertex AI Search)
# grounding cites gs:// URIs into the datastore's SOURCE bucket. That datastore
# (aitana3) indexes the PROD llmops bucket, so its URIs are prod even on dev.
# When this env var is set (dev/test), we rewrite the bucket component of every
# gs:// source URI to the local env's llmops bucket — the docs are duplicated at
# IDENTICAL object paths — so import/preview reads the env's own bucket (a
# same-project grant) instead of the prod bucket (which dev must never read).
# Unset in prod → URIs pass through untouched. See design doc v6.15.0.
_SOURCE_BUCKET_OVERRIDE_ENV = "AI_SEARCH_SOURCE_BUCKET_OVERRIDE"

_GS_PREFIX = "gs://"

# --- Phase B (v6.15.0): resolve a Vertex Search document resource name ----------
# A grounding chunk for a datastore doc with no metadata title/link surfaces only
# its resource name (projects/.../documents/{id}). We look the document up via the
# Discovery Engine documents.get API to recover the real source URI (usually gs://,
# which then flows through the gcs enrichment below and becomes openable) + title.
#
# Best-effort + cached: any failure (IAM, network, missing doc, library) falls back
# to the Phase A snippet label — never breaks the sources render. Kill-switch:
# AI_SEARCH_RESOLVE_SOURCES=0. Requires discoveryengine.documents.get on the search
# project for the backend SA (discoveryengine.viewer covers it — same role the
# search itself needs).
_RESOLVE_ENV = "AI_SEARCH_RESOLVE_SOURCES"
_RESOLVE_CACHE_TTL = 3600.0  # datastore docs are stable — cache resolutions for an hour
_RESOLVE_MAX_PER_CALL = 10  # cap documents.get calls per sources render (latency guard)
_resolve_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}
_de_clients: dict[str, Any] = {}


def clear_resolution_cache() -> None:
    """Drop the document-resolution cache (tests)."""
    _resolve_cache.clear()


def _resolution_enabled() -> bool:
    return os.environ.get(_RESOLVE_ENV, "1").strip().lower() not in ("0", "false", "off")


def _location_from_resource_name(name: str) -> str:
    """Extract the datastore location (e.g. "eu") from a resource name; "global" default."""
    parts = name.split("/")
    try:
        return parts[parts.index("locations") + 1]
    except (ValueError, IndexError):
        return "global"


def _document_client(location: str) -> Any:
    """A cached DocumentServiceClient bound to the datastore's regional endpoint
    (eu/us are multi-region and need e.g. ``eu-discoveryengine.googleapis.com``;
    global uses the default)."""
    key = location or "global"
    if key not in _de_clients:
        from google.cloud import discoveryengine_v1 as de

        options = None if key == "global" else {"api_endpoint": f"{key}-discoveryengine.googleapis.com"}
        _de_clients[key] = de.DocumentServiceClient(client_options=options)
    return _de_clients[key]


def resolve_vertex_document(resource_name: str) -> tuple[str, str] | None:
    """Resolve a Vertex Search document resource name to ``(uri, title)``, or None.

    Best-effort and cached. ``uri`` is the document's source URI (often gs://);
    ``title`` is the datastore's derived title (may be empty). Returns None on any
    error so the caller falls back to the snippet label.
    """
    if not resource_name or not _resolution_enabled():
        return None
    now = time.time()
    hit = _resolve_cache.get(resource_name)
    if hit is not None and (now - hit[0]) < _RESOLVE_CACHE_TTL:
        return hit[1]

    result: tuple[str, str] | None = None
    try:
        client = _document_client(_location_from_resource_name(resource_name))
        doc = client.get_document(name=resource_name)
        uri = getattr(getattr(doc, "content", None), "uri", "") or ""
        try:
            derived = dict(doc.derived_struct_data) if doc.derived_struct_data else {}
        except Exception:
            derived = {}
        title = str(derived.get("title") or "")
        resolved_uri = uri or str(derived.get("link") or "")
        # Docs ingested as inline text have no content.uri and no derived title,
        # but their user-supplied struct_data carries the original `filename` — a
        # real document name, much better than a snippet preview as the label.
        if not title:
            try:
                struct = dict(doc.struct_data) if doc.struct_data else {}
            except Exception:
                struct = {}
            title = str(struct.get("filename") or struct.get("title") or "")
        if resolved_uri or title:
            result = (resolved_uri, title)
    except Exception as exc:
        logger.info(
            "ai_search: could not resolve document %s: %s", resource_name.rsplit("/", 1)[-1], type(exc).__name__
        )

    _resolve_cache[resource_name] = (now, result)
    return result


def remap_source_bucket(gs_uri: str) -> str:
    """Rewrite a gs:// URI's bucket to ``AI_SEARCH_SOURCE_BUCKET_OVERRIDE`` if set.

    No-op for non-gs:// URIs or when the override is unset (prod). Preserves the
    object path verbatim (including any percent-encoding). Idempotent.
    """
    override = os.environ.get(_SOURCE_BUCKET_OVERRIDE_ENV, "").strip()
    if not override or not gs_uri.startswith(_GS_PREFIX):
        return gs_uri
    _bucket, _, object_path = gs_uri[len(_GS_PREFIX) :].partition("/")
    return f"{_GS_PREFIX}{override}/{object_path}" if object_path else f"{_GS_PREFIX}{override}"


def _parse_gs_uri(gs_uri: str) -> tuple[str, str, str]:
    """Split a gs:// URI into ``(bucket, object, filename)``; ``("","","")`` otherwise.

    ``object`` and ``filename`` are URL-decoded — Vertex grounding URIs are
    percent-encoded (e.g. ``EU%20Regulation``), but import-by-reference and the
    Document tab want the real object path.
    """
    if not gs_uri.startswith(_GS_PREFIX):
        return "", "", ""
    bucket, _, obj = gs_uri[len(_GS_PREFIX) :].partition("/")
    obj = unquote(obj)
    filename = obj.rsplit("/", 1)[-1] if obj else ""
    return bucket, obj, filename


_SNIPPET_LABEL_MAX = 80


def _snippet_label(snippet: str) -> str:
    """A short, readable label from a grounding snippet — the first line, trimmed
    to ``_SNIPPET_LABEL_MAX`` chars with an ellipsis. Empty for an empty snippet.
    Used to label an enterprise-search source whose datastore doc has no title."""
    first_line = (snippet or "").strip().splitlines()[0].strip() if snippet.strip() else ""
    if len(first_line) <= _SNIPPET_LABEL_MAX:
        return first_line
    return first_line[: _SNIPPET_LABEL_MAX - 1].rstrip() + "…"


def sources_from_grounding(grounding_metadata: Any) -> list[dict[str, str]]:
    """Extract a deduped source list from grounding metadata.

    Handles both grounding shapes ADK/Gemini emit (google_search ``chunk.web`` and
    Vertex AI Search ``chunk.retrieved_context``). De-duplicates by URI (falling
    back to title), preserves first-seen order. Never raises.

    Each source is ``{title, uri, kind, bucket, object, filename}``:
      - ``kind`` is ``"gcs"`` for a gs:// (enterprise datastore) source, else ``"web"``.
      - For gcs sources the gs:// bucket is remapped to the local env's llmops
        bucket (``remap_source_bucket``) and split into ``bucket``/``object``/
        ``filename`` so the frontend can open it via import-by-reference. ``title``
        falls back to the filename when the datastore gives none.
      - web sources leave ``bucket``/``object``/``filename`` empty.
    """
    if grounding_metadata is None:
        return []
    chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    resolves = 0  # documents.get calls this render (capped for latency)
    for chunk in chunks:
        src = getattr(chunk, "web", None) or getattr(chunk, "retrieved_context", None)
        if src is None:
            continue
        uri = getattr(src, "uri", None) or ""
        # NOTE: document_name is a Vertex resource name (projects/.../documents/{id}),
        # NOT a human title — keep it out of the visible title (it used to leak as
        # the label for datastore docs with no metadata title/link). The retrieved
        # `text` is the grounding snippet the answer was built on; capture it so a
        # link-less source is still useful.
        title = getattr(src, "title", None) or getattr(src, "domain", None) or ""
        document_name = getattr(src, "document_name", None) or ""
        snippet = (getattr(src, "text", None) or "").strip()
        # Phase B: a datastore doc with no gs:// uri — resolve its resource name to
        # the real source uri (+ title) so it becomes openable. Best-effort; the
        # snippet-label fallback below still runs if it can't be resolved.
        if not uri and document_name and resolves < _RESOLVE_MAX_PER_CALL:
            resolves += 1
            resolved = resolve_vertex_document(document_name)
            if resolved:
                r_uri, r_title = resolved
                uri = uri or r_uri  # gs:// → enriched to gcs (openable) below
                title = title or r_title
        # Phase A fallback: still no title/uri → label from the retrieved snippet
        # (else a generic label), instead of surfacing the raw resource name.
        if not uri and not title and document_name:
            title = _snippet_label(snippet) or "Knowledge-base result"
        if not uri and not title:
            continue
        if uri.startswith(_GS_PREFIX):
            uri = remap_source_bucket(uri)
            bucket, obj, filename = _parse_gs_uri(uri)
            entry = {
                "title": title or filename,
                "uri": uri,
                "kind": "gcs",
                "bucket": bucket,
                "object": obj,
                "filename": filename,
                "snippet": snippet,
            }
        else:
            entry = {
                "title": title,
                "uri": uri,
                "kind": "web",
                "bucket": "",
                "object": "",
                "filename": "",
                "snippet": snippet,
            }
        # Dedup by the doc's stable identity — uri, else the datastore resource
        # name (so two link-less datastore docs don't collapse into one), else title.
        key = uri or document_name or title
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def sources_to_a2ui(
    sources: list[dict[str, Any]],
    *,
    surface_id: str = WEB_SOURCES_SURFACE_ID,
    title: str = "Sources",
) -> list[dict[str, Any]]:
    """Build A2UI v0.9 Basic-catalog messages for a ``[{title, uri}]`` list.

    Renders a titled ``Card`` of Markdown-link ``Text`` rows (Basic v0.9 has no
    Link component; ``@a2ui/react`` Text resolves the Markdown). Returns an empty
    list when there is nothing citable.
    """
    seen: set[str] = set()
    items: list[dict[str, str]] = []
    for src in sources or []:
        uri = (src.get("uri") or "").strip()
        label = (src.get("title") or "").strip() or uri
        if not uri and not label:
            continue
        key = uri or label
        if key in seen:
            continue
        seen.add(key)
        items.append({"uri": uri, "label": label})
    if not items:
        return []

    components: list[dict[str, Any]] = []
    seq = 0

    def _add(comp: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        comp["id"] = comp.get("id") or f"src-{seq}"
        components.append(comp)
        return comp["id"]

    # Plain-domain Text only — the Basic catalog Text renders no markdown/links,
    # so `[label](url)` shows as raw text. The workbench renders the rich,
    # clickable card from the data model (below) via `SourcesArtefactTab`; this
    # component tree is just a clean fallback for any generic A2uiSurface render.
    heading_id = _add({"component": "Text", "text": title, "variant": "h4"})
    item_ids = [_add({"component": "Text", "text": it["label"]}) for it in items]
    list_id = _add({"component": "List", "children": item_ids})
    col_id = _add({"component": "Column", "children": [heading_id, list_id]})
    card_id = _add({"component": "Card", "child": col_id})
    components.append({"id": "root", "component": "Column", "children": [card_id]})

    return [
        {"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}},
    ]


def web_search_sources_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Result→A2UI transform for the web/enterprise search sub-agents.

    Reads the sub-agent's grounding metadata from ``tool_context.state`` (the
    tool result itself is free text and carries no chunks) and renders the
    Sources card. Returns ``None`` when there is no context or no citable source
    (the emitter then emits nothing — no empty Sources tab).
    """
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if state is None:
        return None
    try:
        grounding = state.get(_GROUNDING_STATE_KEY)
    except Exception:
        return None
    sources = sources_from_grounding(grounding)
    if not sources:
        return None
    messages = sources_to_a2ui(sources, surface_id=WEB_SOURCES_SURFACE_ID)
    # Stash the raw sources in the surface data model — the workbench
    # `SourcesArtefactTab` reads `dataModel["/"]["sources"]` to render clean,
    # clickable domain links (the redirect URI behind the domain title).
    messages.append(
        {
            "version": "v0.9",
            "updateDataModel": {"surfaceId": WEB_SOURCES_SURFACE_ID, "value": {"sources": sources}},
        }
    )
    return messages


def _sources_artifact(_typed_result: Any) -> dict[str, Any]:
    """Workbench tab + Home-index metadata (7.5) for the Sources surface."""
    return {"kind": "sources", "title": "Sources", "description": "Web pages cited in this answer"}


# Register on the proven 7.3 path: the main agent's after_tool_callback
# (make_a2ui_result_emitter) runs this when the search AgentTool returns, with
# the tracker bound — so the Sources surface actually reaches the wire.
register(
    web_search_sources_to_a2ui,
    tool_names=["web_search_agent", "enterprise_search_agent"],
    name="web-search-sources",
    surface=WEB_SOURCES_SURFACE_ID,
    artifact_meta=_sources_artifact,
)
