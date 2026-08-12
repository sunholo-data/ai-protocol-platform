"""Compaction config must actually reach the chat path's Runner (v6.23.0 spike).

THE BUG THIS PINS. `backend/app.py` builds an `App` carrying
`events_compaction_config`, and `adk/session.py` maintains a careful per-model
tuning table for it. Neither has ever affected a chat turn.

The AG-UI chat path builds its agent via `build_agui_adk_agent`, which calls
`ADKAgent(adk_agent=..., ...)`. `ag_ui_adk` only populates its internal `_app`
from the `from_app()` classmethod (adk_agent.py:395), so on our path `_app` is
None and `_create_runner` takes the component-based branch:

    if self._app is not None:
        return Runner(app=request_app, **service_kwargs)
    else:
        return Runner(app_name=app_name, agent=adk_agent, **service_kwargs)

A Runner with no App has `runner.app is None`, which disables BOTH compaction
triggers in ADK 1.31.1:

    runners.py:622   if self.app and self.app.events_compaction_config:   -> False
    runners.py:1480  events_compaction_config=(... if self.app else None) -> None

So conversation history is never compacted on the chat path, at any length.

MEASURED, not inferred: a 25-turn conversation driven against a real backend
with `compaction_interval=10` produced 100 events across 25 invocations and
**zero** compaction events. Under a working sliding window it must have fired
at turns 10 and 20.

WHY A UNIT TEST DIDN'T CATCH THIS, AND WHY THIS ONE IS SHAPED LIKE THIS.
`tests/unit/test_session_factories.py` asserts `get_compaction_config` returns
the right values, and it does — correctly, for a config nobody reads. Testing
the config in isolation proves nothing about whether it is WIRED. This test
therefore asserts against the Runner the production factory actually produces,
which is the only place the question can be answered.

This is the custom<->ADK seam class of bug that `docs/design/v6.17.0/adk-contract-checklist.md`
exists for, so it runs under `make adk-conformance`.

See docs/design/v6.23.0/compaction-wiring-and-observability.md.
"""

from __future__ import annotations

import pytest

from adk.agui import APP_NAME, build_agui_adk_agent

pytestmark = pytest.mark.adk_contract


@pytest.fixture
def chat_agui_agent(monkeypatch):
    """The AG-UI agent exactly as the chat stream builds it.

    Services are stubbed to in-memory so the guard is hermetic (no Vertex, no
    GCS, no ADC) — the question under test is about wiring, not backends.

    `SessionManager.reset_instance()` first because it is a process-wide
    SINGLETON whose `__init__` early-returns once initialised — later kwargs are
    silently DISCARDED. Without the reset these assertions would describe
    whichever test happened to construct one first, and would keep passing even
    if `build_agui_adk_agent` stopped passing the settings entirely.
    """
    from ag_ui_adk.session_manager import SessionManager
    from google.adk.agents import Agent
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.memory import InMemoryMemoryService
    from google.adk.sessions import InMemorySessionService

    SessionManager.reset_instance()
    agent = Agent(name="probe_agent", model="gemini-2.5-flash", instruction="probe")
    built = build_agui_adk_agent(
        agent,
        user_id="probe-user",
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
    )
    yield built
    # Leave no singleton behind for the rest of the suite.
    SessionManager.reset_instance()


def _runner_for(agui_agent):
    """The Runner the AG-UI agent builds for a request.

    Calls the same private factory the request path calls. Reaching into a
    private is deliberate: the public surface is an async event stream, and
    asserting through it would need a live model. The whole point is to check
    the object handed to ADK, so we check exactly that object.
    """
    from google.adk.agents import Agent

    adk_agent = Agent(name="probe_agent", model="gemini-2.5-flash", instruction="probe")
    return agui_agent._create_runner(adk_agent, "probe-user", APP_NAME)


def test_chat_runner_carries_an_app(chat_agui_agent):
    """A Runner with no App silently disables every App-level ADK feature.

    Compaction is the one that bit us, but resumability, plugins and context
    caching ride on the same object — so this assertion is worth more than the
    single feature that motivated it.
    """
    runner = _runner_for(chat_agui_agent)
    assert runner.app is not None, (
        "The chat path's Runner has no App, so every App-level ADK config "
        "(compaction, plugins, resumability, context caching) is inert. "
        "Build the AG-UI agent with ADKAgent.from_app(app, ...) instead of "
        "ADKAgent(adk_agent=...)."
    )


def test_chat_runner_has_events_compaction_config(chat_agui_agent):
    """The specific regression: conversation history must be compactable.

    Without this, context grows unbounded for the life of a conversation and
    the only backstop is the model's own context limit — which surfaces to the
    user as a hard failure on a long chat or a large document set, not as
    graceful degradation.
    """
    runner = _runner_for(chat_agui_agent)
    assert runner.app is not None, "no App on the Runner — see test_chat_runner_carries_an_app"
    cfg = runner.app.events_compaction_config
    assert cfg is not None, (
        "The chat Runner's App carries no events_compaction_config, so neither "
        "compaction trigger can fire (runners.py:622 and :1480 both guard on it)."
    )
    # Both triggers must be armed. A config with only compaction_interval is
    # the pre-2026-08-06 turn-count behaviour; a token_threshold with no
    # event_retention_size is rejected by ADK's own validator.
    assert cfg.token_threshold is not None and cfg.token_threshold > 0
    assert cfg.event_retention_size is not None and cfg.event_retention_size > 0


def test_compaction_config_matches_the_apps_own_config(chat_agui_agent):
    """The chat path must use the SAME config the deployment declares.

    Guards the half-fix: wiring an App in but building it from a different
    source than `backend/app.py` would leave two tuning surfaces that drift
    apart silently.
    """
    import app as app_mod

    runner = _runner_for(chat_agui_agent)
    assert runner.app is not None, "no App on the Runner"
    assert runner.app.events_compaction_config == app_mod.app.events_compaction_config, (
        "The chat Runner's compaction config differs from the App declared in "
        "backend/app.py — there must be exactly one tuning surface."
    )


class TestSessionSafetySurvivesTheWiring:
    """The hard gate on M1. These are NOT about compaction.

    `ADKAgent.from_app()` re-declares the session knobs with ag_ui_adk's OWN
    defaults — `delete_session_on_cleanup=True`, `session_timeout_seconds=1200`,
    `use_thread_id_as_session_id=False`. Those are precisely the values that
    caused the 2026-08-05 incident: a background sweep permanently deleted any
    session idle >20 minutes from Vertex, and **19 of 75** conversations with
    real turns were destroyed (`44ca9b6`). Tomas lost a 90-minute working
    session to it.

    So the wiring change is one forgotten kwarg away from re-deleting customer
    conversations — a strictly worse outcome than the missing compaction it is
    meant to fix. These assertions exist to make that impossible to ship
    silently, and they are deliberately written to fail loudly rather than to
    describe current behaviour.
    """

    def test_sessions_are_never_deleted_on_cleanup(self, chat_agui_agent):
        mgr = chat_agui_agent._session_manager
        assert mgr._delete_session_on_cleanup is False, (
            "REGRESSION: ag_ui_adk's cleanup sweep would DELETE sessions again. "
            "With Vertex as the store this is permanent and unrecoverable — it "
            "destroyed 19/75 conversations before 44ca9b6. Pass "
            "delete_session_on_cleanup=False."
        )

    def test_idle_timeout_is_not_the_hostile_default(self, chat_agui_agent):
        mgr = chat_agui_agent._session_manager
        assert mgr._timeout == 86400, (
            f"REGRESSION: session idle timeout is {mgr._timeout}s, not 86400s. "
            "ag_ui_adk defaults to 1200s (20 min), which treats a user who "
            "steps away mid-conversation as garbage."
        )

    def test_thread_id_is_the_session_id(self, chat_agui_agent):
        mgr = chat_agui_agent._session_manager
        assert mgr._use_thread_id_as_session_id is True, (
            "REGRESSION: without this, ADK mints a FRESH session per turn and "
            "discards conversation memory between turns — total history loss, "
            "worse than the bug this sprint is fixing."
        )

    def test_the_guard_itself_is_not_vacuous(self):
        """Prove the assertions above can actually fail.

        A singleton that early-returns on re-init is exactly the shape that
        makes a settings assertion silently meaningless. This builds a manager
        with the HOSTILE defaults and confirms the guard would catch them —
        so a future refactor can't neuter the checks without this failing too.
        """
        from ag_ui_adk.session_manager import SessionManager

        SessionManager.reset_instance()
        try:
            hostile = SessionManager.get_instance(
                session_timeout_seconds=1200,
                delete_session_on_cleanup=True,
                use_thread_id_as_session_id=False,
            )
            assert hostile._delete_session_on_cleanup is True
            assert hostile._timeout == 1200
            assert hostile._use_thread_id_as_session_id is False
        finally:
            SessionManager.reset_instance()


def test_the_runner_runs_OUR_agent_not_the_global_root(chat_agui_agent):
    """The other way `from_app` can quietly destroy the product.

    `ADKAgent.from_app(app, ...)` internally does `cls(adk_agent=app.root_agent)`
    — it takes the agent from the App. Passed the deployment's App unmodified,
    every skill would execute the GLOBAL root agent instead of its own: the PPA
    expert, the doc comparer and the Studio copilot would all become the same
    generic assistant, with no error anywhere.

    That is a worse failure than the missing compaction being fixed, and it is
    invisible to every other assertion in this file — the App would be present,
    the config correct, the services real. Only the agent would be wrong.

    The wiring must therefore hand `from_app` an App whose `root_agent` is the
    per-skill agent.
    """
    from google.adk.agents import Agent

    mine = Agent(name="my_specific_skill", model="gemini-2.5-flash", instruction="mine")
    runner = chat_agui_agent._create_runner(mine, "probe-user", APP_NAME)
    assert runner.agent.name == "my_specific_skill", (
        f"The Runner is running {runner.agent.name!r}, not the skill's own agent. "
        "from_app() takes its agent from app.root_agent — pass an App copied "
        "with root_agent set to the per-skill agent."
    )


def test_runner_still_gets_the_real_services(chat_agui_agent):
    """from_app() must not cost us the explicit Vertex/GCS services.

    `build_agui_adk_agent` exists precisely to stop ag_ui_adk falling back to
    its silent in-memory defaults. Switching to from_app() moves service
    plumbing around, and a regression here would mean chat history stops
    persisting — a far worse bug than the one being fixed.
    """
    runner = _runner_for(chat_agui_agent)
    assert runner.session_service is not None
    assert runner.memory_service is not None
    assert runner.artifact_service is not None
