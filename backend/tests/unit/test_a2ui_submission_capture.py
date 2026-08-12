"""Tests for _capture_a2ui_submission — persist A2UI form submissions to session
state (v6.11.0, generalised from the preferences example)."""

from __future__ import annotations

from types import SimpleNamespace

from adk.callbacks import A2UI_FORMS_STATE_KEY, _capture_a2ui_submission


def _cc(text: str) -> SimpleNamespace:
    return SimpleNamespace(user_content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))


def test_saves_submission_context_by_action():
    state: dict = {}
    _capture_a2ui_submission(state, _cc('[a2ui:savePreferences] {"tone":["neutral"],"style":["concise"]}'))
    assert state[A2UI_FORMS_STATE_KEY] == {"savePreferences": {"tone": ["neutral"], "style": ["concise"]}}


def test_latest_submission_per_action_wins_and_accumulates_across_actions():
    state: dict = {A2UI_FORMS_STATE_KEY: {"savePreferences": {"tone": ["formal"]}}}
    _capture_a2ui_submission(state, _cc('[a2ui:savePreferences] {"tone":["neutral"]}'))
    _capture_a2ui_submission(state, _cc('[a2ui:compareConfig] {"clauses":["price"]}'))
    assert state[A2UI_FORMS_STATE_KEY] == {
        "savePreferences": {"tone": ["neutral"]},  # latest wins
        "compareConfig": {"clauses": ["price"]},  # other actions accumulate
    }


def test_ignores_ordinary_and_malformed_messages():
    for text in ["what is in the news", "[a2ui:x] not-json", "[a2ui:x] [1,2]", ""]:
        state: dict = {}
        _capture_a2ui_submission(state, _cc(text))
        assert state == {}


def test_fail_open_on_missing_content():
    state: dict = {}
    _capture_a2ui_submission(state, SimpleNamespace(user_content=None))
    assert state == {}
