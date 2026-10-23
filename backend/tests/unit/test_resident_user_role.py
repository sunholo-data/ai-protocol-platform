"""`resident-user` — eligibility for a resident agent instance (v6.40.0 P1).

The assertions worth having are about what the tag must NOT do. It rides the
same signed-claim mechanism as every other role, so the risk is not that it
fails to work — it is that it quietly grants more than it says.
"""

from __future__ import annotations

from admin.group_tags_routes import is_known_tag
from auth.admin_roles import (
    PLATFORM_ADMIN_TAG,
    RESIDENT_USER_TAG,
    is_admin_conferring_tag,
    is_platform_admin,
    is_resident_user,
    is_skill_admin,
    is_tenant_admin,
)


def test_the_tag_grants_eligibility():
    assert is_resident_user({RESIDENT_USER_TAG})


def test_absent_by_default():
    """Deny by default: eligibility is granted, never assumed."""
    assert not is_resident_user(None)
    assert not is_resident_user(set())
    assert not is_resident_user({"some-other-tag"})


def test_it_confers_no_administrative_authority():
    """THE ASSERTION THAT MATTERS. A capability tag that leaked admin rights
    would be a privilege escalation wearing a feature's name."""
    tags = {RESIDENT_USER_TAG}
    assert not is_platform_admin(tags)
    assert not is_skill_admin(tags)
    assert not is_tenant_admin(tags, "example.com")


def test_it_is_not_admin_conferring():
    """`is_admin_conferring_tag` gates who may GRANT a tag. Marking this one
    conferring would stop tenant admins from giving it to their own users,
    which is the intended workflow; failing to keep the real admin tags
    conferring would be far worse, so both directions are asserted."""
    assert not is_admin_conferring_tag(RESIDENT_USER_TAG)
    assert is_admin_conferring_tag(PLATFORM_ADMIN_TAG)
    assert is_admin_conferring_tag("tenant-admin:example.com")


def test_a_platform_admin_is_not_implicitly_eligible():
    """Platform admins administer everything, but an instance costs money and
    a quota slot. Being able to GRANT eligibility is not the same as having it,
    and conflating them would provision instances nobody asked for."""
    assert not is_resident_user({PLATFORM_ADMIN_TAG})


def test_the_tag_is_grantable_without_a_registry_entry():
    """It is code-defined, like the structural admin tags. A registry-only
    definition would let a typo'd entry fail silently — the grant would report
    success and the capability would never arrive."""
    assert is_known_tag(RESIDENT_USER_TAG)
