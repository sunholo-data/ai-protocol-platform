"""Personal MCP tokens: store (hash-only, expiry, per-user) and the mint/list/revoke routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user, mcp_tokens
from protocols import mcp_token_routes

ALICE = User(uid="uid-alice", email="alice@example.com", domain="example.com", email_verified=True)
OTHER = User(uid="uid-other", email="other@example.com", domain="example.com", email_verified=True)


class FakeStore:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def get(self, c: str, d: str) -> dict | None:
        return self.docs.get(d)

    def set(self, c: str, d: str, data: dict, merge: bool = False) -> None:
        self.docs[d] = dict(data)

    def update(self, c: str, d: str, data: dict) -> None:
        self.docs[d].update(data)

    def delete(self, c: str, d: str) -> None:
        self.docs.pop(d, None)

    def query(self, c: str, filters: list | None = None, **_: Any) -> list[dict]:
        rows = [{**v, "__id": k} for k, v in self.docs.items()]
        for field, _op, value in filters or []:
            rows = [r for r in rows if r.get(field) == value]
        return rows


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    for name, fn in [("get_document", s.get), ("set_document", s.set), ("update_document", s.update)]:
        monkeypatch.setattr(mcp_tokens, name, fn)
    monkeypatch.setattr(mcp_tokens, "delete_document", s.delete)
    monkeypatch.setattr(mcp_tokens, "query_documents", s.query)
    return s


# --- store -------------------------------------------------------------------------


def test_token_is_shown_once_and_only_its_hash_is_stored(store):
    token, record = mcp_tokens.create_token("uid-alice", "Claude Code")
    assert token.startswith("aip_mcp_") and len(token) > 40
    assert token not in str(store.docs)  # plaintext never persisted
    assert record["name"] == "Claude Code" and len(record["id"]) == 12
    assert mcp_tokens.resolve_token(token) == "uid-alice"


def test_resolve_refuses_unknown_foreign_and_expired_tokens(store):
    token, _ = mcp_tokens.create_token("uid-alice", "x", days=1)
    assert mcp_tokens.resolve_token(token + "x") is None
    assert mcp_tokens.resolve_token("eyJhbGciOi.firebase.jwt") is None
    store.docs[next(iter(store.docs))]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert mcp_tokens.resolve_token(token) is None


def test_revoke_only_touches_the_owners_tokens(store):
    token, record = mcp_tokens.create_token("uid-alice", "x")
    assert mcp_tokens.revoke_token("uid-other", record["id"]) is False
    assert mcp_tokens.revoke_token("uid-alice", record["id"][:6]) is False  # too short to be unambiguous
    assert mcp_tokens.revoke_token("uid-alice", record["id"]) is True
    assert mcp_tokens.resolve_token(token) is None


def test_limits(store):
    with pytest.raises(mcp_tokens.TokenError):
        mcp_tokens.create_token("uid-alice", "x", days=0)
    with pytest.raises(mcp_tokens.TokenError):
        mcp_tokens.create_token("uid-alice", "x", days=mcp_tokens.MAX_DAYS + 1)
    for i in range(mcp_tokens.MAX_TOKENS_PER_USER):
        mcp_tokens.create_token("uid-alice", f"t{i}")
    with pytest.raises(mcp_tokens.TokenError):
        mcp_tokens.create_token("uid-alice", "one too many")


# --- routes ---------------------------------------------------------------------


def _client(user: User) -> TestClient:
    app = FastAPI()
    app.include_router(mcp_token_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_mint_list_revoke_round_trip(store):
    c = _client(ALICE)
    created = c.post("/api/mcp/tokens", json={"name": "Claude Code", "days": 30}).json()
    assert created["token"].startswith("aip_mcp_")
    listed = c.get("/api/mcp/tokens").json()
    assert [t["name"] for t in listed] == ["Claude Code"] and "token" not in listed[0]
    assert _client(OTHER).get("/api/mcp/tokens").json() == []
    assert _client(OTHER).delete(f"/api/mcp/tokens/{created['id']}").status_code == 404
    assert c.delete(f"/api/mcp/tokens/{created['id']}").json() == {"ok": True}
    assert c.get("/api/mcp/tokens").json() == []


def test_bad_request_is_a_400_with_a_reason(store):
    resp = _client(ALICE).post("/api/mcp/tokens", json={"days": 9999})
    assert resp.status_code == 400 and "days" in resp.json()["detail"]


@pytest.mark.parametrize("mode", ["anonymous_group_id", "machine"])
def test_only_people_can_mint(store, mode):
    robot = User(uid="uid-r", email="", domain="", auth_mode=mode)
    assert _client(robot).post("/api/mcp/tokens", json={}).status_code == 403
    assert store.docs == {}
