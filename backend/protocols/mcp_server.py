"""MCP server — exposes public skills as MCP tools.

Workshop W3 — MCP: Your Skills as Tools
  `rebuild_tools()` and `_make_skill_tool()` are the moments to walk through:
  each public skill becomes one MCP tool; the handler is 8 lines; the MCP
  transport is entirely transparent to the skill itself.

  Gotcha: `streamable_http_path='/'` below is load-bearing. FastMCP defaults
  to `streamable_http_path='/mcp'`. Mounting the sub-app at `/mcp` in FastAPI
  without overriding this yields POST /mcp/mcp — every client gets 404.

Mounted at ``/mcp`` on the FastAPI app. Each public skill becomes one
MCP tool named ``skill_<safe_skill_id>``; ``tools/call`` invokes the
skill via `process_skill_request()` and returns the accumulated text.

Public-only, no auth: matches the A2A card's discovery semantics. A
real per-user MCP session (auth + private skills) is a follow-up; this
delivers the "external agent talks to an Aitana skill over MCP"
demo-ready surface for the July workshop.

Built on the official ``mcp`` SDK's FastMCP server (``mcp.server.fastmcp``).
The separate ``fastmcp`` package is a predecessor that was folded into
the official SDK — ``mcp>=1.7.1`` already has everything we need.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from auth.access_context import build_access_context
from auth.firebase_auth import User
from skills.skill_config import list_marketplace

if TYPE_CHECKING:
    from starlette.types import ASGIApp

    from db.models import SkillConfig

logger = logging.getLogger(__name__)

_TOOL_PREFIX = "skill_"

# Synthetic caller for MCP invocations — public skills don't check uid
# beyond the visibility filter, and ownership doesn't apply here (MCP
# callers never own skills).
_MCP_USER = User(uid="mcp-caller", email="", domain="", group_tags=frozenset())
_MCP_ACCESS = build_access_context(_MCP_USER)


def _safe_tool_segment(skill_id: str) -> str:
    """Convert a skill_id to MCP-safe tool-name suffix (letters/digits/underscore)."""
    safe = skill_id.replace("-", "_")
    if not safe:
        return "_"
    if not (safe[0].isalpha() or safe[0] == "_"):
        safe = "_" + safe
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in safe)


def _tool_name(skill_id: str) -> str:
    return _TOOL_PREFIX + _safe_tool_segment(skill_id)


def _make_skill_tool(skill: SkillConfig):
    """Build an async tool handler bound to a specific skill.

    Collects all TEXT_MESSAGE_CONTENT deltas from the AG-UI stream and
    returns the concatenated final text — the simplest MCP contract:
    message in, final assistant text out.
    """
    skill_id = skill.skill_id

    async def _invoke(message: str) -> str:
        from skills.skill_processor import process_skill_request  # late import

        pieces: list[str] = []
        async for event in process_skill_request(
            skill_id=skill_id,
            user=_MCP_USER,
            access=_MCP_ACCESS,
            session_id=None,
            message=message,
        ):
            if event.get("type") == "TEXT_MESSAGE_CONTENT":
                delta = event.get("delta")
                if isinstance(delta, str):
                    pieces.append(delta)
        return "".join(pieces)

    return _invoke


# --- Server ---

mcp = FastMCP(
    "aitana-platform",
    stateless_http=True,
    # Serve at the sub-app's root so that mounting at "/mcp" in FastAPI
    # yields POST /mcp (not /mcp/mcp — which was the default).
    streamable_http_path="/",
    # Disable DNS-rebinding protection: with default allowed_hosts=[] the
    # middleware rejects every Host header (including Cloud Run's
    # *.run.app and TestClient's 'testserver'). The attack vector it
    # guards against — a malicious webpage tricking a local browser into
    # reaching a localhost MCP server — doesn't apply to a Cloud Run
    # service fronted by IAM. Setting enable_dns_rebinding_protection=False
    # is the SDK-recommended posture for server-to-server deployments.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# Tracks skill_id -> tool_name so rebuild_tools() can diff without
# re-asking FastMCP (its list_tools() is async-only; we need a sync view).
_registered: dict[str, str] = {}


def rebuild_tools() -> None:
    """Sync the MCP tool registry to the current set of public skills.

    Idempotent: additions and removals are both handled, so callers can
    invoke freely on any skill mutation. Safe to call from sync context
    (no asyncio.run needed).

    Firestore errors (missing indexes, transient gRPC failures) are
    logged and swallowed — the MCP server should come up even when the
    marketplace query is unavailable, and the next mutation retries.
    """
    try:
        public = list_marketplace(limit=100)
    except Exception:
        logger.exception("mcp_server.rebuild_tools: list_marketplace failed; skipping sync")
        return
    current: dict[str, SkillConfig] = {s.skill_id: s for s in public}

    # Remove tools whose skill is no longer public (or deleted).
    for skill_id in list(_registered):
        if skill_id not in current:
            mcp.remove_tool(_registered[skill_id])
            del _registered[skill_id]

    # Add tools for newly-public skills.
    for skill_id, skill in current.items():
        if skill_id in _registered:
            continue
        tool_name = _tool_name(skill_id)
        mcp.add_tool(
            _make_skill_tool(skill),
            name=tool_name,
            description=skill.description or skill.name,
        )
        _registered[skill_id] = tool_name


def get_mcp_asgi_app() -> ASGIApp:
    """Return the MCP streamable-HTTP ASGI app ready to mount on FastAPI."""
    rebuild_tools()
    return mcp.streamable_http_app()
