"""Search sub-agent — wraps Gemini native search tools for all skill agents.

All skill agents (Gemini, Claude, OpenAI) use this sub-agent via AgentTool.
The sub-agent runs on Gemini 2.5 Flash internally with native grounding tools.
This keeps the root agent FunctionTool-compatible — Gemini's built-in grounding
tools cannot coexist with FunctionTools on the same agent request.

Tool selection:
  - No datastore_id  → google_search + url_context (open web)
  - With datastore_id → VertexAiSearchTool only (enterprise corpus)

google_search and VertexAiSearchTool use different API-level tool types and
cannot be combined on the same agent request (400 INVALID_ARGUMENT). Skills
that need both web and enterprise search should request both `google_search`
and `ai_search` in their tool list — _resolve_search_tools creates two
separate AgentTool instances in that case.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools import VertexAiSearchTool, google_search, url_context
from google.genai import types

from adk.a2ui_sources_render import sources_from_grounding

# The calling agent (via AgentTool) only sees the TEXT this sub-agent returns —
# never the grounding metadata. So anything the parent should know about the
# sources has to ride in that text.
#
# What it must NOT ride in is a user-facing 'Sources:' list. The workbench
# already renders one — a clean, clickable **Sources** tab built from the same
# grounding data ([adk/a2ui_sources_render.py](../adk/a2ui_sources_render.py)),
# where a datastore doc opens behind auth and no gs:// URI is ever an href. A
# second list in the chat reply was both a duplicate AND wrong: its links were
# raw `gs://bucket/...` URIs, which no browser can open and which leak internal
# bucket paths at the user (CLAUDE.md #9 — opaque ids are backend addressing).
#
# So this module appends CONTEXT FOR THE AGENT, not copy for the user: friendly
# source NAMES only, never a URI (the model cannot mangle what it never sees),
# framed "already on screen, don't re-list". Prompting alone doesn't hold — the
# sub-agent writes its own list anyway — so `_strip_authored_sources` removes a
# trailing model-authored one at the boundary.
_SOURCES_NOTE = (
    "Name a specific document or page inline where it helps the reader, but do NOT "
    "write a 'Sources:' / 'References:' section and never print a URL, gs:// path or "
    "file path — the user already sees the full, clickable source list in the "
    "workbench 'Sources' tab. Never invent or paraphrase a URL."
)

_WEB_INSTRUCTION = (
    "You are a web search assistant. Use the available tools to find relevant "
    "information from the web and return a comprehensive, well-structured answer. " + _SOURCES_NOTE
)

_ENTERPRISE_INSTRUCTION = (
    "You are a knowledge base search assistant. Use the available tools to find "
    "relevant information from the enterprise knowledge base and return a "
    "comprehensive, well-structured answer. " + _SOURCES_NOTE
)

# Framing for the appended block. Deliberately bracketed and imperative — this
# is the same "context, not copy" shape as `list_documents`' `[ref: <id>]` hint.
_SOURCE_CONTEXT_HEADER = (
    "[grounding sources — ALREADY VISIBLE to the user in the workbench 'Sources' tab, "
    "where each one is clickable. Name one in your prose where it helps, but do NOT "
    "repeat this list in your reply and never print a URL or file path.]"
)

# A trailing source list the model wrote itself is stripped at the boundary —
# see the module docstring. The heading arrives in every markdown dress the
# model reaches for (`Sources:`, `**Sources**`, `## Sources`, `**Citations:**`),
# so normalise the line rather than trying to spell them all in one regex.
_AUTHORED_HEADINGS = frozenset({"sources", "references", "citations"})
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")


def _is_authored_sources_heading(line: str) -> bool:
    """True when ``line`` is JUST a source-list heading, in any markdown dress."""
    text = line.strip().lstrip("#").strip()
    for _ in range(2):  # `**Citations:**` hides the colon inside the emphasis
        text = text.strip("*_").strip().rstrip(":").strip()
    return text.lower() in _AUTHORED_HEADINGS


def _source_label(source: dict[str, str]) -> str:
    """A human label for one grounding source — NEVER a URI.

    A datastore document reads as its filename/title; a web page as its title,
    falling back to the bare hostname (what the Sources tab shows too).
    """
    title = (source.get("title") or "").strip()
    if title:
        return title
    filename = (source.get("filename") or "").strip()
    if filename:
        return filename
    host = (urlparse(source.get("uri") or "").hostname or "").strip()
    if host:
        return host.removeprefix("www.")
    return "Untitled source"


def _format_source_context(grounding_metadata: Any) -> str | None:
    """Build the agent-facing source-context block from grounding metadata.

    Friendly names only, de-duplicated, first-seen order; ``None`` when there is
    nothing citable. The *user-facing* Sources list is not built here at all — it
    renders as its own workbench tab via the proven result→A2UI path
    ([adk/a2ui_sources_render.py](../adk/a2ui_sources_render.py)), from the same
    grounding metadata, with working links.
    """
    sources = sources_from_grounding(grounding_metadata)
    if not sources:
        return None
    seen: set[str] = set()
    lines: list[str] = []
    for source in sources:
        label = _source_label(source)
        if label in seen:
            continue
        seen.add(label)
        lines.append(f"- {label}")
    if not lines:
        return None
    return _SOURCE_CONTEXT_HEADER + "\n" + "\n".join(lines)


def _strip_authored_sources(text: str) -> str:
    """Remove a TRAILING model-authored source list from ``text``.

    Conservative by construction: only a heading line that is *followed to the
    end of the text by list items* is removed, so prose that merely mentions
    sources mid-answer survives untouched.
    """
    lines = text.rstrip().split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if not _is_authored_sources_heading(lines[i]):
            continue
        tail = [line for line in lines[i + 1 :] if line.strip()]
        if tail and all(_LIST_ITEM_RE.match(line) for line in tail):
            return "\n".join(lines[:i]).rstrip()
        break  # a heading with non-list content below it is prose — leave it alone
    return text


def _append_source_context(
    callback_context: CallbackContext,  # unused; required by the ADK callback signature
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """``after_model_callback``: strip a model-authored Sources list, append the
    agent-facing source-context block.

    Returns the modified response, or ``None`` to leave it untouched (nothing to
    strip and nothing citable, or no text at all).
    """
    content = llm_response.content
    if content is None or not content.parts:
        return None
    existing_text = "".join(p.text for p in content.parts if getattr(p, "text", None))
    if not existing_text.strip():
        return None

    block = _format_source_context(getattr(llm_response, "grounding_metadata", None))
    # Rewriting text is only safe when every part IS text — a mixed response
    # (text alongside a function call) keeps its parts and is appended to.
    text_only = all(getattr(p, "text", None) is not None for p in content.parts)
    stripped = _strip_authored_sources(existing_text) if text_only else existing_text
    suffix = "\n\n" + block if block else ""
    if stripped == existing_text and not suffix:
        return None

    parts = [types.Part(text=stripped + suffix)] if text_only else [*content.parts, types.Part(text=suffix)]
    llm_response.content = types.Content(role=content.role, parts=parts)
    return llm_response


def create_web_search_agent() -> LlmAgent:
    """Gemini agent with google_search + url_context for open-web queries."""
    # Function-local import avoids an import cycle (adk.agent imports this module
    # lazily). resolve_model_chain gives this sub-agent's Gemini turn the same
    # retry + region-fallback as the main agent (v6.14.0 reliability sweep). The
    # chain stays Gemini-only, so google_search/url_context grounding survives.
    # `lite` tier (not a pinned id) so this tracks the registry automatically —
    # 2026-08-13: was a hardcoded gemini-2-5-flash, which EOLs 2026-10-16.
    from adk.agent import resolve_model_chain

    return LlmAgent(
        name="web_search_agent",
        model=resolve_model_chain("lite"),
        description="Searches the web and fetches URL content. Use for web search and URL lookup.",
        instruction=_WEB_INSTRUCTION,
        tools=[google_search, url_context],
        after_model_callback=_append_source_context,
    )


def create_enterprise_search_agent(datastore_id: str) -> LlmAgent:
    """Gemini agent with VertexAiSearchTool for enterprise corpus queries.

    Args:
        datastore_id: Full Vertex AI Search resource ID:
            projects/{project}/locations/{location}/collections/{collection}/dataStores/{id}
    """
    # See create_web_search_agent — resilient chain, Gemini-only for VertexAiSearch,
    # `lite` tier tracks the registry automatically (2026-08-13).
    from adk.agent import resolve_model_chain

    return LlmAgent(
        name="enterprise_search_agent",
        model=resolve_model_chain("lite"),
        description="Searches the enterprise knowledge base. Use for document and corpus search.",
        instruction=_ENTERPRISE_INSTRUCTION,
        tools=[VertexAiSearchTool(data_store_id=datastore_id)],
        after_model_callback=_append_source_context,
    )


def create_search_agent(datastore_id: str | None = None) -> LlmAgent:
    """Create a search agent — web or enterprise depending on datastore_id.

    Convenience wrapper used when only one search type is needed. For skills
    requesting both google_search and ai_search, use _resolve_search_tools in
    adk/agent.py which returns two separate AgentTool instances.

    Args:
        datastore_id: When provided, returns an enterprise search agent
            (VertexAiSearchTool only). When None, returns a web search agent
            (google_search + url_context).
    """
    if datastore_id:
        return create_enterprise_search_agent(datastore_id)
    return create_web_search_agent()
