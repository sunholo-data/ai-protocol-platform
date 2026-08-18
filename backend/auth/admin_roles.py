"""Unified admin-role model (v6.9.0 / 9.1).

Single source of truth for "who is an admin", replacing four divergent
definitions that could silently diverge:

  1. ``admin/auth.py``          — ``aitana-admin`` tag (the /api/admin gate)
  2. ``access_context.py``      — ``one-admin`` tag (skill-management)
  3. ``skills/routes.py``       — ``_SKILL_ADMIN_TAGS`` / ``_PLATFORM_ADMIN_TAGS``
  4. ``firestore.rules``        — a hardcoded ``owner@yourcompany.com`` email

The model is driven entirely by **group-tag claims** — the existing signed,
forge-proof mechanism (JWT ``groupTags``), so the rules-admin and the
backend-admin can no longer drift:

  - ``aitana-admin``            — PLATFORM super-admin: all tenants, all skills,
                                  all users, seeding.
  - ``tenant-admin:{domain}``   — TENANT admin (v6.9.0 new shape): manages its
                                  own tenant only. A platform admin is a tenant
                                  admin of every domain; a tenant admin is NOT a
                                  platform admin.
  - ``one-admin``               — folded in as a scoped SKILL-admin capability
                                  (edit/delete skills you don't own), NOT a
                                  platform admin. Retained so ONE's admin team
                                  keeps working; new grants should prefer
                                  ``aitana-admin`` + ownership.

Every predicate takes a plain ``group_tags`` iterable so it works uniformly
against an ``AccessContext``, a ``User``, or a decoded JWT's ``groupTags`` claim
— no coupling to any one representation.
"""

from __future__ import annotations

from collections.abc import Iterable

# Platform super-admin — unrestricted across every tenant.
PLATFORM_ADMIN_TAG = "aitana-admin"

# Prefix for the per-tenant admin claim shape, e.g. ``tenant-admin:acmeenergy.com``.
TENANT_ADMIN_PREFIX = "tenant-admin:"

# Tags that grant skill-management (edit/delete a skill you don't own). A
# platform admin qualifies; ``one-admin`` is the folded legacy skill-admin tag.
SKILL_ADMIN_TAGS = frozenset({PLATFORM_ADMIN_TAG, "one-admin"})


def _as_set(group_tags: Iterable[str] | None) -> frozenset[str]:
    """Normalise any tags iterable (frozenset/list/None) to a frozenset."""
    if not group_tags:
        return frozenset()
    return frozenset(str(t) for t in group_tags)


def is_platform_admin(group_tags: Iterable[str] | None) -> bool:
    """True iff the caller holds the platform super-admin tag (``aitana-admin``)."""
    return PLATFORM_ADMIN_TAG in _as_set(group_tags)


def tenant_admin_domains(group_tags: Iterable[str] | None) -> frozenset[str]:
    """Domains the caller is a tenant-admin of, parsed from ``tenant-admin:{domain}``.

    A platform admin is implicitly a tenant-admin of *every* domain, which this
    set cannot enumerate — use :func:`is_tenant_admin` for the actual check.
    """
    tags = _as_set(group_tags)
    return frozenset(
        t[len(TENANT_ADMIN_PREFIX) :]
        for t in tags
        if t.startswith(TENANT_ADMIN_PREFIX) and len(t) > len(TENANT_ADMIN_PREFIX)
    )


def is_tenant_admin(group_tags: Iterable[str] | None, domain: str) -> bool:
    """True iff the caller may administer ``domain``.

    Platform admins (``aitana-admin``) administer every domain; a
    ``tenant-admin:{domain}`` holder administers only that one. Deny-by-default
    on an empty/blank domain (never grant tenant-admin for "").
    """
    tags = _as_set(group_tags)
    if is_platform_admin(tags):
        return True
    if not domain:
        return False
    return f"{TENANT_ADMIN_PREFIX}{domain}" in tags


def is_skill_admin(group_tags: Iterable[str] | None) -> bool:
    """True iff the caller may manage skills they do not own (platform or ``one-admin``)."""
    return bool(_as_set(group_tags) & SKILL_ADMIN_TAGS)


def is_admin_conferring_tag(tag: str) -> bool:
    """True iff granting ``tag`` would hand someone administrative authority.

    Used to stop **privilege escalation** through the group-tag grant endpoint
    (v6.16.0 M3). Tag grants are the one admin operation that can change *who is
    an admin*, so a tenant admin must never be able to mint one — otherwise
    "manage my own tenant" silently becomes "grant myself aitana-admin", and the
    tenant boundary is decorative.

    Conferring tags: ``aitana-admin`` (platform), any ``tenant-admin:{domain}``
    (tenant authority, including over *other* domains), and ``one-admin``
    (cross-tenant skill management).
    """
    t = (tag or "").strip()
    if not t:
        return False
    if t in SKILL_ADMIN_TAGS:  # aitana-admin, one-admin
        return True
    return t.startswith(TENANT_ADMIN_PREFIX)


__all__ = [
    "PLATFORM_ADMIN_TAG",
    "SKILL_ADMIN_TAGS",
    "TENANT_ADMIN_PREFIX",
    "is_admin_conferring_tag",
    "is_platform_admin",
    "is_skill_admin",
    "is_tenant_admin",
    "tenant_admin_domains",
]
