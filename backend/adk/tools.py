"""ADK FunctionTool registry — maps skill-config tool names to callables.

Model-aware routing:
  - Gemini agents receive ADK built-in tools (VertexAiSearchTool, GoogleSearchTool,
    UrlContextTool) added directly, not via this registry.
  - Claude/OpenAI agents receive AgentTool wrappers created in agent.py.
  - Document tools (list_documents, get_document_content) are the same for all models.
  - Stubs remain for tools not yet ported (code_execution, user_history).

Tools ported in sprint TOOLS-PORTING:
  - list_documents / get_document_content (M1)
  - ai_search / google_search / url_processing (M2, model-aware in agent.py)
  - structured_extraction (M3, registered as after_agent callback, not here)
  - code_execution (M4, model-aware in agent.py)
  - mcp (M5, loaded via mcp/registry.py)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from google.adk.tools import FunctionTool, ToolContext

from db.firestore import query_documents
from tools.documents.context import build_document_context
from tools.url_processing import url_processing
from tools.workshop_docs import search_workshop_docs

logger = logging.getLogger(__name__)

_PARSED_DOCS_COLLECTION = "parsed_documents"


# --- Document tools ---


def _fmt_upload_date(value: object) -> str:
    """Best-effort YYYY-MM-DD from a Firestore ``createdAt`` (datetime or ISO
    string). Returns "" for anything unparseable — the date is a friendly
    disambiguator, never load-bearing."""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return ""
    s = str(value)
    return s[:10] if len(s) >= 10 and s[4:5] == "-" else ""


async def list_documents(
    skill_id: str | None = None,
    limit: int = 20,
    tool_context: ToolContext = None,
) -> str:
    """List parsed documents available in the workspace.

    Args:
        skill_id: Optional skill ID to filter documents by. Omit to list all your documents.
        limit: Maximum number of documents to return (default 20, max 50).

    Returns:
        A formatted list of document names, IDs, and status.
    """
    user_id = None
    if tool_context is not None:
        user_id = tool_context.state.get("user:id") or tool_context.state.get("user_id")

    filters: list[tuple[str, str, object]] = []
    if user_id:
        filters.append(("userId", "==", user_id))
    if skill_id:
        filters.append(("skillId", "==", skill_id))
    filters.append(("status", "==", "parsed"))

    effective_limit = min(int(limit), 50)

    try:
        docs = await asyncio.to_thread(
            query_documents,
            collection=_PARSED_DOCS_COLLECTION,
            filters=filters,
            order_by="createdAt",
            order_direction="DESCENDING",
            limit=effective_limit,
        )
    except Exception as exc:
        logger.warning("list_documents: Firestore query failed: %s", exc)
        return f"Could not retrieve documents: {exc}"

    if not docs:
        return "No documents found in the workspace."

    # Present documents by a FRIENDLY, human label — lead with the filename and
    # its upload date (the natural disambiguator when two files share a name).
    # The opaque [ref: ...] id is for YOUR tool calls only; never echo raw ids to
    # the user (that was the "documentIds need friendly names" feedback).
    lines = [
        f"Found {len(docs)} document(s). Refer to them by name (+ upload date to "
        "disambiguate duplicates); do NOT show the [ref] ids to the user:\n"
    ]
    for doc in docs:
        doc_id = doc.get("__id", "?")
        filename = doc.get("originalFilename", "Unknown")
        status = doc.get("status", "unknown")
        fmt = doc.get("sourceFormat", "")
        summary = doc.get("summary") or {}
        blocks_count = summary.get("totalBlocks", 0)
        uploaded = _fmt_upload_date(doc.get("createdAt"))
        parts = [
            p
            for p in (
                fmt or None,
                (f"{blocks_count} blocks" if blocks_count else None),
                (f"uploaded {uploaded}" if uploaded else None),
                (status if status != "parsed" else None),
            )
            if p
        ]
        detail = f" — {', '.join(parts)}" if parts else ""
        lines.append(f"- {filename}{detail} [ref: {doc_id}]")

    return "\n".join(lines)


async def get_document_content(
    doc_id: str,
    section: str | None = None,
    mode: str = "markdown",
    tool_context: ToolContext = None,
) -> str:
    """Get content of a parsed document.

    Args:
        doc_id: The document ID from list_documents.
        section: Optional section heading to extract (case-insensitive substring). Omit for full document.
        mode: Output format — "markdown" for reading/chat (default), "blocks" for extraction tasks
              where table structure and tracked changes must be preserved exactly.

    Returns:
        Document content as markdown, or JSON blocks string when mode="blocks".
    """
    try:
        content, blocks = await asyncio.to_thread(build_document_context, doc_id, mode, section)
    except KeyError:
        return f"Document '{doc_id}' not found. Use list_documents to see available documents."
    except Exception as exc:
        logger.warning("get_document_content failed for %s: %s", doc_id, exc)
        return f"Could not load document '{doc_id}': {exc}"

    if mode == "blocks" and blocks is not None and tool_context is not None:
        # Populate session state so structured_extraction_callback can consume blocks
        tool_context.state["temp:document_blocks"] = json.dumps(blocks, ensure_ascii=False)
        tool_context.state["temp:document_id"] = doc_id

    return content


# --- Registry ---
# tool name → factory function(config dict) → FunctionTool
# Model-aware tools (ai_search, google_search, code_execution) are resolved
# directly in agent.py's create_agent() based on the skill's model.
# MCP tools are loaded via tools/mcp/registry.py and returned as McpToolset.


def _extract_ppa_clauses_factory(_config: dict) -> FunctionTool:
    """Lazy import — extract_ppa_clauses pulls in google-genai which is slow."""
    from tools.extract_ppa_clauses import extract_ppa_clauses

    return FunctionTool(extract_ppa_clauses)


def _compare_ppa_contracts_factory(_config: dict) -> FunctionTool:
    """Lazy import — compare_ppa_contracts pulls in extract_ppa_clauses transitively."""
    from tools.compare_ppa_contracts import compare_ppa_contracts

    return FunctionTool(compare_ppa_contracts)


def _map_ppa_obligations_factory(_config: dict) -> FunctionTool:
    """Lazy import — map_ppa_obligations pulls in google-genai transitively."""
    from tools.map_ppa_obligations import map_ppa_obligations

    return FunctionTool(map_ppa_obligations)


def _entsoe_day_ahead_prices_factory(_config: dict) -> FunctionTool:
    """Lazy import — entsoe_query pulls in google-cloud-bigquery which is slow."""
    from tools.entsoe_query import entsoe_day_ahead_prices

    return FunctionTool(entsoe_day_ahead_prices)


async def list_bucket_documents(bucket_uri: str, prefix: str = "") -> list[dict]:
    """List PPA contracts (or any documents) in a GCS bucket the agent can read.

    Use this BEFORE extract_ppa_clauses or compare_ppa_contracts when the
    user names a bucket / folder rather than a specific document. Returns
    the list of objects so you can pick the right one(s) by filename, then
    pass their gs:// URLs to the downstream tools without needing to
    upload anything first.

    Args:
        bucket_uri: GCS bucket URI, e.g. `gs://multivac-acme-energy-bucket`
            or `gs://multivac-acme-energy-bucket/PPAs/longform`. The
            optional path suffix narrows the listing.
        prefix: Additional path prefix appended to bucket_uri's prefix.
            Empty for top-level.

    Returns:
        List of dicts with keys: name (object path), size (bytes),
        mimeType, timeCreated (ISO 8601). Capped at ~100 entries —
        use a more specific prefix if you need to narrow further.
        Empty list when the SA cannot read the bucket or the bucket
        doesn't exist (logged WARNING; agent should ask the user for
        a different bucket).
    """
    from tools.org_documents import list_documents_in_bucket

    return await list_documents_in_bucket(bucket_uri, prefix=prefix)


TOOL_REGISTRY: dict[str, Callable[[dict], FunctionTool]] = {
    "list_documents": lambda _config: FunctionTool(list_documents),
    "get_document_content": lambda _config: FunctionTool(get_document_content),
    "url_processing": lambda _config: FunctionTool(url_processing),
    "search_workshop_docs": lambda _config: FunctionTool(search_workshop_docs),
    # v6.4.0 ONE-DEMO M2: typed PPA clause extraction with block_id citations.
    # Lazy-imported so non-PPA skills don't pay the google-genai import cost.
    "extract_ppa_clauses": _extract_ppa_clauses_factory,
    # v6.4.0 ONE-DEMO M3: pairwise PPA comparison with commercial reasoning.
    # Reuses extract_ppa_clauses internally — same lazy pattern.
    "compare_ppa_contracts": _compare_ppa_contracts_factory,
    # v6.7.0 PPA-OBLIGATION M2: extraction → deontic engine wire JSON for the
    # Obligation Analysis artefact. Reuses extract_ppa_clauses internally.
    "map_ppa_obligations": _map_ppa_obligations_factory,
    # v6.4.0 ONE-DEMO M2 (deferred unblock): read-only BigQuery into ONE's
    # ENTSO-E day-ahead price history. sa-platform gets bigquery.dataViewer
    # on your-entsoe-project via Sunholo-org IAM grant (drift; track tf follow-up).
    "entsoe_day_ahead_prices": _entsoe_day_ahead_prices_factory,
    # v6.4.0 ONE-DEMO post-M3: bucket-discovery entry point so the agent
    # can find PPAs in the tenant bucket and feed gs:// URLs to
    # extract_ppa_clauses / compare_ppa_contracts without an upload step.
    "list_bucket_documents": lambda _config: FunctionTool(list_bucket_documents),
}

# Tools handled entirely outside this registry (no ValueError for these)
_MODEL_AWARE = {"ai_search", "google_search", "code_execution"}
# structured_extraction runs as an after_agent callback in agent.py, not as a FunctionTool
_SKIP = {"structured_extraction"}
_MCP_TOOL = "mcp"


# --- User-facing tool catalog ---
# Powers GET /api/tools and the Skill Studio tool picker. Each entry is a
# human-selectable capability: a short `label` + one-line `description` a
# non-technical author can understand, so the picker is a checklist rather
# than a free-text list of internal tool names. `model_aware` tools
# (ai_search / google_search / code_execution) are wired in agent.py per
# model rather than via TOOL_REGISTRY, but are still selectable here.
#
# Keep this in sync with TOOL_REGISTRY + _MODEL_AWARE. The api_tests
# guard (test_tools_route) asserts every catalog `name` is a known,
# resolvable tool so a rename can't silently orphan a picker entry.
TOOL_CATALOG: list[dict[str, str]] = [
    {
        "name": "ai_search",
        "label": "Knowledge search",
        "description": "Search the skill's indexed knowledge base for relevant passages.",
        "category": "Search",
    },
    {
        "name": "google_search",
        "label": "Web search",
        "description": "Look up current information on the public web via Google.",
        "category": "Search",
    },
    {
        "name": "url_processing",
        "label": "Read a web page",
        "description": "Fetch and read the content of a URL the user provides.",
        "category": "Search",
    },
    {
        "name": "list_documents",
        "label": "List documents",
        "description": "See the documents the user has uploaded to this workspace.",
        "category": "Documents",
    },
    {
        "name": "get_document_content",
        "label": "Read a document",
        "description": "Open and read the full text of an uploaded document.",
        "category": "Documents",
    },
    {
        "name": "list_bucket_documents",
        "label": "Browse a storage bucket",
        "description": "List files in a connected cloud storage bucket before reading them.",
        "category": "Documents",
    },
    {
        "name": "code_execution",
        "label": "Run code",
        "description": "Execute code to do calculations, data crunching, or charts.",
        "category": "Analysis",
    },
    {
        "name": "search_workshop_docs",
        "label": "Workshop docs",
        "description": "Search the built-in workshop / product documentation.",
        "category": "Analysis",
    },
    {
        "name": "extract_ppa_clauses",
        "label": "Extract PPA clauses",
        "description": "Pull structured clauses (with citations) from a power-purchase agreement.",
        "category": "Energy (PPA)",
    },
    {
        "name": "compare_ppa_contracts",
        "label": "Compare PPA contracts",
        "description": "Compare two power-purchase agreements side by side.",
        "category": "Energy (PPA)",
    },
    {
        "name": "map_ppa_obligations",
        "label": "Analyze PPA obligations",
        "description": "Map a power-purchase agreement into a verified obligation timeline and settlement model.",
        "category": "Energy (PPA)",
    },
    {
        "name": "entsoe_day_ahead_prices",
        "label": "Energy market prices",
        "description": "Look up ENTSO-E day-ahead electricity prices.",
        "category": "Energy (PPA)",
    },
]


def catalog_tool_names() -> set[str]:
    """Names in TOOL_CATALOG — the set of user-selectable tools."""
    return {entry["name"] for entry in TOOL_CATALOG}


def known_tool_names() -> set[str]:
    """Every tool name the resolver accepts (registry + model-aware + mcp)."""
    return set(TOOL_REGISTRY) | _MODEL_AWARE | {_MCP_TOOL}


def resolve_tools(tool_names: list[str], tool_configs: dict[str, dict]) -> list[FunctionTool]:
    """Resolve a list of tool names to FunctionTool instances.

    Model-aware tools (ai_search, google_search, code_execution) are wired
    separately in agent.py after model detection.
    MCP tools are loaded via tools/mcp/registry.get_mcp_tools() and appended.

    Args:
        tool_names: Tool names from SkillConfig.skill_metadata.tools.
        tool_configs: Per-tool config dict keyed by tool name.

    Returns:
        List of FunctionTool instances ready to pass into an ADK LlmAgent.

    Raises:
        ValueError: If a tool name is not model-aware, not "mcp", and not in
            TOOL_REGISTRY — prevents silent misconfiguration.
    """
    resolved: list[FunctionTool] = []
    for name in tool_names:
        if name in _MODEL_AWARE or name in _SKIP or name == _MCP_TOOL:
            continue
        factory = TOOL_REGISTRY.get(name)
        if factory is None:
            raise ValueError(
                f"Unknown tool {name!r} — not in TOOL_REGISTRY and not model-aware. "
                "Check the skill config or add the tool to TOOL_REGISTRY."
            )
        config = tool_configs.get(name, {})
        resolved.append(factory(config))
    return resolved


class McpServerResolutionError(RuntimeError):
    """Raised at agent-build time when a SKILL.md declares MCP servers
    that don't resolve to actual toolsets.

    G42 (template-mcp-strict-resolution.md): the pre-G42 ``resolve_mcp_tools``
    silently returned a partial list when some declared servers were
    missing from Firestore or had malformed configs. The agent built
    with fewer MCP tools than the SKILL.md asked for and silently
    misbehaved (called nothing, said "I can't help with that", etc.).
    This exception surfaces the misconfiguration at agent-build time
    with a diff so the operator can fix the seed script / SKILL.md
    instead of debugging an apparently-broken agent.
    """


def resolve_mcp_tools(tool_configs: dict[str, dict]) -> list:
    """Return McpToolset instances for any MCP servers listed in tool_configs.

    Called from agent.py when "mcp" appears in the skill's tool list.

    Args:
        tool_configs: Per-tool config dict; reads tool_configs["mcp"]["servers"].

    Returns:
        List of McpToolset instances (empty if no mcp config).

    Raises:
        McpServerResolutionError: if any server_id declared in
            ``tool_configs["mcp"]["servers"]`` failed to resolve to a
            toolset (G42). The exception message lists the missing IDs
            so the operator can fix the seed.
    """
    server_ids: list[str] = (tool_configs.get("mcp") or {}).get("servers", [])
    if not server_ids:
        return []
    from tools.mcp.registry import get_mcp_tools_with_status

    resolved, missing = get_mcp_tools_with_status(server_ids)
    if missing:
        # G42: fail-loud at agent-build time. The agent built with a
        # subset of declared servers can't fulfil the SKILL.md's
        # contract; the silent "skip and continue" mode masked many of
        # this kind of bug last quarter.
        raise McpServerResolutionError(
            f"SKILL.md declares {len(server_ids)} MCP server(s) "
            f"({sorted(server_ids)!r}) but only {len(resolved)} resolved. "
            f"Missing: {sorted(missing)!r}. "
            "Common causes: (1) the server doc doesn't exist in Firestore "
            "mcp_servers/ — re-run scripts/seed_mcp_servers.py for the "
            "target environment; (2) the Firestore doc is missing the "
            "'url' field; (3) the SKILL.md typoed the server name. "
            "See docs/design/template/template-mcp-strict-resolution.md."
        )
    return resolved
