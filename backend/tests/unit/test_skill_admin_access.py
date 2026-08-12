"""Unit tests for the Skill Studio management access gate.

v6.6.0 ONE-FORK-CONVERGENCE M3: skill writes (PUT/DELETE) are allowed for the
skill owner OR a holder of the `one-admin` group tag. This lets ONE's admin
team manage skills they don't personally own via the Skill Studio.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth.access_context import SKILL_ADMIN_TAG, AccessContext
from db.models.access import AccessControl


@dataclass
class _Skill:
    owner_id: str
    access_control: AccessControl


def _skill(owner_id: str = "owner-1") -> _Skill:
    return _Skill(owner_id=owner_id, access_control=AccessControl(type="private"))


class TestSkillAdminTag:
    def test_tag_value(self):
        assert SKILL_ADMIN_TAG == "one-admin"


class TestIsSkillAdmin:
    def test_owner_can_manage(self):
        ctx = AccessContext(uid="owner-1", email="o@x.com")
        assert ctx.is_skill_admin(_skill(owner_id="owner-1")) is True

    def test_one_admin_tag_can_manage_others_skill(self):
        ctx = AccessContext(uid="someone-else", email="a@x.com", group_tags=frozenset({"one-admin"}))
        assert ctx.is_skill_admin(_skill(owner_id="owner-1")) is True

    def test_non_owner_without_tag_cannot_manage(self):
        ctx = AccessContext(uid="someone-else", email="a@x.com", group_tags=frozenset({"ONE"}))
        assert ctx.is_skill_admin(_skill(owner_id="owner-1")) is False

    def test_owner_check_still_distinct(self):
        # is_skill_owner stays strict — the admin tag does not grant ownership.
        ctx = AccessContext(uid="someone-else", email="a@x.com", group_tags=frozenset({"one-admin"}))
        assert ctx.is_skill_owner(_skill(owner_id="owner-1")) is False
