"""Tests for adk/notability.py — the curated-workbench tier classifier (6.11)."""

from __future__ import annotations

import pytest

from adk import a2ui_result_render as rr
from adk.notability import ARTIFACT, INTERNAL, NOTABLE, tool_tier


@pytest.fixture
def registry_snapshot():
    """Snapshot/restore the global result→A2UI registry so a test's temporary
    registrations don't leak into (or get clobbered by) the production ones."""
    saved = rr._registry[:]
    try:
        yield
    finally:
        rr._registry[:] = saved


def _noop_transform(result, tool_context=None):
    return [{"version": "v0.9", "createSurface": {"surfaceId": "workspace", "catalogId": "c"}}]


def test_control_verbs_are_internal():
    assert tool_tier("transfer_to_agent") == INTERNAL
    assert tool_tier("request_handoff") == INTERNAL


def test_empty_or_unknown_tool_defaults_internal():
    assert tool_tier("") == INTERNAL
    assert tool_tier("some_unmapped_plumbing_tool") == INTERNAL


def test_mapped_tool_without_artifact_meta_is_notable(registry_snapshot):
    rr.register(_noop_transform, tool_names=["mytool_notable"], name="t-notable")
    assert tool_tier("mytool_notable") == NOTABLE


def test_mapped_tool_with_artifact_meta_is_artifact(registry_snapshot):
    rr.register(
        _noop_transform,
        tool_names=["mytool_artifact"],
        name="t-artifact",
        artifact_meta=lambda r: {"kind": "k", "title": "T"},
    )
    assert tool_tier("mytool_artifact") == ARTIFACT


def test_artifact_beats_notable_when_both_could_match(registry_snapshot):
    # A tool with an artifact-producing mapping classifies as artifact even
    # though is_render_payload_tool would also be true.
    rr.register(
        _noop_transform,
        tool_names=["mytool_both"],
        name="t-both",
        artifact_meta=lambda r: {"kind": "k"},
    )
    assert tool_tier("mytool_both") == ARTIFACT


def test_tool_produces_artifact_helper(registry_snapshot):
    rr.register(_noop_transform, tool_names=["plain"], name="plain")
    rr.register(_noop_transform, tool_names=["arty"], name="arty", artifact_meta=lambda r: {"kind": "k"})
    assert rr.tool_produces_artifact("arty") is True
    assert rr.tool_produces_artifact("plain") is False
    assert rr.tool_produces_artifact("unknown") is False
