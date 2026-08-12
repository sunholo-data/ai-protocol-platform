"""Tests for tools/search_agent.py."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import LlmResponse
from google.adk.tools import AgentTool, VertexAiSearchTool, google_search, url_context
from google.genai import types


class TestCreateWebSearchAgent:
    def test_returns_llm_agent(self):
        from tools.search_agent import create_web_search_agent

        assert isinstance(create_web_search_agent(), LlmAgent)

    def test_name(self):
        from tools.search_agent import create_web_search_agent

        assert create_web_search_agent().name == "web_search_agent"

    def test_has_two_tools(self):
        from tools.search_agent import create_web_search_agent

        assert len(create_web_search_agent().tools) == 2

    def test_includes_google_search_and_url_context(self):
        from tools.search_agent import create_web_search_agent

        tools = create_web_search_agent().tools
        assert google_search in tools
        assert url_context in tools

    def test_no_vertex_search_tool(self):
        from tools.search_agent import create_web_search_agent

        assert not any(isinstance(t, VertexAiSearchTool) for t in create_web_search_agent().tools)


class TestCreateEnterpriseSearchAgent:
    """Enterprise agent uses VertexAiSearchTool exclusively.

    google_search and VertexAiSearchTool use incompatible API-level tool types
    (400 INVALID_ARGUMENT if combined). Each gets its own named sub-agent.
    """

    def test_returns_llm_agent(self):
        from tools.search_agent import create_enterprise_search_agent

        assert isinstance(create_enterprise_search_agent("my-ds"), LlmAgent)

    def test_name(self):
        from tools.search_agent import create_enterprise_search_agent

        assert create_enterprise_search_agent("my-ds").name == "enterprise_search_agent"

    def test_has_one_tool(self):
        from tools.search_agent import create_enterprise_search_agent

        assert len(create_enterprise_search_agent("my-ds").tools) == 1

    def test_includes_vertex_search_with_correct_datastore(self):
        from tools.search_agent import create_enterprise_search_agent

        agent = create_enterprise_search_agent("my-ds")
        vertex_tools = [t for t in agent.tools if isinstance(t, VertexAiSearchTool)]
        assert len(vertex_tools) == 1
        assert vertex_tools[0].data_store_id == "my-ds"

    def test_no_google_search_or_url_context(self):
        from tools.search_agent import create_enterprise_search_agent

        tools = create_enterprise_search_agent("my-ds").tools
        assert google_search not in tools
        assert url_context not in tools


class TestCreateSearchAgentConvenienceWrapper:
    """create_search_agent() dispatches to web or enterprise based on datastore_id."""

    def test_no_datastore_returns_web_agent(self):
        from tools.search_agent import create_search_agent

        assert create_search_agent().name == "web_search_agent"

    def test_with_datastore_returns_enterprise_agent(self):
        from tools.search_agent import create_search_agent

        assert create_search_agent(datastore_id="my-ds").name == "enterprise_search_agent"


def _web_chunk(uri: str, title: str) -> types.GroundingChunk:
    return types.GroundingChunk(web=types.GroundingChunkWeb(uri=uri, title=title))


def _rc_chunk(uri: str | None, title: str) -> types.GroundingChunk:
    return types.GroundingChunk(retrieved_context=types.GroundingChunkRetrievedContext(uri=uri, title=title))


def _resp(text: str | None, chunks: list[types.GroundingChunk] | None) -> LlmResponse:
    content = None if text is None else types.Content(role="model", parts=[types.Part(text=text)])
    gm = None if chunks is None else types.GroundingMetadata(grounding_chunks=chunks)
    return LlmResponse(content=content, grounding_metadata=gm)


class TestFormatSourceContext:
    """The appended block is CONTEXT FOR THE AGENT — friendly names, no URIs.

    The user-visible list is the workbench Sources tab's job
    (adk/a2ui_sources_render.py), which links correctly; the chat copy did not.
    """

    def test_web_chunk_renders_its_title_only(self):
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(grounding_chunks=[_web_chunk("https://a.test/x", "Article X")])
        block = _format_source_context(gm)
        assert block is not None
        assert "- Article X" in block
        assert "https://a.test/x" not in block

    def test_enterprise_chunk_never_leaks_the_gs_uri(self):
        """REGRESSION: the chat reply used to carry `[title](gs://bucket/...)` —
        an unopenable link that also exposed the internal bucket path."""
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(grounding_chunks=[_rc_chunk("gs://bucket/cases/ppa market.txt", "ppa market")])
        block = _format_source_context(gm)
        assert block is not None
        assert "- ppa market" in block
        assert "gs://" not in block
        assert "](" not in block  # no markdown links at all

    def test_block_tells_the_agent_not_to_repeat_it(self):
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(grounding_chunks=[_web_chunk("https://a.test/x", "Article X")])
        block = _format_source_context(gm)
        assert block is not None
        assert "Sources" in block.splitlines()[0]  # names the tab the user is looking at
        assert "do NOT" in block

    def test_dedupes_preserving_order(self):
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(
            grounding_chunks=[
                _web_chunk("https://a.test/x", "X"),
                _web_chunk("https://b.test/y", "Y"),
                _web_chunk("https://a.test/x", "X"),
            ]
        )
        block = _format_source_context(gm)
        assert block is not None
        assert block.splitlines()[1:] == ["- X", "- Y"]

    def test_title_less_web_chunk_falls_back_to_hostname(self):
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(grounding_chunks=[_web_chunk("https://www.example.test/a/b", "")])
        block = _format_source_context(gm)
        assert block is not None
        assert "- example.test" in block

    def test_title_only_chunk_is_listed(self):
        from tools.search_agent import _format_source_context

        gm = types.GroundingMetadata(grounding_chunks=[_rc_chunk(None, "Untitled corpus doc")])
        block = _format_source_context(gm)
        assert block is not None
        assert "- Untitled corpus doc" in block

    def test_none_and_empty_return_none(self):
        from tools.search_agent import _format_source_context

        assert _format_source_context(None) is None
        assert _format_source_context(types.GroundingMetadata(grounding_chunks=[])) is None


class TestStripAuthoredSources:
    """Prompting alone doesn't hold — a trailing model-written list is stripped."""

    def test_strips_trailing_sources_list(self):
        from tools.search_agent import _strip_authored_sources

        text = "Answer.\n\nSources:\n- [X](https://a.test/x)\n- [Y](gs://b/y.pdf)"
        assert _strip_authored_sources(text) == "Answer."

    def test_strips_bold_and_heading_variants(self):
        from tools.search_agent import _strip_authored_sources

        for heading in ("**Sources**", "## Sources", "References:", "**Citations:**"):
            text = f"Answer.\n\n{heading}\n1. First doc\n2. Second doc"
            assert _strip_authored_sources(text) == "Answer.", heading

    def test_keeps_prose_that_merely_mentions_sources(self):
        from tools.search_agent import _strip_authored_sources

        text = "Sources:\nThe corpus has three relevant documents, all from 2024."
        assert _strip_authored_sources(text) == text

    def test_keeps_a_list_that_is_the_answer_itself(self):
        from tools.search_agent import _strip_authored_sources

        text = "German 10-year solar PPAs:\n- 2024: €49.93/MWh\n- 2025: €51.10/MWh"
        assert _strip_authored_sources(text) == text

    def test_noop_when_no_list_present(self):
        from tools.search_agent import _strip_authored_sources

        assert _strip_authored_sources("Just an answer.") == "Just an answer."


class TestAppendSourceContext:
    def test_appends_block_to_grounded_answer(self):
        from tools.search_agent import _append_source_context

        resp = _resp("Here is the news.", [_web_chunk("https://a.test/x", "Article X")])
        out = _append_source_context(None, resp)  # type: ignore[arg-type]
        assert out is not None
        text = "".join(p.text for p in out.content.parts if p.text)
        assert text.startswith("Here is the news.\n\n[grounding sources")
        assert text.endswith("- Article X")

    def test_no_grounding_metadata_is_noop(self):
        from tools.search_agent import _append_source_context

        assert _append_source_context(None, _resp("Answer.", None)) is None  # type: ignore[arg-type]

    def test_replaces_a_model_authored_list_with_the_context_block(self):
        """REGRESSION (the shipped bug): the model wrote its own gs:// list and
        the callback bailed out, so the wrong links reached the chat reply."""
        from tools.search_agent import _append_source_context

        resp = _resp(
            "Answer.\n\nSources:\n- [ppa market](gs://bucket/cases/ppa market.txt)",
            [_rc_chunk("gs://bucket/cases/ppa market.txt", "ppa market")],
        )
        out = _append_source_context(None, resp)  # type: ignore[arg-type]
        assert out is not None
        text = "".join(p.text for p in out.content.parts if p.text)
        assert "gs://" not in text
        assert text.startswith("Answer.\n\n[grounding sources")

    def test_strips_an_authored_list_even_with_nothing_citable(self):
        from tools.search_agent import _append_source_context

        resp = _resp("Answer.\n\nSources:\n- [X](https://a.test/x)", None)
        out = _append_source_context(None, resp)  # type: ignore[arg-type]
        assert out is not None
        assert "".join(p.text for p in out.content.parts if p.text) == "Answer."

    def test_skips_when_no_text_to_append_to(self):
        from tools.search_agent import _append_source_context

        assert _append_source_context(None, _resp(None, [_web_chunk("https://a.test/x", "X")])) is None  # type: ignore[arg-type]


class TestCallbackDoesNotEmitInline:
    """The callback appends TEXT only — the visual Sources card is the registered
    result→A2UI mapping's job (main-agent after_tool_callback, tracker bound), NOT
    a sub-agent tracker call (that hit the 'digest never renders' trap)."""

    def test_callback_makes_no_tracker_emission(self):
        from observability import timing
        from tools.search_agent import _append_source_context

        tracker = timing.LatencyTracker(skill_id="s", session_id="t", user_id="u")
        token = timing.set_current_tracker(tracker)
        try:
            resp = _resp("News summary.", [_web_chunk("https://a.test/x", "Article X")])
            out = _append_source_context(None, resp)  # type: ignore[arg-type]
        finally:
            timing.reset_current_tracker(token)
        # Context block still appended…
        assert out is not None
        text = "".join(p.text for p in out.content.parts if p.text)
        assert "- Article X" in text
        # …but no A2UI_SURFACE event is emitted from here.
        assert not [e for e in tracker.drain_stage_events() if e.name == timing.A2UI_SURFACE_EVENT_NAME]


class TestSearchAgentsWireCallback:
    def test_web_agent_has_sources_callback(self):
        from tools.search_agent import _append_source_context, create_web_search_agent

        assert create_web_search_agent().after_model_callback is _append_source_context

    def test_enterprise_agent_has_sources_callback(self):
        from tools.search_agent import _append_source_context, create_enterprise_search_agent

        assert create_enterprise_search_agent("my-ds").after_model_callback is _append_source_context

    def test_instructions_forbid_a_chat_side_sources_list(self):
        """The prompt half of the fix — the tab owns the list, the reply doesn't."""
        from tools.search_agent import create_enterprise_search_agent, create_web_search_agent

        for agent in (create_web_search_agent(), create_enterprise_search_agent("my-ds")):
            assert "'Sources:'" in agent.instruction
            assert "Sources' tab" in agent.instruction


class TestResolveSearchTools:
    """_resolve_search_tools returns the right AgentTool(s) for each combination."""

    def test_no_search_tools_returns_empty(self):
        from adk.agent import _resolve_search_tools

        assert _resolve_search_tools(["list_documents"], {}) == []

    def test_google_search_only_returns_google_search_agent_tool(self):
        from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool

        from adk.agent import _resolve_search_tools

        result = _resolve_search_tools(["google_search"], {})
        assert len(result) == 1
        # ADK-native class — propagates grounding metadata to parent session
        assert isinstance(result[0], GoogleSearchAgentTool)
        assert result[0].agent.name == "web_search_agent"

    def test_ai_search_with_datastore_returns_enterprise_agent_tool(self):
        from adk.agent import _resolve_search_tools

        configs = {
            "ai_search": {"datastore_id": "projects/p/locations/eu/collections/default_collection/dataStores/ds"}
        }
        result = _resolve_search_tools(["ai_search"], configs)
        assert len(result) == 1
        assert isinstance(result[0], AgentTool)
        assert result[0].agent.name == "enterprise_search_agent"
        assert result[0].propagate_grounding_metadata is True

    def test_ai_search_without_datastore_skips_and_warns(self, caplog, monkeypatch):
        import logging

        from adk.agent import _resolve_search_tools

        # Skip only when there's NO per-skill datastore AND no platform-default
        # env var — otherwise the default fallback would (correctly) wire it.
        monkeypatch.delenv("VERTEX_AI_SEARCH_DATASTORE_ID", raising=False)
        with caplog.at_level(logging.WARNING):
            result = _resolve_search_tools(["ai_search"], {})
        assert result == []
        assert any("datastore" in r.message for r in caplog.records)

    def test_ai_search_accepts_shorthand_datastore_key(self):
        """SKILL.md files use the shorthand `datastore:` key (not `datastore_id`).
        _resolve_search_tools must read both, else the tool is silently skipped —
        the bug that kept enterprise search out of every demo."""
        from adk.agent import _resolve_search_tools

        configs = {"ai_search": {"datastore": "projects/p/locations/eu/collections/default_collection/dataStores/ds"}}
        result = _resolve_search_tools(["ai_search"], configs)
        assert len(result) == 1
        assert isinstance(result[0], AgentTool)
        assert result[0].agent.name == "enterprise_search_agent"

    def test_ai_search_falls_back_to_platform_default_env(self, monkeypatch):
        """A skill that lists ai_search with no datastore of its own picks up the
        VERTEX_AI_SEARCH_DATASTORE_ID platform default (Aitana: aitana3)."""
        from adk.agent import _resolve_search_tools

        monkeypatch.setenv(
            "VERTEX_AI_SEARCH_DATASTORE_ID",
            "projects/aitana-ai-search/locations/eu/collections/default_collection/dataStores/aitana3",
        )
        result = _resolve_search_tools(["ai_search"], {})
        assert len(result) == 1
        assert isinstance(result[0], AgentTool)
        assert result[0].agent.name == "enterprise_search_agent"

    def test_both_returns_two_agent_tools(self):
        from adk.agent import _resolve_search_tools

        configs = {"ai_search": {"datastore_id": "my-ds"}}
        result = _resolve_search_tools(["google_search", "ai_search"], configs)
        assert len(result) == 2
        names = {r.agent.name for r in result}
        assert names == {"web_search_agent", "enterprise_search_agent"}
