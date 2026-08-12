"""A2A `message/send` invocation surface — mounted at /a2a.

Pairs with `protocols.a2a` (discovery card). This module mounts ADK's
`to_a2a()` Starlette adapter on the FastAPI app so peer agents can POST
strict A2A v0.2 JSON-RPC (`message/send`, `tasks/get`,
`message/sendSubscribe`) and have it execute through our existing
ADK agent + the same backing services (artifact / session / memory).

Why bridge instead of write our own:
  ADK ships `google.adk.a2a.utils.agent_to_a2a.to_a2a` which returns a
  full Starlette A2A server using the `a2a-sdk` `A2AStarletteApplication`
  internally. We get `message/send`, `message/sendSubscribe`,
  `tasks/get`, `tasks/cancel`, and push-notification config RPCs for free
  by passing it our `BaseAgent` + a pre-built `Runner` + the `AgentCard`
  we already author by hand in `protocols.a2a._build_card_model`.

Why we pass our own card:
  ADK's `AgentCardBuilder` would auto-derive a card from the agent's
  metadata and lose our 7 extension descriptors (a2ui-*, mcp-apps-v1,
  adk-workflow-v1), our `protocolVersion: "0.2.0"`, and our hand-tuned
  skill descriptions. Passing `agent_card=` keeps the discovery card
  (served at `/.well-known/agent.json`) and the mounted A2A card
  (served at `/a2a/.well-known/agent.json` by ADK) byte-identical —
  Discovery Engine / Gemini Enterprise see one card, not two.

Why one runner:
  `to_a2a(runner=...)` accepts a pre-built Runner. We construct it
  from the same `get_session_service` / `get_memory_service` /
  `get_artifact_service` singletons the AG-UI surface
  (`adk.agui.build_agui_adk_agent`) uses, so A2A invocations and AG-UI
  invocations share the same session storage on Vertex Agent Engine
  (when `AGENT_ENGINE_ID` is set). OpenTelemetry traces, BigQuery
  logging, and chat history are uniform across the two surfaces.

Stability note: `to_a2a` is decorated `@a2a_experimental` in
google-adk. Pin the dep version and re-verify on minor bumps.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import HTTPException
from google.adk.runners import Runner
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from adk.agui import APP_NAME

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from google.adk.agents import BaseAgent
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)

# A2A spec JSON-RPC error codes — using -32000 (server error reserved range)
# for auth-denied. A2A v0.2 doesn't reserve a dedicated auth code, so we mirror
# JSON-RPC 2.0 implementation-defined server-error semantics.
_AUTH_ERROR_CODE = -32000


def _jsonrpc_error_response(status: int, message: str, request_id: object = None) -> JSONResponse:
    """Return an HTTP error wrapping a JSON-RPC 2.0 error envelope.

    JSON-RPC clients expect `{jsonrpc, id, error}` even on auth failure;
    a bare HTTP 401 with an HTML body would confuse a strict A2A client.
    `id` is null when we can't determine it (no body parsed yet) — per
    RFC, that's the correct value for non-parseable requests.
    """
    return JSONResponse(
        status_code=status,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": _AUTH_ERROR_CODE, "message": message},
        },
    )


class A2AAuthMiddleware(BaseHTTPMiddleware):
    """Run the same Firebase / group-auth / LOCAL_MODE auth as /api/skill/*.

    Discovery (well-known) is unauthenticated by A2A spec — the mounted
    A2A surface still serves `/.well-known/agent.json` and
    `/.well-known/agent-card.json` for backwards compat without auth.
    Invocation (POST /, /tasks/*, etc.) requires Bearer auth, matching
    the policy on `/api/skill/{id}/stream`.

    Gated by env `A2A_INVOCATION_REQUIRE_AUTH` (default `true`). Forks
    that need to disable auth to integrate with a particular peer-agent
    routing flow (e.g. some Gemini Enterprise auth modes inject service
    identity differently) can set it to `false` and rely on network-level
    isolation. Production should always leave it `true`.

    On auth failure the middleware returns a JSON-RPC 2.0 error envelope
    — a strict A2A client expects that shape even at the HTTP layer, so
    a bare 401 HTML page would break the client.
    """

    # Paths under the /a2a mount that stay unauthenticated. The A2A
    # discovery card is public per the spec; everything else requires a
    # Bearer token. Paths here are relative to the mount root (not the
    # full URL), because the middleware sees post-mount paths.
    _UNAUTH_PATHS = ("/.well-known/agent.json", "/.well-known/agent-card.json")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not _auth_required():
            return await call_next(request)

        # Per-A2A-spec discovery paths stay public.
        if request.url.path in self._UNAUTH_PATHS:
            return await call_next(request)

        # Lazy import: auth.__init__ pulls Firebase Admin SDK on first
        # touch; we don't want it on module load.
        from auth import get_current_user

        try:
            user = await get_current_user(request)
        except HTTPException as exc:
            return _jsonrpc_error_response(exc.status_code, str(exc.detail))
        except Exception:
            logger.exception("a2a auth: unexpected error verifying token")
            return _jsonrpc_error_response(500, "internal auth error")

        # Make the resolved user available downstream (e.g. for audit
        # logging) — the A2A executor itself doesn't consult it today,
        # but stashing it on request.state matches what /api/skill does.
        request.state.user = user
        return await call_next(request)


def _auth_required() -> bool:
    return os.environ.get("A2A_INVOCATION_REQUIRE_AUTH", "true").lower() in (
        "true",
        "1",
        "yes",
    )


def _file_input_enabled() -> bool:
    """Whether the A2A FilePart extraction interceptor should be wired in.

    Even when truthy, the interceptor itself rechecks ``ENABLE_A2A_FILE_INPUT``
    per-invocation so toggling the env var at runtime takes effect without
    a redeploy — this helper only decides whether to add it to the
    interceptor list at build time. Keeping it in the list with a
    runtime gate (rather than conditionally appending) means the
    A2aAgentExecutor instance is consistent across configurations and
    the regression guard test doesn't need to swap env vars.
    """
    return os.environ.get("ENABLE_A2A_FILE_INPUT", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _build_interceptors(runner: Runner) -> list:
    """Construct the ExecuteInterceptor list for the A2aAgentExecutor.

    Returns the FileExtractionInterceptor when ENABLE_A2A_FILE_INPUT is
    set; empty list otherwise. The interceptor module itself re-checks
    the env var per-request so this build-time check is only a small
    optimisation (skip the lazy-import + dataclass construction when
    the feature is off everywhere).

    Args:
        runner: The same Runner the executor will use — interceptor
            needs access to artifact_service + session_service.
    """
    if not _file_input_enabled():
        return []
    # Lazy import — only pull the interceptor module in when needed.
    from protocols.file_extraction import make_file_extraction_interceptor

    return [make_file_extraction_interceptor(runner, app_name=APP_NAME)]


def _build_runner(agent: BaseAgent) -> Runner:
    """Construct a Runner with our singleton backing services.

    Imports `adk.session` lazily so importing this module doesn't pull
    Vertex SDK initialisation into hot startup paths (test isolation,
    fast CLI boot). Same lazy pattern as `adk.agui.build_agui_adk_agent`.
    """
    from adk.session import (
        get_artifact_service,
        get_memory_service,
        get_session_service,
    )

    return Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=get_session_service(),
        memory_service=get_memory_service(),
        artifact_service=get_artifact_service(),
    )


def build_a2a_app(
    agent: BaseAgent,
    base_url: str,
) -> Starlette:
    """Build the Starlette sub-app that handles A2A JSON-RPC invocation.

    Returns a Starlette app suitable for `fast_api_app.mount("/a2a", ...)`.
    The mounted app exposes (paths relative to the mount):
      - `POST /` — A2A JSON-RPC entry point (`message/send`,
        `message/sendSubscribe`, `tasks/get`, `tasks/cancel`, etc.)
      - `GET /.well-known/agent-card.json` — A2A v0.3+ canonical card path
      - `GET /.well-known/agent.json` — A2A v0.2 backward-compat path
        (a2a-sdk serves both with the same body)

    Why we don't use `google.adk.a2a.utils.agent_to_a2a.to_a2a` directly:
      `to_a2a()` registers its A2A routes via a Starlette LIFESPAN event.
      When the app is mounted as a sub-app on FastAPI (via `app.mount(...)`),
      Starlette does NOT propagate lifespan to mounted sub-apps by default —
      the lifespan never fires, so the routes never register, and every
      request to `/a2a/*` returns 404. We replicate the same setup
      synchronously here (using the same `a2a-sdk` building blocks ADK
      itself uses) so the routes exist at mount time.

    Args:
        agent: The ADK agent to expose for A2A invocation. v1 wires the
            `ap-orchestrator` here — its existing SequentialAgent pipeline
            handles specialist routing internally, so one A2A endpoint
            covers the full AP flow.
        base_url: The public-facing base URL of the deployed app (e.g.
            `https://gde-ap-agent-...run.app`). The card advertises
            `<base_url>/a2a` as the invocation URL — peers POST there.
    """
    # Lazy import: protocols.a2a's discovery surface should stay light;
    # only the invocation surface needs the AgentCard pydantic model.
    # Same lazy reason for the a2a-sdk symbols and ADK A2A executor.
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryPushNotificationConfigStore, InMemoryTaskStore
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.executor.config import A2aAgentExecutorConfig
    from starlette.applications import Starlette

    from protocols.a2a import _build_card_model

    agent_card = _build_card_model(base_url)
    runner = _build_runner(agent)

    auth_required = _auth_required()
    logger.info(
        "a2a_invocation.build_a2a_app: mounting A2A surface for agent=%s, card.url=%s, auth_required=%s",
        agent.name,
        agent_card.url,
        auth_required,
    )

    # Mirror what `google.adk.a2a.utils.agent_to_a2a.to_a2a` does in its
    # lifespan, but SYNCHRONOUSLY at construction time so the routes
    # exist when FastAPI mounts the sub-app. Building blocks are stable
    # public API on the `a2a-sdk` package; the experimental layer is
    # only ADK's `A2aAgentExecutor` (the actual agent-runner glue),
    # which we still use.
    #
    # FileExtractionInterceptor (G46 M1): pulls A2A FileParts off the
    # incoming message and injects them into session state as
    # document_ids the existing doc-loader callback understands. Gated by
    # `ENABLE_A2A_FILE_INPUT` env var (default off — flag-off behaviour
    # is byte-identical to no interceptor; the interceptor itself returns
    # early when disabled).
    interceptors = _build_interceptors(runner)
    executor_config = A2aAgentExecutorConfig(execute_interceptors=interceptors)
    # `force_new_version=True` is REQUIRED for interceptors to actually fire.
    # ADK's A2aAgentExecutor has two impl paths: NEW (with interceptors) and
    # LEGACY (no interceptors). It picks NEW only if either the caller sets
    # force_new_version OR the peer sends a "new-version" extension hint in
    # the X-A2A-Extensions header. Gemini Enterprise doesn't send that hint
    # (verified live 2026-06-08 on the gde-ap-agent fork), so without
    # force_new_version our file extraction interceptor silently never runs.
    # The regression guard in test_file_extraction.py asserts this flag is set.
    executor = A2aAgentExecutor(runner=runner, config=executor_config, force_new_version=True)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    a2a_starlette = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Build the sub-app and have a2a-sdk extend its routes. This is the
    # same call the lifespan in to_a2a() makes — we just invoke it
    # eagerly so mounting works without lifespan propagation.
    a2a_app = Starlette()
    a2a_starlette.add_routes_to_app(a2a_app)

    # Auth middleware attaches to the SUB-APP so it sees post-mount paths
    # (`/`, `/.well-known/agent.json`, etc.) — the FastAPI mount strips the
    # `/a2a` prefix before delegating.
    a2a_app.add_middleware(A2AAuthMiddleware)
    return a2a_app


# Public-by-mistake imports the tests use to exercise the middleware in
# isolation without standing up the full FastAPI app — these are also the
# right import surface for forks that want to attach their own middleware
# stack on top.
__all__ = ["A2AAuthMiddleware", "build_a2a_app"]
