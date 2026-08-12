"""API tests for GET /api/models — unauthenticated model list endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from protocols.models_route import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestGetModels:
    def test_returns_200_without_auth(self):
        response = client.get("/api/models")
        assert response.status_code == 200

    def test_response_has_models_list(self):
        response = client.get("/api/models")
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) >= 6

    def test_response_has_defaults(self):
        response = client.get("/api/models")
        data = response.json()
        assert "defaults" in data
        assert "google" in data["defaults"]
        assert "anthropic" in data["defaults"]
        assert "openai" in data["defaults"]

    def test_response_has_platform_default(self):
        response = client.get("/api/models")
        data = response.json()
        assert "platform_default" in data
        assert isinstance(data["platform_default"], str)
        assert len(data["platform_default"]) > 0

    def test_platform_default_in_models(self):
        response = client.get("/api/models")
        data = response.json()
        model_ids = {m["id"] for m in data["models"]}
        assert data["platform_default"] in model_ids

    def test_response_has_tier_defaults(self):
        # v6.6.0: named tiers exposed for the CLI (`aiplatform models tiers`).
        response = client.get("/api/models")
        data = response.json()
        assert "tier_defaults" in data
        assert "lite" in data["tier_defaults"]
        assert "smart" in data["tier_defaults"]

    def test_tier_defaults_reference_valid_models(self):
        response = client.get("/api/models")
        data = response.json()
        model_ids = {m["id"] for m in data["models"]}
        for tier_model in data["tier_defaults"].values():
            assert tier_model in model_ids

    def test_each_model_has_required_fields(self):
        response = client.get("/api/models")
        data = response.json()
        required = {"id", "api_name", "provider", "tier", "context_window", "max_output_tokens", "description"}
        for model in data["models"]:
            missing = required - set(model.keys())
            assert not missing, f"model {model.get('id')!r} missing fields: {missing}"

    def test_all_three_providers_in_response(self):
        response = client.get("/api/models")
        data = response.json()
        providers = {m["provider"] for m in data["models"]}
        assert "google" in providers
        assert "anthropic" in providers
        assert "openai" in providers

    def test_compaction_not_in_response(self):
        """Internal compaction config must not leak into the API response."""
        response = client.get("/api/models")
        data = response.json()
        assert "compaction" not in data
