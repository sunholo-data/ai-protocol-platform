"""Folder selection — v6.32.0 document-creation M3.

`ensure_default_folder` returned `folders[0]` from an unordered Firestore
`stream()`. That was harmless only while every user had exactly one folder. The
moment a second, system-created folder ("Created") exists, an upload with no
explicit `folder_id` can silently land in it — the user's own document filed
under the assistant's output.

So the fix is a prerequisite for the Created folder, not a side quest, and these
tests pin it: order must not decide, and a system folder must never be the
default destination.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import db.folders as folders_db

_UID = "user-1"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _folder(fid: str, name: str, *, age_days: int, system: bool = False) -> dict:
    data = {
        "id": fid,
        "name": name,
        "userId": _UID,
        "createdAt": _T0 + timedelta(days=age_days),
        "docCount": 0,
        "parsedCount": 0,
    }
    if system:
        data["system"] = True
    return data


class TestEnsureDefaultFolder:
    def test_never_returns_a_system_folder(self):
        """The finding-4 regression: fails on the pre-fix `folders[0]`."""
        # "Created" first in stream order — exactly what an unordered stream can do.
        listed = [
            _folder("created-id", "Created", age_days=5, system=True),
            _folder("uploads-id", "Uploads 2026-01-01", age_days=0),
        ]
        with patch.object(folders_db, "list_folders", return_value=listed):
            assert folders_db.ensure_default_folder(_UID) == "uploads-id"

    def test_result_is_order_independent(self):
        """Same folders, every permutation, same answer."""
        import itertools

        base = [
            _folder("created-id", "Created", age_days=5, system=True),
            _folder("older-id", "Uploads", age_days=0),
            _folder("newer-id", "Contracts", age_days=3),
        ]
        for perm in itertools.permutations(base):
            with patch.object(folders_db, "list_folders", return_value=list(perm)):
                assert folders_db.ensure_default_folder(_UID) == "older-id", f"order changed the answer: {perm}"

    def test_prefers_the_oldest_non_system_folder(self):
        listed = [
            _folder("newer-id", "Contracts", age_days=9),
            _folder("older-id", "Uploads", age_days=2),
        ]
        with patch.object(folders_db, "list_folders", return_value=listed):
            assert folders_db.ensure_default_folder(_UID) == "older-id"

    def test_creates_a_folder_when_only_system_folders_exist(self):
        """A user whose only folder is 'Created' must not have uploads land there."""
        listed = [_folder("created-id", "Created", age_days=0, system=True)]
        with (
            patch.object(folders_db, "list_folders", return_value=listed),
            patch.object(folders_db, "create_folder", return_value={"id": "fresh-id"}) as mk,
        ):
            assert folders_db.ensure_default_folder(_UID) == "fresh-id"
        assert mk.called

    def test_creates_a_folder_when_none_exist(self):
        with (
            patch.object(folders_db, "list_folders", return_value=[]),
            patch.object(folders_db, "create_folder", return_value={"id": "fresh-id"}) as mk,
        ):
            assert folders_db.ensure_default_folder(_UID) == "fresh-id"
        name = mk.call_args[0][1]
        assert name.startswith("Uploads ")

    def test_missing_created_at_does_not_crash(self):
        """Legacy records predate createdAt; they must still be selectable."""
        legacy = {"id": "legacy-id", "name": "Old", "userId": _UID}
        with patch.object(folders_db, "list_folders", return_value=[legacy]):
            assert folders_db.ensure_default_folder(_UID) == "legacy-id"


class TestEnsureNamedFolder:
    def test_creates_when_absent(self):
        with (
            patch.object(folders_db, "list_folders", return_value=[]),
            patch.object(folders_db, "create_folder", return_value={"id": "new-id"}) as mk,
        ):
            assert folders_db.ensure_named_folder(_UID, "Created", system=True) == "new-id"
        assert mk.call_args[0][1] == "Created"

    def test_is_idempotent_by_name(self):
        """Repeated generations share one folder rather than one folder per call."""
        listed = [_folder("created-id", "Created", age_days=0, system=True)]
        with (
            patch.object(folders_db, "list_folders", return_value=listed),
            patch.object(folders_db, "create_folder") as mk,
        ):
            assert folders_db.ensure_named_folder(_UID, "Created", system=True) == "created-id"
        assert not mk.called, "must not create a second folder with the same name"

    def test_name_match_is_case_insensitive_and_trimmed(self):
        listed = [_folder("created-id", "Created", age_days=0, system=True)]
        with (
            patch.object(folders_db, "list_folders", return_value=listed),
            patch.object(folders_db, "create_folder") as mk,
        ):
            assert folders_db.ensure_named_folder(_UID, "  created  ") == "created-id"
        assert not mk.called

    def test_marks_system_folders(self):
        captured: dict = {}

        def fake_create(uid, name, system=False):
            captured.update(uid=uid, name=name, system=system)
            return {"id": "new-id"}

        with (
            patch.object(folders_db, "list_folders", return_value=[]),
            patch.object(folders_db, "create_folder", side_effect=fake_create),
        ):
            folders_db.ensure_named_folder(_UID, "Created", system=True)

        assert captured["system"] is True

    def test_a_created_folder_is_not_then_chosen_as_the_upload_default(self):
        """The two halves together — the actual regression this sprint guards."""
        listed: list[dict] = [_folder("uploads-id", "Uploads", age_days=0)]

        def fake_create(uid, name, system=False):
            entry = _folder("created-id", name, age_days=7, system=system)
            listed.append(entry)
            return {"id": "created-id"}

        with (
            patch.object(folders_db, "list_folders", side_effect=lambda _u: list(listed)),
            patch.object(folders_db, "create_folder", side_effect=fake_create),
        ):
            assert folders_db.ensure_named_folder(_UID, "Created", system=True) == "created-id"
            # An upload with no explicit folder must still go to the user's own folder.
            assert folders_db.ensure_default_folder(_UID) == "uploads-id"
