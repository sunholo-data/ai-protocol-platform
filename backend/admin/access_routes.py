"""Effective-access dry-run (v6.9.0 / 9.3).

``POST /api/admin/access/check`` answers "what does this user actually see?" —
the union of the three planes that decide access, each annotated with *why*:

  1. **direct**         — tags in the user's signed JWT ``groupTags`` claim
                          (read from the Firebase custom claims).
  2. **domain-derived** — tags the tenant grants to the whole email domain
                          (``clients/{domain}.derived_group_tags``), unioned into
                          the claim at request time by ``firebase_auth``.
  3. **tool-perm**      — the ``tool_permissions`` decision for a named tool,
                          computed by **replicating** ``permissions.can_use_tool``'s
                          exact user → domain → wildcard lookup order (using the
                          same ``_doc_allows`` evaluator) so the dry-run cannot
                          diverge from enforcement.

Optionally resolves a skill's 5-type access decision for the same user.

Aitana-admin gated (deny-by-default). Read-only — no mutation, so no audit row
(the audit trail is for changes; this is an inspection). No content egress: it
returns access-decision metadata only, inside the GCP edge.

See docs/design/v6.9.0/user-group-administration.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from admin.scope import Scope
from auth import User, build_access_context
from auth import permissions as perms
from db import firestore as fs
from db.clients import resolve_derived_group_tags

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/access", tags=["admin-access"])

_CLAIM = "groupTags"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AccessCheckRequest(BaseModel):
    """Accepts friendly aliases (``skillId`` / ``toolName``) or snake_case."""

    email: str
    skill_id: str | None = Field(default=None, alias="skillId")
    tool_name: str | None = Field(default=None, alias="toolName")
    # v6.16.0 Phase 2: include the full per-skill visibility plane. Opt-in —
    # it lists every skill, which single-skill callers don't need.
    include_skills: bool = Field(default=False, alias="includeSkills")

    model_config = {"populate_by_name": True}


class ProvenancedTag(BaseModel):
    tag: str
    # subset of {"direct", "domain-derived"} — a tag can be granted by both.
    provenances: list[str]


class ToolPermissionCheck(BaseModel):
    tool: str
    allowed: bool
    provenance: str = "tool-perm"
    reason: str


class SkillAccessCheck(BaseModel):
    skill: str
    found: bool
    allowed: bool
    reason: str


class SkillVisibilityRow(BaseModel):
    """One skill, as the TARGET user would experience it.

    Splits the verdict into its planes so the admin can see *which* one hid a
    skill — "denied by access control" and "not in your tenant's enabled_skills"
    call for completely different fixes.
    """

    skill_id: str = Field(alias="skillId")
    slug: str = ""
    label: str = ""
    visible: bool
    reason: str
    access_allowed: bool = Field(alias="accessAllowed")
    tenant_allowed: bool = Field(alias="tenantAllowed")
    # True when this viewer's skill-admin tag skipped the tenant narrowing —
    # i.e. the admin sees it but ordinary users of the tenant would not.
    admin_bypass: bool = Field(default=False, alias="adminBypass")

    model_config = {"populate_by_name": True}


class SkillVisibilityPlane(BaseModel):
    """What the target user actually sees in their skill list, and why."""

    # None when the tenant has no narrowing configured (all skills allowed).
    enabled_skills: list[str] | None = Field(default=None, alias="enabledSkills")
    visible_count: int = Field(default=0, alias="visibleCount")
    total_count: int = Field(default=0, alias="totalCount")
    # Skills this user loses ONLY because of the tenant narrowing — the answer
    # to "why does my admin list show more than my users get?".
    hidden_by_tenant_filter: list[str] = Field(default_factory=list, alias="hiddenByTenantFilter")
    skills: list[SkillVisibilityRow] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AccessCheckResponse(BaseModel):
    email: str
    uid: str
    domain: str
    user_found: bool
    tags: list[ProvenancedTag]
    tool_permission: ToolPermissionCheck | None = None
    skill_access: SkillAccessCheck | None = None
    # v6.16.0 Phase 2: the full "what your users actually see" plane.
    skill_visibility: SkillVisibilityPlane | None = Field(default=None, alias="skillVisibility")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fb_auth():
    """firebase_admin.auth, initializing the default app if needed (local twin
    of the same helper in users_routes/group_tags_routes to avoid a cross-module
    import cycle)."""
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
    except ImportError:  # pragma: no cover - deployed only
        return None
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app()
    return fb_auth


def _direct_tags(email: str) -> tuple[str, list[str], bool]:
    """(uid, direct_tags, found) for an email via the Firebase custom claim.

    Degrades gracefully: an unknown email (or Firebase unavailable) yields
    ``("", [], False)`` so the check still returns domain-derived + tool-perm
    provenance for an address that hasn't signed in yet."""
    fb = _fb_auth()
    if fb is None:
        return "", [], False
    try:
        rec = fb.get_user_by_email(email)
    except Exception:
        return "", [], False
    claims = getattr(rec, "custom_claims", None) or {}
    raw = claims.get(_CLAIM) or []
    tags = sorted({str(t) for t in raw}) if isinstance(raw, (list, tuple, set)) else []
    return rec.uid, tags, True


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[1] if "@" in email else ""


def _explain_tool(email: str, domain: str, tool: str) -> tuple[bool, str]:
    """Replicate ``permissions.can_use_tool``'s user → domain → wildcard order
    (using its own ``_doc_allows`` evaluator) to return (allowed, reason).

    Reads Firestore fresh (not the boolean cache) so the reason and the boolean
    are internally consistent and reflect current state — this is an admin
    dry-run, not the hot path."""
    user_doc = fs.get_document(perms.COLLECTION, email) if email else None
    if user_doc is not None:
        ok = perms._doc_allows(user_doc, tool)
        return ok, f"user-level rule → {'allow' if ok else 'deny'}"
    if domain:
        domain_doc = fs.get_document(perms.COLLECTION, domain)
        if domain_doc is not None:
            ok = perms._doc_allows(domain_doc, tool)
            return ok, f"domain-level rule ({domain}) → {'allow' if ok else 'deny'}"
    wildcard_doc = fs.get_document(perms.COLLECTION, "*")
    if wildcard_doc is not None:
        ok = perms._doc_allows(wildcard_doc, tool)
        return ok, f"wildcard rule → {'allow' if ok else 'deny'}"
    return False, "no matching rule → deny (default)"


def _check_skill(skill_id: str, uid: str, email: str, domain: str, effective_tags: frozenset[str]) -> SkillAccessCheck:
    """Best-effort 5-type access decision for a skill, resolved by id.

    Mirrors enforcement by building the same ``AccessContext`` route handlers
    use (from the user's *effective* tags — direct plus domain-derived) and running
    the shared ``can_access`` evaluator. Id-only resolution (a slug would need
    the owner namespace)."""
    from skills.skill_config import get_skill

    skill = get_skill(skill_id)
    if skill is None:
        return SkillAccessCheck(skill=skill_id, found=False, allowed=False, reason="skill not found (resolve by id)")
    synthetic = User(uid=uid or "unknown", email=email, domain=domain, group_tags=effective_tags)
    ctx = build_access_context(synthetic)
    allowed = ctx.can_access(skill)
    ac_type = getattr(skill.access_control, "type", "unknown")
    label = getattr(skill, "display_name", "") or getattr(skill, "name", "") or skill_id
    reason = f"access_control.type={ac_type} → {'allow' if allowed else 'deny'}"
    return SkillAccessCheck(skill=label, found=True, allowed=allowed, reason=reason)


def _skill_visibility(uid: str, email: str, domain: str, effective_tags: frozenset[str]) -> SkillVisibilityPlane:
    """What the TARGET user's skill list actually contains, and why.

    Computed as the target user — never as the admin asking. That distinction is
    the whole point: a skill-admin bypasses the tenant's ``enabled_skills``
    narrowing, so an admin reading their own list sees a strictly wider set and
    cannot tell which entries an ordinary user would lose.

    Uses the same ``skills.visibility`` evaluator as ``GET /api/skills``, so this
    dry-run cannot drift from what that endpoint really returns (the same
    reason ``_explain_tool`` reuses ``permissions._doc_allows``).
    """
    from db.clients import get_client_cached
    from skills import skill_config
    from skills.visibility import evaluate_visibility

    synthetic = User(uid=uid or "unknown", email=email, domain=domain, group_tags=effective_tags)
    ctx = build_access_context(synthetic)

    try:
        configs = skill_config.list_skills(limit=200)
    except Exception as exc:  # inspection surface — degrade visibly, never 500
        log.warning("admin.access: skill list failed (%s)", type(exc).__name__)
        return SkillVisibilityPlane(enabled_skills=None, visible_count=0, total_count=0, skills=[])

    client = get_client_cached(domain) if domain else None
    enabled = client.enabled_skills if client is not None else None

    verdicts = evaluate_visibility(configs, ctx, enabled)
    rows = [
        SkillVisibilityRow(
            skillId=v.skill_id,
            slug=v.slug,
            label=v.label or v.slug or v.skill_id,
            visible=v.visible,
            reason=v.reason,
            accessAllowed=v.access_allowed,
            tenantAllowed=v.tenant_allowed,
            adminBypass=v.admin_bypass,
        )
        for v in verdicts
    ]
    # Only meaningful when access control already allows the skill — otherwise
    # the tenant filter isn't what's hiding it.
    hidden_by_tenant = [
        (v.label or v.slug or v.skill_id) for v in verdicts if v.access_allowed and not v.tenant_allowed
    ]
    return SkillVisibilityPlane(
        enabledSkills=list(enabled) if enabled is not None else None,
        visibleCount=sum(1 for v in verdicts if v.visible),
        totalCount=len(verdicts),
        hiddenByTenantFilter=hidden_by_tenant,
        skills=rows,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/check", response_model=AccessCheckResponse)
def access_check(body: AccessCheckRequest, scope: Scope) -> AccessCheckResponse:
    """Effective-access dry-run for ``body.email``, scoped to the caller's tenants.

    The target user must be inside the caller's scope: this endpoint reports
    another person's tags, tool permissions, and skill visibility, so an
    unscoped version would let a tenant admin enumerate other tenants' users.
    """
    email = body.email.strip()
    domain = _domain_of(email)
    scope.assert_may(domain)
    uid, direct, user_found = _direct_tags(email)
    derived = set(resolve_derived_group_tags(domain)) if domain else set()

    direct_set = set(direct)
    provenanced: list[ProvenancedTag] = []
    for tag in sorted(direct_set | derived):
        provs: list[str] = []
        if tag in direct_set:
            provs.append("direct")
        if tag in derived:
            provs.append("domain-derived")
        provenanced.append(ProvenancedTag(tag=tag, provenances=provs))

    tool_perm: ToolPermissionCheck | None = None
    if body.tool_name:
        allowed, reason = _explain_tool(email, domain, body.tool_name)
        tool_perm = ToolPermissionCheck(tool=body.tool_name, allowed=allowed, reason=reason)

    effective_tags = frozenset(direct_set | derived)

    skill_access: SkillAccessCheck | None = None
    if body.skill_id:
        skill_access = _check_skill(body.skill_id, uid, email, domain, effective_tags)

    # The "what your users actually see" plane. Opt-in via includeSkills so the
    # existing single-skill callers (CLI, groups page) don't pay for a 200-skill
    # listing on every check.
    visibility: SkillVisibilityPlane | None = None
    if body.include_skills:
        visibility = _skill_visibility(uid, email, domain, effective_tags)

    log.info(
        "admin.access: check email=%s tags=%d tool=%s skill=%s by uid=%s",
        email,
        len(provenanced),
        body.tool_name,
        body.skill_id,
        scope.user.uid,
    )
    return AccessCheckResponse(
        email=email,
        uid=uid,
        domain=domain,
        user_found=user_found,
        tags=provenanced,
        tool_permission=tool_perm,
        skill_access=skill_access,
        skill_visibility=visibility,
    )


__all__ = ["router"]
