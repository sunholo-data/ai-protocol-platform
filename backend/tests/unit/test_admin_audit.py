"""Tests for the append-only admin audit helper (admin/audit.py, v6.9.0 / 9.1)."""

from __future__ import annotations

import logging
from unittest.mock import patch

from admin import audit


def test_record_writes_an_append_only_document():
    with patch("admin.audit.set_document") as mock_set:
        audit.record_admin_action(
            actor_uid="admin1",
            actor_email="admin@x.com",
            action="grant_group_tag",
            target="user@y.com",
            before={"group_tags": []},
            after={"group_tags": ["ONE"]},
        )
    assert mock_set.call_count == 1
    coll, doc_id, data = mock_set.call_args[0]
    assert coll == "admin_audit"
    assert doc_id  # a generated uuid — append-only, never overwrites
    assert data["actorUid"] == "admin1"
    assert data["actorEmail"] == "admin@x.com"
    assert data["action"] == "grant_group_tag"
    assert data["target"] == "user@y.com"
    assert data["before"] == {"group_tags": []}
    assert data["after"] == {"group_tags": ["ONE"]}
    assert data["ts"]  # ISO timestamp present


def test_record_uses_a_fresh_id_each_call():
    with patch("admin.audit.set_document") as mock_set:
        audit.record_admin_action(actor_uid="a", action="x", target="t1")
        audit.record_admin_action(actor_uid="a", action="x", target="t2")
    id1 = mock_set.call_args_list[0][0][1]
    id2 = mock_set.call_args_list[1][0][1]
    assert id1 != id2


def test_record_never_raises_on_write_failure(caplog):
    caplog.set_level(logging.ERROR, logger="admin.audit")
    with patch("admin.audit.set_document", side_effect=RuntimeError("firestore down")):
        # Best-effort: must NOT raise into the caller (a mutation must not fail
        # because the audit store blipped) — but the loss is logged at ERROR.
        audit.record_admin_action(actor_uid="a", action="x", target="t")
    assert any("admin_audit write FAILED" in r.message for r in caplog.records)
