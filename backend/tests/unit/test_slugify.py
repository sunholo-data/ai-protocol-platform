"""Unit tests for skills.slugify - slug generation and uniqueness."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from skills import slugify as slugify_mod
from skills.slugify import slugify, unique_slug

# === slugify ===


@pytest.mark.parametrize(
    "name,expected",
    [
        ("General Assistant", "general-assistant"),
        ("Hello, World!", "hello-world"),
        ("Already-kebab-case", "already-kebab-case"),
        ("  trim me  ", "trim-me"),
        ("multiple---hyphens", "multiple-hyphens"),
        ("café", "cafe"),
        ("naïve approach", "naive-approach"),
        ("MIXED-Case String", "mixed-case-string"),
        ("with_underscores", "with-underscores"),
        ("digits 123 mixed", "digits-123-mixed"),
    ],
)
def test_slugify_basic(name: str, expected: str) -> None:
    assert slugify(name) == expected


def test_slugify_empty_falls_back() -> None:
    assert slugify("") == "skill"


def test_slugify_only_punctuation_falls_back() -> None:
    assert slugify("!!!") == "skill"


def test_slugify_only_emoji_falls_back() -> None:
    assert slugify("🎉🚀") == "skill"


def test_slugify_too_short_falls_back() -> None:
    # Single char input becomes a 1-char trimmed result, below the 3-char min.
    assert slugify("a") == "skill"


def test_slugify_truncates_at_max_length() -> None:
    name = "a" * 200
    out = slugify(name)
    assert len(out) <= 60
    assert out.startswith("a")
    assert not out.endswith("-")


def test_slugify_reserved_word_gets_suffix() -> None:
    assert slugify("settings") == "settings-skill"
    assert slugify("New") == "new-skill"


# === unique_slug ===


def test_unique_slug_returns_base_when_free() -> None:
    with patch.object(slugify_mod.fs, "query_documents", return_value=[]):
        assert unique_slug("user-1", "general-assistant") == "general-assistant"


def test_unique_slug_appends_2_on_first_collision() -> None:
    calls = [
        [{"skillId": "other-skill"}],  # base taken
        [],  # -2 free
    ]

    def _q(*args, **kwargs):
        return calls.pop(0)

    with patch.object(slugify_mod.fs, "query_documents", side_effect=_q):
        assert unique_slug("user-1", "general-assistant") == "general-assistant-2"


def test_unique_slug_increments_until_free() -> None:
    sequence = [
        [{"skillId": "a"}],  # base taken
        [{"skillId": "b"}],  # -2 taken
        [{"skillId": "c"}],  # -3 taken
        [],  # -4 free
    ]

    def _q(*args, **kwargs):
        return sequence.pop(0)

    with patch.object(slugify_mod.fs, "query_documents", side_effect=_q):
        assert unique_slug("user-1", "general") == "general-4"


def test_unique_slug_excludes_self() -> None:
    """Resaving a skill's own slug must not collide with itself."""
    with patch.object(
        slugify_mod.fs,
        "query_documents",
        return_value=[{"skillId": "abc-123"}],
    ):
        assert unique_slug("user-1", "general", exclude_skill_id="abc-123") == "general"


def test_unique_slug_truncates_to_fit_with_suffix() -> None:
    """Long base + suffix must still fit within 60 chars."""
    base = "a" * 60
    calls = [
        [{"skillId": "x"}],  # base taken
        [],  # truncated -2 free
    ]

    def _q(*args, **kwargs):
        return calls.pop(0)

    with patch.object(slugify_mod.fs, "query_documents", side_effect=_q):
        result = unique_slug("user-1", base)
    assert len(result) <= 60
    assert result.endswith("-2")
