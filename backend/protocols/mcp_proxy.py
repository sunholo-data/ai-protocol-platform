"""HTTP proxy for frontend → MCP server JSON-RPC traffic.

Architecture (Path A, decided 2026-04-30 — see docs/design/v6.1.0/mcp-app-integrations.md):

The frontend's `@mcp-ui/client` MCP `Client` connects to *this* endpoint
instead of talking to MCP servers directly. Two reasons:

  1. **Auth boundary** — the user's Firebase JWT validates HERE; the upstream
     MCP server never sees it. Servers carry their own auth via the
     `headers` config field on `mcp_servers/{server_id}` (e.g. an HMAC
     secret, OAuth bearer issued for the proxy).
  2. **Per-skill allowlist** — the caller can only reach MCP servers that
     are referenced by ≥1 SkillConfig they can `can_access_skill(...)` —
     mirrors the same 5-type evaluator used by sessions/skills CRUD.

The proxy is intentionally a dumb forwarder: it does not parse JSON-RPC, does
not introspect methods, does not maintain a session pool. The frontend's MCP
`Client` does the protocol work; the proxy just relays bytes (with the right
content-type + body) and enforces the auth + allowlist.

Two MCP clients to keep straight:
  * The **agent's** McpToolset — talks to the upstream server directly via
    ``backend/tools/mcp/registry.py``. UI capability declaration lives there.
  * The **frontend's** Client — talks to *this proxy*. The frontend's own
    `initialize` request carries its capabilities in the JSON-RPC body, so
    they flow through automatically.

Errors:
  * 401 — missing/invalid Firebase JWT (handled upstream by `get_current_user`)
  * 403 — caller has no allowlisted skill for `server_id`
  * 404 — `server_id` not in Firestore
  * 502 — upstream returned 5xx
  * 504 — upstream timed out
  * 4xx — forwarded verbatim from upstream (the JSON-RPC error body explains)
"""

from __future__ import annotations

import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth import User, get_current_user
from db.firestore import get_document
from protocols.artefact_review import (
    ArtefactReview,
    BlockedArtefactError,
    get_registered_artefact_reviewer,
)
from skills import skill_config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-proxy"])

_MCP_COLLECTION = "mcp_servers"
_UPSTREAM_TIMEOUT_SECONDS = 30.0

# Hop-by-hop / sensitive headers we MUST NOT forward upstream.
# 'authorization' is the security-critical one — keeps the caller's Firebase
# JWT out of the upstream MCP server. 'host' / 'content-length' are stripped
# because httpx recomputes them; 'cookie' is stripped to avoid session bleed.
_INBOUND_HEADER_BLOCKLIST: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
    }
)
# Response headers that would corrupt the proxied response if forwarded.
_OUTBOUND_HEADER_BLOCKLIST: frozenset[str] = frozenset(
    {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
    }
)


def _user_can_use_server(server_id: str, request: Request) -> bool:
    """True iff the caller has access to ≥1 SkillConfig whose
    ``tool_configs.mcp.servers`` includes ``server_id``.

    Uses the same 5-type access evaluator the rest of the platform applies
    (see ``auth/access_context.py``). Mirrors ``skills/routes.list_skills``:
    fetch with broad filters, then drop anything the evaluator rejects. If
    this becomes hot, swap for a `mcp_server_id == X` composite index — for
    now, every-skill scan is fine because user counts are small and the
    in-process cache absorbs repeated calls.
    """
    access = request.state.access
    # Iterate the caller's accessible-skill set. We cannot pre-filter by
    # server_id at the Firestore layer (Firestore can't query nested array
    # fields like tool_configs.mcp.servers efficiently without an index we
    # haven't built); the in-memory filter is fine until that becomes hot.
    candidates = skill_config.list_skills(limit=200)
    for skill in candidates:
        if not access.can_access_skill(skill):
            continue
        servers = (skill.skill_metadata.tool_configs or {}).get("mcp", {}).get("servers", [])
        if server_id in servers:
            return True
    return False


def _filter_inbound_headers(headers) -> dict[str, str]:
    """Strip auth + hop-by-hop headers before forwarding upstream."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _INBOUND_HEADER_BLOCKLIST:
            continue
        out[k] = v
    return out


def _filter_outbound_headers(headers) -> dict[str, str]:
    """Strip response headers that would corrupt the proxied response."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _OUTBOUND_HEADER_BLOCKLIST:
            continue
        out[k] = v
    return out


async def _forward(*, server_id: str, request: Request, user: User, method: str) -> Response:
    """Shared auth+allowlist+forward logic for POST + GET + DELETE.

    The MCP Streamable HTTP transport uses POST for JSON-RPC requests, GET to
    open the server-to-client SSE channel for notifications, and DELETE to
    explicitly tear down a session. The proxy is a dumb forwarder for all
    three; auth + allowlist gates apply uniformly.
    """
    server_config = get_document(_MCP_COLLECTION, server_id)
    if server_config is None:
        log.info("mcp_proxy: unknown server_id=%s caller_uid=%s", server_id, user.uid)
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not registered")

    if not _user_can_use_server(server_id, request):
        log.info(
            "mcp_proxy: caller_uid=%s denied access to server_id=%s (no allowlisted skill)",
            user.uid,
            server_id,
        )
        raise HTTPException(status_code=403, detail="Access denied to MCP server")

    upstream_url = server_config.get("url")
    if not upstream_url:
        log.warning("mcp_proxy: server_id=%s has no url field", server_id)
        raise HTTPException(status_code=502, detail="MCP server config has no URL")

    outbound_headers = _filter_inbound_headers(request.headers)
    server_headers = server_config.get("headers") or {}
    if isinstance(server_headers, dict):
        outbound_headers.update({str(k): str(v) for k, v in server_headers.items()})

    body = await request.body() if method in {"POST", "DELETE"} else b""

    log.info(
        "mcp_proxy: forwarding %s caller_uid=%s server_id=%s url=%s body_bytes=%d",
        method,
        user.uid,
        server_id,
        upstream_url,
        len(body),
    )

    try:
        async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_SECONDS) as client:
            upstream = await client.request(
                method=method,
                url=upstream_url,
                content=body if body else None,
                headers=outbound_headers,
            )
    except httpx.TimeoutException as exc:
        log.warning("mcp_proxy: upstream timeout server_id=%s url=%s: %s", server_id, upstream_url, exc)
        raise HTTPException(status_code=504, detail="Upstream MCP server timed out") from exc
    except httpx.HTTPError as exc:
        log.warning("mcp_proxy: upstream connection error server_id=%s url=%s: %s", server_id, upstream_url, exc)
        raise HTTPException(status_code=502, detail="Upstream MCP server unreachable") from exc

    if upstream.status_code >= 500:
        log.warning(
            "mcp_proxy: upstream %d server_id=%s url=%s",
            upstream.status_code,
            server_id,
            upstream_url,
        )
        raise HTTPException(status_code=502, detail="Upstream MCP server error")

    # Sprint 2.13 — server-side ArtefactReviewer interception. Only
    # fires when (a) a reviewer is registered AND (b) the request was a
    # resources/read AND (c) the response carries text/html content.
    # All other combinations pass through unchanged (back-compat with
    # the dumb-forwarder contract). A reviewer crash fails open — the
    # iframe sandbox is the safety net, not the reviewer.
    try:
        reviewed = await _maybe_review_artefact(
            request_body=body,
            response_content=upstream.content,
            server_id=server_id,
        )
        if reviewed is not None:
            return reviewed
    except BlockedArtefactError as exc:
        d = exc.decision
        log.info(
            "mcp_proxy: server-side artefact blocked server_id=%s reason=%s",
            server_id,
            d.reason_code,
        )
        return Response(
            content=json.dumps(
                {
                    "type": "artefact_blocked",
                    "message": d.message,
                    "reason_code": d.reason_code,
                    "appeal_url": d.appeal_url,
                }
            ).encode(),
            status_code=403,
            media_type="application/json",
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_outbound_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


# Sprint 2.13 — soft budget for server-side ArtefactReviewer.review().
# Reviewers exceeding this don't block render; the proxy logs at warn
# level so fork operators can tune their impls. Mirrors the frontend's
# REVIEW_BUDGET_MS in MCPAppToolCallRouter.tsx.
_REVIEW_BUDGET_SECONDS = 0.1


async def _maybe_review_artefact(
    *,
    request_body: bytes,
    response_content: bytes,
    server_id: str,
) -> Response | None:
    """Consult the registered server-side ArtefactReviewer if all gates pass.

    Returns ``None`` when the proxy should fall through to its normal
    forward path. Raises ``BlockedArtefactError`` when the reviewer
    refuses the artefact (caller serialises to 403).

    Pass-through (return None) when:
      * no reviewer registered (back-compat)
      * request body isn't valid JSON-RPC
      * request method isn't ``resources/read``
      * response body isn't a JSON-RPC result with at least one
        ``text/html`` content item
      * reviewer raises (fail-open — sandbox is the safety net)
    """
    reviewer = get_registered_artefact_reviewer()
    if reviewer is None:
        return None
    try:
        req_doc = json.loads(request_body) if request_body else None
    except (ValueError, TypeError):
        return None
    if not isinstance(req_doc, dict) or req_doc.get("method") != "resources/read":
        return None

    try:
        resp_doc = json.loads(response_content) if response_content else None
    except (ValueError, TypeError):
        return None
    if not isinstance(resp_doc, dict):
        return None
    result = resp_doc.get("result")
    if not isinstance(result, dict):
        return None
    contents = result.get("contents")
    if not isinstance(contents, list):
        return None
    # Find the first text/html content with a `text` field.
    html_item = next(
        (
            c
            for c in contents
            if isinstance(c, dict)
            and isinstance(c.get("mimeType"), str)
            and c["mimeType"].startswith("text/html")
            and isinstance(c.get("text"), str)
        ),
        None,
    )
    if html_item is None:
        return None

    tool_name = ""
    params = req_doc.get("params")
    resource_uri = ""
    if isinstance(params, dict):
        uri = params.get("uri")
        if isinstance(uri, str):
            resource_uri = uri
            # MCP tool name isn't on the resources/read request directly;
            # the reviewer gets the URI which is the closest identifier.
            tool_name = uri
    review = ArtefactReview(
        tool_name=tool_name,
        server_id=server_id,
        resource_uri=resource_uri,
        html=html_item["text"],
        csp=None,  # Server-side reviewers see the body; CSP lives in the resource _meta
        structured_content=resp_doc.get("result"),
        invocation_id=str(req_doc.get("id") or ""),
    )

    start = time.monotonic()
    try:
        decision = await reviewer.review(review)
    except Exception as exc:
        log.warning(
            "mcp_proxy: artefact reviewer crashed server_id=%s — forwarding original response: %s",
            server_id,
            exc,
        )
        return None

    duration_ms = (time.monotonic() - start) * 1000
    if duration_ms > _REVIEW_BUDGET_SECONDS * 1000:
        log.warning(
            "mcp_proxy: artefact reviewer slow server_id=%s html_size=%d duration_ms=%.1f",
            server_id,
            len(html_item["text"]),
            duration_ms,
        )

    if decision.action == "block":
        raise BlockedArtefactError(decision)

    return None  # approve + warn both pass through (warn is a frontend-only concern for now)


@router.post("/{server_id}")
async def proxy_mcp_jsonrpc(
    server_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Response:
    """Forward POST (JSON-RPC requests) to the registered MCP server."""
    return await _forward(server_id=server_id, request=request, user=user, method="POST")


@router.get("/{server_id}")
async def proxy_mcp_sse(
    server_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Response:
    """Forward GET (Streamable HTTP SSE channel) to the registered MCP server.

    The MCP TS SDK opens this GET alongside the POST channel to receive
    server-to-client notifications. Without it the SDK aborts the connection
    even though POST round-trips succeed.
    """
    return await _forward(server_id=server_id, request=request, user=user, method="GET")


@router.delete("/{server_id}")
async def proxy_mcp_session_close(
    server_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Response:
    """Forward DELETE (explicit session teardown) to the registered MCP server."""
    return await _forward(server_id=server_id, request=request, user=user, method="DELETE")


__all__ = ["router"]
