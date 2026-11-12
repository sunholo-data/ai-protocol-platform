"""MULTI-LANG M3: a skill's `locales` block round-trips through /api/skills.

Create, read, update (omitted vs `{}`), fork, and validation — through the real
handlers AND the real skill_config persistence (model_dump → store → re-read),
with only the Firestore client swapped for a dict, so a field dropped anywhere
on the way (request model, create kwargs, SkillConfig, serialisation, response
model) fails here.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from skills import skill_config
from skills.routes import router

XX = {"xx": {"display_name": "Xest skill", "initial_message": "Xi! Xow can I xelp?"}}


class _DictFirestore:
    """Just the calls skill_config's create/get/update make, over a dict."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def set_document(self, _collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.docs[doc_id] = copy.deepcopy(data)

    def get_document(self, _collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    def update_document(self, _collection: str, doc_id: str, updates: dict[str, Any]) -> None:
        # Firestore's update() replaces each named top-level field wholesale.
        self.docs[doc_id].update(copy.deepcopy(updates))


@pytest.fixture()
def store():
    fake = _DictFirestore()
    skill_config._cache.clear()
    skill_config._lookups.clear()
    with (
        patch.object(skill_config, "fs", fake),
        patch("skills.routes.unique_slug", side_effect=lambda owner_id, base, exclude_skill_id=None: base),
        patch("skills.routes.check_machine_access_control"),
        patch("skills.routes.validate_skill_extensions"),
    ):
        yield fake
    skill_config._cache.clear()
    skill_config._lookups.clear()


@pytest.fixture()
def client(store):
    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        u = User(uid="user-1", email="owner@example.com", domain="example.com", group_tags=frozenset())
        request.state.access = build_access_context(u)
        return u

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


def _create(client, **extra) -> dict[str, Any]:
    resp = client.post(
        "/api/skills",
        json={"name": "test-skill", "description": "A test skill.", "displayName": "Test skill", **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_stores_and_returns_locales(client, store):
    created = _create(client, locales=XX)
    assert created["locales"] == XX
    assert store.docs[created["skillId"]]["locales"] == XX
    # A fresh read (cache dropped) comes back from the store unchanged.
    skill_config._cache.clear()
    assert client.get(f"/api/skills/{created['skillId']}").json()["locales"] == XX


def test_create_without_locales_returns_an_empty_map(client):
    assert _create(client)["locales"] == {}


def test_update_with_locales_omitted_leaves_them(client):
    sid = _create(client, locales=XX)["skillId"]
    resp = client.put(f"/api/skills/{sid}", json={"displayName": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["displayName"] == "Renamed"
    assert resp.json()["locales"] == XX


def test_update_with_empty_locales_clears_them(client, store):
    sid = _create(client, locales=XX)["skillId"]
    resp = client.put(f"/api/skills/{sid}", json={"locales": {}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["locales"] == {}
    assert store.docs[sid]["locales"] == {}


def test_update_replaces_the_map_and_drops_blank_fields(client):
    sid = _create(client, locales=XX)["skillId"]
    resp = client.put(
        f"/api/skills/{sid}",
        json={"locales": {"xx": {"display_name": "Xenamed", "initial_message": ""}, "yy": {"description": "  "}}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locales"] == {"xx": {"display_name": "Xenamed"}}


def test_fork_copies_locales_and_marks_names(client):
    source = _create(client, locales=XX, initialMessage="Hi! How can I help?")
    resp = client.post(f"/api/skills/{source['skillId']}/fork")
    assert resp.status_code == 201, resp.text
    fork = resp.json()
    assert fork["skillId"] != source["skillId"]
    assert fork["displayName"] == "Test skill (Fork)"
    assert fork["initialMessage"] == "Hi! How can I help?"
    assert fork["locales"] == {"xx": {"display_name": "Xest skill (Fork)", "initial_message": "Xi! Xow can I xelp?"}}


@pytest.mark.parametrize(
    ("locales", "message"),
    [
        ({"xx": {"instructions": "Do x"}}, "unknown field 'instructions'"),
        ({"not a tag": {"display_name": "x"}}, "not a language tag"),
        ({"xx": {"display_name": "x" * 200}}, "at most 128"),
    ],
)
def test_create_and_update_reject_a_bad_block(client, locales, message):
    resp = client.post("/api/skills", json={"name": "bad-skill", "description": "d", "locales": locales})
    assert resp.status_code == 422
    assert message in resp.text

    sid = _create(client)["skillId"]
    resp = client.put(f"/api/skills/{sid}", json={"locales": locales})
    assert resp.status_code == 422
    assert message in resp.text
