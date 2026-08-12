"""Unit tests for `channels.identity.IdentityResolver`.

Uses the same firestore-mock pattern as the rest of the suite — patch
`db.firestore.get_client` and assert the resolver makes the expected
collection/document calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from channels import identity
from channels.identity import COLLECTION, IdentityResolver


@pytest.fixture()
def mock_firestore():
    """Replace `db.firestore.get_client` with a MagicMock that returns sensible defaults.

    The default snapshot is `exists=False` — every test that wants a hit
    overrides `.get.return_value` on the document mock.
    """
    with patch.object(identity, "get_client") as mock_get:
        client = MagicMock()
        snap = MagicMock()
        snap.exists = False
        snap.to_dict.return_value = None
        client.collection.return_value.document.return_value.get.return_value = snap
        mock_get.return_value = client
        yield client, snap


class TestDocId:
    """The deterministic document ID used to key channel_identities."""

    def test_basic(self) -> None:
        # Internal helper but the format is part of the contract — if
        # this changes existing mappings are orphaned.
        assert identity._doc_id("discord", "12345") == "discord_12345"

    def test_rejects_slash_in_channel_user_id(self) -> None:
        # Firestore document IDs cannot contain "/" — fail loud rather
        # than silently corrupt the collection.
        with pytest.raises(ValueError, match="contains '/'"):
            identity._doc_id("email", "bad/value")


class TestResolveHit:
    """`resolve` returns the firebase_uid when the mapping exists."""

    @pytest.mark.asyncio
    async def test_returns_uid_on_hit(self, mock_firestore) -> None:
        client, snap = mock_firestore
        snap.exists = True
        snap.to_dict.return_value = {"firebase_uid": "abc-uid-123"}

        uid = await IdentityResolver.resolve("telegram", "999")
        assert uid == "abc-uid-123"
        client.collection.assert_called_with(COLLECTION)
        client.collection.return_value.document.assert_called_with("telegram_999")

    @pytest.mark.asyncio
    async def test_touches_last_seen_at_on_hit(self, mock_firestore) -> None:
        client, snap = mock_firestore
        snap.exists = True
        snap.to_dict.return_value = {"firebase_uid": "abc"}

        await IdentityResolver.resolve("telegram", "999")
        # last_seen_at touch is a best-effort update; verify it was called.
        update = client.collection.return_value.document.return_value.update
        assert update.called, "expected resolver to touch last_seen_at on hit"
        args = update.call_args[0][0]
        assert "last_seen_at" in args


class TestResolveMiss:
    """`resolve` returns None when no mapping exists."""

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self, mock_firestore) -> None:
        # Fixture default is `exists=False`; just verify the outcome.
        uid = await IdentityResolver.resolve("discord", "never-seen")
        assert uid is None

    @pytest.mark.asyncio
    async def test_returns_none_when_record_missing_uid(self, mock_firestore) -> None:
        # Record exists but has no `firebase_uid` field — treat as miss
        # (a malformed record should not auth as a known user).
        _client, snap = mock_firestore
        snap.exists = True
        snap.to_dict.return_value = {"channel": "discord"}  # no firebase_uid

        uid = await IdentityResolver.resolve("discord", "123")
        assert uid is None


class TestAutoCreate:
    """`auto_create` writes a fresh mapping and returns the synthetic UID."""

    @pytest.mark.asyncio
    async def test_writes_record_with_deterministic_uid(self, mock_firestore) -> None:
        client, _snap = mock_firestore

        uid = await IdentityResolver.auto_create("telegram", "42")
        # UID derived from channel + channel_user_id so re-resolves stable.
        assert uid == "channel-telegram_42"

        set_call = client.collection.return_value.document.return_value.set
        assert set_call.called
        record = set_call.call_args[0][0]
        assert record["channel"] == "telegram"
        assert record["channel_user_id"] == "42"
        assert record["firebase_uid"] == "channel-telegram_42"
        assert "created_at" in record
        assert "last_seen_at" in record

    @pytest.mark.asyncio
    async def test_email_populates_domain(self, mock_firestore) -> None:
        client, _snap = mock_firestore
        await IdentityResolver.auto_create("email", "user@example.com", email="user@example.com")
        record = client.collection.return_value.document.return_value.set.call_args[0][0]
        assert record["email"] == "user@example.com"
        assert record["domain"] == "example.com"

    @pytest.mark.asyncio
    async def test_no_email_leaves_domain_blank(self, mock_firestore) -> None:
        client, _snap = mock_firestore
        await IdentityResolver.auto_create("discord", "12345")
        record = client.collection.return_value.document.return_value.set.call_args[0][0]
        assert record["email"] == ""
        assert record["domain"] == ""
