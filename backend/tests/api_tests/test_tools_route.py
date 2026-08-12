"""API tests for GET /api/tools — unauthenticated tool catalog endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adk.tools import catalog_tool_names, known_tool_names
from protocols.tools_route import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestGetTools:
    def test_returns_200_without_auth(self):
        response = client.get("/api/tools")
        assert response.status_code == 200

    def test_response_has_tools_list(self):
        response = client.get("/api/tools")
        data = response.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) >= 6

    def test_each_tool_has_required_fields(self):
        response = client.get("/api/tools")
        data = response.json()
        required = {"name", "label", "description", "category"}
        for tool in data["tools"]:
            missing = required - set(tool.keys())
            assert not missing, f"tool {tool.get('name')!r} missing fields: {missing}"

    def test_tool_names_are_unique(self):
        response = client.get("/api/tools")
        names = [t["name"] for t in response.json()["tools"]]
        assert len(names) == len(set(names))

    def test_every_catalog_tool_is_resolvable(self):
        """Guard: a rename in TOOL_REGISTRY / _MODEL_AWARE must not orphan a
        picker entry. Every catalog name must be a tool the resolver accepts."""
        orphans = catalog_tool_names() - known_tool_names()
        assert not orphans, f"catalog tools not resolvable by agent: {orphans}"
