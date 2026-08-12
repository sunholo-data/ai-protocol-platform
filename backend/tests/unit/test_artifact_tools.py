"""Unit tests for retrieve_artifact FunctionTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adk.artifact_tools import retrieve_artifact


def _make_part(text: str):
    part = MagicMock()
    part.text = text
    part.inline_data = None
    return part


@pytest.fixture()
def ctx():
    c = MagicMock()
    c.load_artifact = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_returns_first_10k_chars_when_no_section(ctx):
    long_text = "x" * 20_000
    ctx.load_artifact.return_value = _make_part(long_text)
    result = await retrieve_artifact("my-artifact", section=None, tool_context=ctx)
    assert len(result) == 10_000
    assert result == "x" * 10_000


@pytest.mark.asyncio
async def test_returns_content_unchanged_when_shorter_than_10k(ctx):
    ctx.load_artifact.return_value = _make_part("hello world")
    result = await retrieve_artifact("my-artifact", section=None, tool_context=ctx)
    assert result == "hello world"


@pytest.mark.asyncio
async def test_section_filter_returns_matching_chunks(ctx):
    content = "Budget overview\n\nThe budget is 100k.\n\nTimeline overview\n\nQ1 kicks off in March."
    ctx.load_artifact.return_value = _make_part(content)
    result = await retrieve_artifact("my-artifact", section="budget", tool_context=ctx)
    assert "budget" in result.lower()
    assert "Timeline" not in result


@pytest.mark.asyncio
async def test_section_filter_no_match_returns_not_found(ctx):
    ctx.load_artifact.return_value = _make_part("Some content about apples.")
    result = await retrieve_artifact("my-artifact", section="oranges", tool_context=ctx)
    assert "No content matching" in result


@pytest.mark.asyncio
async def test_missing_artifact_returns_not_found_message(ctx):
    ctx.load_artifact.return_value = None
    result = await retrieve_artifact("ghost-artifact", section=None, tool_context=ctx)
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_no_tool_context_returns_error():
    result = await retrieve_artifact("any-id", section=None, tool_context=None)
    assert "tool context" in result.lower()


def test_retrieve_artifact_registered_on_create_agent():
    """retrieve_artifact must appear in create_agent() tool list."""
    from adk.agent import create_agent
    from auth.firebase_auth import User
    from db.models import SkillConfig, SkillMetadata

    user = User(uid="u1", email="test@example.com", domain="example.com")
    skill = SkillConfig(
        name="test-skill",
        description="Test skill for artifact tool registration.",
        instructions="You are a test agent.",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
    )
    agent = create_agent(skill, user)
    tool_names = [getattr(t, "__name__", getattr(t, "name", str(t))) for t in (agent.tools or [])]
    assert "retrieve_artifact" in tool_names
