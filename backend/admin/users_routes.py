"""GET/POST/DELETE /api/admin/users/{email}/groups — per-user group-tag admin.

Grants/revokes group tags by writing the Firebase ``groupTags`` custom claim —
the same claim ``auth/firebase_auth.py`` reads into ``User.group_tags``.
Aitana-admin gated + audited (actor + action + target logged to Cloud Logging).

Domain-wide tags are managed separately in the tenant editor
(``clients/{domain}.derived_group_tags``); this endpoint is for per-user grants
that don't apply to a whole email domain.

A grant is validated against the group-tag registry (``group_tags`` collection):
an unknown tag is rejected with **422** rather than silently minting a useless
claim (NEVER-SILENT). A structural admin tag and — during bootstrap — any tag is
still grantable; see ``group_tags_routes.is_known_tag``.

Claim propagation: a grant/revoke only takes effect on the user's next token
refresh (~1h). The response carries a ``propagation`` block so the operator sees
that, and ``POST .../refresh-claims`` (``revoke_refresh_tokens``) forces it.

See docs/design/v6.9.0/user-group-administration.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.audit import record_admin_action
from admin.group_tags_routes import is_known_tag
from admin.scope import Scope, domain_of_key
from auth.admin_roles import is_admin_conferring_tag

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

_CLAIM = "groupTags"

# Firebase ID tokens live ~1h; a claim change is only reflected after the
# client refreshes its token (or an admin force-refreshes — see refresh-claims).
_TOKEN_TTL_SECONDS = 3600


def _fb_auth():
    """Return firebase_admin.auth, initializing the default app if needed."""
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
    except ImportError as exc:  # pragma: no cover - deployed only
        raise HTTPException(status_code=503, detail="Firebase Admin unavailable") from exc
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    return fb_auth


class Propagation(BaseModel):
    """When a claim mutation actually takes effect.

    ``effective`` is ``"on_next_refresh"`` after a plain grant/revoke (the client
    keeps its current token until it refreshes, ≤ ``token_ttl_seconds``) or
    ``"forced"`` after ``refresh-claims`` revokes the refresh tokens.
    """

    effective: str
    token_ttl_seconds: int = Field(default=_TOKEN_TTL_SECONDS, alias="tokenTtlSeconds")

    model_config = {"populate_by_name": True}


class UserGroups(BaseModel):
    email: str
    uid: str = ""
    group_tags: list[str] = []
    # Present on grant/revoke responses (None on a plain GET) so the UI/CLI can
    # show the operator when the change lands (NEVER-SILENT).
    propagation: Propagation | None = None


class GrantRequest(BaseModel):
    tag: str


class RefreshResult(BaseModel):
    email: str
    uid: str
    propagation: Propagation


def _lookup(fb, email: str):
    """(record, sorted-tags) for an email, or 404 if no such user."""
    try:
        rec = fb.get_user_by_email(email)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"No user for {email!r}") from exc
    claims = rec.custom_claims or {}
    raw = claims.get(_CLAIM) or []
    tags = sorted({str(t) for t in raw}) if isinstance(raw, (list, tuple, set)) else []
    return rec, tags


def _write(fb, rec, tags: list[str]) -> None:
    claims = dict(rec.custom_claims or {})
    claims[_CLAIM] = tags
    fb.set_custom_user_claims(rec.uid, claims)


def _on_next_refresh() -> Propagation:
    return Propagation(effective="on_next_refresh", token_ttl_seconds=_TOKEN_TTL_SECONDS)


def _target_domain(email: str) -> str:
    """The email domain of the user being administered (blank if malformed).

    Shared with the other admin surfaces via :func:`admin.scope.domain_of_key`.
    """
    return domain_of_key(email)


def _assert_may_administer(scope: Scope, email: str) -> None:
    """The target user must live in a domain the caller administers."""
    scope.assert_may(_target_domain(email))


def _assert_may_grant(scope: Scope, tag: str) -> None:
    """Block privilege escalation via tag grants.

    Only a platform admin may grant a tag that confers admin authority. Without
    this, a tenant admin could grant themselves ``aitana-admin`` (or
    ``tenant-admin:someone-else.com``) and the tenant boundary would be
    decorative — every other check in this sprint routes through group tags.
    """
    if is_admin_conferring_tag(tag) and not scope.is_platform:
        raise HTTPException(
            status_code=403,
            detail="Only a platform admin may grant an admin-conferring tag",
        )


@router.get("/{email}", response_model=UserGroups)
def get_user_groups(email: str, scope: Scope) -> UserGroups:
    """Look up a user's per-user group tags, within the caller's scope."""
    _assert_may_administer(scope, email)
    fb = _fb_auth()
    rec, tags = _lookup(fb, email)
    return UserGroups(email=email, uid=rec.uid, group_tags=tags)


@router.post("/{email}/groups", response_model=UserGroups)
def grant_group(email: str, body: GrantRequest, scope: Scope) -> UserGroups:
    """Grant a group tag to a user (idempotent). Aitana-admin only.

    The tag is validated against the registry — an unknown tag is rejected
    with 422 (NEVER-SILENT) rather than writing a claim no skill/tool honours.
    """
    tag = body.tag.strip()
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")
    _assert_may_administer(scope, email)
    _assert_may_grant(scope, tag)
    if not is_known_tag(tag):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown group tag {tag!r}. Register it first (PUT /api/admin/group-tags/{{tag}}) or fix the spelling."
            ),
        )
    fb = _fb_auth()
    rec, tags = _lookup(fb, email)
    new = sorted(set(tags) | {tag})
    _write(fb, rec, new)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="grant_group_tag",
        target=email,
        before={"group_tags": tags},
        after={"group_tags": new},
    )
    log.info("admin.users: grant tag=%s to email=%s by uid=%s", tag, email, scope.user.uid)
    return UserGroups(email=email, uid=rec.uid, group_tags=new, propagation=_on_next_refresh())


@router.delete("/{email}/groups/{tag}", response_model=UserGroups)
def revoke_group(email: str, tag: str, scope: Scope) -> UserGroups:
    """Revoke a group tag from a user (idempotent), within the caller's scope."""
    _assert_may_administer(scope, email)
    _assert_may_grant(scope, tag)
    fb = _fb_auth()
    rec, tags = _lookup(fb, email)
    new = sorted(set(tags) - {tag})
    _write(fb, rec, new)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="revoke_group_tag",
        target=email,
        before={"group_tags": tags},
        after={"group_tags": new},
    )
    log.info("admin.users: revoke tag=%s from email=%s by uid=%s", tag, email, scope.user.uid)
    return UserGroups(email=email, uid=rec.uid, group_tags=new, propagation=_on_next_refresh())


@router.post("/{email}/refresh-claims", response_model=RefreshResult)
def refresh_claims(email: str, scope: Scope) -> RefreshResult:
    """Force claim propagation by revoking the user's refresh tokens.

    Without this, a grant/revoke waits up to the token TTL (~1h) to take effect.
    ``revoke_refresh_tokens`` invalidates outstanding refresh tokens so the
    client must re-authenticate and pick up the new claim. Aitana-admin only,
    audited.
    """
    _assert_may_administer(scope, email)
    fb = _fb_auth()
    rec, _tags = _lookup(fb, email)
    try:
        fb.revoke_refresh_tokens(rec.uid)
    except HTTPException:
        raise
    except Exception as exc:  # surface, don't swallow (NEVER-SILENT)
        log.error("admin.users: revoke_refresh_tokens failed for %s (%s)", email, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to revoke refresh tokens") from exc
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="refresh_claims",
        target=email,
    )
    log.info("admin.users: force refresh-claims email=%s by uid=%s", email, scope.user.uid)
    return RefreshResult(
        email=email,
        uid=rec.uid,
        propagation=Propagation(effective="forced", token_ttl_seconds=_TOKEN_TTL_SECONDS),
    )


__all__ = ["router"]
