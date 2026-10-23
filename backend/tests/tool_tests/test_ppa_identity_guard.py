"""The identity guard, wired into the PPA tools (PRECUT-HARDENING M1b).

test_document_identity.py covers the matching logic. THIS file covers the wiring:
that each tool actually consults it, refuses before spending an LLM call, and —
the subtle one — checks BEFORE its extraction cache, so the guard cannot be
skipped by a warm cache.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tools.extract_ppa_clauses import clause_cache_key


@pytest.mark.asyncio
async def test_extract_refuses_the_wrong_document(monkeypatch):
    """The live 2026-08-09 case, at the tool boundary."""
    import json as _json

    from tools import extract_ppa_clauses as mod

    def _fake_ctx(doc_id, mode, section, caller_uid=None):
        return _json.dumps({"docId": doc_id, "filename": "2024_Northwind_ACME_Iberia.pdf", "blocks": []}), []

    monkeypatch.setattr(mod, "build_document_context", _fake_ctx)

    def _explode(*a, **k):  # extraction must never be reached
        raise AssertionError("extraction ran on a document the user did not name")

    monkeypatch.setattr(mod, "generate_content_resilient", _explode)

    out = _json.loads(await mod.extract_ppa_clauses(doc_id="49651a47", expected_name="Brightfield — Iberia PPA"))

    assert "error" in out
    assert "2024_Northwind_ACME_Iberia.pdf" in out["error"]
    assert out["filename"] == "2024_Northwind_ACME_Iberia.pdf"


@pytest.mark.asyncio
async def test_compare_checks_identity_before_the_extraction_cache(monkeypatch):
    """A warm cache must not be able to skip the guard.

    The cache is keyed on the document id alone. Checking identity after the
    cache read would mean the guard fires only on a session's FIRST call and is
    silently bypassed on every subsequent one — the worst kind of half-fix,
    because it would pass a naive test.
    """
    from tools import compare_ppa_contracts as mod

    monkeypatch.setattr(mod, "resolve_filename", lambda d: "2024_Northwind_ACME_Iberia.pdf")

    async def _explode(*a, **k):
        raise AssertionError("extraction ran despite an identity mismatch")

    monkeypatch.setattr(mod, "extract_ppa_clauses", _explode)

    # Pre-warm the cache for this identity so a post-cache check would sail past.
    ctx = MagicMock()
    ctx.state = {clause_cache_key("49651a47", None, None): '{"doc_id": "49651a47"}'}

    with pytest.raises(mod._ExtractionError) as exc:
        await mod._resolve_clauses(
            doc_id="49651a47",
            gs_url=None,
            identity="49651a47",
            tool_context=ctx,
            expected_name="Brightfield — Iberia PPA",
        )
    assert "Brightfield" in exc.value.payload["error"]


@pytest.mark.asyncio
async def test_map_obligations_refuses_the_wrong_document(monkeypatch):
    import json as _json

    from tools import map_ppa_obligations as mod

    monkeypatch.setattr(mod, "resolve_filename", lambda d: "2024_Northwind_ACME_Iberia.pdf")

    out = _json.loads(await mod.map_ppa_obligations(doc_id="49651a47", expected_name="Brightfield PPA"))
    assert "error" in out
    assert "Do NOT present" in out["error"]


@pytest.mark.asyncio
async def test_a_correct_name_does_not_block_the_tool(monkeypatch):
    """The guard must be invisible when the document IS the one named."""
    import json as _json

    from tools import map_ppa_obligations as mod

    monkeypatch.setattr(mod, "resolve_filename", lambda d: "Brightfield PPA model Iberia.pdf")

    sentinel = RuntimeError("passed the guard")

    async def _past_guard(*a, **k):
        raise sentinel

    monkeypatch.setattr(mod, "extract_ppa_clauses", _past_guard)
    try:
        out = await mod.map_ppa_obligations(doc_id="b2cb2112", expected_name="Brightfield PPA")
    except RuntimeError as exc:
        assert exc is sentinel  # got past the guard, which is the point
        return
    # If it returned instead of raising, it must NOT be an identity refusal.
    assert "Do NOT present" not in _json.loads(out).get("error", "")
