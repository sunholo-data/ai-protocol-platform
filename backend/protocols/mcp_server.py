"""MCP server — exposes skills as MCP tools, per caller.

Workshop W3 — MCP: Your Skills as Tools
  `_visible_skill_tools()` and `_run_skill()` are the moments to walk through:
  each skill the caller can see becomes one MCP tool; the handler runs the
  skill and returns its text; the MCP transport is transparent to the skill.

  Gotcha: `streamable_http_path='/'` below is load-bearing. FastMCP defaults
  to `streamable_http_path='/mcp'`. Mounting the sub-app at `/mcp` in FastAPI
  without overriding this yields POST /mcp/mcp — every client gets 404.

Mounted at ``/mcp`` (see ``get_mcp_asgi_app``). Each visible skill becomes one
tool named ``skill_<slug>`` (friendly, CLAUDE.md #9; the id only on a slug
clash); ``tools/call`` runs it via ``process_skill_request()`` AS THE CALLER and
returns the accumulated text.

Who is calling (``McpAuthGate``):
  * ``Authorization: Bearer aip_mcp_…`` — a personal MCP token
    (``auth.mcp_tokens``), resolved to its owner and put through the SAME
    identity gates as the REST API (``auth.enforce_user_gates``).
  * any other ``Bearer`` — the platform's normal verifiers (Firebase etc.,
    ``get_current_user``), handy for a quick test with a fresh ID token.
  * no header — anonymous, public skills only, when ``MCP_AUTH=optional``
    (the default, so the workshop's open demo keeps working). With
    ``MCP_AUTH=required`` an anonymous call is a 401: set it on any deployment
    that doesn't want its public skills callable at its own model cost.

A caller sees exactly what ``/api/skills`` shows them (``visible_configs``), and
tools run with ``session.user_id`` = their uid, so caller-scoped tools (e.g. a
connector that answers "my hours") answer for THEM. The list is computed per
request, so a skill shared or unshared later shows up without a restart.

Built on the official ``mcp`` SDK's FastMCP server (``mcp.server.fastmcp``).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent
from mcp.types import Tool as MCPTool

from auth.access_context import build_access_context
from auth.firebase_auth import User
from skills.platform import PLATFORM_OWNER_UID
from skills.skill_config import list_marketplace, list_skills

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from db.models import SkillConfig

logger = logging.getLogger(__name__)

_TOOL_PREFIX = "skill_"
_MAX_SKILLS = 200
_SCOPE_KEY = "mcp_user"

# Anonymous caller (MCP_AUTH=optional, no header): public skills only.
_MCP_USER = User(uid="mcp-caller", email="", domain="", group_tags=frozenset())
_MCP_ACCESS = build_access_context(_MCP_USER)

# Fallback carrier for the caller; the request scope is the primary one.
_caller: contextvars.ContextVar[User | None] = contextvars.ContextVar("mcp_caller", default=None)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string", "description": "What to ask the skill, in plain language."}},
    "required": ["message"],
}


def auth_required() -> bool:
    return os.getenv("MCP_AUTH", "optional").strip().lower() == "required"


def _safe_tool_segment(ref: str) -> str:
    """Convert a slug/skill_id to an MCP-safe tool-name suffix (letters/digits/underscore)."""
    safe = ref.replace("-", "_")
    if not safe:
        return "_"
    if not (safe[0].isalpha() or safe[0] == "_"):
        safe = "_" + safe
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in safe)


def _tool_name(ref: str) -> str:
    return _TOOL_PREFIX + _safe_tool_segment(ref)


# --- Who is calling --------------------------------------------------------


async def _authenticate(scope: Scope) -> User | None:
    """The caller for this request, None for anonymous. Raises HTTPException."""
    from starlette.requests import Request

    from auth import enforce_user_gates, get_current_user, mcp_tokens

    header = Request(scope).headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        if auth_required():
            raise HTTPException(status_code=401, detail="This MCP server needs a personal MCP token.")
        return None
    token = header[len("Bearer ") :].strip()
    if mcp_tokens.is_mcp_token(token):
        from auth.firebase_auth import resolve_user_by_uid

        uid = await asyncio.to_thread(mcp_tokens.resolve_token, token)
        user = await asyncio.to_thread(resolve_user_by_uid, uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401, detail="This MCP token is unknown, revoked or expired.")
        enforce_user_gates(user)
        return user
    return await get_current_user(Request(scope))


class McpAuthGate:
    """ASGI wrapper: resolve the caller before the MCP transport sees the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            user = await _authenticate(scope)
        except HTTPException as exc:
            await _reject(send, exc.status_code, exc.detail)
            return
        scope.setdefault("state", {})[_SCOPE_KEY] = user
        reset = _caller.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _caller.reset(reset)


async def _reject(send: Send, status: int, detail: Any) -> None:
    message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="mcp"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _current_user() -> User | None:
    """The authenticated caller of the MCP request being handled, if any."""
    try:
        request = mcp.get_context().request_context.request
        state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
        if _SCOPE_KEY in state:
            return state[_SCOPE_KEY]
    except (LookupError, ValueError):
        pass
    return _caller.get()


# --- Which tools, and running them ----------------------------------------


def _visible_skill_tools(user: User | None) -> dict[str, SkillConfig]:
    """tool name -> skill, for every skill this caller can see (sync: Firestore)."""
    if user is None:
        skills = list_marketplace(limit=100)
    else:
        from db.clients import resolve_enabled_skills
        from skills.visibility import visible_configs

        configs = list_skills(limit=_MAX_SKILLS)
        skills = visible_configs(configs, build_access_context(user), resolve_enabled_skills(user))
    tools: dict[str, SkillConfig] = {}
    for skill in skills:
        name = _tool_name(getattr(skill, "slug", "") or skill.skill_id)
        if name in tools:
            name = _tool_name(skill.skill_id)
        tools[name] = skill
    return tools


async def _run_skill(skill: SkillConfig, message: str, user: User | None) -> str:
    """Run one turn of ``skill`` as ``user`` (or anonymous) and return its text."""
    from skills.skill_processor import process_skill_request  # late import

    caller = user or _MCP_USER
    access = build_access_context(user) if user is not None else _MCP_ACCESS
    pieces: list[str] = []
    async for event in process_skill_request(
        skill_id=skill.skill_id, user=caller, access=access, session_id=None, message=message
    ):
        kind = event.get("type")
        if kind == "TEXT_MESSAGE_CONTENT" and isinstance(event.get("delta"), str):
            pieces.append(event["delta"])
        elif kind == "RUN_ERROR":
            # NEVER SILENT: an error is an MCP tool error, not an empty answer.
            raise ToolError(event.get("message") or "The skill failed to answer.")
    return "".join(pieces) or "(The skill finished without a text answer.)"


class PlatformMCP(FastMCP):
    """FastMCP whose tool list and dispatch are computed per caller."""

    async def list_tools(self) -> list[MCPTool]:
        user = _current_user()
        tools = await asyncio.to_thread(_visible_skill_tools, user)
        return [
            MCPTool(name=name, description=skill.description or skill.name, inputSchema=_INPUT_SCHEMA)
            for name, skill in tools.items()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        user = _current_user()
        skill = (await asyncio.to_thread(_visible_skill_tools, user)).get(name)
        if skill is None:
            raise ToolError(f"Unknown tool '{name}', or you don't have access to it.")
        message = (arguments or {}).get("message")
        if not isinstance(message, str) or not message.strip():
            raise ToolError("Pass a non-empty 'message'.")
        return [TextContent(type="text", text=await _run_skill(skill, message, user))]


# --- Server ---


def new_server() -> PlatformMCP:
    """A fresh server (a session manager can run only once; tests need their own)."""
    return PlatformMCP(
        PLATFORM_OWNER_UID,
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


mcp = new_server()


def get_mcp_asgi_app(server: PlatformMCP | None = None) -> ASGIApp:
    """Return the MCP streamable-HTTP ASGI app, behind the auth gate."""
    return McpAuthGate((server or mcp).streamable_http_app())


class McpExactPath:
    """Serve ``/mcp`` itself (no trailing slash) instead of redirecting.

    Starlette's ``Mount("/mcp")`` only matches ``/mcp/...`` and 307s a bare
    ``/mcp`` to ``/mcp/`` — with an ABSOLUTE ``Location`` built from the
    backend's own host (``http://127.0.0.1:1956/mcp/`` behind the frontend
    proxy), which no client can follow. Next.js, for its part, strips the
    trailing slash from ``/api/proxy/mcp/``. Between them no MCP client could
    ever connect through the proxy. This routes the bare path straight in.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        inner = dict(scope, path="/", raw_path=b"/", root_path=scope.get("root_path", "") + "/mcp")
        await self.app(inner, receive, send)
