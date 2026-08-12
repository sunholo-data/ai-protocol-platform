"""Unit tests for the A2A invocation surface mounted at /a2a.

Covers the contracts the middleware + card-split establish in M1+M2:
  - Card URL is rewritten to advertise the /a2a mount point
  - Dict and pydantic-model variants of the card agree byte-for-byte
  - Auth middleware returns a JSON-RPC error envelope (not bare 401 HTML)
    on missing Bearer when A2A_INVOCATION_REQUIRE_AUTH is on
  - Discovery paths (`/.well-known/agent.json`) bypass auth even when
    invocation paths require it

Full message/send + sendSubscribe round-trip tests run as integration
probes against the live deploy in M3 (scripts/simulate-a2a-peer.py
Step 4 + Step 5) because they need a real model + Vertex Agent Engine
session and would be flaky as pure unit tests.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from protocols.a2a import _build_card_dict, _build_card_model
from protocols.a2a_invocation import A2AAuthMiddleware


def _expects_json_rpc_error(payload: dict[str, Any]) -> None:
    """Assert the payload is a JSON-RPC 2.0 error envelope.

    Strict A2A clients expect `{jsonrpc, id, error}` even at the HTTP
    error layer; an HTML 401 page would break them.
    """
    assert payload.get("jsonrpc") == "2.0", f"missing jsonrpc tag: {payload}"
    assert "error" in payload, f"missing error block: {payload}"
    err = payload["error"]
    assert isinstance(err, dict) and "code" in err and "message" in err, f"malformed error block: {err}"
    # `id` should be present (null is allowed per RFC when we can't parse it)
    assert "id" in payload, f"missing id field: {payload}"


# ---------------------------------------------------------------------------
# Card-shape tests (no FastAPI / Starlette needed)
# ---------------------------------------------------------------------------


def test_a2a_card_url_advertises_a2a_mount_point() -> None:
    """The wire-shape card must advertise card.url = <base>/a2a so peers
    POST message/send to the right place. This is the regression guard
    for the M1 URL change.
    """
    card = _build_card_dict("https://example.com")
    assert card["url"] == "https://example.com/a2a", f"card.url must end with /a2a, got {card['url']!r}"

    # Trailing slash on base must not produce double-slash.
    card_trailing = _build_card_dict("https://example.com/")
    assert card_trailing["url"] == "https://example.com/a2a", (
        f"trailing-slash base produced double-slash: {card_trailing['url']!r}"
    )


def test_a2a_dict_and_model_cards_agree() -> None:
    """The dict variant (wire) and pydantic AgentCard model variant
    (to_a2a input) must describe the same agent.

    If they drift, the /.well-known/agent.json card and the
    /a2a/.well-known/agent.json card (served by ADK) would advertise
    different things — a hard-to-debug split-brain.
    """
    base = "https://example.com"
    d = _build_card_dict(base)
    m = _build_card_model(base)
    # AgentCard pydantic uses snake_case internally but emits camelCase via
    # by_alias=True. exclude_none drops fields the dict variant doesn't set.
    m_dump = m.model_dump(by_alias=True, exclude_none=True)
    # Allow ADK-added fields (e.g. preferredTransport) on the model side —
    # we only assert the dict's content is a subset.
    for field in ("protocolVersion", "name", "description", "url", "version", "skills"):
        assert d[field] == m_dump.get(field), (
            f"dict/model disagree on {field}: dict={d[field]!r} model={m_dump.get(field)!r}"
        )
    # Capabilities: extensions list shape must match (object form, not strings).
    assert d["capabilities"]["extensions"] == m_dump["capabilities"]["extensions"], (
        "extensions arrays diverge between dict and model"
    )


# ---------------------------------------------------------------------------
# Middleware contract tests
# ---------------------------------------------------------------------------


def _build_test_app(*, auth_required: bool) -> Starlette:
    """Tiny Starlette app + the A2A auth middleware for isolation testing.

    Two routes mirror the shape of what ADK's to_a2a mounts:
      - `/` — the JSON-RPC invocation endpoint (auth-required)
      - `/.well-known/agent.json` — discovery (unauthenticated)
    Each returns 200 with a simple payload so we can tell auth-pass
    from auth-fail by status code + body shape.
    """

    async def rpc(_request: Any) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": "x", "result": {"ok": True}})

    async def well_known(_request: Any) -> JSONResponse:
        return JSONResponse({"name": "test agent", "url": "https://example.com/a2a"})

    routes = [
        Route("/", rpc, methods=["POST"]),
        Route("/.well-known/agent.json", well_known, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    # The fixture controls auth via env var.
    monkey = pytest.MonkeyPatch()
    if auth_required:
        monkey.setenv("A2A_INVOCATION_REQUIRE_AUTH", "true")
    else:
        monkey.setenv("A2A_INVOCATION_REQUIRE_AUTH", "false")
    app.state._monkey = monkey  # keep alive for teardown
    app.add_middleware(A2AAuthMiddleware)
    return app


def test_a2a_middleware_returns_jsonrpc_error_on_missing_bearer() -> None:
    """POST / without an Authorization header must return a JSON-RPC
    error envelope (not an HTML 401 page), so a strict A2A client can
    parse the failure without bespoke handling.
    """
    app = _build_test_app(auth_required=True)
    client = TestClient(app)
    resp = client.post("/", json={"jsonrpc": "2.0", "id": "1", "method": "message/send"})

    # FastAPI's get_current_user raises HTTPException with whatever
    # status it deems right; we accept anything in the 4xx range so this
    # test isn't brittle to the underlying verifier's choice. The KEY
    # contract is the error-envelope shape.
    assert 400 <= resp.status_code < 500, f"expected client error, got {resp.status_code}"
    _expects_json_rpc_error(resp.json())
    app.state._monkey.undo()


def test_a2a_middleware_skips_auth_for_discovery_path() -> None:
    """`/.well-known/agent.json` under the /a2a mount must remain
    publicly fetchable even when invocation requires auth. A2A spec
    treats discovery as unauthenticated by default.
    """
    app = _build_test_app(auth_required=True)
    client = TestClient(app)
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200, f"discovery hit auth wall: {resp.status_code}"
    body = resp.json()
    assert body["name"] == "test agent"
    app.state._monkey.undo()


def test_a2a_middleware_passes_through_when_auth_disabled() -> None:
    """`A2A_INVOCATION_REQUIRE_AUTH=false` short-circuits the middleware
    for forks that need to rely on network-level isolation instead of
    Bearer tokens (e.g. some Gemini Enterprise routing flows). The
    invocation endpoint becomes unauthenticated.
    """
    app = _build_test_app(auth_required=False)
    client = TestClient(app)
    resp = client.post("/", json={"jsonrpc": "2.0", "id": "1", "method": "message/send"})
    assert resp.status_code == 200, f"middleware blocked unauth request: {resp.status_code}"
    assert resp.json()["result"] == {"ok": True}
    app.state._monkey.undo()


# ---------------------------------------------------------------------------
# Integration test — FastAPI mount actually accepts the middleware-wrapped
# Starlette sub-app. This catches the "I refactored build_a2a_app and broke
# the mount" regression without standing up Vertex.
# ---------------------------------------------------------------------------


def test_build_a2a_app_returns_mountable_starlette_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_a2a_app(agent, base_url)` returns a Starlette app that
    FastAPI.mount() accepts and exposes via Starlette's TestClient.

    We use a minimal LlmAgent that never actually runs the model — the
    test only verifies the mount + discovery path. Real invocation is
    covered by M3's live-deploy probe (simulate-a2a-peer.py Step 4).
    """
    # Auth off so the test doesn't need a Bearer token; the auth contract
    # is covered by the middleware tests above.
    monkeypatch.setenv("A2A_INVOCATION_REQUIRE_AUTH", "false")

    from google.adk.agents import LlmAgent

    from protocols.a2a_invocation import build_a2a_app

    agent = LlmAgent(
        name="probe_agent",
        model="gemini-2.5-flash",
        description="A test agent for the A2A mount integration test",
        instruction="Probe agent — never reached during this test.",
    )

    a2a_app = build_a2a_app(agent, "https://example.com")

    fastapi_app = FastAPI()
    fastapi_app.mount("/a2a", a2a_app)
    client = TestClient(fastapi_app)

    # to_a2a registers routes via Starlette lifespan; TestClient enters
    # the lifespan automatically. Discovery card should be reachable.
    resp = client.get("/a2a/.well-known/agent.json")
    assert resp.status_code == 200, f"mounted A2A discovery card 404'd: {resp.status_code} body={resp.text[:200]!r}"
    card = resp.json()
    assert card["url"] == "https://example.com/a2a", f"mounted card advertises wrong url: {card['url']!r}"
    assert card["protocolVersion"] == "0.2.0"
    # Sanity: the card from the mount and the dict variant must agree
    # on the cross-surface contract (this is the byte-equality check
    # from a different angle).
    expected = _build_card_dict("https://example.com")
    for field in ("protocolVersion", "name", "description", "url"):
        assert card[field] == expected[field], (
            f"mounted card disagrees with _build_card_dict on {field}: "
            f"mounted={card[field]!r} expected={expected[field]!r}"
        )
