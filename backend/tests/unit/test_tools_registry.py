"""Unit tests for the tool registry (TOOLS-PORTING M1+).

Model-aware tools (ai_search, google_search, code_execution) are NOT in the
TOOL_REGISTRY — they're wired directly in agent.py based on model detection.
Registry entries are model-agnostic FunctionTools callable by any model.
"""

from __future__ import annotations

import pytest
from google.adk.tools import FunctionTool

from adk import tools


def test_registry_contains_document_tools():
    """Document tools are the first real tools added in M1."""
    assert "list_documents" in tools.TOOL_REGISTRY
    assert "get_document_content" in tools.TOOL_REGISTRY


def test_registry_does_not_contain_model_aware_tools():
    """Model-aware tools must NOT be in the registry — they're handled by agent.py."""
    assert "ai_search" not in tools.TOOL_REGISTRY
    assert "google_search" not in tools.TOOL_REGISTRY


def test_each_registry_entry_is_a_factory_returning_functiontool():
    """Registry values are callables that return an ADK FunctionTool."""
    for name, factory in tools.TOOL_REGISTRY.items():
        tool_obj = factory({})
        assert isinstance(tool_obj, FunctionTool), f"{name} did not produce a FunctionTool"


def test_resolve_tools_returns_functiontool_list_for_known_names():
    result = tools.resolve_tools(["list_documents", "get_document_content"], {})
    assert len(result) == 2
    assert all(isinstance(t, FunctionTool) for t in result)


def test_resolve_tools_raises_on_unknown_name():
    """Unknown tool names raise ValueError — prevents silent misconfiguration."""
    with pytest.raises(ValueError, match="not_a_real_tool"):
        tools.resolve_tools(["list_documents", "not_a_real_tool"], {})


def test_resolve_tools_skips_model_aware_tools_silently():
    """Model-aware tools are silently skipped (handled by agent.py, not logged as unknown)."""
    result = tools.resolve_tools(["ai_search", "google_search", "list_documents"], {})
    assert len(result) == 1  # only list_documents passes through


def test_resolve_tools_skips_structured_extraction_silently():
    """structured_extraction is a callback-only tool — must not raise, must not produce a FunctionTool."""
    result = tools.resolve_tools(["structured_extraction", "list_documents"], {})
    assert len(result) == 1  # only list_documents; structured_extraction skipped


def test_resolve_tools_empty_input_returns_empty_list():
    assert tools.resolve_tools([], {}) == []


def test_resolve_tools_accepts_tool_configs_without_crashing():
    """Per-tool config dict is plumbed to the factory without crashing."""
    configs = {"list_documents": {"some_config": True}}
    result = tools.resolve_tools(["list_documents"], configs)
    assert len(result) == 1
    assert isinstance(result[0], FunctionTool)
