"""Tests for the SSE confidentiality invariant (v6.19.0, AIPLA #39).

The load-bearing cases here are the NEGATIVE ones. A suite that only proves
"an allowlisted result passes" would pass against the pre-fix code, which
passed *everything*. Each test below is written so it fails without the filter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from adk import a2ui_result_render
from adk.stream_invariants import (
    ALWAYS_CLIENT_VISIBLE,
    REDACTED_CONTENT,
    is_client_visible_tool,
    redact_privileged_results,
    session_is_lower_trust,
)

SECRET = '{"answer_key": "B", "rubric": "award 2 marks for method"}'


async def _aiter(events: list[dict]) -> AsyncIterator[dict]:
    for event in events:
        yield event


async def _collect(events: list[dict], *, lower_trust: bool) -> list[dict]:
    return [e async for e in redact_privileged_results(_aiter(events), lower_trust=lower_trust)]


def _start(call_id: str, name: str) -> dict:
    return {"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": name}


def _result(call_id: str, content: str = SECRET) -> dict:
    return {"type": "TOOL_CALL_RESULT", "toolCallId": call_id, "content": content, "role": "tool"}


class TestTrustDerivation:
    def test_anonymous_group_is_lower_trust(self):
        assert session_is_lower_trust("anonymous_group_id", "grp-1") is True

    def test_group_id_alone_is_lower_trust(self):
        """Defensive: a group_id without the matching auth_mode still means shared."""
        assert session_is_lower_trust("firebase", "grp-1") is True

    @pytest.mark.parametrize("mode", ["firebase", "identity_platform", "local_mode_stub"])
    def test_identified_owners_are_full_trust(self, mode):
        assert session_is_lower_trust(mode, "") is False


class TestRedaction:
    @pytest.mark.asyncio
    async def test_privileged_result_is_withheld_from_a_group_session(self):
        """The reported bug: a judging tool's answer key reaching a student."""
        events = [_start("c1", "grade_submission"), _result("c1")]

        out = await _collect(events, lower_trust=True)

        assert out[1]["content"] == REDACTED_CONTENT
        assert "answer_key" not in out[1]["content"]

    @pytest.mark.asyncio
    async def test_same_result_passes_for_an_owner_session(self):
        """Redaction must not degrade the trusted path."""
        events = [_start("c1", "grade_submission"), _result("c1")]

        out = await _collect(events, lower_trust=False)

        assert out[1]["content"] == SECRET

    @pytest.mark.asyncio
    async def test_unmatched_tool_call_id_fails_closed(self):
        """No TOOL_CALL_START pairing => we cannot know the tool => withhold.

        AG-UI result events carry no tool name, so an unpairable result is
        indistinguishable from a privileged one. Guessing 'probably fine' here
        is how a filter silently stops filtering.
        """
        out = await _collect([_result("orphan")], lower_trust=True)

        assert out[0]["content"] == REDACTED_CONTENT

    @pytest.mark.asyncio
    async def test_a2ui_tool_result_passes_so_surfaces_still_render(self):
        """Regression guard: redacting this breaks A2UI rendering outright."""
        name = next(iter(ALWAYS_CLIENT_VISIBLE))
        events = [_start("c1", name), _result("c1", '{"surface": "x"}')]

        out = await _collect(events, lower_trust=True)

        assert out[1]["content"] == '{"surface": "x"}'

    @pytest.mark.asyncio
    async def test_registered_render_payload_tool_passes(self, monkeypatch):
        """A tool feeding a registered result->A2UI mapping is client-visible."""
        monkeypatch.setattr(
            a2ui_result_render,
            "is_render_payload_tool",
            lambda name: name == "compare_documents",
        )
        # Re-import target: the module imported the symbol directly.
        monkeypatch.setattr(
            "adk.stream_invariants.is_render_payload_tool",
            lambda name: name == "compare_documents",
        )
        events = [_start("c1", "compare_documents"), _result("c1", '{"rows": []}')]

        out = await _collect(events, lower_trust=True)

        assert out[1]["content"] == '{"rows": []}'

    @pytest.mark.asyncio
    async def test_offloaded_artifact_pointer_is_also_withheld(self):
        """>50K results are replaced by a pointer upstream — redact that too.

        The pointer names an artifact the lower-trust caller should not fetch;
        leaking it just moves the leak one hop.
        """
        pointer = '{"artifact": "gs://bucket/priv.json", "note": "offloaded"}'
        events = [_start("c1", "grade_submission"), _result("c1", pointer)]

        out = await _collect(events, lower_trust=True)

        assert "gs://" not in out[0 + 1]["content"]

    @pytest.mark.asyncio
    async def test_non_tool_events_pass_untouched(self):
        """Text, custom and terminal events must be unaffected."""
        events = [
            {"type": "RUN_STARTED"},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"},
            {"type": "CUSTOM", "name": "A2UI_SURFACE", "value": {"k": 1}},
            {"type": "RUN_FINISHED"},
        ]

        out = await _collect(events, lower_trust=True)

        assert out == events

    @pytest.mark.asyncio
    async def test_redaction_preserves_event_shape(self):
        """Drop-vs-redact: the chip must still show the tool RAN (NEVER SILENT)."""
        events = [_start("c1", "grade_submission"), _result("c1")]

        out = await _collect(events, lower_trust=True)

        assert out[1]["type"] == "TOOL_CALL_RESULT"
        assert out[1]["toolCallId"] == "c1"
        assert out[1]["role"] == "tool"
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_owner_path_yields_identical_objects(self):
        """Full-trust path is pass-through — no copying, no mutation."""
        events = [_start("c1", "grade_submission"), _result("c1")]

        out = await _collect(events, lower_trust=False)

        assert out[0] is events[0]
        assert out[1] is events[1]

    @pytest.mark.asyncio
    async def test_original_event_is_not_mutated(self):
        """Redaction copies; the upstream dict stays intact for other consumers."""
        result = _result("c1")
        events = [_start("c1", "grade_submission"), result]

        await _collect(events, lower_trust=True)

        assert result["content"] == SECRET

    @pytest.mark.asyncio
    async def test_multiple_calls_are_tracked_independently(self):
        name = next(iter(ALWAYS_CLIENT_VISIBLE))
        events = [
            _start("c1", "grade_submission"),
            _start("c2", name),
            _result("c1"),
            _result("c2", '{"surface": "ok"}'),
        ]

        out = await _collect(events, lower_trust=True)

        assert out[2]["content"] == REDACTED_CONTENT
        assert out[3]["content"] == '{"surface": "ok"}'

    @pytest.mark.asyncio
    async def test_malformed_start_does_not_poison_the_map(self):
        """A START missing its name must not make a later result look allowlisted."""
        events = [
            {"type": "TOOL_CALL_START", "toolCallId": "c1"},  # no toolCallName
            _result("c1"),
        ]

        out = await _collect(events, lower_trust=True)

        assert out[1]["content"] == REDACTED_CONTENT


class TestAllowlist:
    def test_unknown_tool_is_privileged_by_default(self):
        """Deny-by-default is the whole point — a new tool must not auto-publish."""
        assert is_client_visible_tool("some_brand_new_tool") is False

    def test_a2ui_emit_tool_is_visible(self):
        assert is_client_visible_tool("send_a2ui_json_to_client") is True
