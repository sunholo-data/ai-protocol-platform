"""Unit tests for `channels.registry.ChannelRegistry`.

Covers registration semantics (idempotent on same instance, conflict on
different instance), `get` / `names`, and the auto-mount machinery that
turns a list of registered channels into `POST /api/{name}/webhook`
routes on a FastAPI app.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels.base import BaseChannel, InboundMessage, OutboundMessage
from channels.registry import ChannelRegistry, ChannelRegistryError


class _StubChannel(BaseChannel):
    """Minimal concrete channel used in registry tests."""

    name = "stub"

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return True

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None  # All registry tests use empty payloads — short-circuit

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        pass


class _SecondStubChannel(BaseChannel):
    name = "second"

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return True

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Clear the registry around every test so cross-test pollution can't mask bugs."""
    ChannelRegistry._clear_for_tests()
    yield
    ChannelRegistry._clear_for_tests()


class TestRegister:
    def test_register_makes_channel_retrievable(self) -> None:
        ch = _StubChannel()
        ChannelRegistry.register(ch)
        assert ChannelRegistry.get("stub") is ch

    def test_register_same_instance_twice_is_idempotent(self) -> None:
        ch = _StubChannel()
        ChannelRegistry.register(ch)
        ChannelRegistry.register(ch)  # should not raise
        assert ChannelRegistry.get("stub") is ch

    def test_register_different_instance_with_same_name_raises(self) -> None:
        ChannelRegistry.register(_StubChannel())
        with pytest.raises(ChannelRegistryError, match="already registered"):
            ChannelRegistry.register(_StubChannel())  # different instance

    def test_get_unknown_name_raises(self) -> None:
        with pytest.raises(ChannelRegistryError, match="No channel registered"):
            ChannelRegistry.get("nope")

    def test_names_returns_sorted(self) -> None:
        ChannelRegistry.register(_SecondStubChannel())
        ChannelRegistry.register(_StubChannel())
        assert ChannelRegistry.names() == ["second", "stub"]


class TestMountWebhooks:
    def test_mount_creates_post_route_per_channel(self) -> None:
        ChannelRegistry.register(_StubChannel())
        ChannelRegistry.register(_SecondStubChannel())

        app = FastAPI()
        ChannelRegistry.mount_webhooks(app)

        # Both channels should now have webhook routes
        paths = {route.path for route in app.routes}
        assert "/api/stub/webhook" in paths
        assert "/api/second/webhook" in paths

    def test_mount_calls_handle_webhook_on_post(self) -> None:
        ch = _StubChannel()
        ch.handle_webhook = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
        ChannelRegistry.register(ch)

        app = FastAPI()
        ChannelRegistry.mount_webhooks(app)

        with TestClient(app) as client:
            resp = client.post("/api/stub/webhook", json={"hello": "world"})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        ch.handle_webhook.assert_called_once()
        # Verify payload + headers + body shape passed through
        call_args = ch.handle_webhook.call_args
        assert call_args.args[0] == {"hello": "world"}  # payload
        assert isinstance(call_args.args[1], dict)  # headers
        assert isinstance(call_args.args[2], bytes)  # raw body

    def test_mount_twice_raises(self) -> None:
        ChannelRegistry.register(_StubChannel())
        app = FastAPI()
        ChannelRegistry.mount_webhooks(app)
        with pytest.raises(ChannelRegistryError, match="already called"):
            ChannelRegistry.mount_webhooks(app)

    def test_mount_handles_form_encoded_payload(self) -> None:
        """Mailgun sends `application/x-www-form-urlencoded` — registry decodes it."""
        ch = _StubChannel()
        captured = {}

        async def capture(payload: Any, headers: Any, body: bytes) -> dict:
            captured["payload"] = payload
            return {"ok": True}

        ch.handle_webhook = capture  # type: ignore[method-assign]
        ChannelRegistry.register(ch)

        app = FastAPI()
        ChannelRegistry.mount_webhooks(app)

        with TestClient(app) as client:
            resp = client.post(
                "/api/stub/webhook",
                data={"sender": "a@b.com", "subject": "hi"},
            )

        assert resp.status_code == 200
        assert captured["payload"]["sender"] == "a@b.com"

    def test_clear_for_tests_empties_registry(self) -> None:
        ChannelRegistry.register(_StubChannel())
        assert ChannelRegistry.names() == ["stub"]
        ChannelRegistry._clear_for_tests()
        assert ChannelRegistry.names() == []
