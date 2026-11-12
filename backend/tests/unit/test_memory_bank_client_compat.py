"""Both SDK layouts retain the same exact memory scope and deletion checks."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from adk import memory_tools, project_memory
from adk.agui import APP_NAME

SCOPE = {"app_name": APP_NAME, "user_id": "caller-1", "project_id": "project-1"}
ENGINE = "reasoningEngines/123"
NAME = f"{ENGINE}/memories/owned"


class _Pager:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def values():
            for memory in self.rows:
                yield SimpleNamespace(memory=memory)

        return values()


@pytest.fixture(params=["vertexai", "agentplatform"])
def bank(request, monkeypatch):
    owned = SimpleNamespace(name=NAME, fact="Synthetic preference", scope=dict(SCOPE), update_time=None)
    foreign = SimpleNamespace(
        name=f"{ENGINE}/memories/other", fact="Other project", scope={**SCOPE, "project_id": "other"}
    )
    api = SimpleNamespace(
        generate=AsyncMock(),
        retrieve=AsyncMock(return_value=_Pager([owned])),
        get=AsyncMock(side_effect=lambda name: owned if name == NAME else foreign),
        delete=AsyncMock(),
    )
    ingest = AsyncMock()
    if request.param == "vertexai":
        api.ingest_events = ingest
        client = SimpleNamespace(agent_engines=SimpleNamespace(memories=api))
    else:
        # Deliberately no agent_engines and no memories.ingest_events: these
        # are absent on the real ADK 2 Agent Platform client.
        client = SimpleNamespace(memory_banks=SimpleNamespace(memories=api, ingest_events=ingest))
    service = SimpleNamespace(_agent_engine_id="123", _get_api_client=lambda: client)
    ctx = SimpleNamespace(_invocation_context=SimpleNamespace(memory_service=service))
    monkeypatch.setattr(memory_tools, "memory_scope_async", AsyncMock(return_value=dict(SCOPE)))
    monkeypatch.setattr(project_memory, "memory_scope_async", AsyncMock(return_value=dict(SCOPE)))
    return ctx, service, api, ingest, owned, foreign


@pytest.mark.asyncio
async def test_remember_and_confirmed_forget_preserve_scope(bank):
    ctx, _, api, _, _, foreign = bank
    assert "Saved to this project" in await memory_tools.remember("Synthetic preference", ctx)
    assert api.generate.await_args.kwargs["scope"] == SCOPE
    preview = await memory_tools.forget("preference", tool_context=ctx)
    assert "NOTHING HAS BEEN DELETED" in preview
    assert api.retrieve.await_args.kwargs["scope"] == SCOPE
    api.delete.assert_not_awaited()
    result = await memory_tools.forget(memory_refs=f"{NAME},{foreign.name}", tool_context=ctx)
    assert "Deleted from memory" in result
    api.delete.assert_awaited_once_with(name=NAME)


@pytest.mark.asyncio
async def test_project_recall_and_autosave_use_current_sdk_surfaces(bank):
    ctx, _, api, ingest, _, _ = bank
    entries = await project_memory.search_scoped_memory(ctx, "preference")
    assert entries[0].content.parts[0].text == "Synthetic preference"
    assert api.retrieve.await_args.kwargs["scope"] == SCOPE
    event = SimpleNamespace(
        content=types.Content(role="user", parts=[types.Part(text="Synthetic preference")]), id="e1", timestamp=1.0
    )
    assert await project_memory.ingest_project_turn(ctx, SCOPE, [event], stream_id="chat-1", idle_seconds=300)
    await asyncio.gather(*tuple(project_memory._background_tasks))
    assert ingest.await_args.kwargs["scope"] == SCOPE
    assert ingest.await_args.kwargs["stream_id"] == "chat-1"
    assert ingest.await_args.kwargs["generation_trigger_config"] == {"generation_rule": {"idle_duration": "300s"}}
    assert ingest.await_args.kwargs["direct_contents_source"].events[0].content.parts[0].text == "Synthetic preference"


@pytest.mark.asyncio
async def test_memory_tool_payload_preserves_wire_shape_and_uploads_as_json(bank):
    from opentelemetry.util.genai.utils import gen_ai_json_dump

    ctx, _, api, _, _, _ = bank
    result = await project_memory.project_load_memory_tool.run_async(args={"query": "preference"}, tool_context=ctx)
    assert api.retrieve.await_args.kwargs["scope"] == SCOPE
    # This is the exact encoder used by the deployed GCS completion uploader.
    # Previously response.result held LoadMemoryResponse and raised TypeError.
    part = types.Part.from_function_response(name="load_memory", response=result)
    output = io.StringIO()
    gen_ai_json_dump(part.function_response.response, output)
    payload = json.loads(output.getvalue())
    assert set(payload) == {"result"}
    entries = payload["result"]["memories"]
    assert len(entries) == 1
    assert entries[0]["content"]["parts"][0]["text"] == "Synthetic preference"


@pytest.mark.asyncio
async def test_project_deletion_never_purges_personal_or_other_project_memories(bank, monkeypatch):
    _, service, api, _, owned, foreign = bank
    personal = SimpleNamespace(name=f"{ENGINE}/memories/personal", scope={"app_name": APP_NAME, "user_id": "caller-1"})
    api.retrieve.return_value = _Pager([owned, foreign, personal])
    monkeypatch.setattr("adk.session.get_memory_service", lambda: service)
    assert await project_memory.purge_project_memories("caller-1", "project-1") == 1
    assert api.retrieve.await_args.kwargs["scope"] == SCOPE
    api.delete.assert_awaited_once_with(name=NAME)
