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
from db.models import DelegationConfig
from db.models.access import AccessControl
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


@pytest.mark.parametrize("opted_in,declared,expected", [(False, True, 403), (True, False, 403), (True, True, 200)])
def test_automatic_refresh_is_explicitly_registered_and_bound_to_stash(seen, opted_in, declared, expected):
    reg.register_action(
        surface_kind="test_board",
        name="poke",
        handler=reg.lookup("test_board", "poke").handler,
        requires_tool="poke_tool",
        automatic=opted_in,
    )
    stash = _stash()
    if declared:
        stash["artifact"]["autoRefresh"] = {"action": "direct:poke"}
    svc = _svc({_KEY: json.dumps(stash)})
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _client().post(
            "/api/sessions/sess-1/surface-action-direct",
            json={"surfaceId": _SID, "automatic": True, "action": {"name": "direct:poke", "context": {}}},
        )
    assert resp.status_code == expected
    assert len(seen) == (1 if expected == 200 else 0)


def test_automatic_request_cannot_supply_approval_context(seen):
    reg.register_action(
        surface_kind="test_board",
        name="poke",
        handler=reg.lookup("test_board", "poke").handler,
        requires_tool="poke_tool",
        automatic=True,
    )
    stash = _stash()
    stash["artifact"]["autoRefresh"] = {"action": "direct:poke"}
    p1, p2, p3, p4, skill = _patched(svc=_svc({_KEY: json.dumps(stash)}))
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _client().post(
            "/api/sessions/sess-1/surface-action-direct",
            json={
                "surfaceId": _SID,
                "automatic": True,
                "action": {"name": "direct:poke", "context": {"proposal_id": "hidden"}},
            },
        )
    assert resp.status_code == 403 and seen == []


def test_manual_result_is_confirmed_in_chat_and_persisted_as_assistant_only(seen):
    reg.register_action(
        surface_kind="test_board",
        name="poke",
        handler=reg.lookup("test_board", "poke").handler,
        requires_tool="poke_tool",
        confirm_in_chat=True,
    )
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    model = AsyncMock(return_value="Done — your reviewed changes are saved.")
    with p1, p2 as sc1, p3 as sc2, p4, patch("adk.a2ui_action_confirmation.confirmation_text", model):
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        resp = _post(_client())
    assert resp.status_code == 200
    assert resp.json()["confirmation"]["content"] == "Done — your reviewed changes are saved."
    assert len(seen) == 1 and model.await_count == 1
    event = svc.append_event.await_args.args[1]
    assert event.content.role == "model" and event.content.parts[0].text == resp.json()["confirmation"]["content"]
    assert event.id == resp.json()["confirmation"]["id"]


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


@pytest.mark.parametrize(
    "scenario", ["allowed", "hidden", "unlisted", "split-grants", "tool-removed", "caller-revoked", "resolution-failed"]
)
def test_delegated_card_action_requires_one_accessible_opted_in_tool_provider(seen, monkeypatch, scenario):
    delegate = _make_skill(skill_id="specialist", a2ui_config=_OPTED_IN_A2UI)
    delegate.skill_metadata.tools = ["poke_tool"]
    other = _make_skill(skill_id="other", a2ui_config=_OPTED_IN_A2UI)
    if scenario == "hidden":
        delegate.owner_id = "someone-else"
        delegate.access_control = AccessControl(type="private")
    elif scenario == "split-grants":
        delegate.skill_metadata.tool_configs = {"a2ui": {"default_surface": "workspace"}}
    elif scenario == "tool-removed":
        delegate.skill_metadata.tools = []
    if scenario == "caller-revoked":
        monkeypatch.setattr("protocols.a2ui_surface_direct_routes.can_use_tool", lambda *args: False)
    lookup = {"specialist": delegate, "other": other}
    monkeypatch.setattr("adk.agent._resolve_delegate_skill", lambda ref, _owner: lookup.get(ref))
    if scenario == "resolution-failed":

        def broken(*args):
            raise RuntimeError("unavailable")

        monkeypatch.setattr("adk.agent._resolve_delegate_skill", broken)
    p1, p2, p3, p4, root = _patched(tools=(), a2ui={})
    root.skill_metadata.delegation = DelegationConfig(
        enabled=True, allow=["other"] if scenario == "unlisted" else ["specialist", "other"]
    )
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = root
        response = _post(_client())
    assert response.status_code == (200 if scenario == "allowed" else 403), response.text
    assert bool(seen) is (scenario == "allowed")


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


def test_private_detail_response_is_never_stashed_or_recorded_for_next_turn(seen):
    secret = "private-purpose-and-arguments-canary"
    detail_id = f"private:{_SID}:review"

    async def review(ctx):
        return reg.ActionResult(
            "ok",
            "Review opened.",
            record=secret,
            private_surfaces=[
                {
                    "surface_id": detail_id,
                    "artifact": {"kind": "private_review", "title": "Review"},
                    "messages": [
                        {
                            "version": "v0.9",
                            "updateDataModel": {
                                "surfaceId": detail_id,
                                "value": {"purpose": secret},
                            },
                        }
                    ],
                }
            ],
        )

    # Even default tell_ai=True must not record a private-result response.
    reg.register_action(surface_kind="test_board", name="poke", handler=review, requires_tool="poke_tool")
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        response = _post(_client())
    assert response.json()["private_surfaces"][0]["messages"][0]["updateDataModel"]["value"]["purpose"] == secret
    assert "no-store" in response.headers["cache-control"]
    svc.append_event.assert_not_awaited()
    assert secret not in json.dumps(svc.get_session.return_value.state)


@pytest.mark.parametrize("target", ["board", "private:other:review"])
def test_private_result_cannot_patch_control_or_another_boards_surface(seen, target):
    async def review(ctx):
        return reg.ActionResult(
            "ok",
            "Review opened.",
            private_surfaces=[
                {
                    "surface_id": f"private:{_SID}:review",
                    "messages": [
                        {"version": "v0.9", "updateDataModel": {"surfaceId": target, "value": {"private": "canary"}}},
                    ],
                }
            ],
        )

    reg.register_action(surface_kind="test_board", name="poke", handler=review)
    svc = _svc()
    p1, p2, p3, p4, skill = _patched(svc=svc)
    with p1, p2 as sc1, p3 as sc2, p4:
        sc1.get_skill.return_value = sc2.get_skill.return_value = skill
        response = _post(_client())
    assert response.json()["status"] == "error"
    assert not response.json().get("private_surfaces")
    svc.append_event.assert_not_awaited()


def test_private_display_surfaces_cannot_submit_even_if_stashed(seen):
    response = _post(_client(), sid="private:board:review", context={"purpose": "canary"})
    assert response.status_code == 422
    assert not seen
