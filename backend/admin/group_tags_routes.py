"""Group-tag registry + tag-holders reverse lookup (v6.9.0 / 9.3).

Two admin surfaces, both ``aitana-admin`` gated (deny-by-default) and audited:

  * ``GET  /api/admin/group-tags``          — list the registry.
  * ``PUT  /api/admin/group-tags/{tag_id}`` — upsert one registry entry.
  * ``GET  /api/admin/groups/{tag}/members`` — holders of a tag (reverse lookup).

The registry makes a group tag first-class (label / description / what-it-grants)
and is the vocabulary that per-user grant validation checks against
(``is_known_tag`` — imported by ``admin/users_routes.py``). Firestore collection
``group_tags``, doc id == the tag id.

The members lookup is an **O(users) scan** of ``firebase_admin.auth.list_users``
(Firebase has no server-side custom-claim query). It is capped and reports
``truncated`` rather than silently dropping holders (NEVER-SILENT).

See docs/design/v6.9.0/user-group-administration.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from admin.audit import record_admin_action
from admin.scope import PlatformScope, Scope
from auth.admin_roles import PLATFORM_ADMIN_TAG, TENANT_ADMIN_PREFIX
from db.firestore import get_document, query_documents, set_document
from db.models.group_tags import GroupTag

log = logging.getLogger(__name__)

COLLECTION = "group_tags"
_CLAIM = "groupTags"

# Cap the list_users() scan so a large user base can't turn a "who holds tag X"
# lookup into an unbounded request. If the scan hits the cap we report
# `truncated=true` (never silently drop holders).
_MEMBERS_SCAN_CAP = 2000

router = APIRouter(prefix="/api/admin/group-tags", tags=["admin-group-tags"])
members_router = APIRouter(prefix="/api/admin/groups", tags=["admin-group-tags"])


# ---------------------------------------------------------------------------
# Registry read helpers (shared with users_routes grant validation)
# ---------------------------------------------------------------------------


def registry_ids() -> set[str]:
    """Return the set of tag ids currently in the registry (best-effort)."""
    try:
        docs = query_documents(COLLECTION)
    except Exception as exc:  # Firestore blip must not hard-fail a grant path
        log.warning("group-tags: registry read failed (%s)", type(exc).__name__)
        return set()
    return {str(d.get("__id")) for d in docs if d.get("__id")}


def is_known_tag(tag: str) -> bool:
    """Whether *tag* may be granted to a user.

    True when any of:
      - it is a **structural** admin tag (``aitana-admin`` or a
        ``tenant-admin:{domain}`` shape) — these are code-defined, never
        registry-managed, and must always be grantable (don't lock admins out);
      - the registry is **empty** — bootstrap escape: before the registry is
        populated (there is no migration backfill), grants must keep working so
        onboarding isn't chicken-and-egg blocked;
      - the tag **is** a registry entry.
    """
    if tag == PLATFORM_ADMIN_TAG or tag.startswith(TENANT_ADMIN_PREFIX):
        return True
    ids = registry_ids()
    if not ids:
        log.info("group-tags: registry empty — allowing grant of %r (bootstrap)", tag)
        return True
    return tag in ids


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------


class GroupTagUpsert(BaseModel):
    """Editable fields of a registry entry. ``id`` comes from the path;
    ``created_by`` / ``created_at`` are stamped server-side."""

    label: str = ""
    description: str = ""
    grants: list[str] = []
    tenant_scope: str | None = None


@router.get("", response_model=list[GroupTag])
def list_group_tags(scope: PlatformScope) -> list[GroupTag]:
    """List the group-tag registry. Aitana-admin only."""
    out: list[GroupTag] = []
    for d in query_documents(COLLECTION):
        tag_id = d.pop("__id", "")
        d.pop("id", None)
        try:
            out.append(GroupTag(id=tag_id, **d))
        except Exception as exc:  # a malformed legacy doc must not 500 the list
            log.warning("group-tags: skipping malformed entry %r (%s)", tag_id, type(exc).__name__)
    log.info("admin.group_tags: list by uid=%s count=%d", scope.user.uid, len(out))
    return out


@router.put("/{tag_id}", response_model=GroupTag)
def upsert_group_tag(tag_id: str, body: GroupTagUpsert, scope: PlatformScope) -> GroupTag:
    """Create or update a registry entry (doc id == tag id). Aitana-admin only."""
    tag_id = tag_id.strip()
    if not tag_id:
        raise HTTPException(status_code=422, detail="tag id is required")

    existing = get_document(COLLECTION, tag_id)
    entry = GroupTag(
        id=tag_id,
        label=body.label,
        description=body.description,
        grants=body.grants,
        tenant_scope=body.tenant_scope,
        # Preserve original authorship/timestamp on update; stamp on first create.
        created_by=(existing or {}).get("created_by") or scope.user.uid,
        created_at=(existing or {}).get("created_at") or GroupTag(id=tag_id).created_at,
    )
    # Store without the id field (it's the doc id).
    data = entry.model_dump()
    data.pop("id", None)
    set_document(COLLECTION, tag_id, data)
    record_admin_action(
        actor_uid=scope.user.uid,
        actor_email=scope.user.email or "",
        action="upsert_group_tag",
        target=tag_id,
        before=existing,
        after=data,
    )
    log.info("admin.group_tags: upsert id=%s by uid=%s", tag_id, scope.user.uid)
    return entry


# ---------------------------------------------------------------------------
# Tag-holders reverse lookup
# ---------------------------------------------------------------------------


def _fb_auth():
    """Return firebase_admin.auth, initializing the default app if needed.

    Kept local (a ~12-line twin of admin.users_routes._fb_auth) to avoid a
    circular import: users_routes imports is_known_tag from this module.
    """
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


class TagMember(BaseModel):
    email: str
    uid: str


class TagMembers(BaseModel):
    tag: str
    members: list[TagMember]
    scanned: int
    truncated: bool
    note: str


@members_router.get("/{tag}/members", response_model=TagMembers)
def list_tag_members(tag: str, scope: Scope) -> TagMembers:
    """List the direct-claim holders of *tag*, **within the caller's scope**.

    Firebase has no server-side claim query, so this iterates every user
    (``list_users``) and filters by the ``groupTags`` custom claim. It is
    therefore **O(users)** and capped at ``_MEMBERS_SCAN_CAP`` — a truncated
    scan is reported, never silently dropped (NEVER-SILENT). Domain-derived
    holders (``clients/{domain}.derived_group_tags``) are NOT enumerated here;
    use effective-access (``/api/admin/access/check``) for a single user's
    domain-derived tags.

    v6.16.0: this endpoint enumerates *people*, so an unscoped version hands a
    tenant admin a directory of every other tenant's users. Holders outside the
    caller's scope are filtered out. ``scanned`` still reports the true scan
    size — under-reporting it would make the truncation warning a lie.
    """
    fb = _fb_auth()
    members: list[TagMember] = []
    scanned = 0
    truncated = False
    try:
        for rec in fb.list_users().iterate_all():
            scanned += 1
            if scanned > _MEMBERS_SCAN_CAP:
                truncated = True
                break
            claims = getattr(rec, "custom_claims", None) or {}
            raw = claims.get(_CLAIM) or []
            tags = raw if isinstance(raw, (list, tuple, set)) else []
            if tag in tags:
                member_email = getattr(rec, "email", "") or ""
                member_domain = member_email.rsplit("@", 1)[-1].lower() if "@" in member_email else ""
                if not scope.may(member_domain):
                    continue
                members.append(TagMember(email=member_email, uid=rec.uid))
    except HTTPException:
        raise
    except Exception as exc:  # surface, don't swallow (NEVER-SILENT)
        log.error("admin.group_tags: member scan failed for tag=%s (%s)", tag, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to enumerate users") from exc

    note = f"O(users) scan of {scanned} account(s); direct-claim holders only (domain-derived tags excluded)."
    if truncated:
        note += f" TRUNCATED at the {_MEMBERS_SCAN_CAP}-user cap — result is partial."
    log.info(
        "admin.group_tags: members tag=%s holders=%d scanned=%d truncated=%s by uid=%s",
        tag,
        len(members),
        scanned,
        truncated,
        scope.user.uid,
    )
    return TagMembers(tag=tag, members=members, scanned=scanned, truncated=truncated, note=note)


__all__ = ["COLLECTION", "is_known_tag", "members_router", "registry_ids", "router"]
