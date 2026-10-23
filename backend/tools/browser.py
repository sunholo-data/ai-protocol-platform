"""`browse` — fetch a page with JavaScript actually executed.

Complements, rather than replaces, the MCP path. A skill that declares
`mcp.servers: ["browser"]` gets the full 30-tool surface for multi-step work.
This FunctionTool is the single-call door for everything else: the CLI, the
channels (Telegram, email), and skills that just want a page read.

Why it exists at all when `url_processing` is already there: `url_processing`
wraps ADK's `load_web_page`, a plain HTTP GET with no JavaScript. Any SPA comes
back as an empty shell, and the model then confabulates over it — which reads as
a model problem and is an infrastructure one.

Degradation is deliberate and visible (Axiom 5 + CLAUDE.md #8). If the browser
service is unreachable this falls back to `url_processing` and SAYS SO in the
returned payload, so a JS-blind answer is never mistaken for a rendered one.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from tools.browser_allowlist import NotAllowed, assert_allowed
from tools.mcp.id_token_cache import IdTokenUnavailable, get_id_token
from tools.url_processing import url_processing

log = logging.getLogger(__name__)

BROWSER_SERVICE_URL = os.environ.get("BROWSER_SERVICE_URL", "").rstrip("/")
BROWSER_ENABLED = os.environ.get("BROWSER_SERVICE_ENABLED", "").lower() in ("1", "true", "yes", "on")
BROWSER_TIMEOUT_SECONDS = float(os.environ.get("BROWSER_TIMEOUT_SECONDS", "90"))


def _auth_headers(user_id: str, session_id: str) -> dict[str, str]:
    headers = {
        "X-Aitana-User": user_id,
        "X-Aitana-Session": session_id,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if BROWSER_SERVICE_URL.startswith("https://"):
        # Local dev against http:// needs no token; Cloud Run does.
        headers["Authorization"] = f"Bearer {get_id_token(BROWSER_SERVICE_URL)}"
    # Self-hosted (no IAM): the service's BROWSER_SHARED_SECRET, so no other
    # local process can claim a user. Same value as the MCP row's static header.
    secret = os.environ.get("BROWSER_SERVICE_SECRET", "")
    if secret:
        headers["X-Browser-Service-Secret"] = secret
    return headers


async def _call_gateway(tool: str, arguments: dict, user_id: str, session_id: str) -> str:
    """Run one MCP tool call against the browser service, return its text."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=BROWSER_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{BROWSER_SERVICE_URL}/mcp",
            json=payload,
            headers=_auth_headers(user_id, session_id),
        )
        response.raise_for_status()
        body = response.json()

    if "error" in body:
        raise RuntimeError(body["error"].get("message", "browser service error"))

    content = (body.get("result") or {}).get("content") or []
    return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")


async def browse(url: str, instruction: str = "", tool_context: Any = None) -> dict:
    """Open a web page in a real browser and return its content as Markdown.

    Unlike a plain fetch, this executes the page's JavaScript, so single-page
    apps and client-rendered dashboards return their actual content.

    Args:
        url: The public http(s) URL to open.
        instruction: Optional note about what you are looking for on the page.
            Recorded for tracing; it does not change what is fetched.

    Returns:
        A dict with `url`, `markdown`, `rendered` (True if a real browser was
        used), and — when the browser was unavailable — a `notice` explaining
        what was used instead.
    """
    user_id, session_id = _identity(tool_context)

    if not BROWSER_ENABLED or not BROWSER_SERVICE_URL:
        text = await url_processing(url)
        return {
            "url": url,
            "markdown": text,
            "rendered": False,
            "notice": (
                "The browser service is not enabled, so this page was fetched without running "
                "its JavaScript. If the content looks empty or incomplete, that is why."
            ),
        }

    # Same gate as the MCP path. The browser service cannot check membership
    # itself (zero roles, no Firestore), so every caller checks before dialling.
    try:
        assert_allowed(user_id)
    except NotAllowed as exc:
        return {"url": url, "markdown": "", "rendered": False, "notice": str(exc)}

    try:
        await _call_gateway("browser_navigate", {"url": url}, user_id, session_id)
        markdown = await _call_gateway("browser_markdown", {}, user_id, session_id)
        return {"url": url, "markdown": markdown, "rendered": True}

    except IdTokenUnavailable as exc:
        log.error("browse: no credentials for the browser service: %s", exc)
        return await _degrade(url, "the browser service could not be authenticated")

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            # Never a silent 4xx (CLAUDE.md #8). This is usually the allowlist,
            # and the user needs to know that rather than see an empty page.
            detail = _text_of(exc.response)
            log.info("browse: gateway refused for user=%s: %s", user_id, detail or status)
            return {
                "url": url,
                "markdown": "",
                "rendered": False,
                "notice": detail
                or (
                    "The browser is not enabled for your account. "
                    "Ask an administrator to add you to the browser allowlist."
                ),
            }
        log.warning("browse: gateway returned %s for %s", status, url)
        return await _degrade(url, f"the browser service returned an error ({status})")

    except Exception as exc:
        log.warning("browse: gateway call failed for %s: %s", url, exc)
        return await _degrade(url, f"the browser service was unreachable ({exc.__class__.__name__})")


async def _degrade(url: str, reason: str) -> dict:
    """Fall back to the JS-blind fetch, saying plainly that we did."""
    text = await url_processing(url)
    return {
        "url": url,
        "markdown": text,
        "rendered": False,
        "notice": (
            f"This page was fetched WITHOUT running its JavaScript because {reason}. "
            "If it looks empty or incomplete, that is why — do not assume the page has no content."
        ),
    }


def _text_of(response: httpx.Response) -> str:
    try:
        body = response.json()
        content = (body.get("result") or {}).get("content") or []
        return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
    except Exception:
        return ""


def _identity(tool_context: Any) -> tuple[str, str]:
    """Best-effort caller identity for the gateway headers.

    ADK hands tools a ToolContext; the browser service refuses a request with no
    user, so falling back to a placeholder here produces a clear refusal rather
    than a confusing success as somebody else.
    """
    user_id = "unknown"
    session_id = "unknown"
    try:
        invocation = getattr(tool_context, "_invocation_context", None)
        if invocation is not None:
            user_id = getattr(invocation, "user_id", None) or user_id
            session = getattr(invocation, "session", None)
            session_id = getattr(session, "id", None) or session_id
    except Exception:  # pragma: no cover - defensive
        pass
    return user_id, session_id
