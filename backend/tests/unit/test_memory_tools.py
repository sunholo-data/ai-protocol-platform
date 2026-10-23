"""remember / forget (adk/memory_tools.py) against a fake Memory Bank client.

What must hold: the scope always comes from the ADK invocation; a bare call
never invents a fact; ``forget`` deletes nothing until it is given refs, and a
ref is deleted only after its memory is re-read and found in the caller's own
scope; every failure is a visible result, never an exception.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adk.memory_tools import forget, memory_scope, remember

_ENGINE = "reasoningEngines/123"


class _Pager:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                yield item

        return _gen()


class _Memories:
    def __init__(self, store: dict[str, SimpleNamespace]):
        self.store = store
        self.generated: list[dict] = []
        self.deleted: list[str] = []
        self.retrieve_calls: list[dict] = []
        self.fail_generate = False

    async def generate(self, **kwargs):
        if self.fail_generate:
            raise RuntimeError("boom")
        self.generated.append(kwargs)

    async def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        scope = kwargs["scope"]
        return _Pager(SimpleNamespace(memory=m) for m in self.store.values() if m.scope == scope)

    async def get(self, *, name):
        return self.store[name]

    async def delete(self, *, name):
        self.deleted.append(name)
        self.store.pop(name, None)


def _ctx(store: dict, uid: str = "alice", *, bank: bool = True):
    memories = _Memories(store)
    client = SimpleNamespace(agent_engines=SimpleNamespace(memories=memories))
    service = SimpleNamespace(_agent_engine_id="123", _get_api_client=lambda: client) if bank else SimpleNamespace()
    inv = SimpleNamespace(user_id=uid, app_name="aitana_platform", memory_service=service)
    return SimpleNamespace(_invocation_context=inv, state={}), memories


def _mem(mid: str, fact: str, uid: str = "alice") -> tuple[str, SimpleNamespace]:
    name = f"projects/p/locations/l/{_ENGINE}/memories/{mid}"
    return name, SimpleNamespace(name=name, fact=fact, scope={"app_name": "aitana_platform", "user_id": uid})


def _run(coro):
    return asyncio.run(coro)


def test_scope_comes_from_the_invocation():
    ctx, _ = _ctx({})
    assert memory_scope(ctx) == {"app_name": "aitana_platform", "user_id": "alice"}
    assert memory_scope(SimpleNamespace(_invocation_context=SimpleNamespace(user_id=None, app_name="a"))) == {}


def test_remember_saves_into_the_callers_scope():
    ctx, memories = _ctx({})
    out = _run(remember(fact="Prefers answers   in Spanish.", tool_context=ctx))
    assert out.startswith('Saved to memory: "Prefers answers in Spanish."')
    call = memories.generated[0]
    assert call["name"] == _ENGINE
    assert call["scope"] == {"app_name": "aitana_platform", "user_id": "alice"}
    assert call["direct_memories_source"] == {"direct_memories": [{"fact": "Prefers answers in Spanish."}]}


def test_bare_remember_asks_and_saves_nothing():
    ctx, memories = _ctx({})
    out = _run(remember(tool_context=ctx))
    assert "Ask the user" in out and memories.generated == []


def test_remember_failure_is_visible_not_raised():
    ctx, memories = _ctx({})
    memories.fail_generate = True
    assert "Nothing was saved" in _run(remember(fact="x", tool_context=ctx))


def test_no_memory_bank_says_so():
    ctx, _ = _ctx({}, bank=False)
    assert "not available" in _run(remember(fact="x", tool_context=ctx))
    assert "not available" in _run(forget(about="x", tool_context=ctx))


def test_forget_first_call_lists_and_deletes_nothing():
    name, mem = _mem("1", "Codename is kumquat.")
    other_name, other = _mem("2", "Bob's secret.", uid="bob")
    ctx, memories = _ctx({name: mem, other_name: other})
    out = _run(forget(about="codename", tool_context=ctx))
    assert "Codename is kumquat." in out and f"[ref: {name}]" in out
    assert "Bob's secret." not in out
    assert "NOTHING HAS BEEN DELETED" in out
    assert memories.deleted == []
    assert memories.retrieve_calls[0]["similarity_search_params"]["search_query"] == "codename"


def test_bare_forget_lists_what_is_remembered():
    name, mem = _mem("1", "Codename is kumquat.")
    ctx, memories = _ctx({name: mem})
    out = _run(forget(tool_context=ctx))
    assert "Codename is kumquat." in out
    assert "simple_retrieval_params" in memories.retrieve_calls[0]


def test_forget_with_refs_deletes_only_the_callers_own():
    mine, mem = _mem("1", "Codename is kumquat.")
    theirs, other = _mem("2", "Bob's secret.", uid="bob")
    ctx, memories = _ctx({mine: mem, theirs: other})
    out = _run(forget(memory_refs=f"[ref: {mine}], {theirs}, reasoningEngines/999/memories/3", tool_context=ctx))
    assert memories.deleted == [mine]
    assert '"Codename is kumquat."' in out
    assert "2 ref(s) were not memories of this user" in out
    assert "Bob's secret." not in out


def test_forget_finds_nothing():
    ctx, _ = _ctx({})
    assert "Nothing remembered matches" in _run(forget(about="anything", tool_context=ctx))
