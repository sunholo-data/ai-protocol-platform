"""Model-B guard — an agent must never hand-author A2UI into chat text.

2026-07-17, deployed dev: the Contract Expert (a Model-B skill, a2ui.enabled:
false) skipped `extract_ppa_clauses`, fetched raw content, and printed a v0.9
createSurface/updateComponents blob straight into the chat. Its instructions
already said "do NOT author any UI" — the model ignored them. Disabling the
toolset stops the agent CALLING an A2UI tool but not TYPING the JSON, so the
guard lives at the after_model boundary.
"""

from __future__ import annotations

import pytest

from adk.callbacks import make_authored_a2ui_stripper, strip_authored_a2ui

_BLOB = """Okay, I have the content. I will now extract the key clauses and present them in a table.

[
  {"version": "v0.9", "createSurface": {"surfaceId": "ppa_clauses_table"}},
  {"version": "v0.9", "updateComponents": {"surfaceId": "ppa_clauses_table", "components": []}}
]"""


def test_strips_authored_a2ui_but_keeps_the_prose():
    cleaned, stripped = strip_authored_a2ui(_BLOB)
    assert stripped is True
    assert "createSurface" not in cleaned
    assert "v0.9" not in cleaned
    assert cleaned.startswith("Okay, I have the content")


def test_strips_fenced_a2ui():
    text = '```json\n[{"version": "v0.9", "updateDataModel": {"surfaceId": "x", "value": {}}}]\n```'
    cleaned, stripped = strip_authored_a2ui(text)
    assert stripped is True
    assert "updateDataModel" not in cleaned


def test_ordinary_json_is_untouched():
    """A user asking for JSON must still get it — only A2UI wire shapes go."""
    text = 'Here is the data: [{"price": 42.5, "zone": "DK1"}]'
    cleaned, stripped = strip_authored_a2ui(text)
    assert stripped is False
    assert cleaned == text


def test_plain_prose_untouched():
    text = "The Google LEAP PPA is a 137-page agreement. I extracted the clauses."
    assert strip_authored_a2ui(text) == (text, False)


class _Part:
    def __init__(self, text: str) -> None:
        self.text = text


class _Content:
    def __init__(self, parts: list[_Part]) -> None:
        self.parts = parts


class _Resp:
    def __init__(self, parts: list[_Part]) -> None:
        self.content = _Content(parts)


@pytest.mark.asyncio
async def test_callback_mutates_response_parts_in_place():
    part = _Part(_BLOB)
    resp = _Resp([part])
    await make_authored_a2ui_stripper()(object(), resp)
    assert "createSurface" not in part.text
    assert part.text.startswith("Okay, I have the content")


@pytest.mark.asyncio
async def test_callback_tolerates_empty_and_textless_responses():
    """Tool-call-only turns have parts with no text — must not explode."""
    await make_authored_a2ui_stripper()(object(), _Resp([_Part("")]))
    await make_authored_a2ui_stripper()(object(), _Resp([]))
