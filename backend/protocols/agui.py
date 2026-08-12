"""AG-UI protocol integration helper.

Workshop W5a — AG-UI: The Backend Half
  This file is the *entire* backend streaming integration. `mount_skill_endpoint`
  wraps an ADK agent in `ADKAgent` and mounts it with `add_adk_fastapi_endpoint`.
  That's it — the middleware translates every ADK event into the 16 canonical
  AG-UI event types. You write none of the translation.

  Key moment: `use_thread_id_as_session_id=True`. The default is False, which
  mints a new ADK session per request and discards conversation memory between
  turns. Enabling this maps the AG-UI threadId directly onto the ADK session ID.
  Ship this from day one — retrofitting is a behavior change for existing threads.

Thin wrapper around `ag_ui_adk.ADKAgent` + `add_adk_fastapi_endpoint`
to mount an ADK agent as an AG-UI SSE endpoint on a FastAPI app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint

if TYPE_CHECKING:
    from fastapi import FastAPI
    from google.adk.agents import BaseAgent
    from google.adk.artifacts import BaseArtifactService
    from google.adk.memory import BaseMemoryService
    from google.adk.sessions import BaseSessionService


def mount_skill_endpoint(
    app: FastAPI,
    skill_id: str,
    agent: BaseAgent,
    *,
    session_service: BaseSessionService | None = None,
    memory_service: BaseMemoryService | None = None,
    artifact_service: BaseArtifactService | None = None,
    app_name: str = "aitana_platform",
) -> None:
    """Wrap an ADK agent in ag-ui-adk and mount it as an AG-UI SSE endpoint.

    Mounts at ``/api/chat/{skill_id}``. The endpoint accepts POSTed AG-UI
    ``RunAgentInput`` payloads and streams AG-UI events as SSE frames.

    Defaults the three backing services to the ``adk.session`` singletons
    so this endpoint gets the same Vertex/GCS backends as the main skill
    stream. ``use_in_memory_services=True`` is left set on purpose: the
    credential service has no real backend so we want the InMemory
    fallback for it, but our explicit
    ``session_service``/``memory_service``/``artifact_service`` arguments
    win — ag_ui_adk uses ``provided or InMemoryX()`` so a non-None
    argument always takes precedence.

    ``use_thread_id_as_session_id=True`` so AG-UI thread IDs map 1:1 onto
    ADK sessions — otherwise ag-ui-adk allocates a fresh session per run
    and conversation memory evaporates between turns. See
    docs/design/v6.0.0/streaming-and-protocols.md "Session identity" for
    the spike finding behind this default.
    """
    # Lazy import so this module stays cheap to import in tests that
    # don't touch the GCP SDKs.
    from adk.session import (
        get_artifact_service,
        get_memory_service,
        get_session_service,
    )

    wrapped = ADKAgent(
        adk_agent=agent,
        app_name=app_name,
        session_service=session_service or get_session_service(),
        memory_service=memory_service or get_memory_service(),
        artifact_service=artifact_service or get_artifact_service(),
        use_in_memory_services=True,
        use_thread_id_as_session_id=True,
        # DO NOT REMOVE — see the long note in ``adk/agui.py``. ag_ui_adk's
        # SessionManager background sweep deletes any session idle >20 min by
        # default, which permanently removes the conversation (and its events)
        # from Vertex Agent Engine while the Firestore index row survives —
        # producing a listed-but-blank conversation on resume.
        delete_session_on_cleanup=False,
        session_timeout_seconds=86400,
    )

    add_adk_fastapi_endpoint(
        app,
        wrapped,
        path=f"/api/chat/{skill_id}",
        extract_headers=["x-user-id", "x-firebase-uid"],
    )
