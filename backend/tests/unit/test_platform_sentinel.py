"""Drift guard for the platform-owner sentinel UID.

The exact string `"aitana-platform"` is referenced across Firestore rules,
Cloud Build seed steps, frontend UI copy, and backend guards. A silent
rename here would ship broken prod. If this test fails, you almost
certainly need to update every place that pins the sentinel, not just the
constant.
"""

from skills.platform import PLATFORM_OWNER_UID


def test_sentinel_value_is_exact():
    assert PLATFORM_OWNER_UID == "aitana-platform"


def test_sentinel_is_string_not_none():
    assert isinstance(PLATFORM_OWNER_UID, str)
    assert PLATFORM_OWNER_UID  # non-empty
