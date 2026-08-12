"""Effective skill visibility — the "what your users actually see" plane (P2).

The load-bearing test here is ``TestNoDivergence``: whatever the admin panel
claims a user sees must equal what ``GET /api/skills`` actually returns for that
user. A dry-run that disagrees with enforcement is worse than no dry-run, because
an admin will act on it.

That risk is not hypothetical — it is the bug that motivated this whole feature.
The tenant screen listed skills ONE's users did not have, because admins bypass
the ``enabled_skills`` narrowing and nothing said so.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# Module-level imports are REQUIRED here, not stylistic: this file uses
# `from __future__ import annotations`, so FastAPI resolves the override's
# `request: Request` annotation against module globals. Importing Request inside
# the helper made FastAPI treat `request` as a missing QUERY PARAM and every
# call 422'd — the same deferred-annotation trap as the admin matrix probes.
from auth import User, build_access_context, get_current_user
from db.models import SkillConfig
from skills.visibility import evaluate_visibility, visible_configs


def _skill(slug: str, ac_type: str = "public", skill_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        skill_id=skill_id or f"id-{slug}",
        slug=slug,
        display_name=slug.replace("-", " ").title(),
        name=slug,
        access_control=SimpleNamespace(type=ac_type),
        owner_id="platform",
    )


def _ctx(*tags: str, email: str = "u@one.com"):
    user = User(uid="u1", email=email, domain=email.split("@")[-1], group_tags=frozenset(tags))
    return build_access_context(user)


_ALL = [_skill("one-assistant"), _skill("one-ppa-expert"), _skill("wip-demo")]
_ENABLED = ["one-assistant", "one-ppa-expert"]


def _real(slug: str) -> SkillConfig:
    """A genuine SkillConfig — the list endpoint serialises through
    SkillResponse, so the divergence test needs real models, not stubs."""
    return SkillConfig(
        name=slug,
        description=f"Description for {slug}.",
        instructions="Help with stuff.",
        skillId=f"id-{slug}",
        displayName=slug.replace("-", " ").title(),
        ownerEmail="platform@aitana.ai",
        ownerId="platform-uid",
        slug=slug,
        accessControl={"type": "public"},
    )


_REAL = [_real("one-assistant"), _real("one-ppa-expert"), _real("wip-demo")]


class TestTenantNarrowing:
    def test_ordinary_user_loses_skills_outside_enabled_list(self):
        v = evaluate_visibility(_ALL, _ctx("ONE"), _ENABLED)
        assert [x.slug for x in v if x.visible] == ["one-assistant", "one-ppa-expert"]

    def test_no_filter_means_everything_access_allows(self):
        v = evaluate_visibility(_ALL, _ctx("ONE"), None)
        assert [x.slug for x in v if x.visible] == ["one-assistant", "one-ppa-expert", "wip-demo"]

    def test_reason_names_the_tenant_filter(self):
        v = {x.slug: x for x in evaluate_visibility(_ALL, _ctx("ONE"), _ENABLED)}
        assert "not in the tenant's enabled_skills" in v["wip-demo"].reason

    def test_empty_enabled_list_hides_everything(self):
        """An empty list is a real (if odd) config — it must narrow to nothing
        rather than being treated as 'unset = allow all'."""
        v = evaluate_visibility(_ALL, _ctx("ONE"), [])
        assert [x.slug for x in v if x.visible] == []


class TestAdminBypassIsVisibleNotHidden:
    """The admin bypass is the reason the admin's own list is misleading.

    It must be reported, not silently applied — that silence *is* the bug.
    """

    def test_admin_still_sees_the_wip_skill(self):
        v = {x.slug: x for x in evaluate_visibility(_ALL, _ctx("aitana-admin"), _ENABLED)}
        assert v["wip-demo"].visible is True

    def test_admin_bypass_is_flagged(self):
        v = {x.slug: x for x in evaluate_visibility(_ALL, _ctx("aitana-admin"), _ENABLED)}
        assert v["wip-demo"].admin_bypass is True
        assert v["wip-demo"].tenant_allowed is False

    def test_reason_warns_that_normal_users_would_not_see_it(self):
        v = {x.slug: x for x in evaluate_visibility(_ALL, _ctx("aitana-admin"), _ENABLED)}
        assert "a normal user in this tenant would NOT see it" in v["wip-demo"].reason

    def test_bypass_not_flagged_when_no_narrowing_exists(self):
        """With no tenant filter there is nothing to bypass — claiming a bypass
        would be a misleading warning."""
        v = evaluate_visibility(_ALL, _ctx("aitana-admin"), None)
        assert all(x.admin_bypass is False for x in v)

    def test_one_admin_also_bypasses(self):
        # one-admin is the legacy skill-admin tag; it bypasses too.
        v = {x.slug: x for x in evaluate_visibility(_ALL, _ctx("one-admin"), _ENABLED)}
        assert v["wip-demo"].visible is True


class TestAccessControlIsTheHardGate:
    def test_private_skill_denied_regardless_of_tenant_list(self):
        private = _skill("secret", ac_type="private")
        private.owner_id = "someone-else"
        v = evaluate_visibility([private], _ctx("ONE"), ["secret"])
        assert v[0].visible is False
        assert v[0].access_allowed is False

    def test_access_denial_wins_over_admin_bypass_in_the_reason(self):
        private = _skill("secret", ac_type="private")
        private.owner_id = "someone-else"
        v = evaluate_visibility([private], _ctx("aitana-admin"), ["other"])
        # A skill-admin may MANAGE skills they don't own, but this plane reports
        # access-control first — the reason must not blame the tenant filter.
        assert "enabled_skills" not in v[0].reason or v[0].access_allowed


class TestNoDivergence:
    """The panel's answer must equal what GET /api/skills REALLY returns.

    This drives the actual route — not just the shared helper — because the
    divergence that matters is someone re-inlining the filter in
    ``skills/routes.py``. Comparing two functions inside ``skills.visibility``
    would be trivially consistent and prove nothing; the first draft of this test
    did exactly that, which is why it now goes through the HTTP layer.
    """

    def _endpoint_slugs(self, user: User, enabled: list[str] | None) -> list[str]:
        """Slugs the real list route returns for this user.

        Harness mirrors tests/api_tests/test_skill_list_tenant_filter.py — the
        AccessContext is attached inside the get_current_user override, which is
        how the real middleware supplies it.
        """
        from skills.routes import router

        app = FastAPI()
        app.include_router(router)

        async def _override(request: Request) -> User:
            request.state.access = build_access_context(user)
            return user

        app.dependency_overrides[get_current_user] = _override

        with (
            patch("skills.routes.skill_config.list_skills", return_value=list(_REAL)),
            patch("skills.routes.resolve_enabled_skills", return_value=enabled),
        ):
            r = TestClient(app).get("/api/skills")
        assert r.status_code == 200, r.text
        return [row["slug"] for row in r.json()]

    @pytest.mark.parametrize(
        "tags,enabled",
        [
            (("ONE",), _ENABLED),
            (("ONE",), None),
            (("aitana-admin",), _ENABLED),
            (("one-admin",), _ENABLED),
            ((), _ENABLED),
            (("ONE",), []),
        ],
    )
    def test_panel_matches_the_real_list_endpoint(self, tags, enabled):
        user = User(uid="u1", email="u@one.com", domain="one.com", group_tags=frozenset(tags))
        ctx = build_access_context(user)

        panel_visible = sorted(v.slug for v in evaluate_visibility(_REAL, ctx, enabled) if v.visible)
        endpoint_visible = sorted(self._endpoint_slugs(user, enabled))

        assert panel_visible == endpoint_visible, (
            f"The effective-access panel and GET /api/skills disagree for tags={tags}. "
            "An admin acting on the panel would be acting on a lie — route both "
            "through skills.visibility.evaluate_visibility."
        )

    def test_helper_agrees_with_itself(self):
        """Cheap invariant kept separately so the expensive HTTP test above
        isn't the only thing guarding the helper."""
        ctx = _ctx("ONE")
        assert [v.slug for v in evaluate_visibility(_ALL, ctx, _ENABLED) if v.visible] == [
            c.slug for c in visible_configs(_ALL, ctx, _ENABLED)
        ]


class TestHiddenByTenantFilterSummary:
    def test_summary_lists_only_access_allowed_skills(self):
        """A skill denied by access control isn't 'hidden by the tenant filter' —
        blaming the filter would send the admin to fix the wrong thing."""
        private = _skill("secret", ac_type="private")
        private.owner_id = "someone-else"
        configs = [*_ALL, private]
        verdicts = evaluate_visibility(configs, _ctx("ONE"), _ENABLED)
        hidden = [v.slug for v in verdicts if v.access_allowed and not v.tenant_allowed]
        assert hidden == ["wip-demo"]
        assert "secret" not in hidden
