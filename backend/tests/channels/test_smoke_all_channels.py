"""Cross-channel smoke test — the M5 framework-contract gate.

Mounts every shipped adapter (Discord + Email + the CLI demo) onto a
single FastAPI app via the live `ChannelRegistry` and confirms that
each `/api/{name}/webhook` endpoint honours the same three contracts:

    1. valid signed payload                 → 200 with `{"ok": ...}`
    2. missing / wrong signature            → 401
    3. non-actionable payload (parse=None)  → 200 `{"ok": true, "skipped": true}`

If a future adapter regresses on any of these, the test fails for that
channel without breaking the others. The test is **the framework's
contract** expressed across every real adapter at once — adding a new
adapter to `_ALL_CHANNELS` is the only change required to enroll it.

The CLI demo channel is included because it is the worked example in
`docs/integrations/channels-adapter-howto.md` — if the howto's code
diverges from the framework, this test catches it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import nacl.signing
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels._demo_cli import CliDemoChannel
from channels.base import BaseChannel
from channels.discord import DiscordChannel
from channels.email_ import EmailChannel
from channels.registry import ChannelRegistry

# --- per-channel test fixtures --------------------------------------------
#
# Each entry describes how to drive one channel through the three
# smoke-test scenarios above. The structure mirrors what a new adapter
# would supply if it wanted to opt into the contract test.


def _email_signed_headers(signing_key: str) -> dict[str, str]:
    """Build a valid Mailgun signature header trio for `signing_key`."""
    timestamp, token = "1700000000", "tk-smoke"
    expected = hmac.new(
        key=signing_key.encode(),
        msg=f"{timestamp}{token}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return {
        "x-mailgun-timestamp": timestamp,
        "x-mailgun-token": token,
        "x-mailgun-signature": expected,
    }


def _discord_signed(signing_key: nacl.signing.SigningKey, body: bytes) -> dict[str, str]:
    """Build a valid Ed25519 signature header pair for `body`."""
    timestamp = "1700000000"
    signature = signing_key.sign(timestamp.encode() + body).signature.hex()
    return {
        "x-signature-ed25519": signature,
        "x-signature-timestamp": timestamp,
    }


# Fixtures live inside fixture-builder callables because Discord's
# signing key is generated per-test and the channel instance has to be
# constructed with the matching verify key.

ChannelBuilder = Callable[[], tuple[BaseChannel, dict[str, Any]]]


def _build_email() -> tuple[BaseChannel, dict[str, Any]]:
    signing_key = "smoke-signing-key"
    ch = EmailChannel(
        signing_key=signing_key,
        api_key="api-test",
        domain="example.test",
        sender_address="bot@example.test",
        api_endpoint="https://api.eu.mailgun.test",
    )
    valid_payload = {
        "sender": "alice@example.com",
        "Message-Id": "<smoke-1@example.com>",
        "body-plain": "hello from smoke",
        "subject": "smoke",
    }
    skipped_payload = {"sender": "alice@example.com", "Message-Id": "<x@y>"}  # no body
    return ch, {
        "name": "email",
        "valid_headers": _email_signed_headers(signing_key),
        "valid_payload": valid_payload,
        "bad_headers": {"x-mailgun-signature": "deadbeef" * 8, "x-mailgun-timestamp": "0", "x-mailgun-token": "x"},
        "skipped_payload": skipped_payload,
        "content_type": "application/x-www-form-urlencoded",
    }


def _build_discord() -> tuple[BaseChannel, dict[str, Any]]:
    sk = nacl.signing.SigningKey.generate()
    ch = DiscordChannel(public_key_hex=sk.verify_key.encode().hex(), token="t")
    # Type 3 (MESSAGE_COMPONENT) is non-actionable — parse_inbound returns None.
    skipped_body = b'{"type":3}'
    # Type 2 with an unknown command name also returns None (still a valid
    # interaction, just not actionable for the framework). Good for smoke
    # because we don't need to mock identity / skill invoke.
    valid_body = b'{"type":2,"data":{"name":"unknown-cmd"},"user":{"id":"u1"}}'
    return ch, {
        "name": "discord",
        "valid_headers": _discord_signed(sk, valid_body),
        "valid_body": valid_body,
        "bad_headers": {"x-signature-ed25519": "00" * 64, "x-signature-timestamp": "0"},
        "skipped_body": skipped_body,
        "skipped_headers": _discord_signed(sk, skipped_body),
        "content_type": "application/json",
    }


def _build_cli() -> tuple[BaseChannel, dict[str, Any]]:
    """CLI demo channel — verify_webhook is always True, so 'bad signature'
    can't fail at the verify layer. We instead assert that a non-actionable
    payload short-circuits (no signature gate means 401 simply isn't part
    of this channel's contract). The skipped + valid checks still apply.
    """
    return CliDemoChannel(), {
        "name": "cli",
        "valid_headers": {},
        "valid_payload": {"user_id": "cli-user", "text": "smoke"},
        "skipped_payload": {"user_id": "cli-user", "text": ""},
        "bad_headers": None,  # CLI has no transport auth → no 401 path
        "content_type": "application/json",
    }


_ALL_CHANNELS: list[ChannelBuilder] = [_build_email, _build_discord, _build_cli]


# --- shared registry / app harness ----------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry():
    ChannelRegistry._clear_for_tests()
    yield
    ChannelRegistry._clear_for_tests()


@pytest.fixture()
def app_with_all_channels() -> tuple[FastAPI, dict[str, tuple[BaseChannel, dict[str, Any]]]]:
    """Build a fresh FastAPI app with every shipped channel mounted.

    Returns the app + a `{name: (channel, fixture_data)}` index so the
    parametrised tests can pull the right inputs per channel.
    """
    index: dict[str, tuple[BaseChannel, dict[str, Any]]] = {}
    for builder in _ALL_CHANNELS:
        ch, data = builder()
        ChannelRegistry.register(ch)
        index[data["name"]] = (ch, data)
    app = FastAPI()
    ChannelRegistry.mount_webhooks(app)
    return app, index


# --- the three contract checks --------------------------------------------


@pytest.mark.parametrize("channel_name", ["email", "discord", "cli"])
class TestChannelWebhookContract:
    """Each shipped channel must satisfy the framework's webhook contract."""

    def test_valid_payload_returns_ok(self, channel_name: str, app_with_all_channels) -> None:
        """A correctly-signed actionable payload returns 200 with `{"ok": ...}`.

        Identity / skill-invoke are mocked so the response never depends
        on Firestore or the skill processor — this is a framework-routing
        smoke check, not an end-to-end skill test.
        """
        app, index = app_with_all_channels
        _ch, data = index[channel_name]
        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value="uid-smoke")),
            patch("channels.base.AttachmentPipeline.upload", AsyncMock(return_value=[])),
            patch("channels._default_skill.get_user_default_skill", AsyncMock(return_value="general-assistant")),
            patch("channels._skill_invoke.invoke_skill_collected", AsyncMock(return_value="ok")),
            # Email's `select_skill` re-resolves through skill_config — stub it.
            patch("channels.email_._resolve_skill_by_name_or_id", AsyncMock(return_value=None)),
            # Discord's parse_inbound returns None on unknown slash command, so
            # we test the skipped case there separately. For the "valid" probe,
            # we don't need to mock anything else — discord's send is only
            # reached on actionable parses.
            patch.object(_ch, "send", AsyncMock()),
        ):
            with TestClient(app) as client:
                if channel_name == "discord":
                    resp = client.post(
                        f"/api/{channel_name}/webhook",
                        content=data["valid_body"],
                        headers={**data["valid_headers"], "content-type": data["content_type"]},
                    )
                else:
                    resp = client.post(
                        f"/api/{channel_name}/webhook",
                        json=data["valid_payload"] if data["content_type"] == "application/json" else None,
                        data=data["valid_payload"]
                        if data["content_type"].startswith("application/x-www-form")
                        else None,
                        headers=data["valid_headers"],
                    )
        assert resp.status_code == 200, f"{channel_name} expected 200 got {resp.status_code} body={resp.text}"
        body = resp.json()
        assert body.get("ok") is True, f"{channel_name} response missing ok=true: {body}"

    def test_bad_signature_returns_401(self, channel_name: str, app_with_all_channels) -> None:
        """Channels with transport-layer auth reject bad signatures with 401.

        The CLI demo has no signature gate (verify_webhook returns True
        unconditionally), so it is skipped here — that's documented in
        `_build_cli` and the howto. Adding a real auth gate would
        change its contract; this test enforces the rule.
        """
        app, index = app_with_all_channels
        _ch, data = index[channel_name]
        if data["bad_headers"] is None:
            pytest.skip(f"{channel_name} has no transport-layer signature to fail")

        with TestClient(app) as client:
            resp = client.post(
                f"/api/{channel_name}/webhook",
                json={"any": "payload"} if data["content_type"] == "application/json" else None,
                data={"any": "payload"} if data["content_type"].startswith("application/x-www-form") else None,
                content=b'{"any":"payload"}' if data["content_type"] == "application/json" else None,
                headers={**data["bad_headers"], "content-type": data["content_type"]},
            )
        assert resp.status_code == 401, f"{channel_name} expected 401 got {resp.status_code} body={resp.text}"

    def test_non_actionable_returns_skipped(self, channel_name: str, app_with_all_channels) -> None:
        """A parse_inbound that returns None short-circuits to skipped=true.

        Verifies that the framework's `handle_webhook` honours the
        non-actionable contract uniformly across adapters: no skill is
        invoked, the response is `{"ok": true, "skipped": true}`.
        """
        app, index = app_with_all_channels
        _ch, data = index[channel_name]
        invoke_mock = AsyncMock()
        with patch("channels._skill_invoke.invoke_skill_collected", invoke_mock):
            with TestClient(app) as client:
                if channel_name == "discord":
                    resp = client.post(
                        f"/api/{channel_name}/webhook",
                        content=data["skipped_body"],
                        headers={**data["skipped_headers"], "content-type": data["content_type"]},
                    )
                else:
                    resp = client.post(
                        f"/api/{channel_name}/webhook",
                        json=data["skipped_payload"] if data["content_type"] == "application/json" else None,
                        data=data["skipped_payload"]
                        if data["content_type"].startswith("application/x-www-form")
                        else None,
                        headers=data["valid_headers"],
                    )
        assert resp.status_code == 200, f"{channel_name} skipped expected 200 got {resp.status_code} body={resp.text}"
        body = resp.json()
        assert body == {"ok": True, "skipped": True}, f"{channel_name} expected skipped=true: {body}"
        assert invoke_mock.await_count == 0, f"{channel_name} should NOT invoke skill on non-actionable event"


# --- registry-level smoke -------------------------------------------------


class TestRegistryEnumeration:
    """The framework's registry exposes the live channel list — used by the
    deployment smoke script (`scripts/smoke-deployed.sh check_channels`).
    """

    def test_all_shipped_channels_are_registered(self, app_with_all_channels) -> None:
        _app, _index = app_with_all_channels
        names = set(ChannelRegistry.names())
        assert {"email", "discord", "cli"}.issubset(names), f"registry missing channels: {names}"

    def test_each_channel_has_a_webhook_route(self, app_with_all_channels) -> None:
        app, _index = app_with_all_channels
        paths = {route.path for route in app.routes}
        for name in ChannelRegistry.names():
            assert f"/api/{name}/webhook" in paths, f"missing route for channel {name!r}: {paths}"
