"""Fixed direct launchers never become an arbitrary-tool or ownership bypass."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adk.a2ui_direct_launchers import LaunchResult, register_launcher
from auth import User, get_current_user
from protocols.a2ui_direct_launch_routes import router

USER = User(uid="mark", email="mark@example.com", domain="example.com")


def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: USER

    @app.middleware("http")
    async def access(request, call_next):
        request.state.access = MagicMock()
        return await call_next(request)

    return TestClient(app)


@pytest.fixture
def setup():
    handler = AsyncMock(
        return_value=LaunchResult(
            "board",
            [
                {"version": "v0.9", "createSurface": {"surfaceId": "board"}},
                {"version": "v0.9", "updateDataModel": {"surfaceId": "board", "value": {"count": 1}}},
            ],
            {"kind": "test_board", "title": "Waiting for you"},
        )
    )
    register_launcher(name="test_board", handler=handler, requires_tool="list_board")
    skill = SimpleNamespace(
        skill_id="skill",
        skill_metadata=SimpleNamespace(
            tools=["list_board"],
            tool_configs={
                "a2ui": {
                    "allow_surface_context_writes": True,
                    "direct_launchers": [{"name": "test_board", "label": "Waiting for you", "tool": "list_board"}],
                }
            },
        ),
    )
    index = SimpleNamespace(owner_uid="mark", skill_id="skill", collaborators=[])
    session = SimpleNamespace(state={})
    service = MagicMock(get_session=AsyncMock(return_value=session), append_event=AsyncMock())
    with (
        patch("protocols.a2ui_direct_launch_routes.skill_config") as skills,
        patch(
            "protocols.a2ui_direct_launch_routes.get_session_index",
            return_value=index,
        ) as get_index,
        patch("protocols.a2ui_direct_launch_routes.get_session_service", return_value=service),
        patch(
            "protocols.a2ui_direct_launch_routes.bootstrap_session",
            new_callable=AsyncMock,
        ) as bootstrap,
        patch("protocols.a2ui_direct_launch_routes.can_use_tool", return_value=True) as permission,
    ):
        skills.get_skill.return_value = skill
        yield SimpleNamespace(
            handler=handler,
            skill=skill,
            index=index,
            service=service,
            bootstrap=bootstrap,
            get_index=get_index,
            permission=permission,
        )


def post(**kw):
    return client().post("/api/skills/skill/sessions/session/surface-launch-direct", json={"name": "test_board", **kw})


def test_launcher_stashes_safe_controls_and_returns_normal_a2ui(setup):
    response = post()
    assert response.status_code == 200, response.text
    assert response.json()["surface_id"] == "board"
    delta = setup.service.append_event.await_args.args[1].actions.state_delta
    assert set(delta) == {"a2ui_surface:board"}
    assert json.loads(delta["a2ui_surface:board"])["artifact"]["kind"] == "test_board"
    setup.handler.assert_awaited_once()
    setup.bootstrap.assert_not_awaited()


def test_first_click_bootstraps_a_fresh_owned_session_without_model_turn(setup):
    setup.get_index.side_effect = [None, setup.index]
    assert post().status_code == 200
    setup.bootstrap.assert_awaited_once()
    assert setup.bootstrap.await_args.kwargs["user"].uid == "mark"
    assert setup.bootstrap.await_args.kwargs["body"].skill_id == "skill"
    setup.handler.assert_awaited_once()


@pytest.mark.parametrize("reason", ["owner", "skill", "grant", "tool", "metadata", "unregistered"])
def test_authorization_refusals_never_run_the_launcher_or_bootstrap(setup, reason):
    if reason == "owner":
        setup.index.owner_uid = "someone-else"
    elif reason == "skill":
        setup.index.skill_id = "other-skill"
    elif reason == "grant":
        setup.permission.return_value = False
    elif reason == "tool":
        setup.skill.skill_metadata.tools = []
    elif reason == "metadata":
        setup.skill.skill_metadata.tool_configs["a2ui"]["direct_launchers"] = []
    elif reason == "unregistered":
        setup.skill.skill_metadata.tool_configs["a2ui"]["direct_launchers"][0]["tool"] = "other_tool"
    assert post().status_code in (403, 404)
    setup.handler.assert_not_awaited()
    setup.bootstrap.assert_not_awaited()


def test_browser_cannot_inject_tool_arguments_or_refs(setup):
    assert post(arguments={"ref": "op://private"}).status_code == 422
    setup.handler.assert_not_awaited()


def test_disabled_or_failing_handler_is_a_visible_refusal(setup):
    from fastapi import HTTPException

    setup.handler.side_effect = HTTPException(503, "Approval service is disabled")
    response = post()
    assert response.status_code == 503
    assert response.json()["detail"] == "Approval service is disabled"
    setup.service.append_event.assert_not_awaited()


def test_session_claimed_by_another_owner_during_bootstrap_is_never_used(setup):
    setup.get_index.side_effect = [None, SimpleNamespace(owner_uid="other", skill_id="skill")]
    response = post()
    assert response.status_code == 403
    setup.handler.assert_not_awaited()
    setup.service.append_event.assert_not_awaited()
