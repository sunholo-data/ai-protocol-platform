"""FastAPI wrapper: the MCP streamable-HTTP endpoint plus health and probe.

Authentication is NOT here. Cloud Run's IAM check runs at the edge, before a
request reaches this container, so the service is deployed
`--no-allow-unauthenticated` with `run.invoker` granted only to the backend's
service account. That is what supplies authentication to a binary that has none
of its own, and it is why nothing in this file inspects a bearer token.

What this file does enforce is that the caller says *which user* it is acting
for — see `gateway._identity_from_headers`. IAM proves the service may call;
the header says on whose behalf.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import gateway
from gateway import build_server, registry, service_identity

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

_server = build_server()

# stateless=True: Cloud Run may route consecutive requests to different
# instances, so MCP transport state must not live in one of them. Our own
# browser session state is keyed by (user, session) in the registry and is
# re-created on a cold instance — a miss costs a page load, not a lost session.
_session_manager = StreamableHTTPSessionManager(app=_server, stateless=True, json_response=True)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await registry.start()
    async with _session_manager.run():
        log.info("browser: ready — %s", service_identity())
        try:
            yield
        finally:
            await registry.stop()


app = FastAPI(title="platform-browser", lifespan=lifespan)


# NOT "/healthz". Google's frontend intercepts that exact path and answers it
# itself with a generic 404 — the request never reaches Cloud Run at all.
# Verified against the deployed dev service on 2026-09-02: `/healthz` returned
# 404 with no Server header, while `/health`, `/_health`, `/status`, `/livez`,
# `/readyz` and even `/healthz/` all returned 403 from "Google Frontend", i.e.
# they reached the IAM check normally. Only the bare `/healthz` is special.
#
# This is invisible locally, where uvicorn serves /healthz happily, and it would
# have broken `aiplatform browse probe` and the post-deploy smoke on every
# deployed environment.
@app.get("/status")
async def status() -> JSONResponse:
    """Liveness plus enough detail to diagnose without opening the console."""
    return JSONResponse(
        {
            "status": "ok",
            "sessions": registry.count,
            **service_identity(),
        }
    )


@app.get("/sessions")
async def sessions(request: Request) -> JSONResponse:
    """Live browser sessions — backs `aiplatform browse session list`.

    Lists user ids, so when BROWSER_SHARED_SECRET is set (self-hosted) it needs
    the same caller secret as /mcp. /status carries only counts and stays open.
    """
    secret = gateway._shared_secret()
    presented = request.headers.get(gateway.SECRET_HEADER, "")
    if secret and not hmac.compare_digest(presented.encode(), secret.encode()):
        return JSONResponse({"error": "service secret required"}, status_code=403)
    return JSONResponse({"sessions": registry.describe()})


# The MCP endpoint is dispatched OUTSIDE FastAPI.
#
# Two things went wrong routing it through FastAPI, both worth recording because
# both look like they should work:
#
#   * a plain `async def mcp_endpoint(request)` makes FastAPI treat `request` as
#     a QUERY PARAMETER (signature inspection), so every call 422s; and
#   * `Mount("/mcp", ...)` does not match the bare `/mcp` — Starlette issues a
#     307 to `/mcp/`. A redirect on a POST is a trap: it survives only if the
#     client follows 307s preserving method and body, and httpx does not by
#     default. The backend registry dials `/mcp` exactly, so this must work
#     without a redirect.
#
# An explicit outer dispatcher avoids both and keeps the raw (scope, receive,
# send) triple the session manager wants, with no reach into Request._send.
_fastapi_app = app


async def app(scope, receive, send) -> None:  # type: ignore[no-redef]
    """ASGI entrypoint: /mcp to the MCP session manager, everything else to FastAPI."""
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        await _session_manager.handle_request(scope, receive, send)
        return
    await _fastapi_app(scope, receive, send)
