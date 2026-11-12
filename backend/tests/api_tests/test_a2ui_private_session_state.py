"""Private A2UI surfaces never reach the session state an ordinary turn persists.

The browser registry already withholds ``private:`` surfaces; the stream
endpoint repeats the filter for stale clients. These tests drive a real turn
through ``/stream`` and read the state ADK receives, so dropping the
server-side filter in ``process_skill_request`` fails here rather than only
at prompt time (elicitation and inbox rendering read the state directly).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from tests.api_tests.test_shared_threads import (  # noqa: F401 — fixtures: app, and the override cleanup
    DANEEL,
    _agui_body,
    _clear_overrides,
    _client_as,
    _thread,
    app,
)
from tests.api_tests.test_stream_skill import _fake_event_stream, _make_skill

PUBLIC = {"catalogId": "workbench", "dataModel": {"title": "Inbox"}}
PRIVATE = {"catalogId": "workbench", "dataModel": {"detail": "PRIVATE_REVIEW_CANARY"}}


def _turn(app, surfaces: dict) -> dict:  # noqa: F811
    captured: dict = {}

    async def _capture(input_data):
        captured["state"] = dict(input_data.state or {})
        async for e in _fake_event_stream(input_data):
            yield e

    body = _agui_body("thanks")
    body["forwardedProps"] = {"a2ui_surface_state": surfaces}
    with (
        patch("skills.skill_processor.get_skill", return_value=_make_skill(access_type="public")),
        patch("db.chat_sessions.get_session_index", return_value=_thread()),
        patch("tools.ailang_messages.notify", AsyncMock(return_value=True)),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capture),
    ):
        resp = _client_as(app, DANEEL).post("/api/skill/test-skill-id/stream", json=body)
    assert resp.status_code == 200, resp.text
    return captured["state"]


def test_private_surfaces_are_dropped_from_turn_state(app):  # noqa: F811
    state = _turn(app, {"workspace": PUBLIC, "private:review-1": PRIVATE})
    assert state["a2ui_surface_state"] == {"workspace": PUBLIC}
    assert "PRIVATE_REVIEW_CANARY" not in json.dumps(state)


def test_only_private_surfaces_replace_the_previous_snapshot_with_empty(app):  # noqa: F811
    state = _turn(app, {"private:review-1": PRIVATE})
    assert state["a2ui_surface_state"] == {}
