"""FastAPI routes for skill CRUD — /api/skills endpoints.

All routes are authenticated (`Depends(get_current_user)`) except
`GET /api/skills/marketplace`, which intentionally stays public so
unauthenticated callers can browse `accessControl.type == "public"` skills.

Non-owner reads of a skill the user cannot access return **404, not 403**,
to avoid leaking existence — see [auth-and-permissions.md](auth-and-permissions.md#api-route-protection).
Real 403s fire only on "can see but cannot modify" (PUT/DELETE).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import User, admin_roles, get_current_user
from db.chat_sessions import list_sessions_for_skill
from db.clients import resolve_enabled_skills
from db.models import SkillConfig
from protocols.sessions_route import ListSessionsResponse, _to_summary
from skills import skill_config
from skills.platform import PLATFORM_OWNER_UID
from skills.slugify import slugify, unique_slug
from skills.visibility import visible_configs

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Group tags that see the full skill set (incl. WIP / demo skills excluded from
# a tenant's enabled_skills) and may manage skills they don't own. `one-admin` =
# deploy admin, `aitana-admin` = platform. Sourced from the unified role model.
_SKILL_ADMIN_TAGS = admin_roles.SKILL_ADMIN_TAGS

# Platform admins (`aitana-admin`) may edit platform-owned skills in place;
# deploy admins (`one-admin`) and everyone else must fork. Platform skills are
# cross-tenant, so only the platform role can mutate the canonical copy.
_PLATFORM_ADMIN_TAGS = frozenset({admin_roles.PLATFORM_ADMIN_TAG})


def _is_platform_admin(access: Any) -> bool:
    """True when the caller holds the platform-admin group tag (`aitana-admin`)."""
    return admin_roles.is_platform_admin(access.group_tags)


# === Request / Response models ===


class CreateSkillRequest(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    instructions: str = ""
    display_name: str = Field(default="", alias="displayName")
    avatar: str = ""
    skill_metadata: dict = Field(default_factory=dict, alias="skillMetadata")
    access_control: dict = Field(default_factory=lambda: {"type": "private"}, alias="accessControl")
    protocols: dict | None = None
    initial_message: str = Field(default="", alias="initialMessage")
    tags: list[str] = []
    references: dict[str, str] = {}
    # v6.6.0 ONE-FORK-CONVERGENCE M3: Skill Studio persists persona + welcome as
    # part of the whole-draft Save. Pass-through dicts validated by SkillConfig.
    persona: dict | None = None
    welcome: dict | None = None

    model_config = {"populate_by_name": True}


class UpdateSkillRequest(BaseModel):
    slug: str | None = None
    description: str | None = None
    instructions: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    avatar: str | None = None
    skill_metadata: dict | None = Field(default=None, alias="skillMetadata")
    access_control: dict | None = Field(default=None, alias="accessControl")
    protocols: dict | None = None
    initial_message: str | None = Field(default=None, alias="initialMessage")
    tags: list[str] | None = None
    references: dict[str, str] | None = None
    # v6.6.0 ONE-FORK-CONVERGENCE M3: Skill Studio persists persona + welcome as
    # part of the whole-draft Save. Pass-through dicts validated by SkillConfig.
    persona: dict | None = None
    welcome: dict | None = None
    # v6.9.0 9.2: page-level shell shape (was silently dropped from the save body)
    # and the marketplace `featured` flag (platform-admin only — gated in the
    # handler; a non-admin sending it is rejected, not silently applied).
    shell: dict | None = None
    featured: bool | None = None

    model_config = {"populate_by_name": True}


class SkillResponse(BaseModel):
    """Serialized skill for API responses."""

    skill_id: str = Field(alias="skillId")
    name: str
    slug: str | None = None
    description: str
    display_name: str = Field(alias="displayName")
    avatar: str
    instructions: str
    skill_metadata: dict = Field(alias="skillMetadata")
    access_control: dict = Field(alias="accessControl")
    owner_id: str = Field(alias="ownerId")
    owner_email: str = Field(alias="ownerEmail")
    protocols: dict
    initial_message: str = Field(alias="initialMessage")
    tags: list[str]
    featured: bool
    usage_count: int = Field(alias="usageCount")
    created_at: float = Field(alias="createdAt")
    updated_at: float = Field(alias="updatedAt")
    # v6.4.0 4.5 SKILL-ONBOARDING: nullable additive. None for skills lacking
    # the welcome frontmatter block — frontend reads `welcome?.introMessage ??
    # initialMessage` so older skills still get a greeting if they set
    # initialMessage. See docs/design/v6.4.0/skill-onboarding.md.
    welcome: dict | None = None
    # v6.4.0 SHELL-MODES: nullable additive page-level shell shape. None for
    # skills lacking the shell frontmatter block — frontend ShellRouter falls
    # back to ChatShell. See docs/design/v6.4.0/skill-driven-shell-modes.md.
    shell: dict | None = None

    model_config = {"populate_by_name": True}

    @classmethod
    def from_config(cls, config: SkillConfig) -> SkillResponse:
        data = config.model_dump(by_alias=True)
        return cls.model_validate(data)


def _fill_welcome_buckets(resp: SkillResponse, user: User) -> SkillResponse:
    """Fill empty welcome-library buckets — VIEWER-INDEPENDENTLY when the skill
    declares whose library it is.

    A shared SKILL.md leaves ``example_documents`` / ``bucket_browser`` ``bucket``
    EMPTY so each env resolves it per-env (no hardcoded bucket, no customer
    project). Resolution order:
      1. ``welcome.documentsTenant`` set (e.g. ONE's ``acme-energy.example``) →
         that TENANT's per-env `documents_bucket` (the env llmops bucket aitana3
         indexes). This is the correct source for a shared library: it does NOT
         depend on who is viewing, so a platform admin sees ONE's PPAs just like a
         ONE user does. (The earlier per-viewer fill 404'd for non-ONE viewers,
         who fell back to the deployment-default bucket — 2026-07-23.)
      2. No ``documentsTenant`` → the VIEWER's own ``documents_bucket``
         (back-compat for skills whose library is the caller's own docs).
    Only empties are filled; a skill that pins an explicit bucket is untouched.
    Best-effort: a resolution failure leaves the value empty, never 500s the fetch."""
    w = resp.welcome
    if not w:
        return resp
    try:
        from db.clients import documents_bucket_for_domain, resolve_documents_bucket

        tenant = (w.get("documentsTenant") or "").strip()
        bucket = documents_bucket_for_domain(tenant) if tenant else None
        if not bucket:
            bucket = resolve_documents_bucket(user)
    except Exception:  # unmapped tenant / fail-closed — leave empty, don't 500 the fetch
        return resp
    if not bucket:
        return resp
    for ed in w.get("exampleDocuments") or []:
        if isinstance(ed, dict) and not ed.get("bucket"):
            ed["bucket"] = bucket
    bb = w.get("bucketBrowser")
    if isinstance(bb, dict) and not bb.get("bucket"):
        bb["bucket"] = bucket
    return resp


# === Routes ===


@router.post("", status_code=201, response_model=SkillResponse)
def create_skill(req: CreateSkillRequest, user: User = Depends(get_current_user)) -> Any:  # noqa: B008
    """Create a new skill. `ownerId` is always set from the JWT — never client-supplied.

    Slug behaviour: if `slug` is omitted we derive one from `name` and silently
    suffix on collision (`-2`, `-3`, ...). If the client supplies `slug` and it
    collides, we still suffix silently — POST is the "I just want a skill, give
    me whatever URL" path; explicit slug edits go through PUT, which surfaces
    409 with a suggestion instead.
    """
    base = req.slug if req.slug else slugify(req.name)
    chosen_slug = unique_slug(user.uid, base)

    # v6.9.0 9.2: an in-product create is durable — mark it so the platform
    # seeder never clobbers it on redeploy (managed_by="firestore" is preserved;
    # template-provenance skills track disk).
    kwargs: dict[str, Any] = {"slug": chosen_slug, "managedBy": "firestore"}
    if req.skill_metadata:
        kwargs["skillMetadata"] = req.skill_metadata
    if req.access_control:
        kwargs["accessControl"] = req.access_control
    if req.protocols:
        kwargs["protocols"] = req.protocols
    if req.references:
        kwargs["references"] = req.references
    if req.persona:
        kwargs["persona"] = req.persona
    if req.welcome:
        kwargs["welcome"] = req.welcome

    config = skill_config.create_skill(
        name=req.name,
        description=req.description,
        instructions=req.instructions,
        owner_id=user.uid,
        owner_email=user.email,
        displayName=req.display_name or req.name,
        avatar=req.avatar,
        initialMessage=req.initial_message,
        tags=req.tags,
        **kwargs,
    )
    return SkillResponse.from_config(config)


@router.get("", response_model=list[SkillResponse])
def list_skills(
    request: Request,
    owner_id: str | None = Query(None, alias="ownerId"),
    tag: str | None = None,
    access_type: str | None = Query(None, alias="accessType"),
    limit: int = Query(50, le=200),
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """List skills the caller can access.

    Implementation note: we currently fetch with the user-supplied filters
    (which may over- or under-return) and then drop anything the evaluator
    rejects. Correct, not optimally fast — a hot-path fan-out concern for
    1A.1b. Revisit with composite indexes once the list view becomes slow.
    """
    access = request.state.access
    configs = skill_config.list_skills(owner_id=owner_id, tag=tag, access_type=access_type, limit=limit)

    # Visibility is decided by three planes (accessControl, the tenant's
    # enabled_skills narrowing, and the skill-admin bypass of that narrowing).
    # v6.16.0: that combination now lives in ONE evaluator shared with
    # /api/admin/access/check, so the "what do my users actually see" screen
    # cannot drift from what this endpoint really returns. Do not re-inline the
    # filter here — see skills/visibility.py for why.
    enabled = resolve_enabled_skills(user)
    visible = visible_configs(configs, access, enabled)

    return [SkillResponse.from_config(c) for c in visible]


@router.get("/marketplace", response_model=list[SkillResponse])
def list_marketplace(limit: int = Query(50, le=200)) -> Any:
    """List public skills for the marketplace. **Intentionally unauthenticated.**"""
    configs = skill_config.list_marketplace(limit=limit)
    return [SkillResponse.from_config(c) for c in configs]


@router.get("/by-slug/{owner_id}/{slug}", response_model=SkillResponse)
def get_skill_by_slug(
    owner_id: str,
    slug: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Resolve `(owner_id, slug)` -> skill, with the same access check as GET /{id}.

    Returns 404 (not 403) when the skill is missing or invisible to the caller,
    matching the UUID GET to avoid leaking existence via slug guessing.
    """
    config = skill_config.find_by_slug(owner_id, slug)
    if config is None or not request.state.access.can_access_skill(config):
        raise HTTPException(status_code=404, detail="Skill not found")
    return _fill_welcome_buckets(SkillResponse.from_config(config), user)


@router.get("/{skill_id}", response_model=SkillResponse)
def get_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Get a skill by ID *or slug*. Returns 404 (not 403) if the user cannot
    access it.

    Alias-tolerant per CLAUDE.md #9 — ``useSkillMeta`` is handed whatever
    identifier the mounting surface holds, and the Skill Studio mounts its
    copilot by slug, so an id-only lookup 404'd the skill's own metadata.
    """
    config = skill_config.get_skill(skill_id) or skill_config.resolve_skill_ref(skill_id, getattr(user, "uid", None))
    if config is None or not request.state.access.can_access_skill(config):
        # Collapse "not found" and "not visible" into one response — don't leak existence.
        raise HTTPException(status_code=404, detail="Skill not found")
    return _fill_welcome_buckets(SkillResponse.from_config(config), user)


@router.put("/{skill_id}", response_model=SkillResponse)
def update_skill(
    skill_id: str,
    req: UpdateSkillRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Update a skill. Only the owner can modify; non-visible skills 404.

    Slug uniqueness is checked at the API layer: if the requested slug is
    already taken by another skill in the owner's namespace, returns 409
    with `{"error": "slug_taken", "suggestion": "<free-slug>"}`. Self-collision
    (saving the same slug back) is excluded.
    """
    updates = req.model_dump(by_alias=True, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    config = skill_config.get_skill(skill_id)
    if config is None or not request.state.access.can_access_skill(config):
        raise HTTPException(status_code=404, detail="Skill not found")
    # Platform-owned skills are read-only — EXCEPT for platform admins
    # (`aitana-admin`). Everyone else must fork. Note: platform skills are
    # seeded from admin/platform_seed.py, so a direct edit here can be
    # overwritten by a re-seed — the fork path is the durable one for tenants.
    if config.owner_id == PLATFORM_OWNER_UID:
        if not _is_platform_admin(request.state.access):
            raise HTTPException(
                status_code=403,
                detail="Platform-owned skills are read-only. Fork to customize.",
            )
    elif not request.state.access.is_skill_admin(config):
        # User can see it, just can't modify it → real 403.
        # Owner or a `one-admin` group-tag holder (Skill Studio admins) may edit.
        raise HTTPException(status_code=403, detail="Only the skill owner or an admin can update")

    # v6.9.0 9.2: `featured` is a marketplace-ranking lever — platform-admin only.
    # Reject rather than silently drop it (NEVER-SILENT), so a non-admin knows.
    if "featured" in updates and not _is_platform_admin(request.state.access):
        raise HTTPException(status_code=403, detail="Only a platform admin can set 'featured'.")

    if "slug" in updates:
        requested = updates["slug"]
        free = unique_slug(config.owner_id, requested, exclude_skill_id=skill_id)
        if free != requested:
            raise HTTPException(
                status_code=409,
                detail={"error": "slug_taken", "suggestion": free},
            )

    updated = skill_config.update_skill(skill_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillResponse.from_config(updated)


@router.delete("/{skill_id}", status_code=204)
def delete_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Delete a skill. Only the owner can delete; non-visible skills 404."""
    config = skill_config.get_skill(skill_id)
    if config is None or not request.state.access.can_access_skill(config):
        raise HTTPException(status_code=404, detail="Skill not found")
    if config.owner_id == PLATFORM_OWNER_UID:
        if not _is_platform_admin(request.state.access):
            raise HTTPException(
                status_code=403,
                detail="Platform-owned skills are read-only. Fork to customize.",
            )
    elif not request.state.access.is_skill_admin(config):
        raise HTTPException(status_code=403, detail="Only the skill owner or an admin can delete")

    deleted = skill_config.delete_skill(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found")


@router.post("/{skill_id}/fork", status_code=201, response_model=SkillResponse)
def fork_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> Any:
    """Fork a skill into a private copy owned by the caller.

    Works on any skill the caller can see — including platform-owned ones,
    which are otherwise read-only. 404 (not 403) if the source is invisible,
    so forking doesn't leak existence. The read-only guard doesn't apply:
    we're creating a new doc, not mutating the source.
    """
    source = skill_config.get_skill(skill_id)
    if source is None or not request.state.access.can_access_skill(source):
        raise HTTPException(status_code=404, detail="Skill not found")

    suffix = secrets.token_hex(3)[:6]
    new_skill = skill_config.create_skill(
        name=f"{source.name}-fork-{suffix}",
        description=source.description,
        instructions=source.instructions,
        owner_id=user.uid,
        owner_email=user.email,
        displayName=f"{source.display_name} (Fork)" if source.display_name else "",
        avatar=source.avatar,
        accessControl={"type": "private"},
        skillMetadata=source.skill_metadata.model_dump(by_alias=True),
        protocols=source.protocols.model_dump(by_alias=True),
        tags=list(source.tags),
        references=dict(source.references),
    )
    return SkillResponse.from_config(new_skill)


@router.get("/{skill_id}/sessions", response_model=ListSessionsResponse)
async def list_skill_sessions(
    skill_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),  # noqa: B008
) -> ListSessionsResponse:
    """List the caller's sessions for a skill, newest first.

    Returns only sessions owned by the authenticated caller — no cross-user
    session visibility. Returns 200 with an empty list when the caller has no
    sessions for this skill.
    """
    ctx = request.state.access
    # Resolve a slug to the canonical id first: the index stores canonical ids,
    # so an unresolved slug returned an EMPTY LIST rather than an error —
    # "you have no conversations" instead of "wrong identifier" (CLAUDE.md #9).
    resolved = skill_config.resolve_skill_ref(skill_id, ctx.uid)
    sessions, next_cursor = list_sessions_for_skill(
        skill_id=resolved.skill_id if resolved else skill_id,
        owner_uid=ctx.uid,
        page_size=page_size,
        cursor=cursor,
    )
    return ListSessionsResponse(
        sessions=[_to_summary(s, ctx.uid) for s in sessions],
        next_cursor=next_cursor,
    )
