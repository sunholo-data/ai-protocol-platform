"""Unit tests for the workshop docs search tool.

The tool indexes the platform's docs corpus on first call and answers
keyword queries with the top-K matching documents + snippets. These tests
exercise the index + scoring contract on the real on-disk docs/, since
the corpus is part of the repo (not a runtime-mounted thing).
"""

from __future__ import annotations

import pytest

from tools import workshop_docs


@pytest.fixture(autouse=True)
def _reset_index_each_test():
    """Force the cached corpus to reload between tests."""
    workshop_docs._INDEX = None
    yield
    workshop_docs._INDEX = None


def test_index_loads_known_workshop_docs():
    """The agenda, code tour, and protocol gotchas must all be indexed."""
    docs = workshop_docs._load_index()
    paths = {d["path"] for d in docs}
    assert "docs/workshop/agenda.md" in paths
    assert "docs/workshop/code-tour.md" in paths
    assert "docs/workshop/protocol-gotchas.md" in paths


def test_index_includes_v6_2_implemented_design_docs():
    """Shipped sprint designs from v6.2.0 should be in the corpus."""
    docs = workshop_docs._load_index()
    paths = {d["path"] for d in docs}
    assert "docs/design/v6.2.0/implemented/a2ui-surface-context.md" in paths
    assert "docs/design/v6.2.0/implemented/budget-enforcement.md" in paths
    assert "docs/design/v6.2.0/implemented/anonymous-group-id-auth.md" in paths


def test_index_includes_integration_howtos():
    """Fork-adoption howtos under docs/integrations/ should be in the corpus."""
    docs = workshop_docs._load_index()
    paths = {d["path"] for d in docs}
    assert "docs/integrations/budget-enforcement.md" in paths
    assert "docs/integrations/tenant-attribution.md" in paths


def test_index_includes_canonical_talk_doc():
    """The living verification-log doc is the source of truth for gotchas."""
    docs = workshop_docs._load_index()
    paths = {d["path"] for d in docs}
    assert "docs/talks/ai-ui-protocol-stack.md" in paths


def test_index_is_memoised():
    """Second call to _load_index() returns the same list object."""
    first = workshop_docs._load_index()
    second = workshop_docs._load_index()
    assert first is second


def test_search_finds_agenda_for_obvious_query():
    """Querying for 'workshop agenda block' should surface agenda.md."""
    result = workshop_docs.search_workshop_docs("workshop agenda block")
    assert "docs/workshop/agenda.md" in result


def test_search_returns_no_match_for_nonsense_query():
    """Made-up tokens return a clear no-match message, not invented content."""
    result = workshop_docs.search_workshop_docs("xyzqwertyzzz nonsense banana")
    assert "No documents matched" in result


def test_search_rejects_empty_query():
    """Empty query gets a usage-style hint, not an exception."""
    result = workshop_docs.search_workshop_docs("")
    assert "at least one search keyword" in result.lower()


def test_search_caps_max_results_at_six():
    """max_results > 6 still caps at 6 (prompt-budget protection)."""
    result = workshop_docs.search_workshop_docs("protocol", max_results=20)
    # Each result block has exactly one "**File:** `" line — count those
    # rather than "### " headings, which can appear inside snippets too.
    assert result.count("**File:** `") <= 6


def test_search_returns_at_least_one_for_common_term():
    """A term that appears across many docs returns at least one hit."""
    result = workshop_docs.search_workshop_docs("a2ui")
    assert "### " in result
    assert "Match strength" in result


def test_search_finds_protocol_gotchas_for_targeted_query():
    """A query naming the doc's content should surface protocol-gotchas.md."""
    result = workshop_docs.search_workshop_docs("protocol gotchas wire state turn behind")
    assert "docs/workshop/protocol-gotchas.md" in result


def test_search_finds_talk_doc_when_title_matches():
    """The canonical talk doc surfaces on title-matched queries."""
    result = workshop_docs.search_workshop_docs("ai protocol stack")
    assert "docs/talks/ai-ui-protocol-stack.md" in result


def test_snippet_is_included_in_match():
    """Every top hit returns a snippet (non-empty body content)."""
    result = workshop_docs.search_workshop_docs("budget enforcement")
    # The result format is title-block → File line → Match line → snippet.
    # Verify the snippet section is non-trivial (>20 chars per hit isn't
    # tested — just that some prose exists between blocks).
    assert "Match strength" in result
    assert len(result) > 200
