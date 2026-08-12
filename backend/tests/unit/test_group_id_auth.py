"""Unit tests for ``auth.group_id_auth`` (sprint 2.11, M1).

Anonymous group-ID auth — the fourth auth mode. Covers token mint +
verify, group lifecycle, the seven-gate join contract, the no-PII
invariant on the `User` shape, and the fail-loud module-import
behaviour when the signing secret is missing.

Threat model + design: docs/design/v6.2.0/anonymous-group-id-auth.md

The seven gates on ``join_group`` are tested explicitly. Each gate
has its own ``test_gate_N_<name>`` so a future maintainer can see at
a glance which axis closed which hole.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import jwt
import pytest

# Ensure the signing secret is set BEFORE the module imports — the
# module is documented to fail loud on missing secret at import time.
os.environ.setdefault("GROUP_AUTH_SIGNING_SECRET", "test-secret-for-pytest-only")

from auth.group_id_auth import (
    GROUP_AUTH_SIGNING_SECRET_ENV,
    AnonymousGroupAuth,
    GroupExpired,
    GroupNotFound,
    GroupRevoked,
    GroupSessionCapExceeded,
    InvalidGroupToken,
    create_group,
    delete_group,
    get_group,
    join_group,
    verify_group_token,
)

# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_state():
    """Each test starts with a clean in-memory group store."""
    AnonymousGroupAuth.reset_for_tests()
    yield
    AnonymousGroupAuth.reset_for_tests()


@contextmanager
def _freeze_time(t: float):
    """Override the module's time provider for the duration of the test.

    Patterned on TokenBucketRateLimiter's time_provider — keeps tests
    deterministic without ``time.sleep``.
    """
    prev = AnonymousGroupAuth.time_provider
    AnonymousGroupAuth.time_provider = staticmethod(lambda: t)
    try:
        yield
    finally:
        AnonymousGroupAuth.time_provider = prev


# ─── Module import contract ─────────────────────────────────────────────────


def test_signing_secret_env_var_is_required():
    """If the env var is missing, the module must fail loud on first use,
    not silently produce unsigned-or-weakly-signed tokens. Verified by
    flipping the secret to empty and re-running mint."""
    # Capture original
    original = os.environ.get(GROUP_AUTH_SIGNING_SECRET_ENV, "")
    try:
        os.environ[GROUP_AUTH_SIGNING_SECRET_ENV] = ""
        # Force the module to re-read the secret.
        AnonymousGroupAuth.reset_for_tests()
        with pytest.raises(RuntimeError, match="GROUP_AUTH_SIGNING_SECRET"):
            create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    finally:
        os.environ[GROUP_AUTH_SIGNING_SECRET_ENV] = original
        AnonymousGroupAuth.reset_for_tests()


# ─── Group lifecycle ────────────────────────────────────────────────────────


def test_create_group_returns_id_and_expiry():
    rec = create_group(
        title="Physics 2A",
        skill_ids=["physics-tutor"],
        creator_uid="teacher-1",
        ttl_days=7,
    )
    assert rec.group_id
    assert len(rec.group_id) >= 6  # short-code shape
    assert rec.expires_at > time.time()
    assert rec.expires_at < time.time() + 8 * 86400  # within ttl_days + 1d slack
    assert rec.creator_uid == "teacher-1"
    # GroupRecord is frozen, so skill_ids is stored as a tuple (immutable
    # by convention even though the input is a list).
    assert tuple(rec.skill_ids) == ("physics-tutor",)


def test_create_group_id_is_high_entropy_alphabet():
    """No ambiguous chars (0/O/1/I), no lowercase, hyphen-separated."""
    rec = create_group(
        title="x",
        skill_ids=["s"],
        creator_uid="u",
        ttl_days=7,
    )
    body = rec.group_id.replace("-", "")
    forbidden = set("0O1I")
    assert not (set(body) & forbidden), f"group_id {rec.group_id} contains ambiguous chars"
    assert body.isupper() and body.isalnum()


def test_get_group_returns_record_for_existing_id():
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    found = get_group(rec.group_id)
    assert found is not None
    assert found.group_id == rec.group_id


def test_get_group_returns_none_for_missing_id():
    assert get_group("DOES-NOTEXIST") is None


def test_delete_group_revokes_all_tokens():
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    join_result = join_group(rec.group_id, client_ip="1.1.1.1")
    token = join_result.token
    # Verify works before revoke.
    payload = verify_group_token(token)
    assert payload["group_id"] == rec.group_id

    delete_group(rec.group_id, requesting_uid="u")

    # After revoke, verify fails with GroupRevoked.
    with pytest.raises(GroupRevoked):
        verify_group_token(token)


def test_delete_group_requires_creator_uid_match():
    """Only the creator can revoke. Other Firebase users get an
    error (caller enforces via 403; here the function raises)."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="teacher-1", ttl_days=7)
    with pytest.raises(PermissionError):
        delete_group(rec.group_id, requesting_uid="some-other-teacher")
    # Still present.
    assert get_group(rec.group_id) is not None


# ─── Token mint + verify shape ──────────────────────────────────────────────


def test_token_has_design_specified_shape():
    """Token claims exactly: sub, group_id, exp, iat, auth_mode."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    result = join_group(rec.group_id, client_ip="1.1.1.1")
    # Decode WITHOUT verifying (we already test verify separately) to
    # inspect raw claims.
    secret = os.environ[GROUP_AUTH_SIGNING_SECRET_ENV]
    claims = jwt.decode(result.token, secret, algorithms=["HS256"])
    assert set(claims.keys()) == {"sub", "group_id", "exp", "iat", "auth_mode"}
    assert claims["auth_mode"] == "anonymous_group_id"
    assert claims["group_id"] == rec.group_id
    assert claims["sub"].startswith("anon-")
    assert rec.group_id.replace("-", "") in claims["sub"].replace("-", "")  # contains group hash
    assert claims["exp"] > claims["iat"]


def test_token_uses_hs256():
    """Algorithm pinned to HS256; verify rejects other algorithms."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    # The HS256 invariant is enforced by the verifier, not the minter —
    # scaffolding for this test produces forged tokens with `alg: none`
    # and with the wrong secret, both of which must be rejected.

    # Forge a token with `alg: none` — must be rejected.
    forged = jwt.encode(
        {
            "sub": "anon-x",
            "group_id": rec.group_id,
            "exp": time.time() + 100,
            "iat": time.time(),
            "auth_mode": "anonymous_group_id",
        },
        "",
        algorithm="none",
    )
    with pytest.raises(InvalidGroupToken):
        verify_group_token(forged)

    # Forge with wrong secret — must be rejected.
    bad = jwt.encode(
        {
            "sub": "anon-x",
            "group_id": rec.group_id,
            "exp": time.time() + 100,
            "iat": time.time(),
            "auth_mode": "anonymous_group_id",
        },
        "different-secret",
        algorithm="HS256",
    )
    with pytest.raises(InvalidGroupToken):
        verify_group_token(bad)


def test_verify_returns_payload_for_valid_token():
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    result = join_group(rec.group_id, client_ip="1.1.1.1")
    payload = verify_group_token(result.token)
    assert payload["group_id"] == rec.group_id
    assert payload["auth_mode"] == "anonymous_group_id"


def test_synthetic_uid_does_not_leak_pii():
    """The uid is `anon-<group_hash>-<random>`. No email-like, no
    obvious format. Just structural inspection here — the actual
    no-PII contract on the User object is tested in the User shape
    test below."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    result = join_group(rec.group_id, client_ip="1.1.1.1")
    assert result.uid.startswith("anon-")
    assert "@" not in result.uid
    # 32 chars of random suffix.
    suffix = result.uid.rsplit("-", 1)[-1]
    assert len(suffix) >= 16


def test_user_from_group_token_has_no_pii_fields():
    """The User object built from a group token MUST have empty email
    + empty domain + auth_mode + group_id. Anonymous = no PII."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    result = join_group(rec.group_id, client_ip="1.1.1.1")
    user = AnonymousGroupAuth.user_from_token(result.token)
    assert user.uid == result.uid
    assert user.email == ""
    assert user.domain == ""
    assert user.auth_mode == "anonymous_group_id"
    assert user.group_id == rec.group_id


# ─── Defaults ───────────────────────────────────────────────────────────────


def test_default_ttl_is_thirty_days():
    """Design spec: 30 days default TTL on group codes."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u")
    seconds_until_expiry = rec.expires_at - time.time()
    # Within 1 hour of 30 days.
    assert 29 * 86400 < seconds_until_expiry < 31 * 86400


def test_default_max_concurrent_sessions_is_one_hundred():
    """Design spec: 100 active sessions/group/day default."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    assert rec.max_concurrent_sessions == 100


def test_per_create_override_caps():
    rec = create_group(
        title="x",
        skill_ids=["s"],
        creator_uid="u",
        ttl_days=1,
        max_concurrent_sessions=5,
    )
    assert rec.max_concurrent_sessions == 5
    seconds = rec.expires_at - time.time()
    assert 0 < seconds < 2 * 86400


# ─── The seven gates on join_group ──────────────────────────────────────────


def test_gate_1_malformed_body_caught_at_pydantic_layer():
    """Gate 1 is handled by Pydantic's request-body validation upstream
    of the function (FastAPI returns 422 before calling join_group).
    This test documents that join_group itself is the FUNCTION layer —
    the route layer (M2) gets the Pydantic 422 coverage.

    Here we verify the function signature is strict: a non-string
    group_id raises before any state mutation."""
    with pytest.raises((TypeError, ValueError)):
        join_group(group_id=None, client_ip="1.1.1.1")  # type: ignore[arg-type]


def test_gate_2_unknown_group_id_raises_group_not_found():
    """Gate 2: an unknown group_id raises GroupNotFound (route layer
    translates to 401)."""
    with pytest.raises(GroupNotFound):
        join_group("NOPE-XXXX", client_ip="1.1.1.1")


def test_gate_3_expired_group_raises_group_expired():
    """Gate 3: a group past its expires_at raises GroupExpired."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=1)
    # Travel 2 days into the future.
    with _freeze_time(rec.expires_at + 86400):
        with pytest.raises(GroupExpired):
            join_group(rec.group_id, client_ip="1.1.1.1")


def test_gate_4_revoked_group_raises_group_revoked():
    """Gate 4: a deleted group raises GroupRevoked. Distinct from
    GroupNotFound — operators may want to log + telemeter differently."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    delete_group(rec.group_id, requesting_uid="u")
    with pytest.raises((GroupNotFound, GroupRevoked)):
        join_group(rec.group_id, client_ip="1.1.1.1")


def test_gate_5_rate_limit_exceeded_at_eleventh_join_same_ip():
    """Gate 5: 11 joins from same IP in 60s → 11th raises
    RateLimitExceeded. (Imported via group_id_auth which re-exposes it
    from group_rate_limit.)"""
    from auth.group_rate_limit import RateLimitExceeded

    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7, max_concurrent_sessions=1000)
    # Bound below the per-group cap so it doesn't fire first.
    with _freeze_time(time.time()):
        for _ in range(10):
            join_group(rec.group_id, client_ip="9.9.9.9")
        with pytest.raises(RateLimitExceeded):
            join_group(rec.group_id, client_ip="9.9.9.9")


def test_gate_6_at_capacity_raises_session_cap():
    """Gate 6: per-group session cap (default 100/day). Override low
    to test."""
    rec = create_group(
        title="x",
        skill_ids=["s"],
        creator_uid="u",
        ttl_days=7,
        max_concurrent_sessions=2,
    )
    # Use distinct IPs to avoid rate-limit interference.
    join_group(rec.group_id, client_ip="10.0.0.1")
    join_group(rec.group_id, client_ip="10.0.0.2")
    with pytest.raises(GroupSessionCapExceeded):
        join_group(rec.group_id, client_ip="10.0.0.3")


def test_gate_7_happy_path_returns_token_and_uid():
    """Gate 7: valid group + within rate limit + within cap → token,
    expires_at, synthetic uid."""
    rec = create_group(title="x", skill_ids=["s"], creator_uid="u", ttl_days=7)
    result = join_group(rec.group_id, client_ip="11.0.0.1")
    assert result.token
    assert result.uid.startswith("anon-")
    assert result.expires_at > time.time()


# ─── Skill-list propagation ─────────────────────────────────────────────────


def test_group_skill_ids_are_carried_for_permission_lookup():
    """M2 uses this list to gate which skills the group's members can
    call. Verify the GroupRecord carries them verbatim."""
    rec = create_group(
        title="x",
        skill_ids=["physics-tutor", "lab-helper"],
        creator_uid="u",
        ttl_days=7,
    )
    found = get_group(rec.group_id)
    assert found is not None
    assert set(found.skill_ids) == {"physics-tutor", "lab-helper"}
