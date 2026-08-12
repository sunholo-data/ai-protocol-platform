"""Anonymous group-ID auth — the fourth auth mode.

Sprint 2.11 (v6.2.0). Short-code session join, no persistent accounts,
no PII. A teacher (or admin) signed in via Firebase calls
``create_group(...)`` to mint a short alphanumeric code; anyone who
knows the code calls ``join_group(code, client_ip)`` to get a signed
HS256 JWT. The JWT body carries a synthetic ``sub`` (uid),
``group_id``, ``exp``, ``iat``, ``auth_mode="anonymous_group_id"``.

The rest of the platform accepts this token via
``auth.__init__.get_current_user``'s shape-dispatcher (M2), producing
a ``User`` with ``email="" / domain="" / auth_mode + group_id set``.
That ``User`` flows into the existing permission system, which falls
back to `group/<group_id>` permission lookups for anonymous-group
users (also M2).

Threat model + axiom alignment (SECURE_BY_CONSTRUCTION = -2):
docs/design/v6.2.0/anonymous-group-id-auth.md §"Security Considerations".

This module ships seven gates on ``join_group``; each is exercised by
a ``test_gate_N_<name>`` case in ``tests/unit/test_group_id_auth.py``.

Storage: an in-memory ``dict[group_id, GroupRecord]`` cache in front of a
Firestore ``anon_groups`` collection (v6.19.0, AIPLA #16). The cache is a
latency optimisation, not the source of truth — ``get_group`` falls back to
Firestore on a miss and rehydrates, so a group survives the container being
recycled.

Why it matters: without the fallback, group codes live only in one Cloud Run
instance's memory. Scale to zero (or scale OUT to a second instance) and the
code stops working, which forces every deployment into ``min-instances=1`` and
defeats the point of running serverless. The reporting fork hit exactly that
and pinned an instance to work around it.

``db.firestore`` already swaps in ``InMemoryFirestoreClient`` under LOCAL_MODE,
so the persistence layer is a no-op round trip locally — no branching here.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass, field

import jwt

from auth.firebase_auth import User
from auth.group_rate_limit import TokenBucketRateLimiter

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────

GROUP_AUTH_SIGNING_SECRET_ENV = "GROUP_AUTH_SIGNING_SECRET"
AUTH_MODE = "anonymous_group_id"
JWT_ALGORITHM = "HS256"
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_CONCURRENT_SESSIONS = 100
DEFAULT_TOKEN_LIFETIME_SECONDS = 8 * 3600  # 8 hours
# Alphabet excludes ambiguous chars (0/O/1/I) per design.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN_BEFORE_HYPHEN = 4
_CODE_LEN_AFTER_HYPHEN = 4
_UID_SUFFIX_BYTES = 16  # 128 bits — collision-proof


# ─── Exceptions ─────────────────────────────────────────────────────────────


class GroupNotFound(Exception):
    """Unknown group_id at join time. Distinct from GroupRevoked for telemetry."""


class GroupRevoked(Exception):
    """Group was explicitly deleted by its creator. All tokens invalidated."""


class GroupExpired(Exception):
    """Group's TTL has elapsed."""


class GroupSessionCapExceeded(Exception):
    """Per-group concurrent-session cap reached for the current day."""


class InvalidGroupToken(Exception):
    """Token failed verification (signature, expiry, missing claims, etc.)."""


class GroupPersistenceError(Exception):
    """A group could not be durably stored, so its code would not survive a restart.

    Raised only on CREATE. Returning a code we cannot persist is a silent
    promise-break: it works in the demo and dies at the next deploy.
    """


# ─── Data types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GroupRecord:
    """A group as persisted (in-memory for v1). Created by ``create_group``."""

    group_id: str
    creator_uid: str
    title: str
    skill_ids: tuple[str, ...]
    created_at: float
    expires_at: float
    max_concurrent_sessions: int


@dataclass(frozen=True)
class JoinResult:
    """Return shape of ``join_group``. Wire format mirrors design §API."""

    token: str
    uid: str
    expires_at: float


# ─── State holder (module-level singleton) ──────────────────────────────────


@dataclass
class AnonymousGroupAuth:
    """Module-level state container. Single instance per process.

    Exposed as a class (rather than module-level globals) so tests can
    reset it cleanly via ``reset_for_tests()``.

    Clock injection is done at the MODULE level (``_state.time_provider``
    sets the module callable) rather than as a dataclass field — a
    dataclass field would be assigned at instance-init time and shadow
    later overrides. See ``time_provider`` property below.
    """

    groups: dict[str, GroupRecord] = field(default_factory=dict)
    """Active groups indexed by group_id."""

    revoked_group_ids: set[str] = field(default_factory=set)
    """Group ids that have been explicitly deleted. Distinct from
    'never existed' so error messages can be specific."""

    sessions_today: dict[tuple[str, str], int] = field(default_factory=dict)
    """Per-group session counter, keyed by (group_id, YYYY-MM-DD)."""

    rate_limiter: TokenBucketRateLimiter = field(default_factory=TokenBucketRateLimiter)

    # NOTE: ``time_provider`` lives on the CLASS (not as a dataclass
    # field) so tests can override it for the whole module by writing
    # ``AnonymousGroupAuth.time_provider = staticmethod(lambda: t)``
    # — the override sticks because instance lookup falls through to
    # the class attribute.

    @classmethod
    def reset_for_tests(cls) -> None:
        """Drop every group, revoked id, session count, and bucket.

        Called by the ``isolate_state`` autouse fixture in
        ``test_group_id_auth.py`` so each test starts clean.
        """
        _state.groups.clear()
        _state.revoked_group_ids.clear()
        _state.sessions_today.clear()
        _state.rate_limiter.reset_all()

    @classmethod
    def user_from_token(cls, token: str) -> User:
        """Build a User from a verified group token.

        ``email`` and ``domain`` are empty strings (no PII). The
        ``auth_mode`` field signals downstream code to use group-level
        permission lookups.
        """
        claims = verify_group_token(token)
        return User(
            uid=claims["sub"],
            email="",
            domain="",
            group_tags=frozenset(),
            auth_mode=AUTH_MODE,
            group_id=claims["group_id"],
        )


# Module-level clock injection point. Mutable by tests via
# ``AnonymousGroupAuth.time_provider = staticmethod(lambda: t)``.
# Living on the CLASS (not as a dataclass field) means tests can
# reassign it and instance lookups (via _state.time_provider) fall
# through to the class attr.
AnonymousGroupAuth.time_provider = staticmethod(time.time)

_state = AnonymousGroupAuth()


# ─── Internal helpers ───────────────────────────────────────────────────────


def _signing_secret() -> str:
    """Read the signing secret from env. Fail loud if missing or empty.

    Module IMPORT must succeed (so tests + tooling don't blow up
    before reading the env). The secret is required at the first
    create/join/verify call.
    """
    secret = os.environ.get(GROUP_AUTH_SIGNING_SECRET_ENV, "")
    if not secret:
        raise RuntimeError(
            f"{GROUP_AUTH_SIGNING_SECRET_ENV} env var is required for "
            f"anonymous group-ID auth. Set it to a long random string "
            f"(rotate to invalidate all live tokens)."
        )
    return secret


def _generate_code() -> str:
    """Mint a short code with the unambiguous alphabet.

    Shape: `XXXX-XXXX` (4 + hyphen + 4). At 32 chars of alphabet,
    that's ~3.4 x 10^11 codes. Combined with the 10/min/IP rate limit
    it's not enumerable.
    """
    parts = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN_BEFORE_HYPHEN)),
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN_AFTER_HYPHEN)),
    ]
    return "-".join(parts)


def _today_iso() -> str:
    """YYYY-MM-DD in UTC for session-cap bucketing."""
    return time.strftime("%Y-%m-%d", time.gmtime(AnonymousGroupAuth.time_provider()))


def _synthesize_uid(group_id: str) -> str:
    """Per-join synthetic uid. Shape: `anon-<group_id>-<random_hex>`."""
    suffix = secrets.token_hex(_UID_SUFFIX_BYTES)
    # Include group_id (hyphens stripped) so the uid is intuitively
    # tied to its group; the random suffix guarantees uniqueness.
    cleaned = group_id.replace("-", "")
    return f"anon-{cleaned}-{suffix}"


def _check_group_active(record: GroupRecord) -> None:
    """Common gate logic: revoked? expired? Raise typed exception."""
    if record.group_id in _state.revoked_group_ids:
        raise GroupRevoked(f"group {record.group_id} has been revoked")
    if AnonymousGroupAuth.time_provider() >= record.expires_at:
        raise GroupExpired(f"group {record.group_id} expired")


# ─── Public API: lifecycle ──────────────────────────────────────────────────


# ─── Firestore persistence (v6.19.0, AIPLA #16) ─────────────────────────────
#
# `db.firestore` resolves to InMemoryFirestoreClient under LOCAL_MODE, so these
# helpers need no local-vs-cloud branching.

_GROUPS_COLLECTION = "anon_groups"


def _record_to_doc(record: GroupRecord) -> dict:
    return {
        "group_id": record.group_id,
        "creator_uid": record.creator_uid,
        "title": record.title,
        "skill_ids": list(record.skill_ids),
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "max_concurrent_sessions": record.max_concurrent_sessions,
    }


def _doc_to_record(doc: dict) -> GroupRecord:
    return GroupRecord(
        group_id=doc["group_id"],
        creator_uid=doc["creator_uid"],
        title=doc["title"],
        skill_ids=tuple(doc.get("skill_ids") or ()),
        created_at=doc["created_at"],
        expires_at=doc["expires_at"],
        max_concurrent_sessions=doc["max_concurrent_sessions"],
    )


def _persist_group(record: GroupRecord, *, required: bool) -> None:
    """Write a group to Firestore.

    ``merge=True`` so a re-persist only touches this record's own fields and
    leaves any externally-managed keys on the doc intact. (The reporting fork
    learned this the hard way: a full overwrite silently dropped a field another
    script owned.)

    ``required`` is the deliberate deviation from the fork's implementation,
    which is best-effort everywhere. On CREATE, a failed write means we would
    hand back a code that works right now and dies at the next restart — a
    promise we cannot keep, and the user has no way to know. That is precisely
    the silent failure this codebase forbids, so create raises. Refresh/revoke
    stay best-effort: the in-memory state is already correct there, and a
    Firestore blip should not fail a request that otherwise succeeded.
    """
    try:
        from db import firestore as fs

        fs.set_document(_GROUPS_COLLECTION, record.group_id, _record_to_doc(record), merge=True)
    except Exception as exc:
        logger.exception("group_auth: failed to persist group=%s", record.group_id)
        if required:
            raise GroupPersistenceError(
                f"could not persist group {record.group_id}: the code would stop working "
                "when this instance restarts. Refusing to return a code that will not last."
            ) from exc


def _load_group_from_firestore(group_id: str) -> GroupRecord | None:
    """Read a group from Firestore. Returns None for missing, revoked, or error.

    Read failures degrade to None rather than raising: the caller treats that as
    "no such group", which is the safe direction for an auth lookup.
    """
    try:
        from db import firestore as fs

        doc = fs.get_document(_GROUPS_COLLECTION, group_id)
        if not doc or doc.get("revoked"):
            return None
        return _doc_to_record(doc)
    except Exception:
        logger.exception("group_auth: failed to load group=%s from firestore", group_id)
        return None


def _mark_revoked_in_firestore(group_id: str) -> None:
    """Flag a persisted group as revoked. Best-effort — in-memory state already is."""
    try:
        from db import firestore as fs

        fs.update_document(_GROUPS_COLLECTION, group_id, {"revoked": True})
    except Exception:
        logger.exception("group_auth: failed to mark revoked group=%s", group_id)


def create_group(
    *,
    title: str,
    skill_ids: list[str] | tuple[str, ...],
    creator_uid: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
    max_concurrent_sessions: int = DEFAULT_MAX_CONCURRENT_SESSIONS,
) -> GroupRecord:
    """Mint a new group code. Called by the teacher-facing endpoint (M2).

    Args:
        title: Free-form, for display + audit. Not on the JWT.
        skill_ids: Which skills the group's members can access. The
            permission system (M2) reads this list when deciding
            ``can_use_tool`` for anonymous-group users.
        creator_uid: The teacher's Firebase uid. Required for the
            revoke gate ("only the creator can delete").
        ttl_days: Days until the group expires. Default 30; AIPLA
            typically sets shorter (per-class).
        max_concurrent_sessions: Per-group cap (default 100/day).
    """
    # Verify the signing secret early — fail loud BEFORE we mint state.
    _signing_secret()

    now = AnonymousGroupAuth.time_provider()
    code = _generate_code()
    # Defensive: regenerate if collision (vanishingly rare; loop bounded).
    while code in _state.groups or code in _state.revoked_group_ids:
        code = _generate_code()

    record = GroupRecord(
        group_id=code,
        creator_uid=creator_uid,
        title=title,
        skill_ids=tuple(skill_ids),
        created_at=now,
        expires_at=now + ttl_days * 86400,
        max_concurrent_sessions=max_concurrent_sessions,
    )
    # Persist BEFORE caching: if the durable write fails we must not hand back
    # a code, and we must not leave a phantom in this instance's memory either.
    _persist_group(record, required=True)
    _state.groups[code] = record
    logger.info(
        "group_auth: created group=%s creator=%s ttl_days=%d skills=%d cap=%d",
        code,
        creator_uid,
        ttl_days,
        len(skill_ids),
        max_concurrent_sessions,
    )
    return record


def get_group(group_id: str) -> GroupRecord | None:
    """Lookup; returns None for missing or revoked.

    Cache hit -> Firestore fallback -> rehydrate. The fallback is what lets a
    code survive this instance being recycled or a request landing on a
    different instance (AIPLA #16).
    """
    if group_id in _state.revoked_group_ids:
        return None
    cached = _state.groups.get(group_id)
    if cached is not None:
        return cached

    loaded = _load_group_from_firestore(group_id)
    if loaded is not None:
        _state.groups[group_id] = loaded  # rehydrate so the next hit is local
    return loaded


def delete_group(group_id: str, requesting_uid: str) -> None:
    """Revoke a group. Only the creator may delete.

    Sets the group_id in ``revoked_group_ids`` (not just deletes from
    ``groups``) so ``verify_group_token`` can distinguish revoked from
    never-existed for telemetry.

    Raises:
        PermissionError: caller is not the creator.
        GroupNotFound: group never existed (or was already gone).
    """
    # Via get_group (not the raw cache) so a creator can revoke a code minted
    # by an instance that has since been recycled — otherwise revocation
    # silently 404s on exactly the groups most in need of it.
    record = get_group(group_id)
    if record is None:
        # Revoking a non-existent group is a no-op for idempotency,
        # but we raise so the route can return a clean 404.
        raise GroupNotFound(f"group {group_id} not found")
    if record.creator_uid != requesting_uid:
        logger.warning(
            "group_auth: refused revoke uid=%s creator=%s group=%s",
            requesting_uid,
            record.creator_uid,
            group_id,
        )
        raise PermissionError(f"only the group's creator ({record.creator_uid}) may revoke it")
    _state.groups.pop(group_id, None)
    _state.revoked_group_ids.add(group_id)
    _mark_revoked_in_firestore(group_id)
    logger.info("group_auth: revoked group=%s by uid=%s", group_id, requesting_uid)


# ─── Public API: join ───────────────────────────────────────────────────────


def join_group(group_id: str, *, client_ip: str) -> JoinResult:
    """Mint a token for a caller holding a valid group code.

    Seven gates in order — see design doc §"Implementation Plan / Phase 1".

    Args:
        group_id: The short code the caller is presenting.
        client_ip: For per-IP rate limiting (gate #5). Caller (the
            route) is responsible for passing the verified peer IP.

    Returns:
        JoinResult with the signed token + synthetic uid + expires_at.

    Raises:
        - TypeError / ValueError: gate #1 (malformed args)
        - GroupNotFound: gate #2
        - GroupExpired: gate #3
        - GroupRevoked: gate #4
        - RateLimitExceeded: gate #5
        - GroupSessionCapExceeded: gate #6
    """
    # Gate 1: type validation at function boundary (route layer adds
    # Pydantic 422 for body shape).
    if not isinstance(group_id, str) or not group_id:
        raise ValueError("group_id must be a non-empty string")
    if not isinstance(client_ip, str) or not client_ip:
        raise ValueError("client_ip must be a non-empty string")

    # Gate 5 (rate limit) is FIRST so brute-force attempts don't even
    # get to learn whether the group exists.
    _state.rate_limiter.check(client_ip)

    # Gates 2 + 4: lookup, revocation.
    if group_id in _state.revoked_group_ids:
        raise GroupRevoked(f"group {group_id} has been revoked")
    record = _state.groups.get(group_id)
    if record is None:
        raise GroupNotFound(f"group {group_id} not found")

    # Gate 3: expiry.
    _check_group_active(record)

    # Gate 6: per-group session cap (per-day).
    cap_key = (group_id, _today_iso())
    current = _state.sessions_today.get(cap_key, 0)
    if current >= record.max_concurrent_sessions:
        raise GroupSessionCapExceeded(f"group {group_id} reached daily session cap ({record.max_concurrent_sessions})")

    # Gate 7: happy path — mint token.
    now = AnonymousGroupAuth.time_provider()
    uid = _synthesize_uid(group_id)
    exp = now + DEFAULT_TOKEN_LIFETIME_SECONDS
    claims = {
        "sub": uid,
        "group_id": group_id,
        "exp": exp,
        "iat": now,
        "auth_mode": AUTH_MODE,
    }
    token = jwt.encode(claims, _signing_secret(), algorithm=JWT_ALGORITHM)
    _state.sessions_today[cap_key] = current + 1
    logger.info(
        "group_auth: joined group=%s uid=%s session_n=%d",
        group_id,
        uid,
        current + 1,
    )
    return JoinResult(token=token, uid=uid, expires_at=exp)


# ─── Public API: verify ─────────────────────────────────────────────────────


def verify_group_token(token: str) -> dict:
    """Verify a JWT minted by ``join_group``.

    Returns the decoded claims dict on success. Raises ``InvalidGroupToken``
    for ANY failure (bad signature, wrong algorithm, expired, missing
    required claims). ``GroupRevoked`` is raised separately so callers
    can distinguish "expired" from "actively-revoked-by-creator".
    """
    try:
        claims = jwt.decode(
            token,
            _signing_secret(),
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidGroupToken("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidGroupToken(f"token invalid: {exc}") from exc

    # Claim shape check.
    required = {"sub", "group_id", "exp", "iat", "auth_mode"}
    if set(claims.keys()) != required:
        raise InvalidGroupToken(f"token claims must be {sorted(required)}; got {sorted(claims.keys())}")
    if claims["auth_mode"] != AUTH_MODE:
        raise InvalidGroupToken(f"token auth_mode is {claims['auth_mode']!r}, expected {AUTH_MODE!r}")

    # Revocation check (cross-reference state). Distinct exception
    # type so the route layer can pick a different status code if it
    # wants (we return 401 in both cases by design, but telemetry
    # benefits from the distinction).
    if claims["group_id"] in _state.revoked_group_ids:
        raise GroupRevoked(f"group {claims['group_id']} revoked")

    return claims


__all__ = [
    "AUTH_MODE",
    "GROUP_AUTH_SIGNING_SECRET_ENV",
    "AnonymousGroupAuth",
    "GroupExpired",
    "GroupNotFound",
    "GroupRecord",
    "GroupRevoked",
    "GroupSessionCapExceeded",
    "InvalidGroupToken",
    "JoinResult",
    "create_group",
    "delete_group",
    "get_group",
    "join_group",
    "verify_group_token",
]
