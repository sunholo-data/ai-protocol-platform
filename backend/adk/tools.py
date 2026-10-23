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
from tools.browser import browse
from tools.documents.context import build_document_context
from tools.my_context import my_context
from tools.resident_agent import ask_assistant
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
    project: str = "",
    tool_context: ToolContext = None,
) -> str:
    """List parsed documents available in the workspace.

    Args:
        skill_id: Optional skill ID to filter documents by. Omit to list all your documents.
        limit: Maximum number of documents to return (default 20, max 50).
        project: Optional project name (or part of it) to list only that project's
            documents. Call list_projects to see the user's projects. In a chat
            that is in a project, only that project's documents can be listed:
            this argument is ignored there, and "all" does not widen it.

    Returns:
        A formatted list of document names, IDs, and status.
    """
    # Resolve the caller from the ADK INVOCATION first, state only as a fallback.
    #
    # CROSS-USER EXPOSURE (fixed 2026-08-25). This used to read `user:id` /
    # `user_id` from state alone — and adk/callbacks.py::_resolve_user_id already
    # documents that those keys "are NEVER populated by the request path (only
    # read)". So user_id was always None, the userId filter below was never
    # appended, and every caller got a listing of EVERY user's documents, whose
    # content they could then open by id.
    #
    # Measured on the test deployment: a customer's tester owned 2 parsed
    # documents and her sessions listed 14 of the 16 present, spanning another
    # tenant. The unit tests passed throughout because they set `user:id` in a
    # fixture — the one thing production never does.
    user_id = None
    if tool_context is not None:
        inv = getattr(tool_context, "_invocation_context", None)
        user_id = getattr(inv, "user_id", None) if inv is not None else None
        if not user_id:
            user_id = tool_context.state.get("user:id") or tool_context.state.get("user_id")

    if not user_id:
        # FAIL CLOSED. An unidentified caller gets nothing, never everything —
        # the alternative is handing one tenant another tenant's document list.
        logger.warning("list_documents: no caller uid resolved — refusing to list")
        return (
            "Could not identify the current user, so no documents can be listed. "
            "This is a bug — report it rather than working around it."
        )

    project = (project or "").strip()
    from adk.project_scope import current_project

    # Strict project chats (projects.md §6.3, Mark 2026-10-20): inside a
    # project only that project's documents can be listed, whatever `project`
    # says — "all" or another project's name used to be a way out.
    scoped = await asyncio.to_thread(current_project, tool_context)
    if scoped is not None:
        return await asyncio.to_thread(_list_found_project, user_id, scoped, min(int(limit), 50), True)
    if project.lower() in ("all", "*"):
        project = ""
    elif project:
        return await asyncio.to_thread(_list_project_documents, user_id, project, min(int(limit), 50))

    filters: list[tuple[str, str, object]] = [("userId", "==", user_id)]
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
        # NOT "no documents". This lists the PARSED INDEX; the tenant bucket can
        # hold thousands of files that have never been parsed. Saying "none" here
        # is how a present-but-unparsed contract becomes "unavailable" and the
        # agent falls back to a near-miss it CAN open (the 2026-08-09 bug).
        return (
            "No documents have been parsed into the workspace yet. That does NOT mean "
            "the user has no documents: the tenant bucket usually holds far more than "
            "is pre-parsed. Call list_bucket_documents to look there, then pass "
            "gs_url= to open a file directly — the document tools parse on the fly."
        )

    # Present documents by a FRIENDLY, human label — lead with the filename and
    # its upload date (the natural disambiguator when two files share a name).
    # The opaque [ref: ...] id is for YOUR tool calls only; never echo raw ids to
    # the user (that was the "documentIds need friendly names" feedback).
    # Scope disclosure is load-bearing, not politeness. This is the PARSED INDEX,
    # typically a few dozen files, while the tenant bucket holds the real library
    # (measured on test 2026-08-25: 14 parsed vs 152 longform PPAs in the bucket).
    # Announcing a bare "Found 14 document(s)" tells a well-behaved model those 14
    # are what exists — so a contract it was asked for, sitting unparsed in the
    # bucket, reads as absent and it settles for the nearest thing it can open.
    truncated = len(docs) >= effective_limit
    more = (
        f" This is the first {len(docs)} only — there are more; raise `limit` (max 50) or filter by name."
        if truncated
        else ""
    )
    lines = [
        f"Found {len(docs)} PARSED document(s) in the workspace.{more}\n"
        "This is ONLY the pre-parsed index, not the user's whole library — the tenant "
        "bucket normally holds many more. If what you need is not listed here, call "
        "list_bucket_documents and pass gs_url= to open it directly (the document "
        "tools parse on the fly); do not conclude it is unavailable, and never "
        "substitute a different document.\n"
        "Refer to them by name (+ upload date to disambiguate duplicates); do NOT "
        "show the [ref] ids to the user:\n"
    ]
    lines += [_document_line(doc) for doc in docs]
    return "\n".join(lines)


def _document_line(doc: dict) -> str:
    """One friendly listing line: filename, format, size, upload date, then the
    opaque ref the model uses and never shows (CLAUDE.md #9)."""
    doc_id = doc.get("__id") or doc.get("id") or "?"
    filename = doc.get("originalFilename", "Unknown")
    status = doc.get("status") or doc.get("parseStatus") or "unknown"
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
    return f"- {filename}{detail} [ref: {doc_id}]"


def _list_project_documents(user_id: str, project: str, limit: int) -> str:
    """``list_documents(project=…)``: the documents in one of the caller's
    projects, resolved by friendly name. An unknown name returns the caller's
    project names to choose from, never an empty list (NEVER SILENT #8)."""
    import db.projects as projects_db

    try:
        found, candidates = projects_db.resolve_project(user_id, project)
        if found is None:
            if not candidates:
                return f"The user has no projects, so there is no project called \u201c{project}\u201d."
            return (
                f"No single project matches \u201c{project}\u201d. The user's projects are: "
                f"{', '.join(candidates[:20])}. Ask which one they mean, or call again with its exact name."
            )
    except Exception as exc:
        logger.warning("list_documents(project=%r) failed: %s", project, exc)
        return f"Could not list that project's documents: {exc}"
    return _list_found_project(user_id, found, limit, False)


def _list_found_project(user_id: str, found: dict, limit: int, by_default: bool) -> str:
    import db.projects as projects_db

    try:
        docs, next_cursor = projects_db.list_project_documents(user_id, found["id"], page_size=limit)
    except Exception as exc:
        logger.warning("list_documents(project=%r) failed: %s", found.get("id"), exc)
        return f"Could not list that project's documents: {exc}"
    name = found.get("name") or "this project"
    if not docs:
        return (
            f"The project \u201c{name}\u201d has no documents yet. The user can add some by asking you "
            "(add_to_project) or by dragging documents onto the project in the sidebar."
            + (" This chat is in that project, so it can only use the project's documents." if by_default else "")
        )
    more = f" Showing the first {len(docs)}; raise `limit` (max 50) for more." if next_cursor else ""
    scope_note = (
        f"This chat is in the project \u201c{name}\u201d, so it can only use these documents. If the answer "
        "is not in them, say so and offer to add the right document (add_to_project); never look elsewhere. "
        if by_default
        else ""
    )
    header = (
        f"{scope_note}The project \u201c{name}\u201d has these documents.{more} Refer to them by name (+ upload "
        "date to disambiguate duplicates); do NOT show the [ref] ids to the user:\n"
    )
    return header + "\n".join(_document_line(doc) for doc in docs)


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
    from tools.documents.context import DocumentAccessError, resolve_caller_uid

    caller_uid = resolve_caller_uid(tool_context)
    try:
        content, blocks = await asyncio.to_thread(build_document_context, doc_id, mode, section, caller_uid=caller_uid)
    except DocumentAccessError:
        # Deliberately indistinguishable from "not found": telling the caller a
        # document EXISTS but is someone else's is itself a disclosure.
        logger.warning("get_document_content: access denied for %s (uid=%s)", doc_id, caller_uid or "<none>")
        return f"Document '{doc_id}' not found. Use list_documents to see available documents."
    except KeyError:
        return f"Document '{doc_id}' not found. Use list_documents to see available documents."
    except Exception as exc:
        logger.warning("get_document_content failed for %s: %s", doc_id, exc)
        return f"Could not load document '{doc_id}': {exc}"

    if mode == "blocks" and blocks is not None and tool_context is not None:
        # Populate session state so structured_extraction_callback can consume blocks
        tool_context.state["temp:document_blocks"] = json.dumps(blocks, ensure_ascii=False)
        tool_context.state["temp:document_id"] = doc_id

    await _record_session_document(tool_context, doc_id)
    return content


async def _record_session_document(tool_context: ToolContext | None, doc_id: str) -> None:
    """Record that this conversation worked with ``doc_id``.

    ``chat_sessions.documentIds`` was only written for documents the user
    attached as tabs, so a document the agent opened by itself was invisible to
    the document's Conversations panel and to ``GET /api/documents/recent`` —
    verified on dev 2026-10-20, where a chat that read and summarised a document
    left ``documentIds`` empty. Called only after the ownership check passed, so
    ``doc_id`` is the caller's own canonical id. Best-effort: never fails the read.
    """
    invocation = getattr(tool_context, "_invocation_context", None) if tool_context is not None else None
    session_id = getattr(getattr(invocation, "session", None), "id", None)
    if not session_id:
        return
    from db.chat_sessions import add_session_documents

    try:
        await asyncio.to_thread(add_session_documents, session_id, [doc_id])
    except Exception as exc:
        logger.warning("get_document_content: could not record %s on session %s: %s", doc_id, session_id, exc)


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
        bucket_uri: GCS bucket URI, e.g. `gs://example-customer-documents`
            or `gs://example-customer-documents/PPAs/longform`. The
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


def _generate_document_factory(_config: dict) -> FunctionTool:
    """v6.32.0 document-creation M2. Lazy-imported so skills that never generate
    a document don't pay the ailang-parse convert import cost."""
    from tools.documents.generate import generate_document

    return FunctionTool(generate_document)


def _ailang_inbox_factory(name: str) -> Callable[[dict], FunctionTool]:
    """Read / mark AILANG inbox messages (tools/ailang_inbox.py); readable
    inboxes come from admin_config/ailang_messages.readableInboxes."""

    def _factory(_config: dict) -> FunctionTool:
        from tools import ailang_inbox

        return FunctionTool(getattr(ailang_inbox, name))

    return _factory


def _ailang_messages_factory(_config: dict) -> FunctionTool:
    """Send a message to an admin-allowed AILANG inbox (tools/ailang_messages.py)."""
    from tools.ailang_messages import send_ailang_message

    return FunctionTool(send_ailang_message)


TOOL_REGISTRY: dict[str, Callable[[dict], FunctionTool]] = {
    "list_documents": lambda _config: FunctionTool(list_documents),
    "get_document_content": lambda _config: FunctionTool(get_document_content),
    # v6.32.0 DOC-CREATE M2: markdown in, a real document in the workspace.
    "generate_document": _generate_document_factory,
    "url_processing": lambda _config: FunctionTool(url_processing),
    # v6.39.0 BROWSER-P1: a real browser that executes the page's JavaScript.
    # Sits alongside url_processing rather than replacing it — url_processing is
    # the cheap JS-blind GET and the declared fallback when the browser service
    # is unavailable, and `browse` says so in its result when it degrades.
    "browse": lambda _config: FunctionTool(browse),
    # v6.40.0 RESIDENT-P1 M11: delegate to the user's long-lived resident agent.
    # Unlike every other tool here this one holds a CONVERSATION that outlives
    # the request, so its thread id is derived from the invocation rather than
    # passed by the model — see tools/resident_agent.py.
    "ask_assistant": lambda _config: FunctionTool(ask_assistant),
    "search_workshop_docs": lambda _config: FunctionTool(search_workshop_docs),
    # User guide: the caller's own setup (roles, skills, folders, features), so the
    # platform-guide skill answers "why can't I…?" about THEIR account.
    "my_context": lambda _config: FunctionTool(my_context),
    # v6.4.0 TENANT-DEMO M2: typed PPA clause extraction with block_id citations.
    # Lazy-imported so non-PPA skills don't pay the google-genai import cost.
    "extract_ppa_clauses": _extract_ppa_clauses_factory,
    # v6.4.0 TENANT-DEMO M3: pairwise PPA comparison with commercial reasoning.
    # Reuses extract_ppa_clauses internally — same lazy pattern.
    "compare_ppa_contracts": _compare_ppa_contracts_factory,
    # v6.7.0 PPA-OBLIGATION M2: extraction → deontic engine wire JSON for the
    # Obligation Analysis artefact. Reuses extract_ppa_clauses internally.
    "map_ppa_obligations": _map_ppa_obligations_factory,
    # v6.4.0 TENANT-DEMO M2 (deferred unblock): read-only BigQuery into the tenant's
    # ENTSO-E day-ahead price history. sa-platform gets bigquery.dataViewer
    # on your-entsoe-project via Sunholo-org IAM grant (drift; track tf follow-up).
    "entsoe_day_ahead_prices": _entsoe_day_ahead_prices_factory,
    # v6.4.0 TENANT-DEMO post-M3: bucket-discovery entry point so the agent
    # can find PPAs in the tenant bucket and feed gs:// URLs to
    # extract_ppa_clauses / compare_ppa_contracts without an upload step.
    "list_bucket_documents": lambda _config: FunctionTool(list_bucket_documents),
    # Message an AILANG agent/team inbox. Inboxes are allowlisted in
    # admin_config/ailang_messages; the coordinator URL/key come from env.
    "ailang_messages": _ailang_messages_factory,
    "ailang_inbox_list": _ailang_inbox_factory("ailang_inbox_list"),
    "ailang_inbox_read": _ailang_inbox_factory("ailang_inbox_read"),
    "ailang_inbox_ack": _ailang_inbox_factory("ailang_inbox_ack"),
}


# Private-tier tools (backend/private/*/tools.py) — absent from the public
# template, so this adds nothing there. See backend/extensions.py.
def _merge_private_tools() -> None:
    from extensions import private_tools

    registry, catalog = private_tools()
    for name, factory in registry.items():
        if name in TOOL_REGISTRY:
            raise ValueError(f"private tool {name!r} shadows a public tool")
        TOOL_REGISTRY[name] = factory
    _PRIVATE_CATALOG.extend(catalog)


_PRIVATE_CATALOG: list[dict[str, str]] = []
_merge_private_tools()

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
        "name": "my_context",
        "label": "Your setup",
        "description": "Look up how the app is set up for the person asking: their roles, skills, folders and features.",
        "category": "Help",
    },
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
        "name": "browse",
        "label": "Browse a web page (runs JavaScript)",
        "description": (
            "Open a page in a real browser so single-page apps and dashboards return their "
            "actual content, not an empty shell. Slower than 'Read a web page' — pick that one "
            "unless the page needs JavaScript to show anything."
        ),
        "category": "Search",
    },
    {
        "name": "ask_assistant",
        "label": "Ask my resident assistant",
        "description": (
            "Put a question or task to the user's long-running assistant, which keeps its own "
            "workspace and remembers this conversation between turns. Use it for work that "
            "benefits from continuity or takes more than one step; use the direct tools for a "
            "single lookup."
        ),
        "category": "Agents",
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
        "name": "generate_document",
        "label": "Create a document",
        "description": "Write a Word document into the workspace, optionally styled from a template.",
        "category": "Documents",
    },
    {
        "name": "list_bucket_documents",
        "label": "Browse a storage bucket",
        "description": "List files in a connected cloud storage bucket before reading them.",
        "category": "Documents",
    },
    {
        "name": "ailang_messages",
        "label": "Message an AILANG agent",
        "description": "Send a message to an AILANG agent or team inbox that an admin has allowed.",
        "category": "Communication",
    },
    {
        "name": "ailang_inbox_list",
        "label": "List AILANG inbox messages",
        "description": "See unread counts and messages in the AILANG inboxes an admin has made readable.",
        "category": "Communication",
    },
    {
        "name": "ailang_inbox_read",
        "label": "Read an AILANG message",
        "description": "Read one AILANG inbox message in full.",
        "category": "Communication",
    },
    {
        "name": "ailang_inbox_ack",
        "label": "Mark AILANG messages read",
        "description": "Mark AILANG inbox messages read or unread (self-hosted deployments with the ailang CLI).",
        "category": "Communication",
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


TOOL_CATALOG.extend(_PRIVATE_CATALOG)


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


def resolve_mcp_tools(tool_configs: dict[str, dict], user_email: str = "", user_domain: str = "") -> list:
    """Return McpToolset instances for any MCP servers listed in tool_configs.

    Called from agent.py when "mcp" appears in the skill's tool list.

    Args:
        tool_configs: Per-tool config dict; reads tool_configs["mcp"]["servers"].
        user_email: the authenticated user's email, closed over by any
            per-call header provider. NOT the ADK ``user_id``, which is the
            Firebase UID — every policy on this platform is keyed on email and
            domain, so passing the UID silently fails every gate.
        user_domain: the authenticated user's domain, same reason.

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

    resolved, missing = get_mcp_tools_with_status(server_ids, user_email, user_domain)
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
