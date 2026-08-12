"""Which skills a user actually sees — one evaluator, two callers (v6.16.0 P2).

Skill visibility is decided by **three** independent planes, and until now the
combination lived inline in ``skills/routes.py::list_skills``:

  1. ``accessControl`` — the 5-type access model (public / owner / domain /
     specific / tagged), evaluated by ``AccessContext.can_access``.
  2. the tenant's ``enabled_skills`` **narrowing** — a strict subset filter, never
     a widen.
  3. the **admin bypass** — skill-admins skip plane 2 so they can see and test
     WIP/demo skills that aren't in the tenant's production set.

Plane 3 is why an admin's own skill list is *wrong by construction* as a proxy
for what their users see: the admin is looking at a strictly wider set and has no
way to tell which entries a normal user would lose. That is the exact confusion
that motivated the effective-access screen ("my tenant page lists skills ONE's
users don't have").

So the screen must NOT re-derive this logic — a second implementation would
drift from enforcement, and a dry-run that disagrees with reality is worse than
no dry-run. Instead both callers go through :func:`evaluate_visibility`:

  * ``GET /api/skills`` keeps only the entries where ``visible`` is true.
  * ``POST /api/admin/access/check`` returns the full per-skill verdict, with the
    reason each skill is or isn't visible, computed **as the target user**.

Because the list and the explanation come from the same function, the panel
cannot claim something the API wouldn't do. ``tests/api_tests/`` asserts exactly
that equivalence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from auth import admin_roles


@dataclass(frozen=True)
class SkillVisibility:
    """Per-skill verdict across all three planes, with human-readable reasons."""

    skill_id: str
    slug: str
    label: str

    # Plane 1 — the 5-type access model.
    access_allowed: bool
    access_reason: str

    # Plane 2 — the tenant's enabled_skills narrowing.
    tenant_allowed: bool
    tenant_reason: str

    # Plane 3 — did a skill-admin tag skip plane 2 for this viewer?
    admin_bypass: bool

    @property
    def visible(self) -> bool:
        """The effective verdict — what the user actually gets."""
        if not self.access_allowed:
            return False
        return True if self.admin_bypass else self.tenant_allowed

    @property
    def reason(self) -> str:
        """Why, in the order the planes actually decide.

        Access control is checked first because it is the hard gate — a skill
        denied there is invisible regardless of the tenant's list.
        """
        if not self.access_allowed:
            return self.access_reason
        if self.admin_bypass and not self.tenant_allowed:
            return (
                f"{self.tenant_reason}, but you hold a skill-admin tag so the tenant "
                "filter is bypassed — a normal user in this tenant would NOT see it"
            )
        if not self.tenant_allowed:
            return self.tenant_reason
        return f"{self.access_reason}; {self.tenant_reason}"


def _label_of(cfg: Any) -> str:
    return str(getattr(cfg, "display_name", "") or getattr(cfg, "name", "") or getattr(cfg, "slug", "") or "")


def evaluate_visibility(
    configs: list[Any],
    access: Any,
    enabled_skills: list[str] | None,
) -> list[SkillVisibility]:
    """Evaluate every plane for each skill, for ONE viewer.

    Args:
        configs: Candidate skill configs (already fetched).
        access: The viewer's ``AccessContext`` — supplies ``can_access`` and
            ``group_tags``. Build it from the *target* user when explaining, not
            from the admin doing the asking.
        enabled_skills: The tenant's narrowing list, or ``None`` for "no filter".

    Returns:
        One verdict per config, in input order. Callers filter on ``.visible``;
        the admin screen renders ``.reason``.
    """
    is_skill_admin = admin_roles.is_skill_admin(getattr(access, "group_tags", frozenset()))
    allowed_slugs = set(enabled_skills) if enabled_skills is not None else None

    out: list[SkillVisibility] = []
    for cfg in configs:
        access_allowed = bool(access.can_access_skill(cfg))
        ac_type = getattr(getattr(cfg, "access_control", None), "type", "unknown")
        access_reason = f"access_control.type={ac_type} → {'allow' if access_allowed else 'deny'}"

        slug = getattr(cfg, "slug", None) or ""
        if allowed_slugs is None:
            tenant_allowed = True
            tenant_reason = "tenant has no enabled_skills filter (all skills)"
        elif slug and slug in allowed_slugs:
            tenant_allowed = True
            tenant_reason = "in the tenant's enabled_skills"
        else:
            tenant_allowed = False
            tenant_reason = "not in the tenant's enabled_skills"

        out.append(
            SkillVisibility(
                skill_id=str(getattr(cfg, "skill_id", "") or getattr(cfg, "id", "") or ""),
                slug=slug,
                label=_label_of(cfg),
                access_allowed=access_allowed,
                access_reason=access_reason,
                tenant_allowed=tenant_allowed,
                tenant_reason=tenant_reason,
                # The bypass only *matters* when the narrowing is active.
                admin_bypass=bool(is_skill_admin and allowed_slugs is not None),
            )
        )
    return out


def visible_configs(configs: list[Any], access: Any, enabled_skills: list[str] | None) -> list[Any]:
    """The subset of ``configs`` this viewer actually sees (list-endpoint path)."""
    verdicts = evaluate_visibility(configs, access, enabled_skills)
    return [cfg for cfg, v in zip(configs, verdicts, strict=True) if v.visible]


__all__ = ["SkillVisibility", "evaluate_visibility", "visible_configs"]
