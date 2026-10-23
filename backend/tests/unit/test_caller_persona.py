"""Caller personas: a machine caller's own voice, appended only for that caller."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from adk import caller_persona as cp


@pytest.fixture(autouse=True)
def _clean():
    cp.clear_cache()
    yield
    cp.clear_cache()


def _settings(monkeypatch, personas):
    monkeypatch.setattr(cp, "load_settings", lambda: {"personas": personas})


def _ctx(uid: str):
    return SimpleNamespace(_invocation_context=SimpleNamespace(user_id=uid), state={})


def _run(provider, uid):
    return asyncio.run(provider(_ctx(uid)))


def test_persona_applies_only_to_its_caller(monkeypatch, tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("---\ntype: soul\n---\n# SOUL\n## How I speak\nPlainly.\n")
    _settings(monkeypatch, {"machine:daneel": {"name": "Daneel", "file": str(soul)}})
    provider = cp.wrap_with_caller_persona("Answer from the docs.")
    out = _run(provider, "machine:daneel")
    assert out.startswith("Answer from the docs.") and "## Who is replying" in out
    assert "as Daneel" in out and "## How I speak\nPlainly." in out and "type: soul" not in out
    assert _run(provider, "mark") == "Answer from the docs."
    assert _run(provider, "") == "Answer from the docs."


def test_file_edits_apply_on_the_next_turn(monkeypatch, tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text("first voice")
    _settings(monkeypatch, {"machine:x": {"name": "X", "file": str(soul)}})
    assert "first voice" in cp.persona_for("machine:x")[1]
    soul.write_text("second voice")
    later = time.time() + 5
    os.utime(soul, (later, later))
    assert "second voice" in cp.persona_for("machine:x")[1]


def test_unreadable_or_odd_personas_fall_back_to_the_skill_voice(monkeypatch, tmp_path, caplog):
    secret = tmp_path / "secrets.env"
    secret.write_text("KEY=1")
    empty = tmp_path / "EMPTY.md"
    empty.write_text("---\na: 1\n---\n   ")
    _settings(
        monkeypatch,
        {
            "missing": {"file": str(tmp_path / "nope.md")},
            "notmd": {"file": str(secret)},
            "empty": {"file": str(empty)},
            "inline": {"name": "Inline", "text": "I speak briefly."},
        },
    )
    assert cp.persona_for("missing") is None and cp.persona_for("notmd") is None and cp.persona_for("empty") is None
    assert "unreadable" in caplog.text and "empty" in caplog.text
    assert cp.persona_for("inline") == ("Inline", "I speak briefly.")


def test_persona_is_bounded(monkeypatch):
    _settings(monkeypatch, {"m": {"text": "x" * (cp.MAX_CHARS + 500)}})
    name, text = cp.persona_for("m")
    assert name == "m" and text.endswith("[cut to fit]") and len(text) < cp.MAX_CHARS + 20


def test_agent_chain_includes_the_persona_wrapper():
    import inspect

    from adk import agent

    assert "wrap_with_caller_persona," in inspect.getsource(agent.create_agent)
