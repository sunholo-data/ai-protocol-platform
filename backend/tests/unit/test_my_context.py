"""my_context: the caller's own setup, and nothing about anyone else."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from tools import my_context as mc

ME = SimpleNamespace(uid="u-carol", email="carol@acme.com", domain="acme.com", group_tags=frozenset({"acme"}))


def _skill(name, owner, slug=None, desc=""):
    return SimpleNamespace(name=name, display_name=name.title(), owner_id=owner, slug=slug, description=desc)


async def test_unverified_caller_gets_nothing():
    with patch.object(mc, "_caller", return_value=None):
        out = await mc.my_context(tool_context=None)
    assert out["status"] == "error" and "skills" not in out


async def test_reports_the_callers_roles_skills_folders_and_features():
    skills = [
        _skill("contract-expert", "u-eve", "contract-expert", "Reads contracts."),
        _skill("mine", "u-carol", "mine"),
    ]
    folders = [
        {"id": "f1", "name": "Project Phoenix", "docCount": 4, "knowledgeBaseId": None},
        {"id": "f2", "name": "Market research", "docCount": 3, "knowledgeBaseId": "kb-123"},
        {"id": "f3", "name": "Created", "docCount": 1, "system": True},
    ]
    with (
        patch.object(mc, "_caller", return_value=ME),
        patch("skills.skill_config.list_skills", return_value=skills),
        patch("skills.visibility.visible_configs", side_effect=lambda configs, access, enabled: configs),
        patch("db.clients.resolve_enabled_skills", return_value=None),
        patch("db.folders.list_folders", return_value=folders) as lf,
        patch("db.clients.resolve_documents_bucket", return_value="b"),
    ):
        out = await mc.my_context(tool_context=None)
    lf.assert_called_once_with("u-carol")  # keyed to the caller, nobody else
    assert out["status"] == "ok" and out["workspace"] == "acme.com"
    assert out["roles"] == ["member (no admin roles)"]
    assert [(s["name"], s["owner"], s.get("open")) for s in out["skills"]] == [
        ("Contract-Expert", "shared with you", "/chat/@u-eve/contract-expert"),
        ("Mine", "yours", "/chat/@u-carol/mine"),
    ]
    assert [(f["name"], f["searchable"]) for f in out["folders"]] == [
        ("Project Phoenix", False),
        ("Market research", True),
        ("Created", False),
    ]
    assert out["features"]["document upload"] == "on"
    dumped = json.dumps(out)
    assert "kb-123" not in dumped and '"f1"' not in dumped and "gs://" not in dumped  # friendly names only


async def test_one_part_failing_does_not_sink_the_answer():
    from db.clients import UnmappedTenantError

    with (
        patch.object(mc, "_caller", return_value=ME),
        patch.object(mc, "_skills", side_effect=RuntimeError("firestore down")),
        patch("db.folders.list_folders", return_value=[]),
        patch("db.clients.resolve_documents_bucket", side_effect=UnmappedTenantError("acme.com")),
    ):
        out = await mc.my_context(tool_context=None)
    assert out["skills"] == "unavailable right now"
    assert out["features"]["document upload"].startswith("off")


def test_platform_admin_role_is_named():
    admin = SimpleNamespace(**{**ME.__dict__, "group_tags": frozenset({"aitana-admin"})})
    assert mc._roles(admin)[0].startswith("platform admin")
