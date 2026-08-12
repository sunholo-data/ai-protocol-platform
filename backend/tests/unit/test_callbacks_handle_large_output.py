"""Unit tests for M3 ADK callbacks (AGENT-FACTORY M3).

* `_handle_large_output` — `after_tool_callback`. Keeps small tool
  responses as-is; for >50K char responses saves an ADK artifact via
  `tool_context.save_artifact(...)` and returns a pointer string so the
  model sees a short reference instead of megabytes of text.
* `make_before_agent(skill_id)` — `before_agent_callback` factory.
  Returns a callback that annotates the current OTEL span with
  `skill_id` and (if present on session state) `routing_choice`.
* `_after_agent` — documented no-op reserved for v6.1 structured
  extraction (not tested beyond "it's a callable that returns None").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import adk.a2ui_ppa_render  # noqa: F401 — registers compare/extract as render-payload tools
from adk.callbacks import _after_agent, _handle_large_output, make_before_agent

# --- _handle_large_output ---


def _mk_tool_context() -> MagicMock:
    ctx = MagicMock()
    ctx.save_artifact = AsyncMock()
    return ctx


async def test_handle_large_output_passes_small_response_through():
    ctx = _mk_tool_context()
    resp = {"result": "small"}
    out = await _handle_large_output(tool=MagicMock(name="search"), args={}, tool_context=ctx, tool_response=resp)
    # Small response should come back unchanged; no artifact saved.
    assert out is resp or out == resp
    ctx.save_artifact.assert_not_called()


async def test_handle_large_output_saves_artifact_for_large_response():
    ctx = _mk_tool_context()
    # 60K chars — well over the 50K threshold.
    big_text = "x" * 60_000
    out = await _handle_large_output(
        tool=MagicMock(name="big_search"), args={}, tool_context=ctx, tool_response=big_text
    )
    # Should return a string (not the original), which is the pointer, and
    # must have called save_artifact exactly once.
    assert isinstance(out, str)
    assert out is not big_text
    assert ctx.save_artifact.call_count == 1


async def test_handle_large_output_pointer_mentions_artifact():
    ctx = _mk_tool_context()
    big_text = "y" * 60_000
    out = await _handle_large_output(
        tool=MagicMock(name="big_search"), args={}, tool_context=ctx, tool_response=big_text
    )
    # Pointer should be informative enough for the model to understand
    # that the full response is saved as an artifact.
    assert "artifact" in out.lower()


async def test_handle_large_output_threshold_is_50k_chars():
    ctx = _mk_tool_context()
    # Exactly 50_000 characters — at threshold, should pass through.
    at_threshold = "z" * 50_000
    out = await _handle_large_output(tool=MagicMock(name="s"), args={}, tool_context=ctx, tool_response=at_threshold)
    ctx.save_artifact.assert_not_called()
    assert out == at_threshold or out is at_threshold


async def test_render_payload_tools_are_never_offloaded():
    """extract/compare results are the workbench render payload — offloading
    them strands the UI (frontend gets the pointer, not the typed JSON)."""
    ctx = _mk_tool_context()
    big_json = '{"differences": []}' + "x" * 60_000  # well over threshold
    for name in ("compare_ppa_contracts", "extract_ppa_clauses"):
        ctx.save_artifact.reset_mock()
        tool = MagicMock()
        tool.name = name  # real string attribute (MagicMock(name=) does NOT set .name)
        out = await _handle_large_output(tool=tool, args={}, tool_context=ctx, tool_response=big_json)
        assert out == big_json, f"{name} should be returned untouched"
        ctx.save_artifact.assert_not_called()


async def test_non_render_tools_still_offload_when_large():
    ctx = _mk_tool_context()
    tool = MagicMock()
    tool.name = "ai_search"
    out = await _handle_large_output(tool=tool, args={}, tool_context=ctx, tool_response="x" * 60_000)
    assert isinstance(out, str) and "artifact" in out.lower()
    ctx.save_artifact.assert_called_once()


async def test_offload_roundtrip_save_then_load_real_artifact_service():
    """REGRESSION (2026-07-15 'document fetching not working'): `save_artifact`
    is a coroutine; the offload used to call it WITHOUT awaiting, so the artifact
    was never written and every `retrieve_artifact` 404'd. A mock can't catch an
    un-awaited coroutine — a REAL save→load roundtrip can. Offload a >50K
    response, then load the artifact back and assert the full content survives."""
    from google.adk.agents import LlmAgent
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools.tool_context import ToolContext

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="t", user_id="u", session_id="s")
    ictx = InvocationContext(
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
        invocation_id="inv-roundtrip",
        agent=LlmAgent(name="probe", model="gemini-2.5-flash", description="d", instruction="i"),
        session=session,
    )
    tool_context = ToolContext(ictx)

    big = "R" * 60_000
    pointer = await _handle_large_output(
        tool=SimpleNamespace(name="get_document_content"), args={}, tool_context=tool_context, tool_response=big
    )
    assert "artifact" in pointer.lower()

    # The artifact MUST actually be retrievable — this is the assertion that
    # fails if save_artifact isn't awaited (the coroutine never runs).
    loaded = await tool_context.load_artifact(filename=f"get_document_content_response_{tool_context.invocation_id}")
    assert loaded is not None, "artifact was not saved — save_artifact was not awaited"
    assert loaded.text == big


# --- make_before_agent ---


def test_make_before_agent_returns_callable():
    cb = make_before_agent("my-skill-id")
    assert callable(cb)


def test_before_agent_sets_skill_id_on_current_span():
    cb = make_before_agent("my-skill-id")
    # Mock the OTEL span so the callback can set attributes on it.
    mock_span = MagicMock()
    ctx = MagicMock()
    ctx.state = {}
    from unittest.mock import patch

    with patch("adk.callbacks.trace.get_current_span", return_value=mock_span):
        cb(callback_context=ctx)  # ADK calls by keyword; parameter name is enforced.
    mock_span.set_attribute.assert_any_call("skill_id", "my-skill-id")


def test_before_agent_sets_routing_choice_when_present_in_state():
    cb = make_before_agent("skill-1")
    mock_span = MagicMock()
    ctx = MagicMock()
    ctx.state = {"routing_choice": "thinking"}
    from unittest.mock import patch

    with patch("adk.callbacks.trace.get_current_span", return_value=mock_span):
        cb(callback_context=ctx)  # ADK calls by keyword; parameter name is enforced.
    mock_span.set_attribute.assert_any_call("routing_choice", "thinking")


def test_before_agent_skips_routing_choice_when_absent():
    cb = make_before_agent("skill-1")
    mock_span = MagicMock()
    ctx = MagicMock()
    ctx.state = {}
    from unittest.mock import patch

    with patch("adk.callbacks.trace.get_current_span", return_value=mock_span):
        cb(callback_context=ctx)  # ADK calls by keyword; parameter name is enforced.
    # Only skill_id should be set; no routing_choice.
    call_args = [c.args for c in mock_span.set_attribute.call_args_list]
    keys = {a[0] for a in call_args}
    assert "skill_id" in keys
    assert "routing_choice" not in keys


# --- _after_agent ---


def test_after_agent_is_noop_returning_none():
    # Placeholder for v6.1 structured extraction. Must be a callable that
    # accepts a CallbackContext and returns None without touching state.
    ctx = SimpleNamespace(state={})
    result = _after_agent(callback_context=ctx)
    assert result is None
    assert ctx.state == {}
