"""The MCP gateway: authenticated MCP-over-HTTP in, MCP-over-stdio out.

This is what makes the browser reachable from a skill with one line of
SKILL.md — `mcp.servers: ["browser"]` — rather than needing browser-specific
code in the agent build path. The backend's existing MCP registry dials this
service like any other MCP server; it happens to be a gateway rather than a
leaf, which the caller never has to know.

Two things it does that a plain proxy would not:

* **Filters `tools/list`.** The model can only call what it can name, so the
  credential tools are removed from the listing rather than merely refused on
  call. Defence by absence beats defence by rejection.
* **Pre-checks URLs.** Obscura's own SSRF guard fires correctly but reports a
  generic network error, so a refusal and an outage look identical. We resolve
  first and give the real reason (CLAUDE.md #8).

Identity arrives in headers, not tool arguments. `X-Aitana-User` is set by the
backend's `header_provider`; the model never sees it and therefore cannot
forge it by writing a different value into a tool call.

Who may SEND that header: on Cloud Run, IAM (`run.invoker` for the backend's SA
only). Self-hosted on a shared machine there is no IAM, and any local process
could claim any user. Set `BROWSER_SHARED_SECRET` (the same value as the
backend's `BROWSER_SERVICE_SECRET`) and every request must carry it in
`X-Browser-Service-Secret`, compared in constant time.
"""

from __future__ import annotations

import copy
import hmac
import logging
import os
import time
import uuid
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server

import allowlist
from allowlist import NotAllowed
from sessions import SessionGone, SessionRegistry, TooManySessions
from ssrf import BlockedUrl, assert_fetchable

log = logging.getLogger(__name__)

USER_HEADER = "x-aitana-user"
SESSION_HEADER = "x-aitana-session"
SECRET_HEADER = "x-browser-service-secret"


def _shared_secret() -> str:
    """Caller secret for self-hosted deployments; empty = rely on the platform (IAM)."""
    return os.environ.get("BROWSER_SHARED_SECRET", "")


# Tools whose first argument is a URL we must vet before Obscura sees it.
_URL_ARG_TOOLS = {"browser_navigate": "url"}

# Obscura's `browser_markdown` truncates at max_chars, DEFAULT 4000, and appends
# "...(truncated, N more chars)". 4000 characters is roughly a page and a half:
# react.dev came back as 4033 of 16078 chars, news.ycombinator.com as 4032 of
# 9974 (measured 2026-09-02).
#
# The model will not know to raise it, and silently reading 25% of a page is
# precisely the "confabulate over incomplete content" failure this service
# exists to fix — worse than the JS-blind fetch it replaces, because the
# truncated content LOOKS complete.
#
# So the supervisor supplies a default when the caller does not. Not unlimited:
# the backend offloads any tool result over 50K to an artifact, which strands
# the render path, so this stays clear of that threshold. An explicit
# max_chars from the caller always wins.
MARKDOWN_DEFAULT_MAX_CHARS = int(os.environ.get("BROWSER_MARKDOWN_MAX_CHARS", "40000"))
_MAX_CHARS_DEFAULTS = {"browser_markdown": ("max_chars", MARKDOWN_DEFAULT_MAX_CHARS)}

# ...and the ADVERTISED schema has to be rewritten too, or the default above
# almost never fires.
#
# `setdefault` only helps when the caller OMITS the argument. But the model
# reads Obscura's own schema, which announces `default: 4000`, and copies that
# number into the call — so it arrives explicitly set, the supervisor's default
# politely stands aside, and the page is truncated exactly as before.
#
# Watched live on dev, 2026-09-03: browser_markdown{"max_chars":4000} came back
# a quarter of solidjs.com, and the model spent SIX further browser_search calls
# hunting for a tagline that was in the part it never received. Test, whose
# model happened to omit the argument, answered the same question in two calls.
# The mitigation was there and did nothing, because it was fixing the rarer half
# of the problem.
#
# So the number the model is TOLD is the number we want it to send.
_SCHEMA_DEFAULT_REWRITES = {"browser_markdown": {"max_chars": MARKDOWN_DEFAULT_MAX_CHARS}}


def _rewrite_advertised_defaults(tool: types.Tool) -> types.Tool:
    """Restate our defaults in the schema the model reads.

    Upstream's advice for the parameter is written for a small context window;
    ours is a full page, and a truncated page that LOOKS complete is the failure
    this service exists to remove.
    """
    rewrites = _SCHEMA_DEFAULT_REWRITES.get(tool.name)
    if not rewrites:
        return tool

    schema = copy.deepcopy(tool.inputSchema or {})
    props = schema.get("properties")
    if not isinstance(props, dict):
        return tool

    changed = False
    for arg_name, value in rewrites.items():
        prop = props.get(arg_name)
        if not isinstance(prop, dict):
            continue
        prop["default"] = value
        prop["description"] = (
            f"Maximum characters of page text to return. Defaults to {value}, which is a whole "
            "page for almost any site. Do not lower it to fit a page you have not seen: a "
            "truncated page reads as complete and you will answer from the part you did not get. "
            "Omit this argument unless you specifically want less."
        )
        changed = True

    return tool.model_copy(update={"inputSchema": schema}) if changed else tool


registry = SessionRegistry()

# Tool schemas are identical for every user, so one probe at startup serves all
# listings. Populated lazily by `_tool_catalogue`.
_catalogue: list[types.Tool] | None = None
_catalogue_at: float = 0.0
_CATALOGUE_TTL = 900.0


class _RequestIdentity:
    """Who is calling, resolved from headers."""

    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id


def _identity_from_headers(headers: dict[str, str]) -> _RequestIdentity:
    """Pull the caller out of the request headers.

    Fails closed: an unidentified caller is refused rather than treated as
    anonymous. Cloud Run IAM has already proven the *service* may call us; this
    header says which *person* it is calling for, and per-user session isolation
    is meaningless without it.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    secret = _shared_secret()
    if secret and not hmac.compare_digest((lowered.get(SECRET_HEADER) or "").encode(), secret.encode()):
        raise NotAllowed(
            "The browser refused this caller: it did not present the service secret. "
            "This is a configuration problem, not something you did."
        )
    user_id = (lowered.get(USER_HEADER) or "").strip()
    if not user_id:
        raise NotAllowed(
            "The browser could not tell which user this request is for. "
            "This is a configuration problem, not something you did."
        )
    session_id = (lowered.get(SESSION_HEADER) or "").strip() or f"auto-{uuid.uuid4().hex[:12]}"
    return _RequestIdentity(user_id=user_id, session_id=session_id)


def _current_headers(server: Server) -> dict[str, str]:
    """Headers of the in-flight MCP request, via the low-level request context."""
    try:
        ctx = server.request_context
    except LookupError:
        return {}
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None)
    if headers is None:
        return {}
    try:
        return dict(headers)
    except Exception:  # pragma: no cover - defensive
        return {}


async def _tool_catalogue() -> list[types.Tool]:
    """Obscura's tool schemas, filtered to the allowlist.

    Probed once from a throwaway subprocess and cached: schemas do not vary by
    user, and paying a spawn per `tools/list` would make every agent build slow.
    """
    global _catalogue, _catalogue_at

    now = time.monotonic()
    if _catalogue is not None and (now - _catalogue_at) < _CATALOGUE_TTL:
        return _catalogue

    probe = await registry.get_or_create(user_id="__catalogue__", session_id="probe")
    try:
        tools = await probe.list_tools()
    finally:
        await registry.close("__catalogue__", "probe", reason="tool catalogue probe finished")

    permitted = set(allowlist.filter_tools([t.name for t in tools]))
    _catalogue = [_rewrite_advertised_defaults(t) for t in tools if t.name in permitted]
    _catalogue_at = now

    log.info(
        "browser: tool catalogue ready — %d of %d obscura tools exposed",
        len(_catalogue),
        len(tools),
    )
    return _catalogue


def _text(message: str) -> list[types.ContentBlock]:
    return [types.TextContent(type="text", text=message)]


def build_server() -> Server:
    """Construct the low-level MCP server."""
    server: Server = Server("aitana-browser")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        # The listing is gated too, not just the calls: a user who may not
        # browse should not see a browser in their tool list at all.
        # Identity only. Membership is the backend's decision — see the note in
        # allowlist.py: this service holds no roles and so cannot read the
        # policy store, and a second weaker copy of a policy it cannot evaluate
        # would be worse than none.
        try:
            _identity_from_headers(_current_headers(server))
        except NotAllowed as exc:
            log.info("browser: tools/list refused: %s", exc)
            return []
        return await _tool_catalogue()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.ContentBlock]:
        arguments = arguments or {}
        started = time.monotonic()

        try:
            identity = _identity_from_headers(_current_headers(server))
        except NotAllowed as exc:
            return _text(str(exc))

        # --- gates, each returning something the user can actually read ---

        if name not in allowlist.ALLOWED_TOOLS:
            # Should be unreachable: it is not in tools/list. Refuse loudly
            # anyway — a model that invented a name must not get a silent no-op.
            log.warning("browser: user=%s attempted withheld tool %r", identity.user_id, name)
            return _text(f"The browser tool {name!r} is not available.")

        # Raise Obscura's stingy truncation default unless the caller chose one.
        default = _MAX_CHARS_DEFAULTS.get(name)
        if default is not None:
            arg_name, value = default
            arguments.setdefault(arg_name, value)

        url_arg = _URL_ARG_TOOLS.get(name)
        if url_arg and isinstance(arguments.get(url_arg), str):
            try:
                assert_fetchable(arguments[url_arg])
            except BlockedUrl as exc:
                log.info("browser: user=%s blocked url %r: %s", identity.user_id, arguments[url_arg], exc)
                return _text(str(exc))

        # --- run it -------------------------------------------------------

        try:
            session = await registry.get_or_create(identity.user_id, identity.session_id)
        except TooManySessions as exc:
            return _text(str(exc))
        except Exception as exc:
            log.exception("browser: could not start a session for user=%s", identity.user_id)
            return _text(f"The browser could not be started: {exc}")

        try:
            result = await session.call_tool(name, arguments)
        except SessionGone as exc:
            return _text(str(exc))
        except Exception as exc:
            log.exception("browser: tool %s failed for user=%s", name, identity.user_id)
            return _text(f"The browser could not complete {name}: {exc}")

        elapsed_ms = (time.monotonic() - started) * 1000
        log.info(
            "browser: tool=%s user=%s session=%s ok elapsed_ms=%.0f peak_rss_mb=%.0f",
            name,
            identity.user_id,
            identity.session_id,
            elapsed_ms,
            session.peak_rss_mb,
        )

        content = list(getattr(result, "content", None) or [])
        if not content:
            # Never a silent empty result — the caller gets a terminal state.
            return _text(f"{name} completed but returned nothing.")
        return content

    return server


def service_identity() -> dict[str, str]:
    """What this process is running as, for `aiplatform browse probe`.

    The likeliest production failure for this service is an IAM or ingress
    misconfiguration, so the service should be able to say who it is without
    anyone having to open the console.
    """
    info = {
        "service": os.environ.get("K_SERVICE", "local"),
        "revision": os.environ.get("K_REVISION", "local"),
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT", "unknown"),
        "commit": os.environ.get("COMMIT_SHA", "unknown"),
        "service_account": "unknown",
    }
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            info["service_account"] = resp.read().decode().strip()
    except Exception:
        pass
    return info
