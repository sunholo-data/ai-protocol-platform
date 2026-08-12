"""
Aitana Platform v6 — FastAPI application.

Uses ADK's get_fast_api_app() for the agent endpoints,
plus custom routes for channels, direct API, and protocols.
"""

import logging
import os
import sys as _sys_for_local_mode

import firebase_admin
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

from adk.session import get_artifact_service, get_artifact_service_uri, get_memory_service_uri, get_session_service_uri
from config.gcp import check_startup_project, resolve_gcp_credentials
from config.local_mode import (
    assert_safe_local_mode,
    disabled_services,
    is_local_mode,
    warn_on_session_artifact_pairing,
)
from observability.logging_setup import setup_logging
from observability.telemetry import setup_telemetry

# ----------------------------------------------------------------------------
# LOCAL_MODE safety + service banner (must run BEFORE any GCP init)
# ----------------------------------------------------------------------------
# If LOCAL_MODE=1 is paired with K_SERVICE / GAE_ENV / KUBERNETES_SERVICE_HOST
# the auth-bypass stub would be active in a deployed context — refuse to start.
assert_safe_local_mode()
warn_on_session_artifact_pairing()

if is_local_mode():
    # Print the banner directly to stderr so the operator sees it on `make dev`.
    # Logging here is unreliable — uvicorn hasn't wired the app-level logger yet.
    print(
        "[startup] LOCAL_MODE: ON — Firestore in-memory, auth stubbed.\n"
        "[startup]              Disabled: " + ", ".join(disabled_services()) + "\n"
        "[startup]              Data resets on next boot unless LOCAL_MODE_PERSIST=1.",
        file=_sys_for_local_mode.stderr,
        flush=True,
    )

# Configure logging FIRST (issue #39). Without this the root logger defaults to
# WARNING and every `logger.info` from our own modules — including the per-turn
# TTFT line that `LatencyTracker.emit_log()` writes on every chat turn — is
# dropped before it leaves the process. Runs in LOCAL_MODE too; it needs no
# credentials, it only sets a level and a formatter.
_log_level = setup_logging()

# In LOCAL_MODE, skip Cloud Trace / Cloud Logging telemetry init — no creds, no
# remote exporters. Logs still print to stdout.
if not is_local_mode():
    setup_telemetry()

_log = logging.getLogger(__name__)
_log.info("logging configured at %s", _log_level)

# Startup guard (v6.19.0, AIPLA #42): refuse to boot on a provably-wrong GCP
# project rather than logging a warning and carrying on. The old guard compared
# against a hardcoded brand prefix and was warn-only — it fired on every
# correctly-configured fork and was silent exactly when it mattered. See
# config.gcp.check_startup_project for the derivation rules.
_resolved_project = check_startup_project(local_mode=is_local_mode())
_log.info("GCP project: %s", _resolved_project)

# Touch the singleton here so the upload endpoint and ADK runner share the same instance.
get_artifact_service()

# print() rather than _log.info() because the app-level logger has no handler/level
# wired — uvicorn's own logger is the only one that reliably shows up at startup.
# These three banners are the at-a-glance confirmation that the laptop is hitting
# the cloud backends, not the silent in-memory fallbacks ag_ui_adk used to install.
import sys as _sys  # noqa: E402

_artifact_bucket = os.getenv("ADK_ARTIFACT_BUCKET")
_agent_engine_id = os.getenv("AGENT_ENGINE_ID")
# Local-dev escape hatch (TTFT-OPTIMIZATION 1.21): force in-memory session
# + memory services even when AGENT_ENGINE_ID is set. Cuts laptop TTFT
# from ~9s to ~2s by avoiding Vertex Agent Engine round-trips to
# europe-west1 per turn. Production unaffected — that env var is only
# set in dev shells. See docs/design/v6.1.0/ttft-optimization.md.
_force_local_session = os.getenv("AITANA_LOCAL_SESSION", "").strip().lower() == "memory"
_session_using_vertex = bool(_agent_engine_id) and not _force_local_session


def _service_banner(kind: str) -> str:
    """Compose the [startup] banner line for `Session service:` / `Memory service:`.
    Three cases: Vertex (production / non-overridden dev), forced-in-memory (local
    dev with the escape hatch on), or unset-AGENT_ENGINE_ID (fully local)."""
    if _session_using_vertex:
        if kind == "Session":
            return f"Vertex AI Agent Engine={_agent_engine_id} (chat history persists)"
        return f"Vertex AI Memory Bank={_agent_engine_id} (cross-session recall via load_memory tool)"
    suffix = "chat history" if kind == "Session" else "memory"
    if _force_local_session and _agent_engine_id:
        return f"in-memory (FORCED via AITANA_LOCAL_SESSION=memory — fast local dev; {suffix} will NOT persist)"
    return f"in-memory ({suffix} will NOT persist — set AGENT_ENGINE_ID for Vertex)"


print(f"[startup] Session service: {_service_banner('Session')}", file=_sys.stderr, flush=True)
print(f"[startup] Memory service: {_service_banner('Memory')}", file=_sys.stderr, flush=True)
print(
    "[startup] Artifact service: "
    + (
        f"GCS bucket={_artifact_bucket} (artifacts persist across reloads)"
        if _artifact_bucket
        else "in-memory (artifacts evaporate on reload — set ADK_ARTIFACT_BUCKET for GCS)"
    ),
    file=_sys.stderr,
    flush=True,
)

# Probe ADC once. ADK's otel_to_cloud=True branch calls google.auth.default()
# inside get_fast_api_app to wire Cloud Trace / Cloud Logging exporters, which
# crashes on CI runners with no credentials. Disable the flag when ADC is
# unavailable — Cloud Run and dev laptops still enable GCP telemetry; CI and
# offline tests fall back to no-op exporters.
_adc = resolve_gcp_credentials()
# LOCAL_MODE must ALSO gate this (v6.20.0, downstream fork entry #4). A
# developer with ADC on their machine — the normal case — kept _adc non-None in
# LOCAL_MODE, so ADK wired Cloud Trace / Cloud Logging exporters and the backend
# pushed telemetry to a real GCP project while the startup banner claimed
# "Disabled: cloud_trace, cloud_logging". For a FORK that means quietly
# exporting to OUR project. The banner's claim is now enforced, not aspirational.
_otel_to_cloud = _adc is not None and not is_local_mode()

# Belt-and-braces: stop the SDK at the source too, so anything that wires an
# exporter without consulting the flag above is still a no-op.
if is_local_mode():
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# Quota-project guard: when running locally with user ADC against Vertex AI
# Agent Engine, the SDK sets x-goog-user-project from credentials.quota_project_id
# — NOT from the project= argument. A drifted quota_project (commonly left over
# from working on another GCP project) makes every Vertex Sessions call fail
# with an opaque 401 CREDENTIALS_MISSING. Surface it loudly at boot so the
# dev sees the fix command before the first chat turn breaks.
# Cloud Run service accounts don't expose .quota_project_id, so this is a
# no-op there.
if os.getenv("AGENT_ENGINE_ID") and _adc is not None:
    _adc_quota_project = getattr(_adc[0], "quota_project_id", None)
    if _adc_quota_project and _resolved_project != "(unset)" and _adc_quota_project != _resolved_project:
        _log.error(
            "STARTUP ERROR: ADC quota_project=%r does not match GOOGLE_CLOUD_PROJECT=%r. "
            "Vertex AI Agent Engine calls will fail with 401 CREDENTIALS_MISSING. "
            "Fix: gcloud auth application-default set-quota-project %s",
            _adc_quota_project,
            _resolved_project,
            _resolved_project,
        )

# API-key-vs-Vertex self-heal: when the genai client sees both
# GOOGLE_GENAI_USE_VERTEXAI=true AND an API key in env, it attaches the key to
# Vertex calls, and Vertex Sessions/Memory reject API-key auth with a 401
# CREDENTIALS_MISSING — silently breaking every chat turn. A deploy that mounts
# GOOGLE_API_KEY (e.g. the shared secret-env list on platform-backend) would
# otherwise take the whole service down. Actively unset the offending vars here,
# BEFORE any genai client is created, so the app is robust regardless of the
# deploy env; the helper logs loudly so the env still gets cleaned at the source.
# (Locally, `make dev` already unsets them; this covers deploy + `uv run uvicorn`.)
from config.gcp import neutralize_api_key_in_vertex_mode  # noqa: E402

neutralize_api_key_in_vertex_mode()

# Initialise firebase-admin once, at import time. ADC (the Cloud Run service
# account, or `gcloud auth application-default login` locally) provides
# credentials — no env vars, no secret manager. Called defensively under a
# try/except so hot-reload or re-imports don't crash with ValueError.
# Skip in LOCAL_MODE — no GCP creds, no Firebase to talk to.
if not is_local_mode():
    try:
        firebase_admin.initialize_app()
    except ValueError:
        # Already initialised — safe to ignore.
        pass
else:
    # Seed the in-memory Firestore with workshop fixture so the demo skills
    # are visible the moment the user opens the chat UI. Idempotent.
    from db.local_fixture import seed_local_fixture

    seed_local_fixture()

allow_origins = os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Create the ADK FastAPI app with built-in agent endpoints
# Service URIs are env-var-driven: Vertex AI Agent Engine in production, in-memory locally
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    session_service_uri=get_session_service_uri(),
    artifact_service_uri=get_artifact_service_uri(),
    memory_service_uri=get_memory_service_uri(),
    allow_origins=allow_origins,
    otel_to_cloud=_otel_to_cloud,
)
app.title = "Sunholo AI Protocol Platform"
app.description = "Open-source AI protocol platform — Skills + AG-UI + A2UI + MCP Apps + A2A on Google ADK"
app.version = "6.0.0"

# Override ADK's built-in /list-apps route (added by web=True) to return the
# canonical APP_NAME constant instead of filesystem subdirectory names. ADK's
# default returns directory names which don't match APP_NAME, so the dev UI's
# "Agent not found: <dir>" error misleads fork authors into guessing wrong paths.
# Route is inserted at position 0 so it wins over the ADK-registered one.
from fastapi.routing import APIRoute  # noqa: E402

from adk.agui import APP_NAME as _APP_NAME  # noqa: E402


async def _list_apps_canonical():
    return [_APP_NAME]


app.router.routes.insert(
    0,
    APIRoute("/list-apps", _list_apps_canonical, methods=["GET"], include_in_schema=False),
)


# --- ADK-native route guard (SECURITY, 2026-08-07) -------------------------
#
# `get_fast_api_app(web=True, …)` mounts ADK's own routes — /apps/…, /run,
# /run_sse, /debug/trace/…, /dev-ui, /builder/… — and NONE of them carry our
# `Depends(get_current_user)`. Our proxy (frontend/src/app/api/proxy/[...path])
# is a deliberate catch-all that forwards any path and relies entirely on the
# backend to authenticate, so on a deployed env those routes were reachable
# from the public internet with no token at all. Measured 2026-08-07 on dev,
# test and test.yourcompany.com: `/api/proxy/api/skills` → 401 (correct) while
# `/api/proxy/apps/aitana_platform/users/{uid}/sessions` → 200.
#
# What that exposed: a session's full event list (every message, tool call and
# extracted clause — i.e. ONE's contract content, exactly what CLAUDE.md's
# security rule governs), its artifacts (parsed documents), DELETE on a
# session, PATCH on user memory, and /run + /run_sse, which EXECUTE the agent
# and bill for it. A Firebase uid is not a secret — it appears in URLs, logs
# and admin surfaces — so obscurity was never the boundary.
#
# This is middleware rather than per-route dependencies on purpose: the routes
# are registered by ADK, so we cannot decorate them, and a future `google-adk`
# bump that adds a route would silently reopen the hole. Middleware denies by
# path prefix, so new ADK routes are covered the day they appear.
#
# Auth reuses the app's real entry point (`get_current_user` → the same
# Firebase / group-auth / LOCAL_MODE dispatch every other route uses) plus
# `resolve_admin_scope`, so an admin token keeps working — including the
# `aitana-adk-testing` skill's token-mint recipe — and LOCAL_MODE dev is
# unaffected. Note the ADK dev UI / playground now needs an admin token too.
#
# Guarded by tests/api_tests/test_adk_native_route_guard.py, which walks the
# LIVE route table so an ADK-added route that escapes the prefixes fails CI
# rather than shipping open.
_ADK_NATIVE_PREFIXES = ("/apps/", "/debug/", "/builder/", "/dev-ui", "/dev/")
_ADK_NATIVE_EXACT = frozenset({"/list-apps", "/run", "/run_sse", "/run_live"})


def _is_adk_native_path(path: str) -> bool:
    """True for a route ADK registered, which therefore has no auth of its own.

    Our own routes all live under `/api/…` (plus `/health`, `/version`, `/mcp/…`,
    `/.well-known/agent.json`), so none of them match — `/api/debug/slow-stream`
    is ours and is NOT caught by the `/debug/` prefix.
    """
    return path in _ADK_NATIVE_EXACT or path.startswith(_ADK_NATIVE_PREFIXES)


# Imports are local to the function body: this module imports `Request`,
# `HTTPException` and `get_current_user` further down, and annotations here are
# evaluated eagerly (no `from __future__ import annotations`), so referencing
# them at def time would NameError on startup.
@app.middleware("http")
async def _require_admin_for_adk_native_routes(request, call_next):  # type: ignore[no-untyped-def]
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    if not _is_adk_native_path(request.url.path):
        return await call_next(request)

    from admin.scope import resolve_admin_scope
    from auth import get_current_user

    try:
        user = await get_current_user(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception:
        # Never let an auth-layer bug fail OPEN on these routes.
        logging.getLogger(__name__).exception("adk-route-guard: auth error on %s", request.url.path)
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    if resolve_admin_scope(user) is None:
        return JSONResponse(
            status_code=403,
            content={"detail": "aitana-admin group required for ADK-native routes"},
        )
    return await call_next(request)


# --- Health ---


@app.get("/health")
async def health():
    return {"status": "ok", "version": "6.0.0"}


# --- LOCAL_MODE status (public, no auth required) ---
# Frontend banner reads this to know whether to mount itself and to show
# which GCP services are stubbed. Always returns 200 so a fetch failure
# is unambiguously a connectivity issue, not "the backend doesn't know
# about LOCAL_MODE".


@app.get("/api/local-mode-status")
async def local_mode_status():
    return {
        "local_mode": is_local_mode(),
        "disabled_services": disabled_services(),
    }


# --- Custom endpoints (beyond ADK's built-in agent routes) ---

import json  # noqa: E402
from contextlib import AsyncExitStack, asynccontextmanager  # noqa: E402

from fastapi import Depends, HTTPException, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from adk.stream_invariants import redact_privileged_results, session_is_lower_trust  # noqa: E402
from admin.access_routes import router as admin_access_router  # noqa: E402
from admin.analytics_routes import router as admin_analytics_router  # noqa: E402
from admin.audit_routes import router as admin_audit_router  # noqa: E402
from admin.clients import me_router as clients_me_router  # noqa: E402
from admin.clients import router as admin_clients_router  # noqa: E402
from admin.compaction_replay_routes import router as admin_compaction_replay_router  # noqa: E402
from admin.compaction_second_pass_routes import router as admin_compaction_second_pass_router  # noqa: E402
from admin.group_tags_routes import members_router as admin_group_members_router  # noqa: E402
from admin.group_tags_routes import router as admin_group_tags_router  # noqa: E402
from admin.platform_config_routes import router as admin_platform_config_router  # noqa: E402
from admin.prewarm_routes import router as admin_prewarm_router  # noqa: E402
from admin.routes import router as admin_router  # noqa: E402
from admin.tenants import router as admin_tenants_router  # noqa: E402
from admin.tool_permissions_routes import router as admin_tool_permissions_router  # noqa: E402
from admin.users_routes import router as admin_users_router  # noqa: E402
from auth import User, get_current_user  # noqa: E402
from auth.group_routes import router as group_auth_router  # noqa: E402
from auth.routes import router as auth_router  # noqa: E402
from buckets.routes import router as buckets_router  # noqa: E402
from internal_tasks.recompact_routes import router as internal_tasks_recompact_router  # noqa: E402
from protocols.a2a import router as a2a_router  # noqa: E402
from protocols.a2ui_surface_action_routes import router as a2ui_surface_action_router  # noqa: E402
from protocols.a2ui_surface_action_run_routes import router as a2ui_surface_action_run_router  # noqa: E402
from protocols.a2ui_surface_data_routes import router as a2ui_surface_data_router  # noqa: E402
from protocols.iframe_context_routes import router as iframe_context_router  # noqa: E402
from protocols.mcp_proxy import router as mcp_proxy_router  # noqa: E402
from protocols.mcp_server import get_mcp_asgi_app  # noqa: E402
from protocols.mcp_server import mcp as mcp_server  # noqa: E402
from protocols.models_route import router as models_router  # noqa: E402
from protocols.session_bootstrap_routes import router as session_bootstrap_router  # noqa: E402
from protocols.sessions_route import router as sessions_router  # noqa: E402
from protocols.tools_route import router as tools_router  # noqa: E402
from protocols.voice_routes import router as voice_router  # noqa: E402
from skills.routes import router as skills_router  # noqa: E402
from skills.skill_processor import SkillNotFoundError, process_skill_request  # noqa: E402
from tools.documents.import_by_reference import router as documents_import_router  # noqa: E402
from tools.documents.rag_routes import router as rag_corpus_router  # noqa: E402
from tools.documents.routes import router as doc_folders_router  # noqa: E402
from tools.documents.upload import router as documents_router  # noqa: E402
from tools.media_utils import router as media_router  # noqa: E402

app.include_router(auth_router)
app.include_router(group_auth_router)
app.include_router(skills_router)
app.include_router(buckets_router)
app.include_router(admin_router)
app.include_router(admin_prewarm_router)
app.include_router(admin_clients_router)
app.include_router(admin_tenants_router)
app.include_router(admin_analytics_router)
app.include_router(admin_audit_router)  # v6.16.0 P4 audit reader
app.include_router(admin_users_router)
app.include_router(admin_group_tags_router)  # v6.9.0 9.3 group-tag registry
app.include_router(admin_group_members_router)  # v6.9.0 9.3 tag-holders reverse lookup
app.include_router(admin_access_router)  # v6.9.0 9.3 effective-access dry-run
app.include_router(admin_tool_permissions_router)  # v6.9.0 9.3 tool-permission admin plane
app.include_router(admin_platform_config_router)  # v6.14.0 platform preamble admin plane
app.include_router(admin_compaction_replay_router)  # compaction replay — read-only tuning tool
app.include_router(admin_compaction_second_pass_router)  # second-pass recompact — MUTATES; admin/CLI path (1e)
app.include_router(internal_tasks_recompact_router)  # Cloud Tasks OIDC-gated target (1e)
app.include_router(clients_me_router)
app.include_router(documents_router)
app.include_router(documents_import_router)
app.include_router(doc_folders_router)
app.include_router(rag_corpus_router)
app.include_router(media_router)
app.include_router(a2a_router)
app.include_router(models_router)
app.include_router(tools_router)
app.include_router(sessions_router)
app.include_router(session_bootstrap_router)
app.include_router(mcp_proxy_router)
app.include_router(iframe_context_router)
app.include_router(a2ui_surface_action_router)
app.include_router(a2ui_surface_action_run_router)
app.include_router(a2ui_surface_data_router)
app.include_router(voice_router)

# ----------------------------------------------------------------------------
# Channel framework (v6.1.0 sprint 1.6 M1)
# ----------------------------------------------------------------------------
# Channels self-register via `ChannelRegistry.register(...)`. `mount_webhooks`
# then exposes `POST /api/{name}/webhook` for each registered channel. M1
# ships the framework with no adapters; M2/M3 register Discord + Email here.
from channels.discord import DiscordChannel  # noqa: E402
from channels.email_ import EmailChannel  # noqa: E402
from channels.registry import ChannelRegistry  # noqa: E402
from channels.telegram_ import TelegramChannel  # noqa: E402
from channels.whatsapp import WhatsAppChannel  # noqa: E402

# Phase 1+ adapters register here.
# Each adapter gates on its required env var so local dev and LOCAL_MODE
# can boot without provider creds. Production sets the env vars via
# Secret Manager (see cloudbuild.yaml).
#
# M2 (Discord): gated on DISCORD_PUBLIC_KEY (needed for webhook verify).
# Adapters that need a persistent gateway connection expose `start_gateway()`,
# which the deployment wires into Cloud Run startup — we don't open the
# gateway from inside `import` because it would block.
_discord_public_key = os.getenv("DISCORD_PUBLIC_KEY", "")
if _discord_public_key:
    ChannelRegistry.register(DiscordChannel())
else:
    _log.info("discord channel not registered: DISCORD_PUBLIC_KEY not set")

# M3 (Email): gated on MAILGUN_SIGNING_KEY.
_mailgun_signing_key = os.getenv("MAILGUN_SIGNING_KEY", "")
if _mailgun_signing_key:
    ChannelRegistry.register(
        EmailChannel(
            signing_key=_mailgun_signing_key,
            api_key=os.getenv("MAILGUN_API_KEY", ""),
            domain=os.getenv("MAILGUN_DOMAIN", ""),
            sender_address=os.getenv("EMAIL_SENDER_ADDRESS", ""),
            api_endpoint=os.getenv("MAILGUN_ENDPOINT", "https://api.eu.mailgun.net"),
        )
    )
else:
    _log.info("email channel not registered: MAILGUN_SIGNING_KEY not set")

# M4 (Telegram): gated on TELEGRAM_BOT_TOKEN.
_telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
if _telegram_bot_token:
    ChannelRegistry.register(TelegramChannel())
else:
    _log.info("telegram channel not registered: TELEGRAM_BOT_TOKEN not set")

# M4 (WhatsApp): gated on TWILIO_ACCOUNT_SID.
_twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
if _twilio_account_sid:
    ChannelRegistry.register(WhatsAppChannel())
else:
    _log.info("whatsapp channel not registered: TWILIO_ACCOUNT_SID not set")

ChannelRegistry.mount_webhooks(app)

# Mount the MCP streamable-HTTP server. FastAPI does NOT propagate lifespan
# events to mounted sub-apps, so the FastMCP session_manager task group
# never starts — every request fails with "Task group is not initialized".
# We compose FastMCP's session_manager.run() into the parent app's lifespan
# so the task group is up for the lifetime of the service.
_parent_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_mcp(app_: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(_parent_lifespan(app_))
        await stack.enter_async_context(mcp_server.session_manager.run())
        yield


app.router.lifespan_context = _lifespan_with_mcp
app.mount("/mcp", get_mcp_asgi_app())

# ----------------------------------------------------------------------------
# A2A invocation surface (G45 / Sprint A2A-INVOKE)
# ----------------------------------------------------------------------------
# Mount the A2A JSON-RPC invocation Starlette sub-app at /a2a so peer agents
# and Gemini Enterprise can POST `message/send`, `tasks/get`, etc. The mount
# MUST happen AFTER all `app.include_router(...)` calls — Starlette dispatches
# mounts by prefix match, but ordering matters when paths could conflict.
#
# Feature-gated via ENABLE_A2A_INVOCATION (default off until we're ready to
# expose this in production). The try/except guards a partial init from
# taking out `/api/*` traffic — if the A2A sub-app fails to build we log and
# keep serving the rest of the platform.
if os.environ.get("ENABLE_A2A_INVOCATION", "false").lower() in ("true", "1", "yes"):
    try:
        from app import root_agent
        from protocols.a2a import A2A_INVOCATION_PATH
        from protocols.a2a_invocation import build_a2a_app

        _a2a_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:1956")
        app.mount(A2A_INVOCATION_PATH, build_a2a_app(root_agent, _a2a_base_url))
        _log.info("a2a invocation surface mounted at %s (base_url=%s)", A2A_INVOCATION_PATH, _a2a_base_url)
    except Exception:
        _log.exception("Failed to mount A2A invocation surface — continuing without it")


class _StreamSkillRequest(BaseModel):
    """Body schema for POST /api/skill/{skill_id}/stream.

    Accepts two wire formats in one model:

    1. Simple (CLI / tests):
       ``{"message": "hello", "sessionId": "..."}``

    2. AG-UI HttpAgent (frontend):
       ``{"threadId": "...", "runId": "...", "messages": [...], ...}``

    ``effective_message`` and ``effective_session_id`` normalize both shapes
    so the endpoint never needs to branch on which format arrived.
    """

    # Simple format
    message: str = ""
    sessionId: str | None = None
    attachments: list[dict] | None = None
    documentIds: list[str] | None = None

    # AG-UI HttpAgent format (extra fields silently ignored by Pydantic default)
    threadId: str | None = None
    runId: str | None = None
    messages: list[dict] = Field(default_factory=list)
    state: dict | None = None
    forwardedProps: dict | None = None

    model_config = ConfigDict(extra="ignore")

    @property
    def effective_session_id(self) -> str | None:
        return self.sessionId or self.threadId

    @property
    def effective_message(self) -> str:
        if self.message:
            return self.message
        for msg in reversed(self.messages):
            if msg.get("role") == "user" and msg.get("content"):
                return str(msg["content"])
        return ""


def _extract_document_ids(body: "_StreamSkillRequest") -> list[str] | None:
    """Pull the per-turn document_ids list from the wire body.

    Priority (multi-doc-context-fix.md / 1.22 Phase 2):
      1. ``forwardedProps.document_ids`` — the AG-UI HttpAgent path the
         chat page uses; this is the FRESH per-turn signal derived from
         the user's currently-ticked tabs.
      2. ``documentIds`` (top-level) — the simple/CLI/test wire format.
      3. ``state.document_ids`` — legacy fallback. AG-UI's HttpAgent
         mirrors backend STATE_SNAPSHOT events into ``agent.state`` and
         round-trips that state on every subsequent ``runAgent`` call;
         after turn N, the client sends turn N's state back on turn
         N+1. That makes ``state.document_ids`` ONE TURN BEHIND, so
         we read it last as a fallback only.

    Earlier order (state ahead of forwardedProps) caused the Bug-2026-04-28
    multi-doc regression: user opened doc 2 mid-session; backend kept
    seeing only doc 1's id because state had `[doc1]` from the prior
    turn's STATE_SNAPSHOT and forwardedProps was never consulted.
    See backend.log line ``WARNING:adk.callbacks:doc loader: turn start
    — document_ids=['6ecff3e0...']`` and the corresponding frontend
    console line showing ``includedDocIds= ["6ecff3e0...", "41ea1884...",
    "e222aa3d..."]`` — three ids out, one in.

    Returns None when no list is present so the loader treats the turn as
    "no docs attached" rather than "empty selection".
    """
    candidates = (
        (body.forwardedProps or {}).get("document_ids"),
        body.documentIds,
        (body.state or {}).get("document_ids"),
    )
    for value in candidates:
        if isinstance(value, list) and value:
            cleaned = [str(d) for d in value if d]
            if cleaned:
                return cleaned
    return None


def _extract_a2ui_surface_state(body: "_StreamSkillRequest") -> dict | None:
    """Pull the per-turn A2UI surface snapshot from
    ``forwardedProps.a2ui_surface_state`` (the AG-UI HttpAgent path).

    Sprint 2.10. Shape: ``{surfaceId: {catalogId, dataModel}}``. None when
    the frontend hasn't sent any active surface state (no A2UI rendered
    yet, or all surfaces empty). The
    ``wrap_with_a2ui_surface_context`` InstructionProvider treats a
    missing key as "no block to inject" — it's safe to always thread
    the value through.

    Defensive: we only accept dicts. A list / string / number from a
    misbehaving client is dropped rather than crashing the stream.
    """
    raw = (body.forwardedProps or {}).get("a2ui_surface_state")
    if isinstance(raw, dict) and raw:
        return raw
    return None


def _extract_resumed_flag(body: "_StreamSkillRequest") -> bool:
    """True when the frontend signalled this chat was entered by clicking a
    conversation thread from the per-document Conversations panel.

    Read from forwardedProps.resumed_session (the AG-UI HttpAgent path) or
    state.resumed_session (custom state path). Triggers eager doc injection
    in the agent's first LLM request — see make_document_injector.
    """
    candidates = (
        (body.state or {}).get("resumed_session"),
        (body.forwardedProps or {}).get("resumed_session"),
    )
    return any(bool(v) for v in candidates)


from observability.timing import (  # noqa: E402
    STAGE_FIRST_SSE_BYTE,
    STAGE_REQUEST_RECEIVED,
    STAGE_SESSION_INDEX_DONE,
    LatencyTracker,
    reset_current_tracker,
    set_current_tracker,
)


@app.post("/api/skill/{skill_id}/stream")
async def stream_skill(
    skill_id: str,
    body: _StreamSkillRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """SSE endpoint: stream AG-UI events for one turn of `skill_id`.

    Non-existent OR non-visible skills both return 404 — do not leak
    skill existence to callers who cannot see them. `get_current_user`
    populates `request.state.access` so the processor can apply the
    same 5-type access rules the CRUD routes use.

    When ``sessionId`` is set and the session is owned by another user the
    caller can see (tagged access), the stream opens read-only:
      1. First frame: ``{"type": "session_meta", "isReadOnly": true}``
      2. Agent is not invoked; message is ignored.
    The caller's UI should disable the composer on ``isReadOnly: true``.
    """
    # Bind a per-request LatencyTracker to the async context so all
    # downstream callbacks (loader, injector, tool hooks) can call
    # ``get_current_tracker().mark(...)`` without explicit plumbing. The
    # finally below resets the binding and emits the structured log.
    tracker = LatencyTracker(
        skill_id=skill_id,
        session_id=body.effective_session_id or "",
        user_id=user.uid,
    )
    tracker.mark(STAGE_REQUEST_RECEIVED)
    _tracker_token = set_current_tracker(tracker)
    request.state.latency_tracker = tracker

    access = request.state.access
    is_read_only = False
    found_existing_session = False
    session_id = body.effective_session_id

    # Only query Firestore when the caller explicitly requested resumption via
    # sessionId (the custom format field). threadId from HttpAgent is always
    # present — treating it as a resumption intent would emit session_meta on
    # every fresh chat, which breaks the AG-UI Zod discriminated union.
    if body.sessionId:
        from db.chat_sessions import get_session_index

        existing = get_session_index(body.sessionId)
        if existing is not None:
            found_existing_session = True
            if not access.can_access(existing):
                raise HTTPException(status_code=403, detail="Access denied to session")
            is_read_only = not access.is_owner(existing)

    extracted_doc_ids = _extract_document_ids(body) if not is_read_only else None
    extracted_resumed = _extract_resumed_flag(body) if not is_read_only else False
    extracted_surface_state = _extract_a2ui_surface_state(body) if not is_read_only else None
    _log.info(
        "stream_skill: skill=%s session=%s read_only=%s document_ids=%s resumed=%s "
        "wire_locations=(top:%s, state:%s, fwd:%s)",
        skill_id,
        session_id,
        is_read_only,
        extracted_doc_ids,
        extracted_resumed,
        body.documentIds,
        (body.state or {}).get("document_ids"),
        (body.forwardedProps or {}).get("document_ids"),
    )

    # The session_index write is the last synchronous step before the SSE
    # stream opens; mark its completion so the timing log distinguishes
    # "Firestore is slow" from "model is slow". Note that
    # ``process_skill_request`` itself does the write — we mark on the way
    # back from its first event, before agent code starts touching the model.
    event_iter = process_skill_request(
        skill_id=skill_id,
        user=user,
        access=access,
        session_id=session_id,
        message=body.effective_message if not is_read_only else "",
        attachments=body.attachments if not is_read_only else None,
        document_ids=extracted_doc_ids,
        resumed_session=extracted_resumed,
        a2ui_surface_state=extracted_surface_state,
    )
    try:
        # Surface SkillNotFoundError *before* returning the StreamingResponse so
        # the client sees a proper 404 rather than a half-open SSE stream.
        first_event: dict | None = await event_iter.__anext__()
    except SkillNotFoundError as exc:
        # Reset context binding even on the error path.
        reset_current_tracker(_tracker_token)
        # Deliberately the SAME response for missing and forbidden — skills use
        # 404 for both so a tag-gated skill's existence doesn't leak. But the
        # bare "Skill not found" told the user nothing they could act on, so it
        # now names the ref and both possibilities. process_skill_request has
        # already logged which one it actually was.
        raise HTTPException(
            status_code=404,
            detail=(
                f"Skill '{exc.ref}' is not available — it either doesn't exist "
                f"or your account doesn't have access to it. If you expect "
                f"access, ask an admin to check the skill's access tags."
            ),
        ) from exc
    except StopAsyncIteration:
        first_event = None

    # ``process_skill_request`` runs the synchronous _ensure_session_index
    # before yielding its first event, so by the time we get here the
    # session index is durable.
    tracker.mark(STAGE_SESSION_INDEX_DONE)

    # Probe param: when set, append a final LATENCY_REPORT Custom event so
    # the ``aiplatform skill probe`` CLI can read the per-stage timings without
    # scraping logs. Free of charge for normal callers.
    probe_mode = request.query_params.get("probe") == "1"

    # v6.19.0 (AIPLA #39): the agent events are re-joined into ONE generator
    # before any stream-boundary filter sees them. `first_event` was pulled out
    # above only so a SkillNotFoundError could become a 404 before the stream
    # opens — that is a routing concern, not a reason for two event paths. A
    # filter applied to the loop but not the prelude is exactly the divergence
    # the reporting fork warned about, so make the split un-reproducible rather
    # than remembering to patch both.
    async def _agent_events():
        if first_event is not None:
            yield first_event
        async for event in event_iter:
            yield event

    guarded_events = redact_privileged_results(
        _agent_events(),
        lower_trust=session_is_lower_trust(user.auth_mode, user.group_id),
        thread_id=session_id or "<none>",
    )

    async def _sse():
        first_byte_marked = False
        try:
            # Only emit session_meta when resuming an existing Firestore session.
            # HttpAgent always sends threadId (even for fresh chats) — we must not
            # treat that as a resumption signal or session_meta leaks into fresh
            # streams and breaks the client's Zod discriminated union.
            if found_existing_session:
                session_meta = json.dumps({"type": "session_meta", "isReadOnly": is_read_only})
                if not first_byte_marked:
                    tracker.mark(STAGE_FIRST_SSE_BYTE)
                    first_byte_marked = True
                yield f"data: {session_meta}\n\n"
            async for event in guarded_events:
                if not first_byte_marked:
                    tracker.mark(STAGE_FIRST_SSE_BYTE)
                    first_byte_marked = True
                yield f"data: {json.dumps(event)}\n\n"
            if probe_mode:
                report = tracker.build_latency_report_event()
                if report is not None:
                    yield f"data: {json.dumps(report.model_dump(by_alias=True, exclude_none=True))}\n\n"
        finally:
            tracker.emit_log()
            reset_current_tracker(_tracker_token)

    return StreamingResponse(_sse(), media_type="text/event-stream")


@app.get("/api/debug/slow-stream")
async def slow_stream_probe(
    seconds: float = 360.0,
    gap: float = 60.0,
    user: User = Depends(get_current_user),  # noqa: B008
) -> StreamingResponse:
    """MODEL-RELIABILITY M1 — transport probe for the long-stream failure class.

    Streams SSE ticks with `gap`-second silent stretches for `seconds`
    total: long *idle* gaps exercise idle-timeout reaping (undici
    bodyTimeout, LB idle), long *total* duration exercises the Cloud Run
    request timeout. `scripts/smoke-long-stream.sh` curls this through the
    deployed frontend proxy and fails if the stream dies early — the
    permanent regression guard for the v5 long-stream incident (deploy-skill
    trap 20).

    Gating: auth-required always; on Cloud Run additionally requires
    SLOW_STREAM_PROBE_ENABLED=true (a cloudbuild substitution — true while
    only dev deploys; flip off per-trigger when cutting test/prod). Local
    dev is always enabled.
    """
    import asyncio
    import time as _time
    from collections.abc import AsyncGenerator

    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    enabled = os.environ.get("SLOW_STREAM_PROBE_ENABLED", "").lower() == "true"
    if on_cloud_run and not enabled:
        raise HTTPException(status_code=404, detail="probe disabled on this environment")

    total = min(max(seconds, 0.1), 900.0)
    pause = min(max(gap, 0.05), 90.0)

    async def _ticks() -> AsyncGenerator[str, None]:
        start = _time.monotonic()
        n = 0
        while True:
            elapsed = _time.monotonic() - start
            if elapsed >= total:
                break
            n += 1
            yield f'data: {{"tick": {n}, "elapsed": {elapsed:.1f}}}\n\n'
            await asyncio.sleep(min(pause, total - elapsed))
        yield f'data: {{"done": true, "ticks": {n}, "elapsed": {_time.monotonic() - start:.1f}}}\n\n'

    return StreamingResponse(_ticks(), media_type="text/event-stream")


# TODO: Direct API endpoints (backward compat with v5 CLI)
# POST /direct/tools/ai-search
# POST /direct/tools/extract-files
# POST /direct/models/gemini
# GET  /direct/models

# TODO: Channel webhooks
# POST /api/telegram/webhook
# POST /api/email/webhook


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=1956)
