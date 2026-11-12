"""/api/me/memories — Your memory (docs/design/private/memory-screen.md).

Owner-only: the scope comes from the token, a project id must be the caller's
(else 404), and a ref outside the caller's scope is never deleted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adk.agui import APP_NAME
from auth import User, get_current_user

ENGINE = "reasoningEngines/123"


def _scope(uid: str, project_id: str | None = None) -> dict[str, str]:
    return {"app_name": APP_NAME, "user_id": uid, **({"project_id": project_id} if project_id else {})}


class _Pager:
    def __init__(self, rows):
        self.rows = list(rows)

    def __aiter__(self):
        async def values():
            for row in self.rows:
                yield row

        return values()


class _Bank:
    """Exact-scope matching, like Memory Bank."""

    def __init__(self, memories):
        self.memories = memories
        self.deleted: list[str] = []
        self.agent_engines = SimpleNamespace(memories=self)

    async def retrieve(self, *, name, scope, **_):
        return _Pager(SimpleNamespace(memory=m) for m in self.memories if m.scope == scope)

    async def get(self, *, name):
        return next(m for m in self.memories if m.name == name)

    async def delete(self, *, name):
        self.deleted.append(name)
        self.memories = [m for m in self.memories if m.name != name]


def _memory(key: str, fact: str, uid: str = "alice", project_id: str | None = None, when: str = "2026-10-01"):
    return SimpleNamespace(
        name=f"projects/x/locations/eu/{ENGINE}/memories/{key}",
        fact=fact,
        scope=_scope(uid, project_id),
        update_time=when,
    )


@pytest.fixture()
def bank(monkeypatch):
    b = _Bank(
        [
            _memory("a1", "Prefers Spanish", when="2026-10-02"),
            _memory("a2", "Team is Northern region", when="2026-10-03"),
            _memory("p1", "Project fact", project_id="proj-alice"),
            _memory("b1", "Bob's fact", uid="bob"),
        ]
    )
    service = SimpleNamespace(_agent_engine_id="123", _get_api_client=lambda: b)
    monkeypatch.setattr("adk.session.get_memory_service", lambda: service)
    monkeypatch.setattr(
        "db.projects.get_project", lambda uid, pid: {"id": pid} if (uid, pid) == ("alice", "proj-alice") else None
    )
    return b


def _client(uid: str = "alice") -> TestClient:
    from projects.memory_routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    return TestClient(app)


def test_lists_only_the_callers_personal_memories_newest_first(bank):
    body = _client().get("/api/me/memories").json()
    assert body["available"] is True
    assert [m["fact"] for m in body["memories"]] == ["Team is Northern region", "Prefers Spanish"]


def test_project_memory_needs_the_callers_own_project(bank):
    assert [m["fact"] for m in _client().get("/api/me/memories?project_id=proj-alice").json()["memories"]] == [
        "Project fact"
    ]
    res = _client("bob").get("/api/me/memories?project_id=proj-alice")
    assert res.status_code == 404 and res.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_delete_one_and_never_someone_elses(bank):
    mine = bank.memories[0].name
    res = _client().delete("/api/me/memories/item", params={"ref": mine})
    assert res.status_code == 200 and res.json() == {"deleted": 1, "refused": 0, "failed": 0}
    bobs = next(m.name for m in bank.memories if m.scope["user_id"] == "bob")
    res = _client().delete("/api/me/memories/item", params={"ref": bobs})
    assert res.status_code == 404
    assert bank.deleted == [mine]


def test_delete_all_needs_confirmation_and_stays_in_scope(bank):
    assert _client().delete("/api/me/memories").status_code == 400
    res = _client().delete("/api/me/memories", params={"confirm": "forget"})
    assert res.json()["deleted"] == 2
    left = {m.fact for m in bank.memories}
    assert left == {"Project fact", "Bob's fact"}  # project and other users untouched


def test_no_memory_bank_says_unavailable(monkeypatch):
    from google.adk.memory import InMemoryMemoryService

    monkeypatch.setattr("adk.session.get_memory_service", lambda: InMemoryMemoryService())
    assert _client().get("/api/me/memories").json() == {"available": False, "memories": [], "truncated": False}
    assert _client().delete("/api/me/memories", params={"confirm": "forget"}).status_code == 503
