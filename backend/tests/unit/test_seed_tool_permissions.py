"""Unit tests for scripts/seed_tool_permissions.build_docs (pure doc builder).

The seed script is the reproducible source of the per-env tool_permissions
grants (see docs/ops/env-config-parity.md). These tests pin the ONE preset's
shape so a refactor can't silently change what a ONE tenant is granted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# The script lives in backend/scripts and inserts backend/ onto sys.path at
# import; make it importable as `scripts.seed_tool_permissions`.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.seed_tool_permissions import (  # noqa: E402
    CREATED_BY,
    DEFAULT_TOOLS,
    ONE_DOMAIN,
    ONE_TOOLS,
    build_docs,
)

# Every skill a ONE-tagged user can REACH — the surface the explicit prod
# allowlist (`--one --explicit`) must cover. Derivation (do NOT trim without
# re-checking the delegation graph — a dropped skill = a silent 403 on prod):
#   - clients/acmeenergy.com.enabled_skills (the dropdown ONE lands on):
#       one-assistant, one-ppa-expert, one-doc-compare, web-researcher
#   - one-assistant delegates (delegation.allow): + claude-assistant, gpt-assistant
#   - discoverJobs + one-ppa-expert.delegation.allow: + one-obligation-analysis
#   - maxDepth 2 escalation (claude→deep-reasoner, gpt→openai-reasoner):
#       + deep-reasoner, openai-reasoner
_REACHABLE_ONE_SKILLS = [
    "one-assistant",
    "one-ppa-expert",
    "one-doc-compare",
    "one-obligation-analysis",
    "web-researcher",
    "claude-assistant",
    "gpt-assistant",
    "deep-reasoner",
    "openai-reasoner",
]


def _declared_tools(skill: str) -> list[str]:
    """Parse metadata.tools from a template's SKILL.md YAML frontmatter."""
    md = (_BACKEND_ROOT / "skills" / "templates" / skill / "SKILL.md").read_text()
    # Frontmatter is the block between the leading `---` and the next `---`.
    _, frontmatter, _ = md.split("---", 2)
    meta = (yaml.safe_load(frontmatter) or {}).get("metadata") or {}
    return list(meta.get("tools") or [])


def _one(explicit: bool):
    docs = build_docs(domain=None, email=None, tools=None, wildcard=False, one=True, one_explicit=explicit)
    assert len(docs) == 1
    return docs[0]


def test_one_preset_defaults_to_wildcard_grant():
    doc_id, data = _one(explicit=False)
    assert doc_id == ONE_DOMAIN
    assert data == {
        "type": "domain",
        "tools": ["*"],
        "denied": [],
        "created_by": CREATED_BY,
    }


def test_one_preset_explicit_is_the_union():
    doc_id, data = _one(explicit=True)
    assert doc_id == ONE_DOMAIN
    assert data["type"] == "domain"
    assert data["denied"] == []
    assert data["tools"] == ONE_TOOLS


def test_one_explicit_union_covers_skill_delegate_and_framework_tools():
    """The explicit list must be a true superset — if it drops any of these a ONE
    user hits a silent 403 mid-turn (the bug this seed exists to fix)."""
    _, data = _one(explicit=True)
    tools = set(data["tools"])
    # the v6.14.0 Toolbox tools the sprint added
    assert {"popular_baby_names", "names_by_decade_source"} <= tools
    # ONE skill tools across all four skills
    assert {
        "ai_search",
        "extract_ppa_clauses",
        "compare_ppa_contracts",
        "map_ppa_obligations",
        "entsoe_day_ahead_prices",
        "list_documents",
        "list_bucket_documents",
        "get_document_content",
    } <= tools
    # wildcard baseline re-granted (domain doc shadows the wildcard)
    assert set(DEFAULT_TOOLS) <= tools
    # framework tools auto-wired by agent.py but still permission-gated
    assert {"load_artifacts", "retrieve_artifact"} <= tools
    # delegate (web-researcher / reasoners) tool
    assert "url_processing" in tools


def test_build_docs_wildcard_and_domain_together():
    docs = build_docs(domain="example.com", email=None, tools=None, wildcard=True, one=False, one_explicit=False)
    by_id = dict(docs)
    assert by_id["example.com"]["type"] == "domain"
    assert by_id["example.com"]["tools"] == ["*"]
    assert by_id["*"]["type"] == "wildcard"
    assert by_id["*"]["tools"] == DEFAULT_TOOLS


def test_explicit_flag_without_one_is_a_noop():
    # --explicit only matters alongside --one
    docs = build_docs(domain=None, email=None, tools=None, wildcard=True, one=False, one_explicit=True)
    assert [d for d, _ in docs] == ["*"]


def test_explicit_allowlist_covers_every_reachable_ONE_skill_tool():
    """Drift guard for the PROD allowlist.

    Prod grants ONE the explicit ``ONE_TOOLS`` list (not ``["*"]``), so if any
    ONE-reachable skill declares a tool that ``ONE_TOOLS`` omits, a ONE user
    would hit a silent mid-turn 403 on the customer-facing env. This test fails
    loudly at CI time when someone adds a tool to a ONE-reachable skill without
    also adding it to ``ONE_TOOLS`` — turning the fragile allowlist into a
    maintained one. (Framework/AgentTool/MCP tool names — load_artifacts,
    web_search_agent, toolbox_* — are not in any skill YAML and are covered by the
    dedicated superset test above.)
    """
    # The customer skill templates are excluded from the public template, so in
    # a sanitized tree there is nothing to drift-check. Skip rather than raise
    # FileNotFoundError — same self-skipping pattern as the other
    # customer-touching suites (test_local_fixture, test_a2ui_obligation_render).
    missing_templates = [
        s for s in _REACHABLE_ONE_SKILLS if not (_BACKEND_ROOT / "skills" / "templates" / s / "SKILL.md").is_file()
    ]
    if missing_templates:
        pytest.skip(f"customer skill templates absent (template fork): {', '.join(missing_templates)}")

    declared: set[str] = set()
    for skill in _REACHABLE_ONE_SKILLS:
        declared |= set(_declared_tools(skill))

    missing = sorted(declared - set(ONE_TOOLS))
    assert not missing, (
        f"ONE_TOOLS is missing tool(s) declared by ONE-reachable skills: {missing}. "
        f"Add them to ONE_TOOLS in scripts/seed_tool_permissions.py and re-seed prod "
        f"(seed_tool_permissions.py --env prod --one --explicit)."
    )
