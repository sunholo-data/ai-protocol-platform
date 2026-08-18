"""Channel identity enrichment — security tests (v6.21.0 M3).

Channel users have no JWT. Before this milestone they got
`User(uid, email="", domain="", group_tags=frozenset())`, so they failed
every domain-restricted or group-tagged skill. Enrichment opens that path,
which makes **where the tags come from** the whole security question.

The invariant under test is stated at `channels/identity.py:22-24`:

    Group tags are stored advisory-only; the authoritative copy is the
    Firebase custom claim. Channels do not grant privileges via
    group_tags — only the auth.firebase_auth.get_current_user path does.

`test_mirror_tampering_grants_nothing` is the load-bearing test in this
file. If it ever goes green for the wrong reason, a writer with access to
one Firestore collection gains skill access they were never granted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from auth.firebase_auth import User, clear_user_cache, resolve_user_by_uid
from channels._skill_invoke import _build_channel_user, _enrichment_enabled


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """The resolver caches by UID — never leak a record between tests."""
    clear_user_cache()
    yield
    clear_user_cache()


@pytest.fixture()
def enrichment_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_IDENTITY_ENRICHMENT", "1")


def _record(email: str = "", claims: dict[str, Any] | None = None) -> MagicMock:
    """A stand-in for a `firebase_admin.auth.UserRecord`."""
    record = MagicMock()
    record.email = email
    record.custom_claims = claims
    return record


class TestTheInvariant:
    """The advisory mirror must never become an authority."""

    def test_mirror_tampering_grants_nothing(self, enrichment_on: None) -> None:
        """Writing group_tags into channel_identities must grant NO access.

        Simulates an attacker (or a stale sync) putting privileged tags on
        the `channel_identities` doc while the authoritative custom claim
        carries none. The resulting User must have no tags.

        The mirror is served through BOTH Firestore seams — the module-local
        `channels.identity.get_client` binding and the shared
        `db.firestore.get_client` — so any future code that reaches for the
        mirror by either route sees the tampered tags and fails this test.

        Patching only the first is not enough: a negative control (injecting
        a `from db.firestore import get_client` mirror read into
        `_build_channel_user`) passed against the single-seam version. A
        security test that cannot fail is worse than no test.
        """
        tampered_mirror = {
            "channel": "discord",
            "channel_user_id": "847239",
            "firebase_uid": "uid-1",
            "email": "attacker@evil.test",
            "domain": "evil.test",
            "group_tags": ["ONE", "admin", "finance"],  # <-- the tampering
        }

        snapshot = MagicMock()
        snapshot.exists = True
        snapshot.to_dict.return_value = tampered_mirror
        firestore = MagicMock()
        firestore.collection.return_value.document.return_value.get.return_value = snapshot

        with (
            patch("channels.identity.get_client", return_value=firestore),
            patch("db.firestore.get_client", return_value=firestore),
            patch("firebase_admin.auth.get_user", return_value=_record(email="real@corp.test", claims={})),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset(), "the advisory mirror granted privileges — invariant broken"
        assert "ONE" not in user.group_tags
        assert user.email == "real@corp.test", "email must come from the authoritative record too"
        assert user.domain == "corp.test", "domain must derive from the authoritative email, not the mirror"

    def test_channel_supplied_data_is_never_consulted(self, enrichment_on: None) -> None:
        """A Discord nickname / guild role must not reach group_tags."""
        with patch(
            "firebase_admin.auth.get_user",
            return_value=_record(email="u@corp.test", claims={"groupTags": ["LEGIT"]}),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset({"LEGIT"})


class TestAuthoritativeResolution:
    def test_custom_claim_grants_tags(self, enrichment_on: None) -> None:
        with patch(
            "firebase_admin.auth.get_user",
            return_value=_record(email="u@corp.test", claims={"groupTags": ["ONE"]}),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset({"ONE"})
        assert user.email == "u@corp.test"
        assert user.domain == "corp.test"

    def test_no_claim_yields_no_tags(self, enrichment_on: None) -> None:
        with patch("firebase_admin.auth.get_user", return_value=_record(email="u@corp.test", claims=None)):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset()

    def test_derived_domain_tags_are_unioned_not_forked(self, enrichment_on: None) -> None:
        """Reuses `_apply_derived_group_tags` — same union as the JWT path."""
        with (
            patch(
                "firebase_admin.auth.get_user",
                return_value=_record(email="u@acmeenergy.com", claims={"groupTags": ["BASE"]}),
            ),
            patch("db.clients.resolve_derived_group_tags", return_value=frozenset({"ONE"})),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset({"BASE", "ONE"})

    def test_malformed_claim_grants_nothing(self, enrichment_on: None) -> None:
        """A non-iterable groupTags claim must not raise or grant."""
        with patch(
            "firebase_admin.auth.get_user",
            return_value=_record(email="u@corp.test", claims={"groupTags": 42}),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset()


class TestFailClosed:
    def test_unknown_uid_stays_restricted(self, enrichment_on: None) -> None:
        with patch("firebase_admin.auth.get_user", side_effect=ValueError("no such user")):
            user = _build_channel_user("uid-missing")

        assert user == User(uid="uid-missing", email="", domain="", group_tags=frozenset())

    def test_firebase_unavailable_stays_restricted(self, enrichment_on: None) -> None:
        """An outage must not become a permissive default."""
        with patch("firebase_admin.auth.get_user", side_effect=RuntimeError("backend unavailable")):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset()
        assert user.email == ""

    def test_derived_tag_lookup_failure_does_not_block(self, enrichment_on: None) -> None:
        """Firestore trouble degrades to base claims, never to an exception."""
        with (
            patch(
                "firebase_admin.auth.get_user",
                return_value=_record(email="u@corp.test", claims={"groupTags": ["BASE"]}),
            ),
            patch("db.clients.resolve_derived_group_tags", side_effect=RuntimeError("firestore down")),
        ):
            user = _build_channel_user("uid-1")

        assert user.group_tags == frozenset({"BASE"})


class TestFeatureFlag:
    def test_flag_off_preserves_todays_behaviour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHANNEL_IDENTITY_ENRICHMENT", raising=False)

        with patch("firebase_admin.auth.get_user", return_value=_record(claims={"groupTags": ["ONE"]})) as spy:
            user = _build_channel_user("uid-1")

        assert user == User(uid="uid-1", email="", domain="", group_tags=frozenset())
        spy.assert_not_called(), "flag off must not even reach Firebase"

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_flag_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CHANNEL_IDENTITY_ENRICHMENT", value)
        assert _enrichment_enabled()

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_falsy_flag_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CHANNEL_IDENTITY_ENRICHMENT", value)
        assert not _enrichment_enabled()

    def test_flag_is_read_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not captured at import — a flip must not need a redeploy."""
        monkeypatch.delenv("CHANNEL_IDENTITY_ENRICHMENT", raising=False)
        assert not _enrichment_enabled()
        monkeypatch.setenv("CHANNEL_IDENTITY_ENRICHMENT", "1")
        assert _enrichment_enabled()


class TestCaching:
    def test_repeated_lookups_hit_the_cache(self) -> None:
        """One Admin SDK call per inbound message would be a latency bug."""
        with patch(
            "firebase_admin.auth.get_user",
            return_value=_record(email="u@corp.test", claims={"groupTags": ["ONE"]}),
        ) as spy:
            for _ in range(5):
                resolve_user_by_uid("uid-1")

        assert spy.call_count == 1

    def test_cache_is_per_uid(self) -> None:
        with patch("firebase_admin.auth.get_user", side_effect=lambda uid: _record(email=f"{uid}@corp.test")) as spy:
            a = resolve_user_by_uid("uid-a")
            b = resolve_user_by_uid("uid-b")

        assert spy.call_count == 2
        assert a is not None and b is not None
        assert a.email != b.email

    def test_failures_are_cached_too(self) -> None:
        """A missing UID must not retry Firebase on every message."""
        with patch("firebase_admin.auth.get_user", side_effect=ValueError("nope")) as spy:
            assert resolve_user_by_uid("uid-x") is None
            assert resolve_user_by_uid("uid-x") is None

        assert spy.call_count == 1

    def test_cache_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = {"t": 0.0}
        monkeypatch.setattr("auth.firebase_auth.time.monotonic", lambda: clock["t"])

        with patch("firebase_admin.auth.get_user", return_value=_record(email="u@corp.test")) as spy:
            resolve_user_by_uid("uid-1")
            clock["t"] = 3600.0
            resolve_user_by_uid("uid-1")

        assert spy.call_count == 2

    def test_clear_cache_applies_a_claim_change_immediately(self) -> None:
        with patch("firebase_admin.auth.get_user", return_value=_record(claims={"groupTags": ["OLD"]})):
            first = resolve_user_by_uid("uid-1")

        clear_user_cache()

        with patch("firebase_admin.auth.get_user", return_value=_record(claims={"groupTags": ["NEW"]})):
            second = resolve_user_by_uid("uid-1")

        assert first is not None and second is not None
        assert first.group_tags == frozenset({"OLD"})
        assert second.group_tags == frozenset({"NEW"})


class TestGuildAllowlistStillGates:
    """Enrichment must not weaken the per-guild allowlist that precedes it.

    A Discord guild is a weaker trust boundary than Firebase auth — guild
    membership is not employment. `on_unknown_user` must still reject an
    unlisted user before any identity is resolved at all.
    """

    @pytest.mark.asyncio
    async def test_unlisted_guild_user_is_rejected_before_enrichment(self, enrichment_on: None) -> None:
        from channels.base import InboundMessage
        from channels.discord import DiscordChannel

        adapter = DiscordChannel(public_key_hex="aa" * 32, token="t")
        msg = InboundMessage(
            channel_user_id="999",
            channel_chat_id="c1",
            text="hello",
            metadata={"guild_id": "guild-with-no-route"},
        )

        with (
            patch("channels.discord.get_document", return_value=None),
            patch("firebase_admin.auth.get_user", return_value=_record(claims={"groupTags": ["ONE"]})) as spy,
        ):
            uid = await adapter.on_unknown_user(msg)

        assert uid is None, "no route doc must reject the user"
        spy.assert_not_called(), "rejection must happen before any privilege resolution"

    @pytest.mark.asyncio
    async def test_dm_is_rejected_regardless_of_claims(self, enrichment_on: None) -> None:
        from channels.base import InboundMessage
        from channels.discord import DiscordChannel

        adapter = DiscordChannel(public_key_hex="aa" * 32, token="t")
        msg = InboundMessage(channel_user_id="999", channel_chat_id="c1", text="hi", metadata={})

        with patch("firebase_admin.auth.get_user", return_value=_record(claims={"groupTags": ["ONE"]})):
            assert await adapter.on_unknown_user(msg) is None
