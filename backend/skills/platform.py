"""Platform-owned skill sentinel.

Platform skills (default skills shipped by Aitana Labs and available to
every tenant) are stored in Firestore with `owner_id == PLATFORM_OWNER_UID`.
The sentinel is a string — not None — because:

1. Firestore queries on `ownerId` are strings; treating "no owner" as
   `None` would require a separate nullable schema and break the existing
   index.
2. `accessControl`-based visibility already distinguishes public from
   private; the sentinel is specifically about **mutation authority**,
   not visibility.
3. A non-UID string that cannot be a real Firebase uid (Firebase uids are
   28-char base64-ish) makes accidental match-by-collision impossible.

The string value is pinned by `tests/unit/test_platform_sentinel.py` —
renaming it requires coordinated updates across Firestore rules, Cloud
Build seed steps, and frontend "Fork to customize" UI copy.
"""

PLATFORM_OWNER_UID: str = "aitana-platform"
