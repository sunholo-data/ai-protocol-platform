"""Unit tests for the GroupTag registry model (v6.9.0 9.3)."""

from __future__ import annotations

from db.models import GroupTag
from db.models.group_tags import GroupTag as GroupTagDirect


def test_grouptag_defaults() -> None:
    t = GroupTag(id="ONE")
    assert t.label == ""
    assert t.grants == []
    assert t.tenant_scope is None
    assert t.created_by == ""
    assert t.created_at > 0


def test_grouptag_alias_roundtrip() -> None:
    t = GroupTag(id="ONE", tenantScope="one.com", createdBy="admin", createdAt=123.0)
    assert t.tenant_scope == "one.com"
    assert t.created_by == "admin"
    assert t.created_at == 123.0
    dumped = t.model_dump(by_alias=True)
    assert dumped["tenantScope"] == "one.com"
    assert dumped["createdBy"] == "admin"


def test_grouptag_reexport_is_same_class() -> None:
    assert GroupTag is GroupTagDirect
