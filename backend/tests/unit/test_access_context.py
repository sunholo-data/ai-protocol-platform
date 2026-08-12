"""Unit tests for the AccessContext 5-type evaluator.

Covers the rule table in resource-access-control.md:

    public   → always True
    owner    → always True (regardless of access_control.type)
    private  → only owner
    domain   → user.domain == ac.domain
    specific → user.email in ac.emails
    tagged   → user.group_tags ∩ ac.tags non-empty

Pure-Python evaluator — no mocks needed.
"""

from __future__ import annotations

import pytest

from auth.access_context import AccessContext, build_access_context, can_access
from auth.firebase_auth import User
from db.models.access import AccessControl

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ctx(
    uid: str = "caller-uid",
    email: str = "caller@yourcompany.com",
    domain: str = "yourcompany.com",
    group_tags: frozenset[str] = frozenset(),
) -> AccessContext:
    return AccessContext(uid=uid, email=email, domain=domain, group_tags=group_tags)


OWNER_UID = "owner-uid"


# ---------------------------------------------------------------------------
# Evaluator rows
# ---------------------------------------------------------------------------


def test_public_always_true_even_for_stranger() -> None:
    ac = AccessControl(type="public")
    assert can_access(ac, _ctx(uid="stranger", email="stranger@example.com", domain="example.com"), OWNER_UID)


def test_public_true_without_email_or_domain() -> None:
    """public must not require any auth context — anon marketplace case."""
    ac = AccessControl(type="public")
    assert can_access(ac, _ctx(uid="", email="", domain=""), OWNER_UID)


def test_private_false_for_non_owner() -> None:
    ac = AccessControl(type="private")
    assert not can_access(ac, _ctx(uid="not-owner"), OWNER_UID)


def test_private_true_for_owner() -> None:
    ac = AccessControl(type="private")
    assert can_access(ac, _ctx(uid=OWNER_UID), OWNER_UID)


def test_owner_wins_regardless_of_type() -> None:
    """Owner access trumps the access_control.type — domain, specific, tagged, all."""
    for ac in (
        AccessControl(type="private"),
        AccessControl(type="domain", domain="elsewhere.com"),
        AccessControl(type="specific", emails=["somebody@else.com"]),
        AccessControl(type="tagged", tags=["some-other-tag"]),
    ):
        assert can_access(ac, _ctx(uid=OWNER_UID, domain="anything.com"), OWNER_UID), (
            f"owner should always win for type={ac.type}"
        )


def test_domain_match_returns_true() -> None:
    ac = AccessControl(type="domain", domain="yourcompany.com")
    assert can_access(ac, _ctx(domain="yourcompany.com"), OWNER_UID)


def test_domain_mismatch_returns_false() -> None:
    ac = AccessControl(type="domain", domain="yourcompany.com")
    assert not can_access(ac, _ctx(uid="x", domain="other.com"), OWNER_UID)


def test_domain_empty_user_domain_false() -> None:
    """A user with no email/domain (e.g. anon-ish) can't pass a domain check."""
    ac = AccessControl(type="domain", domain="yourcompany.com")
    assert not can_access(ac, _ctx(uid="x", email="", domain=""), OWNER_UID)


def test_specific_match_returns_true() -> None:
    ac = AccessControl(type="specific", emails=["caller@yourcompany.com", "other@example.com"])
    assert can_access(ac, _ctx(email="caller@yourcompany.com"), OWNER_UID)


def test_specific_mismatch_returns_false() -> None:
    ac = AccessControl(type="specific", emails=["someone@else.com"])
    assert not can_access(ac, _ctx(uid="x", email="caller@yourcompany.com"), OWNER_UID)


def test_specific_empty_list_returns_false() -> None:
    ac = AccessControl(type="specific", emails=[])
    assert not can_access(ac, _ctx(uid="x", email="anyone@example.com"), OWNER_UID)


def test_specific_none_list_returns_false() -> None:
    ac = AccessControl(type="specific", emails=None)
    assert not can_access(ac, _ctx(uid="x", email="anyone@example.com"), OWNER_UID)


def test_tagged_match_returns_true() -> None:
    ac = AccessControl(type="tagged", tags=["aitana-admin", "finance-team"])
    assert can_access(
        ac,
        _ctx(uid="x", group_tags=frozenset({"aitana-admin"})),
        OWNER_UID,
    )


def test_tagged_multiple_match_returns_true() -> None:
    """Intersection non-empty, doesn't need to be a subset."""
    ac = AccessControl(type="tagged", tags=["acme:alpha", "acme:beta"])
    assert can_access(
        ac,
        _ctx(uid="x", group_tags=frozenset({"acme:beta", "other-tag"})),
        OWNER_UID,
    )


def test_tagged_no_overlap_returns_false() -> None:
    ac = AccessControl(type="tagged", tags=["aitana-admin"])
    assert not can_access(
        ac,
        _ctx(uid="x", group_tags=frozenset({"unrelated"})),
        OWNER_UID,
    )


def test_tagged_user_has_no_tags_returns_false() -> None:
    ac = AccessControl(type="tagged", tags=["aitana-admin"])
    assert not can_access(ac, _ctx(uid="x", group_tags=frozenset()), OWNER_UID)


def test_tagged_none_tags_returns_false() -> None:
    ac = AccessControl(type="tagged", tags=None)
    assert not can_access(ac, _ctx(uid="x", group_tags=frozenset({"aitana-admin"})), OWNER_UID)


# ---------------------------------------------------------------------------
# AccessContext helper methods
# ---------------------------------------------------------------------------


class _FakeSkill:
    """Minimal resource conforming to the _HasAccess Protocol."""

    def __init__(self, access_control: AccessControl, owner_id: str) -> None:
        self.access_control = access_control
        self.owner_id = owner_id


def test_is_owner_true() -> None:
    ctx = _ctx(uid="u1")
    skill = _FakeSkill(AccessControl(type="private"), owner_id="u1")
    assert ctx.is_owner(skill)


def test_is_owner_false() -> None:
    ctx = _ctx(uid="u1")
    skill = _FakeSkill(AccessControl(type="private"), owner_id="u2")
    assert not ctx.is_owner(skill)


def test_is_owner_false_when_skill_owner_id_empty() -> None:
    ctx = _ctx(uid="u1")
    skill = _FakeSkill(AccessControl(type="private"), owner_id="")
    assert not ctx.is_owner(skill)


def test_can_access_skill_delegates_to_evaluator() -> None:
    ctx = _ctx(uid="caller", domain="yourcompany.com")
    skill = _FakeSkill(AccessControl(type="domain", domain="yourcompany.com"), owner_id="owner-xyz")
    assert ctx.can_access_skill(skill)


def test_is_skill_owner_alias() -> None:
    ctx = _ctx(uid="caller")
    skill = _FakeSkill(AccessControl(type="private"), owner_id="caller")
    assert ctx.is_skill_owner(skill)


# ---------------------------------------------------------------------------
# build_access_context
# ---------------------------------------------------------------------------


def test_build_access_context_from_user() -> None:
    user = User(
        uid="u1",
        email="u1@yourcompany.com",
        domain="yourcompany.com",
        group_tags=frozenset({"aitana-admin"}),
    )
    ctx = build_access_context(user)
    assert ctx.uid == "u1"
    assert ctx.email == "u1@yourcompany.com"
    assert ctx.domain == "yourcompany.com"
    assert ctx.group_tags == frozenset({"aitana-admin"})


def test_access_context_is_frozen() -> None:
    ctx = _ctx(uid="x")
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is a dataclass detail
        ctx.uid = "mutated"  # type: ignore[misc]
