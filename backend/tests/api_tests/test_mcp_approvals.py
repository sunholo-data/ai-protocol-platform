"""MCP approvals: a skill run that leaves a human approval asks for it by elicitation.

Design: docs/design/v6.41.0/human-approvals.md ("MCP: Multi Round-Trip Requests").
At 2026-07-28 the tool call returns ``input_required`` with one form per pending
approval; the client asks the person and retries with their answer. The retry
decides the approval and NEVER runs the skill again. Everything else (no
approval right, no elicitation, an older protocol, high risk) gets text saying
how to approve elsewhere.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from mcp import types

from adk import approvals
from protocols import mcp_server
from tests.api_tests.test_mcp_server import ALICE, PRIVATE, PUBLIC, TOKEN, _as_token_owner
from tests.api_tests.test_mcp_server_protocols import _app, _client
from tests.unit.test_approvals_core import FakeProvider

APPROVE = ["mcp", "approve"]


@pytest.fixture
def provider():
    approvals.clear_registry()
    fake = FakeProvider()
    approvals.register_provider(fake)
    yield fake
    approvals.clear_registry()


class Runs(list):
    """The skill runs seen, plus overrides for the approvals they prepare."""

    def __init__(self) -> None:
        super().__init__()
        self.risk: dict = {}


@pytest.fixture
def runs(provider):
    """A skill that prepares one approval per run, owned by its caller, in that run's session."""
    calls = Runs()

    async def fake(**kw) -> AsyncGenerator[dict, None]:
        calls.append(kw)
        provider.add(str(len(calls)), owner_uid=kw["user"].uid, session_id=kw["session_id"], **calls.risk)
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "Prepared a review of 17 entries."}

    with patch("skills.skill_processor.process_skill_request", side_effect=fake):
        yield calls


def _answer(action: str, approve: bool | None = True):
    asked: list[types.ElicitRequestParams] = []

    async def callback(_context, params):
        asked.append(params)
        content = None if approve is None else {"approve": approve}
        return types.ElicitResult(action=action, content=content if action == "accept" else None)

    return callback, asked


async def _call(mode: str = "2026-07-28", scopes: list[str] = APPROVE, elicitation=None) -> types.CallToolResult:
    server = mcp_server.new_server()
    app = _app(server)
    a, b = _as_token_owner(scopes=scopes)
    with (
        a,
        b,
        patch.object(mcp_server, "list_skills", return_value=[PUBLIC, PRIVATE]),
        patch("db.clients.resolve_enabled_skills", return_value=None),
        patch("skills.visibility.visible_configs", side_effect=lambda configs, access, _e: list(configs)),
        patch("notifications.hooks.notify_token_approval"),
        patch("adk.approvals.set_document"),
    ):
        options = {"elicitation_callback": elicitation} if elicitation is not None else {}
        async with server.session_manager.run(), _client(app, mode, TOKEN, **options) as c:
            return await c.call_tool("skill_my_hours", {"message": "relabel those entries"})


def _text(result: types.CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, types.TextContent))


@pytest.mark.asyncio
async def test_accepting_the_form_approves_once_without_rerunning_the_skill(provider, runs):
    callback, asked = _answer("accept")
    result = await _call(elicitation=callback)
    assert len(runs) == 1
    assert len(asked) == 1 and "Change 1" in asked[0].message and "Review of 1" in asked[0].message
    assert asked[0].requested_schema["properties"]["approve"]["type"] == "boolean"
    assert [(ref, decision) for ref, _, decision, _ in provider.calls] == [("1", "approve")]
    approver = provider.calls[0][3]
    assert approver.channel == "mcp" and approver.uid == ALICE.uid
    text = _text(result)
    assert "Prepared a review of 17 entries." in text and "approved 1" in text
    assert runs[0]["session_id"].startswith("mcp-")


@pytest.mark.asyncio
async def test_declining_cancels_and_dismissing_leaves_it_pending(provider, runs):
    callback, _ = _answer("decline", approve=None)
    await _call(elicitation=callback)
    assert [decision for _, _, decision, _ in provider.calls] == ["cancel"]

    provider.calls.clear()
    callback, _ = _answer("cancel", approve=None)
    result = await _call(elicitation=callback)
    assert provider.calls == []
    assert "aiplatform approvals approve fake:2" in _text(result)


@pytest.mark.asyncio
async def test_unticked_approve_box_is_not_an_approval(provider, runs):
    callback, _ = _answer("accept", approve=False)
    result = await _call(elicitation=callback)
    assert provider.calls == []
    assert "“Approve and save” unticked, so nothing was saved" in _text(result)


@pytest.mark.asyncio
async def test_accept_is_the_approval_and_the_box_starts_ticked(provider, runs):
    callback, asked = _answer("accept", approve=None)  # accept with no field values
    await _call(elicitation=callback)
    assert [decision for _, _, decision, _ in provider.calls] == ["approve"]
    assert asked[0].requested_schema["properties"]["approve"]["default"] is True
    assert "Accept to approve and save this" in asked[0].message


@pytest.mark.asyncio
async def test_a_token_without_approval_rights_is_told_where_to_approve(provider, runs):
    callback, asked = _answer("accept")
    result = await _call(scopes=["mcp"], elicitation=callback)
    assert asked == [] and provider.calls == []
    text = _text(result)
    assert "Waiting for your approval: Change 1" in text and "aiplatform approvals approve fake:1" in text
    assert "Review of 1" in text  # the reader sees what waits, not just its title


@pytest.mark.asyncio
async def test_clients_without_elicitation_or_on_the_old_protocol_get_text(provider, runs):
    assert "aiplatform approvals approve fake:1" in _text(await _call())
    callback, asked = _answer("accept")
    legacy = await _call(mode="legacy", elicitation=callback)
    assert asked == [] and provider.calls == []
    assert "aiplatform approvals approve fake:2" in _text(legacy)


@pytest.mark.asyncio
async def test_high_risk_is_never_asked_by_form(provider, runs):
    runs.risk["risk"] = "high"
    callback, asked = _answer("accept")
    result = await _call(elicitation=callback)
    assert asked == [] and provider.calls == []
    assert "needs a fresh sign-in" in _text(result)


@pytest.mark.asyncio
async def test_runs_with_nothing_to_approve_answer_as_before(provider):
    async def plain(**_kw) -> AsyncGenerator[dict, None]:
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "31.5h."}

    callback, asked = _answer("accept")
    with patch("skills.skill_processor.process_skill_request", side_effect=plain):
        result = await _call(elicitation=callback)
    assert asked == [] and _text(result) == "31.5h."


def _modern_call(client, params: dict, token: str) -> dict:
    import json as _json

    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**params, "_meta": meta}}
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": params["name"],
        "Authorization": f"Bearer {token}",
    }
    resp = client.post("/mcp", headers=headers, content=_json.dumps(body))
    text = resp.text
    if text.lstrip().startswith("{"):
        return _json.loads(text)
    return [_json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")][-1]


def test_sealed_state_is_opaque_bound_to_its_user_and_tamper_proof(provider, runs):
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.testclient import TestClient

    from auth.firebase_auth import User

    server = mcp_server.new_server()
    gated = mcp_server.get_mcp_asgi_app(server)

    @asynccontextmanager
    async def lifespan(_app):
        async with server.session_manager.run():
            yield

    app = Starlette(
        routes=[Route("/mcp", mcp_server.McpExactPath(gated), methods=["GET", "POST", "DELETE"]), Mount("/mcp", gated)],
        lifespan=lifespan,
    )
    call = {"name": "skill_my_hours", "arguments": {"message": "relabel"}}
    common = [
        patch.object(mcp_server, "list_skills", return_value=[PUBLIC, PRIVATE]),
        patch("db.clients.resolve_enabled_skills", return_value=None),
        patch("skills.visibility.visible_configs", side_effect=lambda configs, access, _e: list(configs)),
        patch("adk.approvals.set_document"),
        patch("notifications.hooks.notify_token_approval"),
    ]
    for p in common:
        p.start()
    try:
        with TestClient(app) as http:
            a, b = _as_token_owner(scopes=APPROVE)
            with a, b:
                first_raw = _modern_call(http, call, TOKEN)
                assert "result" in first_raw, first_raw
                first = first_raw["result"]
            assert first["resultType"] == "input_required"
            state = first["requestState"]
            assert "digest-1" not in state and "Change 1" not in state  # sealed, not plain JSON
            answer = {"fake:1": {"action": "accept", "content": {"approve": True}}}
            mallory = User(uid="uid-mallory", email="m@example.com", domain="example.com", email_verified=True)
            a, b = _as_token_owner(mallory, scopes=APPROVE)
            with a, b:
                stolen = _modern_call(http, {**call, "inputResponses": answer, "requestState": state}, TOKEN)
            a, b = _as_token_owner(scopes=APPROVE)
            with a, b:
                tampered = _modern_call(
                    http, {**call, "inputResponses": answer, "requestState": state[:-4] + "AAAA"}, TOKEN
                )
    finally:
        for p in common:
            p.stop()
    assert "error" in stolen or stolen.get("result", {}).get("isError")
    assert "error" in tampered or tampered.get("result", {}).get("isError")
    assert provider.calls == [] and len(runs) == 1


# --- the approval page by URL elicitation, and runs that only prepare ----------------


@pytest.mark.asyncio
async def test_high_risk_opens_the_approval_page_and_reports_its_outcome(provider, runs, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://app.example/")
    runs.risk["risk"] = "high"
    asked: list = []

    async def open_page_and_approve(_context, params):
        asked.append(params)
        # The person approves on the page (a signed-in web session), then consents here.
        provider.rows["1"] = provider.rows["1"].__class__(**{**provider.rows["1"].__dict__, "state": "completed"})
        return types.ElicitResult(action="accept")

    result = await _call(elicitation=open_page_and_approve)
    assert len(asked) == 1 and asked[0].mode == "url"
    assert asked[0].url == "https://app.example/approve/fake%3A1"
    assert provider.calls == []  # the page decided it, not the MCP call
    assert "Change 1: completed" in _text(result)
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_a_page_left_undecided_says_it_is_still_waiting(provider, runs, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://app.example")
    callback, asked = _answer("accept", approve=None)
    result = await _call(scopes=["mcp"], elicitation=callback)
    assert asked[0].mode == "url"  # no approval right: the page, not a form
    assert "Still waiting for your decision on the approval page: https://app.example/approve/fake%3A1" in _text(result)


@pytest.mark.asyncio
async def test_a_run_that_only_prepared_says_what_it_prepared(provider):
    async def silent(**kw) -> AsyncGenerator[dict, None]:
        provider.add("1", owner_uid=kw["user"].uid, session_id=kw["session_id"])
        return
        yield  # pragma: no cover

    callback, _ = _answer("accept")
    with patch("skills.skill_processor.process_skill_request", side_effect=silent):
        result = await _call(elicitation=callback)
    text = _text(result)
    assert "Prepared for your approval: Change 1." in text and "(The skill finished" not in text
