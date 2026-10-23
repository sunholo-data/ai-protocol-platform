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
async def test_section_given_as_int_is_coerced_not_crashed(ctx):
    """Prod 2026-09-14: the model sent ``section=1`` ("section 1"); ``int.lower``
    raised and the whole turn died with a RUN_ERROR, then the orphaned tool_call
    poisoned every later turn on the session. A tool argument is untrusted."""
    content = "Section 1\n\nIntro text.\n\nSection 2\n\nMore text."
    ctx.load_artifact.return_value = _make_part(content)
    result = await retrieve_artifact("my-artifact", section=1, tool_context=ctx)  # type: ignore[arg-type]
    assert "Section 1" in result
    assert "Section 2" not in result


# --- The offload pointer loop (PRECUT-HARDENING M2) -------------------------
# Live failure, session 9aba32cf 2026-08-24: get_document_content(mode="blocks")
# returned 63,214 chars -> offloaded to an artifact. retrieve_artifact(section=…)
# then returned ANOTHER 63,214-char pointer, twice, byte-identical, and the agent
# burned 72s before escaping via ADK's builtin load_artifacts.
#
# Mechanism: blocks mode is json.dumps with no indent, so the payload contains no
# "\n\n" at all. split("\n\n") yields ONE chunk holding everything; any section
# substring matches it; matches[:_MAX_CHUNKS] is a no-op on one element; and the
# section path had no character cap (only the no-section path did). The full
# payload was returned, and retrieve_artifact is NOT offload-exempt (exemption
# requires a registered A2UI mapping), so _handle_large_output re-offloaded it.
#
# The cap is what makes the loop structurally impossible, so it is asserted
# against the offload threshold rather than by example.


@pytest.mark.asyncio
async def test_section_path_caps_single_line_payload(ctx):
    """A blocks-mode payload has no blank lines — the whole thing is one chunk."""
    single_line = (
        '{"docId":"d","blocks":[' + ",".join(f'{{"text":"Success Fee clause {i}"}}' for i in range(4000)) + "]}"
    )
    assert "\n\n" not in single_line
    assert len(single_line) > 50_000
    ctx.load_artifact.return_value = _make_part(single_line)

    result = await retrieve_artifact("art", section="Success Fee", tool_context=ctx)

    assert len(result) <= 10_000


@pytest.mark.asyncio
async def test_section_path_never_returns_an_offloadable_payload(ctx):
    """Structural guard: the return can never be large enough to be re-offloaded.

    If this fails, the pointer loop is reachable again regardless of input shape.
    """
    from adk.callbacks import _LARGE_OUTPUT_THRESHOLD

    # Well-formed paragraphs, but FEW and LARGE. _MAX_CHUNKS=5 bounds the chunk
    # COUNT, not the chunk SIZE, so five 20K paragraphs sail past it — the same
    # hole the one-giant-chunk case exploits, reached from the other direction.
    # (A many-small-paragraph corpus does NOT discriminate: the count cap alone
    # already keeps it small, so such a test passes before and after the fix.)
    content = "\n\n".join(f"Success Fee paragraph {i} " + "y" * 20_000 for i in range(5))
    assert len(content) > _LARGE_OUTPUT_THRESHOLD
    ctx.load_artifact.return_value = _make_part(content)

    result = await retrieve_artifact("art", section="Success Fee", tool_context=ctx)

    assert len(result) <= _LARGE_OUTPUT_THRESHOLD


@pytest.mark.asyncio
async def test_section_path_falls_back_to_single_newline_split(ctx):
    """With no blank lines, single-\\n lines are the useful unit — not one blob."""
    content = "\n".join(["Intro line", "Success Fee is 2%", "Unrelated tail", "Another Success Fee note"])
    ctx.load_artifact.return_value = _make_part(content)

    result = await retrieve_artifact("art", section="Success Fee", tool_context=ctx)

    assert "Success Fee is 2%" in result
    assert "Unrelated tail" not in result


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
