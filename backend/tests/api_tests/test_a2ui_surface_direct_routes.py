"""API tests for ``POST /api/sessions/{id}/surface-action-direct`` (two-way workbench mode C).

Gates: owner-only, skill opt-in, surface must be stashed, a handler must be
registered for the surface's kind, the skill must declare the handler's tool.
Then the handler runs against the STASH (never the client's copy), its patches
are appended to the stash, and the outcome is recorded for the AI's next turn.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adk import a2ui_direct_actions as reg
from auth import User, get_current_user
from auth.access_context import AccessContext
from protocols.a2ui_surface_direct_routes import router
from tests.api_tests.test_a2ui_surface_data_routes import _OPTED_IN_A2UI, _make_index, _make_skill

_SID = "board"
_KEY = f"a2ui_surface:{_SID}"


@pytest.fixture(autouse=True)
def caller_tool_grant(monkeypatch):
    monkeypatch.setattr("protocols.a2ui_surface_direct_routes.can_use_tool", lambda *args: True)


def _client(uid: str = "viewer") -> TestClient:
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain, group_tags=frozenset())
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.state.access = ctx
        return await call_next(request)

    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _stash() -> dict:
    return {
        "surfaceId": _SID,
        "messages": [
            {"version": "v0.9", "createSurface": {"surfaceId": _SID}},
            {
                "version": "v0.9",
                "updateDataModel": {"surfaceId": _SID, "value": {"items": [{"ref": "#1"}, {"ref": "#2"}]}},
            },
        ],
        "artifact": {"kind": "test_board", "title": "Board"},
    }


def _svc(state: dict | None = None) -> MagicMock:
    session = MagicMock()
    session.state = state if state is not None else {_KEY: json.dumps(_stash())}
    svc = MagicMock()
    svc.get_session = AsyncMock(return_value=session)
    svc.append_event = AsyncMock()
    return svc


@pytest.fixture
def seen():
    calls: list[reg.DirectActionContext] = []

    async def handler(ctx):
        calls.append(ctx)
        refs = [i["ref"] for i in ctx.shown()]
        if ctx.action_context.get("ref") not in refs:
            return reg.ActionResult("error", "not on this board")
        patch_msg = {"version": "v0.9", "updateDataModel": {"surfaceId": _SID, "path": "/done", "value": True}}
        return reg.ActionResult("ok", "Done.", [patch_msg], "The user did it.")

    reg.register_action(surface_kind="test_board", name="poke", handler=handler, requires_tool="poke_tool")
    yield calls
    reg._registry.pop(("test_board", "poke"), None)


def _post(client, name="direct:poke", context=None, sid=_SID):
    body = {"surfaceId": sid, "action": {"name": name, "context": context if context is not None else {"ref": "#1"}}}
    return client.post("/api/sessions/sess-1/surface-action-direct", json=body)


def _patched(index=None, tools=("poke_tool",), a2ui=_OPTED_IN_A2UI, svc=None):
    skill = _make_skill(a2ui_config=a2ui)
    skill.skill_metadata.tools = list(tools)
    p1 = patch("protocols._a2ui_surface_shared.get_session_index", return_value=index or _make_index())
    p2 = patch("protocols._a2ui_surface_shared.skill_config")
    p3 = patch("protocols.a2ui_surface_direct_routes.skill_config")
    p4 = patch("protocols.a2ui_surface_direct_routes.get_session_service", return_value=svc or _svc())
    return p1, p2, p3, p4, skill


def test_runs_the_handler_against_the_stash_and_records_for_the_ai(seen):
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _post(_client())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok" and resp.json()["message"] == "Done."
    assert [i["ref"] for i in seen[0].shown()] == ["#1", "#2"]
    delta = svc.append_event.await_args.args[1].actions.state_delta
    assert json.loads(delta[_KEY])["messages"][-1]["updateDataModel"]["path"] == "/done"  # reload shows it
    last = delta[f"a2ui_surface_context.{_SID}.lastAction"]
    assert last["direct"] is True and last["result"] == "The user did it."
    assert delta[f"a2ui_surface_context.{_SID}.recentActions"] == [last]


def test_a_ref_the_board_never_showed_is_refused_by_the_handler(seen):
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _post(_client(), context={"ref": "#99"})
    assert resp.json()["status"] == "error"


def test_non_owner_unknown_action_missing_stash_and_missing_tool_are_refused(seen):
    cases = [
        ({"index": _make_index(owner_uid="someone-else")}, {}, 403),
        ({}, {"name": "direct:nope"}, 404),
        ({"svc": _svc(state={})}, {}, 404),
        ({"tools": ()}, {}, 403),
        ({"a2ui": {"default_surface": "workspace"}}, {}, 403),
    ]
    for kw, post_kw, code in cases:
        svc = kw.pop("svc", None) or _svc()
        p1, p2, p3, p4, skill = _patched(svc=svc, **kw)
        with p1, p2 as sc1, p3 as sc2, p4:
            sc1.get_skill.return_value = sc2.get_skill.return_value = skill
            sc1.resolve_skill_ref.return_value = None
            with patch("protocols._a2ui_surface_shared._delegate_with_grant", return_value=None):
                resp = _post(_client(), **post_kw)
        assert resp.status_code == code, (kw, post_kw, resp.text)
    assert not seen  # the handler never ran


def test_a_handler_that_raises_still_answers_the_click(seen):
    async def boom(ctx):
        raise RuntimeError("x")

    reg.register_action(surface_kind="test_board", name="poke", handler=boom, requires_tool="poke_tool")
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _post(_client())
    assert resp.status_code == 200 and resp.json()["status"] == "error" and resp.json()["message"]


def test_revoked_caller_tool_grant_refuses_before_handler(seen, monkeypatch):
    monkeypatch.setattr("protocols.a2ui_surface_direct_routes.can_use_tool", lambda *args: False)
    p1, p2, p3, p4, skill = _patched()
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        response = _post(_client())
    assert response.status_code == 403 and not seen


def test_large_patch_history_compacts_without_losing_board_or_scope(seen):
    stash = _stash()
    stash["messages"].append(
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": _SID,
                "components": [{"id": "root", "component": "Text", "text": "Board"}],
            },
        }
    )
    for n in range(405):
        stash["messages"].append(
            {"version": "v0.9", "updateDataModel": {"surfaceId": _SID, "path": "/count", "value": n}}
        )
    svc = _svc({_KEY: json.dumps(stash)})
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        response = _post(_client())
    assert response.json()["status"] == "ok"
    saved = json.loads(svc.append_event.await_args.args[1].actions.state_delta[_KEY])
    assert len(saved["messages"]) == 3
    context = reg.DirectActionContext(None, "s", "k", _SID, saved, {})
    assert context.shown() == [{"ref": "#1"}, {"ref": "#2"}]
    assert context.data_model()["count"] == 404 and context.data_model()["done"] is True
    assert saved["messages"][1]["updateComponents"]["components"][0]["text"] == "Board"
