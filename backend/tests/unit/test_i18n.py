"""backend/i18n.py — the backend half of the one resolution rule.

The resolution cases live in tests/fixtures/i18n_resolution.json, which the
frontend's vitest suite also reads, so pick_locale / localized_field and
resolveLocalized / localizedField can't drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from i18n import (
    MAX_SKILL_LOCALES,
    locale_chain,
    localized_field,
    normalise_locale,
    pick_locale,
    validate_locales_block,
)

_TABLE = json.loads((Path(__file__).parents[1] / "fixtures" / "i18n_resolution.json").read_text())["cases"]


@pytest.mark.parametrize("case", _TABLE, ids=[c["name"] for c in _TABLE])
def test_shared_resolution_table(case):
    default = case.get("default", "en")
    if case["fn"] == "pick":
        got = pick_locale(case["value"], case["locale"], default)
    else:
        got = localized_field(case["base"], case["variants"], case["field"], case["locale"])
    assert got == case["expect"]


def test_table_covers_both_shapes():
    assert {c["fn"] for c in _TABLE} == {"pick", "field"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("da", "da"),
        ("DA", "da"),
        ("pt_br", "pt-BR"),
        ("PT-br", "pt-BR"),
        (" en ", "en"),
        ("es-419", "es-419"),
        ("fil", "fil"),
        ("english", None),
        ("en-US-x", None),
        ("e", None),
        ("", None),
        ("-US", None),
        (None, None),
        (42, None),
    ],
)
def test_normalise_locale(raw, expected):
    assert normalise_locale(raw) == expected


def test_locale_chain_is_deduplicated():
    assert locale_chain("pt-BR", "da") == ["pt-BR", "pt", "da", "en"]
    assert locale_chain("en", "en") == ["en"]


# === validate_locales_block ===


def test_validate_keeps_filled_fields_and_normalises_tags():
    got = validate_locales_block(
        {
            "xx": {"display_name": "Xeneral", "initial_message": "Xi!\n"},
            "pt_br": {"description": "  Xescription  "},
        }
    )
    assert got == {
        "xx": {"display_name": "Xeneral", "initial_message": "Xi!"},
        "pt-BR": {"description": "Xescription"},
    }


def test_validate_drops_blank_values_and_empty_locales():
    got = validate_locales_block({"xx": {"display_name": "", "description": None}, "yy": None, "zz": {}})
    assert got == {}


@pytest.mark.parametrize("raw", [None, {}])
def test_validate_empty(raw):
    assert validate_locales_block(raw) == {}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (["xx"], "must be a mapping"),
        ("xx", "must be a mapping"),
        ({"english": {"display_name": "x"}}, "not a language tag"),
        ({"xx": "Xeneral"}, "must be a mapping of fields"),
        ({"xx": {"instructions": "Do x"}}, "unknown field 'instructions'"),
        ({"xx": {"displayName": "X"}}, "unknown field 'displayName'"),
        ({"xx": {"display_name": 3}}, "must be text"),
        ({"xx": {"display_name": "x" * 129}}, "at most 128"),
        ({"xx": {"description": "x" * 1025}}, "at most 1,024"),
        ({"xx": {"initial_message": "x" * 4001}}, "at most 4,000"),
        ({"pt_BR": {"display_name": "a"}, "pt-BR": {"display_name": "b"}}, "repeats"),
        ({"pt_BR": None, "pt-BR": {"display_name": "b"}}, "repeats"),
    ],
)
def test_validate_rejects_mistakes(raw, message):
    with pytest.raises(ValueError, match=message):
        validate_locales_block(raw)


def test_validate_caps_the_number_of_locales():
    many = {f"a{chr(97 + i // 26)}{chr(97 + i % 26)}": {"display_name": "x"} for i in range(MAX_SKILL_LOCALES + 1)}
    assert len(many) == MAX_SKILL_LOCALES + 1
    with pytest.raises(ValueError, match="limit"):
        validate_locales_block(many)
