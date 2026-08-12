"""TTFT instrumentation — end-to-end SSE-level assertions.

Exercises ``POST /api/skill/{skill_id}/stream`` with the agent's event
stream mocked, and verifies:

  1. The structured ``event="ttft"`` log line is emitted on every request
     in full/log modes — and never in off mode.
  2. STAGE_PROGRESS Custom events are interleaved into the SSE stream and
     arrive *before* the first TEXT_MESSAGE_CONTENT (so the chat UI can
     display a stage label before any model token lands).
  3. STAGE_PROGRESS labels are conditional on real backend events:
     no docs loaded → no "Reading documents…" label flashes.
  4. ``?probe=1`` causes a final LATENCY_REPORT event to ride at the
     end of the stream; without it, no LATENCY_REPORT appears.
  5. ``AITANA_TTFT_MODE=off`` produces zero observable instrumentation
     overhead in the wire stream — the SSE shape is identical to before
     this sprint.

The mode flips are done via monkeypatch on the live ``observability.timing``
module rather than reloading. Reloading ``fast_api_app`` re-mounts the
FastMCP server on the same lifespan, which deadlocks the TestClient
context. The flag check inside ``mark()`` is the one we want to exercise
anyway — flipping the constants in place tests the actual production path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from ag_ui.core import (
    EventType,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from fastapi import Request
from fastapi.testclient import TestClient

import observability.timing as timing_module
from auth import User, build_access_context, get_current_user
from db.models import SkillConfig, SkillMetadata

# --- Helpers (parallel to test_stream_skill.py) ---


def _make_skill(skill_id: str = "ttft-skill") -> SkillConfig:
    return SkillConfig(
        name="ttft-skill",
        description="Under TTFT test.",
        instructions="Be helpful.",
        skillId=skill_id,
        ownerId="someone-else",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl={"type": "public"},
    )


def _make_user() -> User:
    return User(uid="caller-uid", email="caller@yourcompany.com", domain="yourcompany.com")


async def _fake_event_stream(input_data) -> AsyncGenerator:
    """Minimal AG-UI event sequence that triggers TEXT_MESSAGE_CONTENT
    (so ``first_model_token`` gets marked).

    Patching ``ADKAgent.run`` bypasses the real ADK callbacks
    (``_composed_before_agent``, ``_document_injector``) where the
    labelled marks normally fire. We simulate the ``Thinking…`` mark
    here so the test exercises the STAGE_PROGRESS interleave path —
    same effect as what ``make_document_injector`` produces in
    production. Off-mode short-circuits inside ``mark`` so this is
    correctly silent in the off-mode test.
    """
    from observability.timing import STAGE_BEFORE_MODEL_DONE, get_current_tracker

    get_current_tracker().mark(STAGE_BEFORE_MODEL_DONE, user_label="Thinking…")

    thread_id = input_data.thread_id
    run_id = input_data.run_id
    yield RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id)
    yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id="m1", role="assistant")
    yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Hi.")
    yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")
    yield RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Flip ``observability.timing`` mode in place. Avoids reloading
    fast_api_app, which would re-enter the MCP lifespan and deadlock."""
    monkeypatch.setattr(timing_module, "TTFT_MODE", mode, raising=True)
    monkeypatch.setattr(timing_module, "_ENABLED", mode != "off", raising=True)
    monkeypatch.setattr(timing_module, "_FULL", mode == "full", raising=True)


@pytest.fixture(scope="module")
def app():
    # Single shared app — TestClient is constructed without entering its
    # context manager so no lifespan startup runs (MCP session_manager
    # would otherwise try to start twice across tests).
    import fast_api_app as module

    return module.app


@pytest.fixture()
def client(app):
    async def _override(request: Request) -> User:
        user = _make_user()
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _parse_sse(text: str) -> list[dict]:
    frames = [line for line in text.splitlines() if line.startswith("data:")]
    return [json.loads(line[len("data:") :].strip()) for line in frames]


# --- Tests ---


def test_ttft_log_line_emitted_on_full_mode(client, caplog, monkeypatch):
    """In full mode, every ``/stream`` call writes one ``event="ttft"`` log."""
    _set_mode(monkeypatch, "full")
    skill = _make_skill()
    caplog.set_level(logging.INFO, logger="observability.timing")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/ttft-skill/stream", json={"message": "hi"})

    assert resp.status_code == 200, resp.text
    ttft_records = [r for r in caplog.records if r.name == "observability.timing" and "ttft" in r.getMessage()]
    assert len(ttft_records) >= 1, "expected at least one ttft log line on full mode"


def test_off_mode_emits_no_ttft_log_or_stage_progress(client, caplog, monkeypatch):
    """Kill switch contract: off mode adds nothing to the wire and writes
    no structured log line. M5 baseline depends on this being a true no-op."""
    _set_mode(monkeypatch, "off")
    skill = _make_skill()
    caplog.set_level(logging.INFO, logger="observability.timing")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/ttft-skill/stream", json={"message": "hi"})

    assert resp.status_code == 200, resp.text

    # No ttft log line.
    ttft_records = [r for r in caplog.records if r.name == "observability.timing" and "ttft" in r.getMessage()]
    assert ttft_records == [], "off mode must not emit ttft log line"

    # No STAGE_PROGRESS or LATENCY_REPORT events on the wire.
    events = _parse_sse(resp.text)
    custom_events = [e for e in events if e.get("type") == "CUSTOM"]
    assert custom_events == [], f"off mode must not interleave any Custom events; got {custom_events!r}"


def test_stage_progress_silent_when_no_docs_attached(client, monkeypatch):
    """If the request attaches no documents, we must NOT flash a
    "Reading 0 documents…" label. The label is conditional on real
    loader work."""
    _set_mode(monkeypatch, "full")
    skill = _make_skill()
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/ttft-skill/stream", json={"message": "hi"})

    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    stage_progress_labels = [
        e["value"]["label"] for e in events if e.get("type") == "CUSTOM" and e.get("name") == "STAGE_PROGRESS"
    ]
    # "Thinking…" is allowed (always fires before model). "Reading …" must NOT appear.
    assert not any("Reading" in lbl for lbl in stage_progress_labels), (
        f"unexpected Reading label without docs attached: {stage_progress_labels!r}"
    )


def test_latency_report_emitted_only_with_probe_param(client, monkeypatch):
    _set_mode(monkeypatch, "full")
    skill = _make_skill()
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        # No probe param → no LATENCY_REPORT.
        resp_plain = client.post("/api/skill/ttft-skill/stream", json={"message": "hi"})
        events_plain = _parse_sse(resp_plain.text)
        report_plain = [e for e in events_plain if e.get("name") == "LATENCY_REPORT"]
        assert report_plain == [], "LATENCY_REPORT must not leak when probe param is unset"

        # ?probe=1 → exactly one LATENCY_REPORT at end of stream.
        resp_probe = client.post("/api/skill/ttft-skill/stream?probe=1", json={"message": "hi"})

    events_probe = _parse_sse(resp_probe.text)
    reports = [e for e in events_probe if e.get("name") == "LATENCY_REPORT"]
    assert len(reports) == 1, f"expected exactly one LATENCY_REPORT, got {len(reports)}"
    payload = reports[0]["value"]
    # Payload must include the keys the CLI pretty-printer relies on.
    for key in (
        "skill_id",
        "session_id",
        "user_id",
        "model_used",
        "routing_choice",
        "tools_invoked_count",
        "ttft_mode",
    ):
        assert key in payload, f"LATENCY_REPORT payload missing {key!r}: {payload!r}"


def test_latency_report_includes_optimization_m1_marks(client, monkeypatch):
    """TTFT-OPTIMIZATION M1: agent_factory_done and runner_setup_done
    marks must appear in the LATENCY_REPORT event so `aiplatform skill
    probe --json` can attribute the 5.7s before-agent gap. If either is
    missing, M2's candidate selection has no data to work with."""
    _set_mode(monkeypatch, "full")
    skill = _make_skill()
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/ttft-skill/stream?probe=1", json={"message": "hi"})

    events = _parse_sse(resp.text)
    reports = [e for e in events if e.get("name") == "LATENCY_REPORT"]
    assert len(reports) == 1
    payload = reports[0]["value"]
    # The agent_factory_done mark fires from skill_processor unconditionally
    # in full mode. runner_setup_done is fired by _composed_before_agent in
    # production but is bypassed in this test (ADKAgent.run is patched), so
    # we don't assert its presence here — but we DO assert the constants
    # are at least known to the report machinery via a separate unit test.
    assert "agent_factory_done_ms" in payload, f"M1 mark missing from LATENCY_REPORT: {sorted(payload)!r}"


def test_stage_progress_arrives_before_first_text_content(client, monkeypatch):
    """The whole point: stage labels reach the wire before any model
    token. If this regresses, the perceived-snappiness UX is dead."""
    _set_mode(monkeypatch, "full")
    skill = _make_skill()
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/ttft-skill/stream", json={"message": "hi"})

    events = _parse_sse(resp.text)
    types_in_order = [(e.get("type"), e.get("name")) for e in events]

    # Find indices of the first STAGE_PROGRESS and the first TEXT_MESSAGE_CONTENT.
    first_stage_idx = next(
        (i for i, (t, n) in enumerate(types_in_order) if t == "CUSTOM" and n == "STAGE_PROGRESS"),
        None,
    )
    first_content_idx = next(
        (i for i, (t, _n) in enumerate(types_in_order) if t == "TEXT_MESSAGE_CONTENT"),
        None,
    )
    assert first_stage_idx is not None, "expected at least one STAGE_PROGRESS event in full mode"
    assert first_content_idx is not None, "expected TEXT_MESSAGE_CONTENT in mocked stream"
    assert first_stage_idx < first_content_idx, (
        f"STAGE_PROGRESS at idx {first_stage_idx} must precede first TEXT_MESSAGE_CONTENT at idx {first_content_idx}"
    )
