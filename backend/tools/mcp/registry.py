"""MCP toolset registry — loads McpToolset instances from Firestore server configs.

Firestore schema: mcp_servers/{server_id}
  url:       str  — HTTP or SSE endpoint URL
  transport: str  — "http" (default) | "sse"
  headers:   dict — optional HTTP headers (e.g. Authorization). A value of the
                    form ``${ENV_VAR}`` is resolved from the process environment
                    at toolset-build time — see "Secret-bearing headers" below.
  name:      str  — human-readable label

Secret-bearing headers (v6.23.0 MAPS-GROUNDING):
  Every server seeded before Maps Grounding Lite had ``headers: {}`` — none
  needed a credential, because they were either loopback sidecars or unauthed.
  Grounding Lite is the first that does (``X-Goog-Api-Key``), and a raw API key
  must NOT sit in a Firestore document: ``mcp_servers/*`` is readable by anything
  holding the runtime SA, it lands in seed-script source (and therefore git), and
  it survives in Firestore backups. So a header VALUE may be written as
  ``"${MAPS_GROUNDING_API_KEY}"`` and is resolved from the environment (mounted
  from Secret Manager via ``--set-secrets`` on Cloud Run) when the toolset is
  built. Firestore stores the NAME of the secret; only the process holds it.

  An unresolved reference is a HARD failure (``_build_toolset`` returns None →
  the server is reported missing → ``resolve_mcp_tools_strict`` raises). It must
  never fall through to sending the literal ``${VAR}`` as a credential: that
  yields a 401 at ``tools/list`` time, which ADK surfaces as the indistinguishable
  "MCP server returned no tools" — the exact silent-misconfiguration failure G42
  exists to prevent.

Usage from adk/tools.py via resolve_tools when "mcp" is in tool_names:
  configs = skill_tool_config.get("mcp", {}).get("servers", [])
  toolsets = get_mcp_tools(configs)

UI capability declaration (M2B-BACKEND, MCP-APP-INTEGRATIONS):
  Per the MCP Apps spec the *client* should advertise that it can render UI
  resources back from the server. The canonical mechanism is the
  ``ClientSession`` ``capabilities`` arg::

      capabilities = {"extensions": {"io.modelcontextprotocol/ui": {
          "mimeTypes": ["text/html;profile=mcp-app"],
      }}}

  ADK as of v1.24.1 does NOT plumb that arg through ``StreamableHTTPConnectionParams``
  → ``MCPSessionManager._create_client`` → ``streamablehttp_client(...)`` →
  ``ClientSession.initialize()`` (verified via mcp__adk-mcp__search_code on
  2026-04-30). Workaround: declare the capability via a static HTTP header
  (``UI_CAPABILITY_HEADER``) on the connection params. Spec-compliant servers
  should key off it; the live ext-apps map-server emits UI resources
  unconditionally so the demo works either way. See
  ``docs/design/v6.1.0/mcp-app-integrations.md`` Open Questions.

  When ADK adds capability passthrough, swap the header for the canonical
  ``capabilities`` arg and remove ``UI_CAPABILITY_HEADER`` from this file.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from db.firestore import get_document

log = logging.getLogger(__name__)

_MCP_COLLECTION = "mcp_servers"

# --- Config-doc cache (v6.14.0 cold-start / build-cost) ----------------------
# The agent-build path reads mcp_servers/{id} from Firestore for EVERY declared
# server on EVERY turn (no agent cache previously). A short module TTL removes
# those per-turn round-trips. Caches BOTH a found config and a `None` miss (so a
# not-yet-seeded server like ext-apps-map isn't re-read every turn). The seed
# writes Firestore directly, so the TTL is the invalidation — a freshly-seeded
# server resolves within one TTL window (matches the "wait ~a minute" after a
# seed). Firestore errors are NOT cached (they re-raise to the caller).
_CONFIG_CACHE_TTL = 60.0
_config_cache: dict[str, tuple[float, dict | None]] = {}


def clear_registry_cache() -> None:
    """Drop the mcp_servers config cache (tests / after a re-seed)."""
    _config_cache.clear()


def _cached_server_config(server_id: str) -> dict | None:
    """Firestore mcp_servers/{id} read, memoised for ``_CONFIG_CACHE_TTL``.

    Raises on a Firestore error (not cached) — the caller classifies it as
    missing, same as before this cache existed.
    """
    now = time.time()
    entry = _config_cache.get(server_id)
    if entry is not None and (now - entry[0]) < _CONFIG_CACHE_TTL:
        return entry[1]
    config = get_document(_MCP_COLLECTION, server_id)  # may raise → not cached
    _config_cache[server_id] = (now, config)
    return config


def derive_in_process_mcp_base_url() -> str:
    """Return the base URL a fork should seed into ``mcp_servers/*.url``
    when registering one of THIS service's in-process MCP servers.

    G42 part (a) (template-mcp-strict-resolution.md): a fork that mounts
    its own in-process MCP servers (via ``app.mount("/mcp/<name>", …)``)
    and seeds ``mcp_servers/<name>`` documents at startup must point the
    URL at the LOOPBACK address, not the public Cloud Run URL.

    Why this matters: the ``McpToolset`` that consumes the seed runs
    inside this Python process. On Cloud Run the public hostname routes
    to the FRONTEND container, and ``next.config.mjs`` has no rewrite
    for ``/mcp/*`` — so dialling the public URL produces a 404 at MCP
    session creation time. The agent then boots with the toolset's
    tools missing entirely (no ``lookup_vendor``, no ``check_duplicate``,
    etc.), the LLM calls them anyway per its SKILL.md, and ADK crashes
    the run.

    Surfaced by the gde-ap-agent fork (2026-06-06) as "Tool
    'lookup_vendor' not found" on the deployed service. Root cause:
    the seed wrote the public Cloud Run URL into Firestore.

    Returns ``http://127.0.0.1:<PORT>`` where PORT is taken from the
    ``PORT`` env var (Cloud Run sets this) or falls back to 1956 (the
    local uvicorn bind). ``MCP_INTERNAL_BASE_URL`` overrides everything
    for ops-controlled scenarios (test fixtures, alternate binds).

    The 127.0.0.1 default (not ``localhost``) dodges Node's IPv6 DNS
    trap discussed in ``scripts/seed_mcp_servers.py``.
    """
    override = os.environ.get("MCP_INTERNAL_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    port = os.environ.get("PORT", "1956")
    return f"http://127.0.0.1:{port}"


# Attribute name we stamp on every produced MCPTool so observability callbacks
# (see ``adk/mcp_observability.py``) can recover the originating server_id
# without parsing tool names. Subclassing keeps LLM-visible tool names
# unchanged — the alternative (``tool_name_prefix``) would change them.
SERVER_ID_ATTR = "_aitana_mcp_server_id"

# UI capability declaration — see module docstring for the workaround
# rationale. Lives on the connection params headers dict, merged with any
# server-configured headers (operator-supplied value wins on collision).
UI_CAPABILITY_HEADER = "x-aitana-mcp-ui-supported"
UI_CAPABILITY_MIME_TYPE = "text/html;profile=mcp-app"


class TaggedMcpToolset(McpToolset):
    """McpToolset subclass that tags every produced MCPTool with its server_id.

    Why a subclass and not ``tool_name_prefix``: the prefix would alter the
    function names the LLM sees (``mcp_<server>_<tool>`` instead of just
    ``<tool>``), which would surprise prompts that reference tool names
    explicitly and complicate the workshop demo. Stamping a private
    attribute on each tool keeps the LLM-visible surface unchanged and
    gives observability callbacks a clean way to recover the server_id.
    """

    # Per-instance cache of the resolved tool list. `get_tools()` otherwise opens
    # a fresh MCP session + `tools/list` handshake to the server on EVERY call —
    # and ADK calls it once per request (invocation_context.canonical_tools_cache
    # is per-request). Once the agent cache (v6.14.0) keeps this toolset instance
    # alive across turns, this memo means the ~network handshake happens once per
    # instance per TTL, not once per turn. The tool CALL still opens its own
    # session; only the schema LISTING is cached. MCP tool lists are context-
    # independent, so caching across readonly_contexts is safe.
    _TOOLS_CACHE_TTL = 300.0

    def __init__(self, *, server_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._aitana_server_id = server_id
        self._tools_cache: tuple[float, list] | None = None

    @property
    def aitana_server_id(self) -> str:
        return self._aitana_server_id

    async def get_tools(self, readonly_context: ReadonlyContext | None = None):  # type: ignore[override]
        cached = self._tools_cache
        if cached is not None and (time.monotonic() - cached[0]) < self._TOOLS_CACHE_TTL:
            return cached[1]
        tools = await super().get_tools(readonly_context=readonly_context)
        for tool in tools:
            try:
                setattr(tool, SERVER_ID_ATTR, self._aitana_server_id)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("mcp_registry: failed to tag tool %r with server_id: %s", tool, exc)
        self._tools_cache = (time.monotonic(), tools)
        return tools


def get_mcp_tools(server_ids: list[str]) -> list[McpToolset]:
    """Return McpToolset instances for the given server IDs.

    Reads each server's config from Firestore `mcp_servers/{server_id}`.
    Servers not found in Firestore are logged and skipped.

    NOTE: this function preserves the legacy "silently skip missing"
    behaviour because some callers (admin scripts, test fixtures) rely
    on it. The agent-build path goes through ``resolve_mcp_tools_strict``
    (in ``backend/adk/tools.py``) which fails loudly when a SKILL.md
    declares servers that don't resolve — see G42 / template-mcp-strict-resolution.md.

    Args:
        server_ids: List of Firestore document IDs under mcp_servers/.

    Returns:
        List of McpToolset instances ready to add to an agent's tools list.
    """
    resolved, _missing = get_mcp_tools_with_status(server_ids)
    return resolved


def get_mcp_tools_with_status(server_ids: list[str]) -> tuple[list[McpToolset], list[str]]:
    """Resolve server IDs to toolsets AND track which ones failed.

    G42 (template-mcp-strict-resolution.md): the agent-build path needs
    to know whether every declared MCP server actually resolved. The
    silently-skip behaviour of ``get_mcp_tools`` masks the most common
    MCP misconfiguration — a SKILL.md that declares `mcp.servers:
    ["vendor-master"]` against a Firestore that has no such row.
    Pre-G42 the agent built with zero MCP tools and silently misbehaved;
    post-G42 the strict resolver raises with a clear diff.

    Returns:
        A tuple ``(resolved_toolsets, missing_server_ids)`` where
        ``missing_server_ids`` includes any server_id that:
          * raised an exception when fetched from Firestore, OR
          * returned None (no document under mcp_servers/{id}), OR
          * had a config that `_build_toolset` couldn't honour
            (e.g. no `url` field).
    """
    resolved: list[McpToolset] = []
    missing: list[str] = []
    for server_id in server_ids:
        try:
            config = _cached_server_config(server_id)
        except Exception as exc:
            log.warning("mcp_registry: failed to load server config %r: %s", server_id, exc)
            missing.append(server_id)
            continue

        if config is None:
            log.warning("mcp_registry: server %r not found in Firestore; skipping", server_id)
            missing.append(server_id)
            continue

        toolset = _build_toolset(server_id, config)
        if toolset is None:
            # _build_toolset already logged the reason (missing url, etc).
            missing.append(server_id)
            continue
        resolved.append(toolset)

    return resolved, missing


class UnresolvedHeaderSecret(Exception):
    """A ``${ENV_VAR}`` header reference had no value in the environment."""


# Matches a header value that is EXACTLY one env reference: "${MAPS_KEY}".
# Deliberately not a substring substitution — a credential header is the whole
# value in every real case, and partial interpolation invites a half-built
# Authorization header that fails in a much more confusing way.
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_header_secrets(server_id: str, headers: dict) -> dict:
    """Resolve ``${ENV_VAR}`` header values from the environment.

    Non-matching values pass through untouched, so existing servers with plain
    headers are unaffected.

    Raises:
        UnresolvedHeaderSecret: if a referenced env var is unset or empty. See
            the module docstring — failing loudly here is the whole point.
    """
    resolved = {}
    for key, value in headers.items():
        match = _ENV_REF.match(value) if isinstance(value, str) else None
        if match is None:
            resolved[key] = value
            continue
        var_name = match.group(1)
        secret = os.environ.get(var_name, "").strip()
        if not secret:
            raise UnresolvedHeaderSecret(
                f"mcp_servers/{server_id} header {key!r} references ${{{var_name}}} "
                f"but {var_name} is unset or empty in this process. Mount it "
                f"(Cloud Run: --set-secrets={var_name}={var_name}:latest; local: "
                f"backend/.env) — refusing to dial the server with a literal "
                f"placeholder as its credential."
            )
        resolved[key] = secret
    return resolved


def _merge_ui_capability_header(headers: dict) -> dict:
    """Return headers dict with the UI capability header set, unless the
    operator already supplied their own value (which wins).
    """
    merged = dict(headers) if headers else {}
    if UI_CAPABILITY_HEADER not in merged:
        merged[UI_CAPABILITY_HEADER] = UI_CAPABILITY_MIME_TYPE
    return merged


def _build_toolset(server_id: str, config: dict) -> McpToolset | None:
    """Build a McpToolset from a Firestore server config dict.

    Always declares UI extension capability via ``UI_CAPABILITY_HEADER`` so
    spec-compliant servers know they can return UI resources. See module
    docstring for why this is the workaround path rather than the canonical
    ``ClientSession.capabilities`` arg.
    """
    url = config.get("url")
    if not url:
        log.warning("mcp_registry: server %r has no url field; skipping", server_id)
        return None

    transport = config.get("transport", "http").lower()
    server_headers = config.get("headers") or {}
    try:
        server_headers = _resolve_header_secrets(server_id, server_headers)
    except UnresolvedHeaderSecret as exc:
        # Same contract as a missing `url`: return None so the caller records
        # the server as missing and the strict resolver raises with a clear diff.
        log.error("mcp_registry: %s", exc)
        return None
    headers = _merge_ui_capability_header(server_headers)

    if transport == "sse":
        connection = SseConnectionParams(url=url, headers=headers)
    else:
        connection = StreamableHTTPConnectionParams(url=url, headers=headers)

    return TaggedMcpToolset(server_id=server_id, connection_params=connection)
