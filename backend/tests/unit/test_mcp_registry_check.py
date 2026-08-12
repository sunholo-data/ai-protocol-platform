"""Tests for the MCP-registry consistency check (issue #14 safeguard).

Two layers:
* Template-vs-catalog (PR time): every MCP server id declared in any real
  skill template must be one the seed tooling can actually seed — catches
  typos and seed-support-less declarations before merge.
* Verifier logic: missing / url-less registry docs report as ``mcp_missing``
  (deploy-gate fatal); loopback drift on deployed envs reports as a warning;
  a registry read failure degrades to a warning, never a false "missing".
"""

from __future__ import annotations

from pathlib import Path

from admin.mcp_registry_check import (
    KNOWN_SEEDABLE_SERVER_IDS,
    LOOPBACK_BY_DESIGN,
    declared_servers_by_skill,
    verify_mcp_registry,
)

TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "skills" / "templates"


def _write_template(root: Path, name: str, servers: list[str]) -> None:
    d = root / name
    d.mkdir(parents=True)
    servers_yaml = "\n".join(f"        - {s}" for s in servers)
    (d / "SKILL.md").write_text(
        f"""---
name: {name}
description: test skill
metadata:
  toolConfigs:
    mcp:
      servers:
{servers_yaml}
---

Instructions body.
"""
    )


class TestTemplatesMatchSeedCatalog:
    def test_every_declared_server_is_seedable(self):
        """A template declaring a server the seed tooling can't provide would
        pass locally and hard-500 on the first env whose registry lacks it
        (exactly issue #14). Pin declarations to the known-seedable set."""
        declared = declared_servers_by_skill(TEMPLATES_ROOT)
        unknown = {
            f"{skill} -> {sid}"
            for skill, ids in declared.items()
            for sid in ids
            if sid not in KNOWN_SEEDABLE_SERVER_IDS
        }
        assert not unknown, (
            f"Templates declare MCP servers the seed tooling doesn't know: {sorted(unknown)}. "
            "Either add seeding support (scripts/seed_mcp_servers.py + "
            "admin.mcp_registry_check.KNOWN_SEEDABLE_SERVER_IDS) or drop the declaration."
        )

    def test_loopback_by_design_is_subset_of_catalog(self):
        assert LOOPBACK_BY_DESIGN <= KNOWN_SEEDABLE_SERVER_IDS


class TestDeclaredServersBySkill:
    def test_reads_declarations(self, tmp_path):
        _write_template(tmp_path, "skill-a", ["ext-apps-map"])
        _write_template(tmp_path, "skill-b", ["toolbox", "ext-apps-map"])
        declared = declared_servers_by_skill(tmp_path)
        assert declared == {
            "skill-a": ["ext-apps-map"],
            "skill-b": ["toolbox", "ext-apps-map"],
        }

    def test_skips_templates_without_declarations_and_malformed(self, tmp_path):
        d = tmp_path / "no-mcp"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: no-mcp\n---\n\nBody.\n")
        bad = tmp_path / "broken"
        bad.mkdir()
        (bad / "SKILL.md").write_text("no frontmatter at all")
        assert declared_servers_by_skill(tmp_path) == {}

    def test_missing_root_is_empty(self, tmp_path):
        assert declared_servers_by_skill(tmp_path / "nope") == {}


class TestVerifyMcpRegistry:
    def _registry(self, docs: dict):
        def fake_get_document(collection: str, doc_id: str):
            assert collection == "mcp_servers"
            return docs.get(doc_id)

        return fake_get_document

    def test_missing_doc_reports_missing(self, tmp_path, monkeypatch):
        _write_template(tmp_path, "skill-a", ["ext-apps-map"])
        import db.firestore as fs

        monkeypatch.setattr(fs, "get_document", self._registry({}))
        result = verify_mcp_registry(tmp_path, deployed=False)
        assert result["ok"] is False
        assert result["mcp_missing"] == ["skill-a -> ext-apps-map"]

    def test_urlless_doc_reports_missing(self, tmp_path, monkeypatch):
        _write_template(tmp_path, "skill-a", ["ext-apps-map"])
        import db.firestore as fs

        monkeypatch.setattr(fs, "get_document", self._registry({"ext-apps-map": {"name": "x"}}))
        result = verify_mcp_registry(tmp_path, deployed=False)
        assert result["mcp_missing"] == ["skill-a -> ext-apps-map"]

    def test_resolvable_registry_is_ok(self, tmp_path, monkeypatch):
        _write_template(tmp_path, "skill-a", ["ext-apps-map", "toolbox"])
        import db.firestore as fs

        monkeypatch.setattr(
            fs,
            "get_document",
            self._registry(
                {
                    "ext-apps-map": {"url": "https://map.example.com/mcp"},
                    "toolbox": {"url": "http://127.0.0.1:5000/mcp/example"},
                }
            ),
        )
        result = verify_mcp_registry(tmp_path, deployed=True)
        assert result["ok"] is True
        assert result["mcp_missing"] == []
        # toolbox loopback is by-design — no warning even on a deployed env.
        assert result["mcp_warnings"] == []

    def test_loopback_on_deployed_env_warns_but_passes(self, tmp_path, monkeypatch):
        _write_template(tmp_path, "skill-a", ["ext-apps-map"])
        import db.firestore as fs

        monkeypatch.setattr(
            fs,
            "get_document",
            self._registry({"ext-apps-map": {"url": "http://127.0.0.1:3001/mcp"}}),
        )
        deployed = verify_mcp_registry(tmp_path, deployed=True)
        assert deployed["ok"] is True
        assert len(deployed["mcp_warnings"]) == 1
        assert "loopback" in deployed["mcp_warnings"][0]
        # Same registry on a local run is legitimate — no warning.
        local = verify_mcp_registry(tmp_path, deployed=False)
        assert local["mcp_warnings"] == []

    def test_read_failure_degrades_to_warning_not_missing(self, tmp_path, monkeypatch):
        """An unreadable registry must not fail the deploy gate as 'missing' —
        that would block good deploys on a transient Firestore error."""
        _write_template(tmp_path, "skill-a", ["ext-apps-map"])
        import db.firestore as fs

        def boom(collection, doc_id):
            raise RuntimeError("firestore unavailable")

        monkeypatch.setattr(fs, "get_document", boom)
        result = verify_mcp_registry(tmp_path, deployed=True)
        assert result["ok"] is True
        assert result["mcp_missing"] == []
        assert any("cannot verify" in w for w in result["mcp_warnings"])
