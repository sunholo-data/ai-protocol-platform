"""API tests for the MCP server at /mcp — per-caller tools, auth, the bare path.

Over the wire (a real streamable-HTTP app, the same wrapping fast_api_app
uses), because the failures this file guards are BETWEEN the pieces: who the
caller is when the SDK finally calls our handler, and whether a client can
reach /mcp at all through the frontend proxy (the slash redirect).

Pins:
  - anonymous callers see public skills only; MCP_AUTH=required 401s them
  - a personal MCP token acts as its owner: their visible skills, their uid
    reaching the skill run (so caller-scoped tools answer for them)
  - the token path runs the same identity gates as the REST API
  - bad / expired tokens are 401 with a WWW-Authenticate challenge
  - RUN_ERROR becomes an MCP tool error, never an empty answer
  - bare POST /mcp is served, not redirected
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from auth.firebase_auth import User
from db.models import SkillConfig, SkillMetadata
from db.models.access import AccessControl
from protocols import mcp_server

ALICE = User(uid="uid-alice", email="alice@example.com", domain="example.com", email_verified=True)
TOKEN = "aip_mcp_test-token"


def _skill(skill_id: str, *, slug: str = "", access: str = "public", description: str = "") -> SkillConfig:
    return SkillConfig(
        name=skill_id,
        slug=slug or None,
        description=description or f"The {skill_id} skill.",
        instructions="Be helpful.",
        skillId=skill_id,
        ownerId="owner-uid",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl=AccessControl(type=access),
    )


PUBLIC = _skill("public-helper", slug="helper")
PRIVATE = _skill("26124699-f558-4c1e-9f1a-0123456789ab", slug="my-hours", access="domain")


@pytest.fixture
def client():
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
    with (
        patch.object(mcp_server, "list_marketplace", return_value=[PUBLIC]),
        patch.object(mcp_server, "list_skills", return_value=[PUBLIC, PRIVATE]),
        patch("db.clients.resolve_enabled_skills", return_value=None),
        patch("skills.visibility.visible_configs", side_effect=lambda configs, access, _e: list(configs)),
        TestClient(app) as c,
    ):
        yield c


def _rpc(client: TestClient, method: str, params: dict | None = None, token: str | None = None):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    resp = client.post("/mcp", headers=headers, content=json.dumps(body), follow_redirects=False)
    if resp.status_code != 200:
        return resp, None
    text = resp.text
    if text.lstrip().startswith("{"):
        return resp, json.loads(text)
    data = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
    return resp, data[-1] if data else None


def _as_token_owner(user: User | None = ALICE):
    return (
        patch("auth.mcp_tokens.resolve_token", return_value=user.uid if user else None),
        patch("auth.firebase_auth.resolve_user_by_uid", return_value=user),
    )


def _names(msg: dict) -> list[str]:
    return sorted(t["name"] for t in msg["result"]["tools"])


# --- the bare path ---------------------------------------------------------


def test_bare_mcp_path_is_served_not_redirected(client):
    resp, msg = _rpc(client, "tools/list")
    assert resp.status_code == 200, (resp.status_code, resp.headers.get("location"))
    assert msg and "result" in msg


# --- anonymous ---------------------------------------------------------------


def test_anonymous_sees_public_skills_only(client, monkeypatch):
    monkeypatch.delenv("MCP_AUTH", raising=False)
    _, msg = _rpc(client, "tools/list")
    assert _names(msg) == ["skill_helper"]  # the slug, not the id (#9)


def test_required_mode_rejects_anonymous_with_a_challenge(client, monkeypatch):
    monkeypatch.setenv("MCP_AUTH", "required")
    resp, _ = _rpc(client, "tools/list")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")


# --- personal tokens ------------------------------------------------------------


def test_token_caller_sees_their_skills_by_friendly_name(client, monkeypatch):
    monkeypatch.setenv("MCP_AUTH", "required")
    a, b = _as_token_owner()
    with a, b:
        _, msg = _rpc(client, "tools/list", token=TOKEN)
    assert _names(msg) == ["skill_helper", "skill_my_hours"]


def test_token_call_runs_the_skill_as_its_owner(client):
    seen: dict = {}

    async def fake(**kw) -> AsyncGenerator[dict, None]:
        seen.update(kw)
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "You logged "}
        yield {"type": "TOOL_CALL_START", "tool_call_name": "severa_my_hours"}
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "31.5h."}

    a, b = _as_token_owner()
    with a, b, patch("skills.skill_processor.process_skill_request", side_effect=fake):
        _, msg = _rpc(client, "tools/call", {"name": "skill_my_hours", "arguments": {"message": "my hours?"}}, TOKEN)
    assert msg["result"]["content"][0]["text"] == "You logged 31.5h."
    assert not msg["result"].get("isError")
    assert seen["user"].uid == "uid-alice" and seen["access"].uid == "uid-alice"
    assert seen["skill_id"] == PRIVATE.skill_id  # canonical id downstream


def test_unknown_or_expired_token_is_401(client):
    a, b = _as_token_owner(None)
    with a, b:
        resp, _ = _rpc(client, "tools/list", token=TOKEN)
    assert resp.status_code == 401 and "www-authenticate" in resp.headers


def test_token_path_runs_the_rest_api_identity_gates(client):
    unverified = User(uid="uid-x", email="squat@example.com", domain="example.com", email_verified=False)
    a, b = _as_token_owner(unverified)
    gate = HTTPException(status_code=403, detail={"code": "EMAIL_NOT_VERIFIED", "message": "unverified"})
    with a, b, patch("auth.enforce_user_gates", side_effect=gate) as gates:
        resp, _ = _rpc(client, "tools/list", token=TOKEN)
    assert resp.status_code == 403 and resp.json() == {"error": "unverified"}
    gates.assert_called_once_with(unverified)


def test_anonymous_cannot_call_a_private_skill(client, monkeypatch):
    monkeypatch.delenv("MCP_AUTH", raising=False)
    _, msg = _rpc(client, "tools/call", {"name": "skill_my_hours", "arguments": {"message": "hi"}})
    assert msg["result"]["isError"] and "Unknown tool" in msg["result"]["content"][0]["text"]


def test_run_error_is_a_tool_error_not_an_empty_answer(client):
    async def failing(**_kw) -> AsyncGenerator[dict, None]:
        yield {"type": "RUN_ERROR", "message": "The model is overloaded. Try again."}

    with patch("skills.skill_processor.process_skill_request", side_effect=failing):
        _, msg = _rpc(client, "tools/call", {"name": "skill_helper", "arguments": {"message": "hi"}})
    assert msg["result"]["isError"] and "overloaded" in msg["result"]["content"][0]["text"]


def test_empty_message_is_refused(client):
    _, msg = _rpc(client, "tools/call", {"name": "skill_helper", "arguments": {"message": "  "}})
    assert msg["result"]["isError"]


# --- naming ------------------------------------------------------------------------


def test_tool_name_sanitises_and_falls_back_to_the_id_on_a_slug_clash():
    assert mcp_server._tool_name("my-skill-1") == "skill_my_skill_1"
    assert mcp_server._tool_name("1abc") == "skill__1abc"
    twins = [_skill("id-one", slug="same"), _skill("id-two", slug="same")]
    with patch.object(mcp_server, "list_marketplace", return_value=twins):
        assert sorted(mcp_server._visible_skill_tools(None)) == ["skill_id_two", "skill_same"]
