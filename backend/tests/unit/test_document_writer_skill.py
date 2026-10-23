"""The document-writer skill is wired end-to-end — v6.32.0 document-creation.

A skill is only real when ADK actually builds an agent that can call the tool.
Each assertion here corresponds to a way that silently fails to happen:

* the template does not parse            → the deploy seed step fails the build
* the tool is missing from `tools:`      → the agent simply never calls it
* `job`/`jobFloor` are absent            → no door with `discoverJobs` finds it,
                                           and the skill is unreachable without
                                           hand-editing every front door
* `a2ui.enabled` is true                 → the agent authors UI instead of the
                                           registered server-side mapping
* a required param has no default        → the model invents a value or
                                           interrogates the user for it
"""

from __future__ import annotations

from pathlib import Path

import pytest

from admin.platform_seed import _parse_template

_TEMPLATE = Path(__file__).resolve().parents[2] / "skills" / "templates" / "document-writer" / "SKILL.md"


@pytest.fixture(scope="module")
def parsed() -> dict:
    return _parse_template(_TEMPLATE)


class TestTemplateParses:
    def test_the_seeder_can_parse_it(self, parsed):
        """The deploy seed step fails the build on a bad template."""
        assert parsed["name"] == "document-writer"
        assert parsed["display_name"] == "Document Writer"
        assert parsed["description"].strip()
        assert len(parsed["instructions"]) > 500, "the skill needs real instructions, not a stub"

    def test_the_avatar_it_names_actually_exists(self, parsed):
        avatar = parsed["avatar"]
        repo_root = Path(__file__).resolve().parents[3]
        assert (repo_root / "frontend" / "public" / avatar.lstrip("/")).is_file(), f"missing avatar {avatar}"


class TestToolWiring:
    def test_it_declares_generate_document(self, parsed):
        assert "generate_document" in parsed["metadata"]["tools"]

    def test_it_can_resolve_a_template_by_name(self, parsed):
        """Without list_documents the model cannot know which templates exist,
        so it invents a name (CLAUDE.md #9)."""
        assert "list_documents" in parsed["metadata"]["tools"]

    def test_every_declared_tool_is_in_the_registry(self, parsed):
        from adk.tools import _MODEL_AWARE, TOOL_REGISTRY

        for tool in parsed["metadata"]["tools"]:
            if tool in _MODEL_AWARE:  # resolved per-model in create_agent, not the registry
                continue
            assert tool in TOOL_REGISTRY, f"{tool} is declared but not registered — the agent would drop it"


class TestDiscoverability:
    def test_it_is_a_job_so_any_door_finds_it(self, parsed):
        """`discoverJobs` doors pick this up with no hand-maintained allow entry."""
        assert parsed["metadata"]["job"] is True

    def test_a_door_confirms_before_handing_off(self, parsed):
        """Generation writes a real file and takes tens of seconds — a silent
        hand-off would violate principle #8."""
        assert parsed["metadata"]["jobFloor"] == "confirm"


class TestModelBRendering:
    def test_the_agent_does_not_author_a2ui(self, parsed):
        """The created-document card comes from the registered server-side
        mapping; if the agent authors UI it will describe the result in prose."""
        a2ui = parsed["metadata"]["toolConfigs"]["a2ui"]
        assert a2ui["enabled"] is False
        assert a2ui["default_surface"] == "workspace"

    def test_the_mapping_it_relies_on_is_registered(self):
        import adk.a2ui_document_render  # noqa: F401
        from adk.a2ui_result_render import is_render_payload_tool, tool_produces_artifact

        assert is_render_payload_tool("generate_document")
        assert tool_produces_artifact("generate_document")


class TestAgentActuallyBuilds:
    def test_adk_builds_an_agent_with_the_tool_callable(self, parsed):
        """The end-to-end check: a real ADK agent, with generate_document on it."""
        from auth import User
        from db.models import SkillConfig

        cfg = SkillConfig(
            name=parsed["name"],
            description=parsed["description"],
            instructions=parsed["instructions"],
            skillMetadata=parsed["metadata"],
            displayName=parsed["display_name"],
            ownerId="u1",
        )
        from adk.agent import create_agent

        agent = create_agent(cfg, User(uid="u1", email="a@b.com", domain="b.com"))
        names = {getattr(t, "name", "") for t in (agent.tools or [])}
        assert "generate_document" in names
        assert "list_documents" in names


class TestToolDeclaration:
    def test_only_the_content_is_required(self):
        """A param with no default is REQUIRED in ADK's declaration, and the model
        then either invents a value or interrogates the user for it. Only the
        document body should be mandatory."""
        from google.adk.tools import FunctionTool

        from tools.documents.generate import generate_document

        decl = FunctionTool(generate_document)._get_declaration()
        params = decl.parameters_json_schema

        assert params.get("required") == ["content_markdown"]
        for optional in ("filename", "target", "template"):
            assert optional in params["properties"]

    def test_tool_context_is_not_exposed_to_the_model(self):
        from google.adk.tools import FunctionTool

        from tools.documents.generate import generate_document

        decl = FunctionTool(generate_document)._get_declaration()
        params = decl.parameters_json_schema
        assert "tool_context" not in params["properties"]

    def test_it_can_look_up_public_facts(self, parsed):
        """2026-10-01: asked to describe a named client, it had no web access and
        left a [TO CONFIRM] placeholder in a customer offer."""
        assert "google_search" in parsed["metadata"]["tools"]

    def test_it_never_gets_the_untenanted_bucket_lister(self, parsed):
        """SECURITY. Document Writer is PUBLIC; list_bucket_documents lists ANY
        gs:// URI with the backend SA and has no tenant check, so here it would
        let one tenant enumerate another's bucket (CLAUDE.md Security Hard Rules).
        It belongs only on tag-gated skills."""
        assert "list_bucket_documents" not in parsed["metadata"]["tools"]
