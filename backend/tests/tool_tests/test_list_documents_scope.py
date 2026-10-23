"""list_documents must not present itself as the whole library (PRECUT-HARDENING M9).

Root cause of the 2026-08-09 wrong-document answer, understood properly on
2026-08-25: the agent called `list_bucket_documents`, GOT a listing containing the
contract it was asked for, ignored it, and instead took a `search_documents`
near-miss from the parsed corpus.

That is a path-preference bug, and this tool is why it exists. `list_documents`
reads `parsed_documents` — a PRE-PARSED INDEX holding a few dozen files — and
announces "Found 14 document(s)". The actual library is the tenant bucket: 152
longform PPAs, 7,219 objects. A model told "found 14 documents" with no scope
qualifier correctly concludes those 14 are what exists, and treats `doc_id` as
the real route with the bucket as a last resort.

Same failure class as `search_documents` calling nearest-K hits "relevant": a
tool asserting completeness it cannot support.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adk import tools as tools_mod


def _doc(name: str, i: int) -> dict:
    return {"__id": f"id{i}", "originalFilename": name, "status": "parsed", "sourceFormat": "pdf"}


@pytest.fixture()
def ctx():
    c = MagicMock()
    c.state = {"user:id": "u1"}
    return c


@pytest.mark.asyncio
async def test_result_discloses_it_is_only_the_parsed_subset(monkeypatch, ctx):
    monkeypatch.setattr(tools_mod, "query_documents", lambda **k: [_doc("A.pdf", 1), _doc("B.pdf", 2)])

    out = await tools_mod.list_documents(tool_context=ctx)

    lowered = out.lower()
    assert "parsed" in lowered, "must say these are the PARSED documents, not all documents"
    assert "list_bucket_documents" in out, "must point at where the rest of the library lives"


@pytest.mark.asyncio
async def test_empty_result_points_at_the_bucket_instead_of_claiming_none_exist(monkeypatch, ctx):
    """'No documents found' reads as 'you have none' when the bucket holds thousands."""
    monkeypatch.setattr(tools_mod, "query_documents", lambda **k: [])

    out = await tools_mod.list_documents(tool_context=ctx)

    assert "list_bucket_documents" in out
    assert out.strip() != "No documents found in the workspace."


@pytest.mark.asyncio
async def test_truncation_is_disclosed(monkeypatch, ctx):
    """limit defaults to 20 and caps at 50 — a full page must not look complete.

    Silent truncation is the same bug in miniature: the model cannot tell 20-of-20
    from 20-of-200.
    """
    monkeypatch.setattr(tools_mod, "query_documents", lambda **k: [_doc(f"F{i}.pdf", i) for i in range(20)])

    out = await tools_mod.list_documents(limit=20, tool_context=ctx)

    assert "more" in out.lower(), "a full page must say there may be more"


@pytest.mark.asyncio
async def test_a_short_page_is_not_falsely_flagged_as_truncated(monkeypatch, ctx):
    monkeypatch.setattr(tools_mod, "query_documents", lambda **k: [_doc("A.pdf", 1)])

    out = await tools_mod.list_documents(limit=20, tool_context=ctx)

    assert "raise `limit`" not in out


# --- Ownership filtering (PRECUT-HARDENING M10) ------------------------------
# CROSS-USER EXPOSURE, found 2026-08-25. list_documents resolved the caller from
# `user:id` / `user_id` in session state — and adk/callbacks.py:_resolve_user_id
# documents that those keys "are NEVER populated by the request path (only
# read)". So user_id was always None, the userId filter was never appended, and
# EVERY user saw EVERY parsed document, then could read its content.
#
# Measured on test: a customer's tester owned 2 parsed documents; her sessions'
# list_documents returned 14 of the 16 on the deployment, including another
# tenant's uploads, and the agent went on to open two of them.
#
# The correct resolution already existed in the same codebase for the RAG
# loader: prefer the ADK invocation context's user_id, fall back to state.


class _Inv:
    def __init__(self, uid):
        self.user_id = uid


def _ctx_with_invocation(uid, state=None):
    c = MagicMock()
    c.state = state if state is not None else {}
    c._invocation_context = _Inv(uid)
    return c


@pytest.mark.asyncio
async def test_filters_by_the_invocation_uid_when_state_is_empty(monkeypatch):
    """The production shape: state carries no uid, the invocation does."""
    seen = {}

    def _q(**kw):
        seen.update(kw)
        return [_doc("A.pdf", 1)]

    monkeypatch.setattr(tools_mod, "query_documents", _q)

    await tools_mod.list_documents(tool_context=_ctx_with_invocation("uid-real"))

    assert ("userId", "==", "uid-real") in seen["filters"], "no ownership filter — every user would see every document"


@pytest.mark.asyncio
async def test_never_returns_an_unfiltered_listing(monkeypatch):
    """If the caller cannot be identified, return NOTHING rather than everything.

    Failing open here means handing one tenant another tenant's document list.
    """
    monkeypatch.setattr(tools_mod, "query_documents", lambda **k: [_doc("A.pdf", 1)])

    c = MagicMock()
    c.state = {}
    c._invocation_context = None

    out = await tools_mod.list_documents(tool_context=c)

    assert "A.pdf" not in out


@pytest.mark.asyncio
async def test_state_still_works_as_a_fallback(monkeypatch):
    seen = {}

    def _q(**kw):
        seen.update(kw)
        return [_doc("A.pdf", 1)]

    monkeypatch.setattr(tools_mod, "query_documents", _q)
    c = MagicMock()
    c.state = {"user:id": "uid-state"}
    c._invocation_context = None

    await tools_mod.list_documents(tool_context=c)
    assert ("userId", "==", "uid-state") in seen["filters"]
