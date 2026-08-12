"""Unit tests for protocols.agui.mount_skill_endpoint.

Verifies that the AG-UI mount helper wires an ADK agent into a FastAPI
app at the expected path and with the expected service wiring (the real
session/memory/artifact backends from adk.session, NOT ag_ui_adk's
silent InMemory defaults). Does NOT exercise the live agent loop —
that's covered by tests/api_tests/test_stream_skill.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService

from protocols.agui import mount_skill_endpoint


@pytest.fixture
def dummy_agent() -> Agent:
    """An Agent with no tools -- just enough for ADKAgent to accept."""
    return Agent(
        name="unit_test_agent",
        model="gemini-2.5-flash",
        instruction="Unit test agent. Never called.",
        tools=[],
    )


def test_d4_build_agui_adk_agent_threads_user_id_through_to_adk_agent(dummy_agent: Agent) -> None:
    """D4' fix-locking (chat-history-deep-fixes-2): when a caller passes
    ``user_id`` to ``build_agui_adk_agent`` (the production path —
    ``skill_processor.process_skill_request`` now passes ``user.uid``),
    the resulting ADKAgent must resolve that exact value as the user_id
    for every Vertex Agent Engine call. This keeps the
    (app_name, user_id, session_id) triple consistent with the Firestore
    ``chat_sessions/{id}.owner_uid``, so subsequent
    ``GET /api/sessions/{id}/messages`` reads succeed.

    Pre-fix: ``build_agui_adk_agent`` did not accept ``user_id``; calling
    with the kwarg raised TypeError. Post-fix: passes through to ADKAgent.
    """
    from ag_ui.core import RunAgentInput

    from adk.agui import build_agui_adk_agent

    wrapped = build_agui_adk_agent(dummy_agent, user_id="firebase-uid-abc")

    fake_input = RunAgentInput(
        thread_id="thread-xyz",
        run_id="run-1",
        messages=[],
        state={},
        tools=[],
        context=[],
        forwarded_props={},
    )
    resolved = wrapped._get_user_id(fake_input)
    assert resolved == "firebase-uid-abc", (
        f"build_agui_adk_agent must thread user_id through so Vertex creates "
        f"the session under the Firebase auth uid (matching Firestore "
        f"owner_uid). Got {resolved!r} — divergence will reproduce Bugs A'/C."
    )


def test_default_user_id_extractor_diverges_from_firebase_uid(dummy_agent: Agent) -> None:
    """D1' (chat-history-deep-fixes-2 H1): documents the writer-side
    user_id divergence that causes GET /api/sessions/{id}/messages to
    500 with `Session ... does not belong to user`.

    Without an explicit ``user_id`` (the current ``build_agui_adk_agent``
    API), ag_ui_adk's ADKAgent falls back to ``_default_user_extractor``
    which returns ``f"thread_user_{input.thread_id}"`` —
    see ``ag_ui_adk/adk_agent.py:451-454``.

    That value is what ag_ui_adk passes to
    ``VertexAiSessionService.create_session(user_id=...)``. Meanwhile,
    the Firestore index written synchronously by
    ``skills.skill_processor._ensure_session_index`` stores
    ``owner_uid = user.uid`` (Firebase auth uid).

    The two diverge → GET /messages queries Vertex with the Firestore
    owner_uid → Vertex rejects with the documented ValueError → 500.

    Locking the divergence here so the fix (thread user_id through
    build_agui_adk_agent) can be verified end-to-end.
    """
    from ag_ui.core import RunAgentInput

    from adk.agui import build_agui_adk_agent

    wrapped = build_agui_adk_agent(dummy_agent)

    fake_input = RunAgentInput(
        thread_id="thread-abc-123",
        run_id="run-1",
        messages=[],
        state={},
        tools=[],
        context=[],
        forwarded_props={},
    )
    resolved = wrapped._get_user_id(fake_input)
    assert resolved == "thread_user_thread-abc-123", (
        "documents the bug: ag_ui_adk's default extractor uses thread_id, "
        "not the Firebase auth uid; this is what gets stored as the Vertex "
        "session's user_id and is the root cause of the GET /messages 500."
    )


def test_mount_registers_chat_route(dummy_agent: Agent) -> None:
    """Endpoint is registered at /api/chat/{skill_id} after mount."""
    app = FastAPI()
    mount_skill_endpoint(app, "unit_test", dummy_agent)

    paths = {route.path for route in app.routes}
    assert "/api/chat/unit_test" in paths, f"expected /api/chat/unit_test in routes, got {paths}"


def test_mount_forwards_explicit_services(dummy_agent: Agent) -> None:
    """When explicit services are provided, they reach ADKAgent — together
    with use_thread_id_as_session_id=True (AG-UI thread IDs map 1:1 to ADK
    sessions, see PROTOCOLS-1A5 M1) and use_in_memory_services=True (only
    matters for the credential service, which has no real backend yet —
    explicit session/memory/artifact arguments still win because ag_ui_adk
    does ``provided or InMemoryX()``).
    """
    app = FastAPI()
    session_service = InMemorySessionService()
    memory_service = InMemoryMemoryService()
    artifact_service = InMemoryArtifactService()

    with patch("protocols.agui.ADKAgent") as mock_adk_agent:
        mount_skill_endpoint(
            app,
            "with_services",
            dummy_agent,
            session_service=session_service,
            memory_service=memory_service,
            artifact_service=artifact_service,
        )

        mock_adk_agent.assert_called_once()
        kwargs = mock_adk_agent.call_args.kwargs
        assert kwargs["adk_agent"] is dummy_agent
        assert kwargs["session_service"] is session_service
        assert kwargs["memory_service"] is memory_service
        assert kwargs["artifact_service"] is artifact_service
        assert kwargs["use_in_memory_services"] is True
        assert kwargs["use_thread_id_as_session_id"] is True
        assert kwargs["app_name"] == "aitana_platform"


def test_mount_defaults_to_session_singletons(dummy_agent: Agent) -> None:
    """Without explicit services, mount_skill_endpoint pulls the singletons
    from adk.session — that's what gets the real Vertex/GCS backends in
    production. Previously it left these unset and ag_ui_adk silently
    swapped in InMemory defaults, masking the deployed configuration.
    """
    app = FastAPI()

    fake_session = InMemorySessionService()
    fake_memory = InMemoryMemoryService()
    fake_artifact = InMemoryArtifactService()

    with (
        patch("protocols.agui.ADKAgent") as mock_adk_agent,
        patch("adk.session.get_session_service", return_value=fake_session),
        patch("adk.session.get_memory_service", return_value=fake_memory),
        patch("adk.session.get_artifact_service", return_value=fake_artifact),
    ):
        mount_skill_endpoint(app, "no_services", dummy_agent)

        kwargs = mock_adk_agent.call_args.kwargs
        assert kwargs["session_service"] is fake_session
        assert kwargs["memory_service"] is fake_memory
        assert kwargs["artifact_service"] is fake_artifact
        assert kwargs["use_thread_id_as_session_id"] is True


def test_mount_respects_custom_app_name(dummy_agent: Agent) -> None:
    """app_name override flows through to ADKAgent."""
    app = FastAPI()

    with patch("protocols.agui.ADKAgent") as mock_adk_agent:
        mount_skill_endpoint(app, "custom", dummy_agent, app_name="custom_app")

        kwargs = mock_adk_agent.call_args.kwargs
        assert kwargs["app_name"] == "custom_app"


def test_mount_extracts_auth_headers(dummy_agent: Agent) -> None:
    """Verify that x-user-id + x-firebase-uid are forwarded to the agent.

    These headers must remain on the extract list so ag-ui-adk sees the
    caller identity in request context. Real Firebase token verification
    happens upstream on /api/skill/{id}/stream via get_current_user.
    """
    app = FastAPI()

    with patch("protocols.agui.add_adk_fastapi_endpoint") as mock_add_endpoint:
        mount_skill_endpoint(app, "auth_headers", dummy_agent)

        kwargs = mock_add_endpoint.call_args.kwargs
        assert kwargs["path"] == "/api/chat/auth_headers"
        assert kwargs["extract_headers"] == ["x-user-id", "x-firebase-uid"]


# ---------------------------------------------------------------------------
# Session-durability regression (2026-08-05)
# ---------------------------------------------------------------------------
#
# ag_ui_adk's SessionManager runs a background sweep that DELETES any tracked
# session idle longer than ``session_timeout_seconds``. Both defaults are
# hostile to a persistent chat product — 1200s (20 min) and
# ``delete_session_on_cleanup=True`` — and with Vertex as the store the delete
# is permanent: the session and its events go from Agent Engine, while the
# Firestore ``chat_sessions`` index row (metadata only) survives. Net effect:
# the conversation keeps listing in the left panel and resuming it renders a
# blank chat. Measured on test: 19 of 75 real conversations destroyed.
#
# These tests fail if either knob regresses to its library default.


def test_mount_disables_session_cleanup_deletion(dummy_agent: Agent) -> None:
    """mount_skill_endpoint must never let the cleanup sweep delete sessions."""
    app = FastAPI()

    with patch("protocols.agui.ADKAgent") as mock_adk_agent:
        mount_skill_endpoint(app, "durable", dummy_agent)

        kwargs = mock_adk_agent.call_args.kwargs
        assert kwargs["delete_session_on_cleanup"] is False, (
            "ag_ui_adk would permanently delete idle sessions from Vertex Agent "
            "Engine, destroying the user's conversation history"
        )
        assert kwargs["session_timeout_seconds"] > 1200


def test_build_agui_adk_agent_disables_session_cleanup_deletion(dummy_agent: Agent) -> None:
    """Same guarantee on the chat path (``build_agui_adk_agent``), which is the
    builder every real chat turn and surface-action run goes through.

    Reads ``from_app``'s kwargs, not the constructor's: v6.23.0 COMPACTION-WIRE
    switched this builder to ``ADKAgent.from_app(app, ...)`` so the deployment's
    App (and its ``events_compaction_config``) reaches the Runner. The session
    guarantee is unchanged and, if anything, more fragile now — ``from_app``
    re-declares these knobs with ag_ui_adk's hostile defaults, so they must
    still be passed explicitly.

    ``tests/unit/test_compaction_reaches_chat_runner.py`` asserts the same
    guarantee against the REAL ``SessionManager`` rather than a mock; keep both.
    A mock proves we passed the argument, the real manager proves it took
    effect — and the manager is a singleton that silently discards kwargs after
    first init, which a mock can never catch.
    """
    from adk.agui import build_agui_adk_agent

    with patch("adk.agui.ADKAgent") as mock_adk_agent:
        build_agui_adk_agent(
            dummy_agent,
            user_id="uid-1",
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            artifact_service=InMemoryArtifactService(),
        )

        assert mock_adk_agent.call_args is None, (
            "build_agui_adk_agent called ADKAgent(...) directly again — that "
            "leaves _app None and the Runner without an App, which is exactly "
            "the bug that made compaction dead code. Use from_app()."
        )
        kwargs = mock_adk_agent.from_app.call_args.kwargs
        assert kwargs["delete_session_on_cleanup"] is False, (
            "ag_ui_adk would permanently delete idle sessions from Vertex Agent "
            "Engine, destroying the user's conversation history"
        )
        assert kwargs["session_timeout_seconds"] > 1200
        assert kwargs["use_thread_id_as_session_id"] is True, (
            "from_app defaults this to False, which mints a fresh ADK session "
            "per turn and discards conversation memory between turns"
        )
