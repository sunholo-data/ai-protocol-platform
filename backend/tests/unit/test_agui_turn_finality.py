"""Turn-finality guard for `stream_agui_events` (bucket-C audit, v6.17.0).

Two invariants that were previously UNtested (test_agui.py covers only mounting):

- **Never-silent (CLAUDE.md #8):** a RUN_FINISHED carrying no visible output — a
  turn that emitted only reasoning — is rewritten to a visible, retryable
  RUN_ERROR (code EMPTY_RUN). A silent dead turn is a bug.
- **The load-bearing exemption:** a run whose only output is an A2UI_SURFACE (a
  Model-B workbench render with NO chat text) IS a real result and must pass
  through as RUN_FINISHED. Without this, every Model-B render turn would be
  mis-flagged empty and 404'd as an error. Confirm cards (which emit
  TOOL_CALL_START + A2UI_SURFACE) are covered by the same output accounting.

Part of `make adk-conformance`. See docs/design/v6.17.0/adk-contract-checklist.md.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adk.agui import stream_agui_events

pytestmark = pytest.mark.adk_contract


class _Ev:
    """A synthetic AG-UI event: `.type` (str is fine — the stream reads
    `getattr(type, "value", str(type))`), optional `.name` for CUSTOM, and a
    `model_dump` the stream serializes with."""

    def __init__(self, type_value: str, name: str | None = None):
        self.type = type_value
        self.name = name

    def model_dump(self, **_kwargs):
        d: dict = {"type": self.type}
        if self.name is not None:
            d["name"] = self.name
        return d


def _agent_yielding(events: list[_Ev]):
    class _Agent:
        def run(self, _run_input):
            async def _gen():
                for e in events:
                    yield e

            return _gen()

    return _Agent()


async def _collect(events: list[_Ev]) -> list[dict]:
    agent = _agent_yielding(events)
    # heartbeat_seconds=0 disables the silence-tick path — deterministic.
    return [ev async for ev in stream_agui_events(agent, SimpleNamespace(thread_id="t"), heartbeat_seconds=0)]


async def test_empty_run_becomes_a_visible_run_error():
    out = await _collect([_Ev("RUN_STARTED"), _Ev("RUN_FINISHED")])
    types = [e.get("type") for e in out]
    assert "RUN_ERROR" in types, "an output-less RUN_FINISHED must be rewritten to a visible RUN_ERROR"
    assert "RUN_FINISHED" not in types
    err = next(e for e in out if e.get("type") == "RUN_ERROR")
    assert err.get("code") == "EMPTY_RUN"


async def test_a2ui_surface_only_run_is_not_empty():
    # The load-bearing exemption: a workbench render with no chat text is a real result.
    out = await _collect([_Ev("RUN_STARTED"), _Ev("CUSTOM", name="A2UI_SURFACE"), _Ev("RUN_FINISHED")])
    types = [e.get("type") for e in out]
    assert "RUN_FINISHED" in types, "an A2UI-surface-only run must pass through, not become an empty-run RUN_ERROR"
    assert "RUN_ERROR" not in types
