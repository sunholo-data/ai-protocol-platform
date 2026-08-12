"""Pydantic round-trip tests for SkillConfig.shell (v6.4.0 SHELL-MODES M1).

Covers null / chat-primary / doc-compare / workbench-primary / custom /
unknown-mode so the page-level shell declaration stays backwards-compatible
with skills written before the `shell` field existed, while rejecting
invalid mode values at the write boundary.

See docs/design/v6.4.0/skill-driven-shell-modes.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from db.models import (
    ShellChat,
    ShellWorkbench,
    ShellWorkbenchTab,
    SkillConfig,
    SkillShell,
)


def test_skill_config_round_trip_with_no_shell() -> None:
    """Legacy skills written before the shell field round-trip with `shell=None`.
    Validates additive-nullable backwards-compat — the chat-primary default is
    applied at render, not forced onto the stored document."""
    cfg = SkillConfig(name="legacy-skill", description="A legacy skill predating the shell field")
    assert cfg.shell is None

    dumped = cfg.model_dump(by_alias=True)
    restored = SkillConfig.model_validate(dumped)
    assert restored.shell is None


def test_shell_defaults_to_chat_primary() -> None:
    """An opted-in but unspecified shell block defaults to chat-primary with a
    column chat in the open state — i.e. the existing ChatShell behavior."""
    shell = SkillShell()
    assert shell.mode == "chat-primary"
    assert shell.chat.position == "column"
    assert shell.chat.default_state == "open"
    assert shell.workbench is None


def test_skill_config_round_trip_chat_primary() -> None:
    cfg = SkillConfig(
        name="chat-primary-skill",
        description="Explicit chat-primary shell",
        shell=SkillShell(mode="chat-primary"),
    )
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["shell"]["mode"] == "chat-primary"
    restored = SkillConfig.model_validate(dumped)
    assert restored.shell is not None
    assert restored.shell.mode == "chat-primary"


def test_skill_config_round_trip_doc_compare() -> None:
    """doc-compare with chat as a minimised right drawer — the one-doc-compare shape."""
    cfg = SkillConfig(
        name="doc-compare-skill",
        description="Side-by-side document comparison experience",
        shell=SkillShell(
            mode="doc-compare",
            chat=ShellChat(position="right-drawer", default_state="minimised"),
        ),
    )
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["shell"]["mode"] == "doc-compare"
    assert dumped["shell"]["chat"]["position"] == "right-drawer"
    assert dumped["shell"]["chat"]["defaultState"] == "minimised"

    restored = SkillConfig.model_validate(dumped)
    assert restored.shell is not None
    assert restored.shell.mode == "doc-compare"
    assert restored.shell.chat.position == "right-drawer"
    assert restored.shell.chat.default_state == "minimised"


def test_skill_config_round_trip_workbench_primary() -> None:
    """workbench-primary with statically-declared tabs bound to protocol surfaces."""
    cfg = SkillConfig(
        name="workbench-primary-skill",
        description="Workbench-led experience with declared tabs",
        shell=SkillShell(
            mode="workbench-primary",
            chat=ShellChat(position="left-drawer", default_state="minimised"),
            workbench=ShellWorkbench(
                default_tab="compare",
                tabs=[
                    ShellWorkbenchTab(
                        id="compare",
                        label="Compare contracts",
                        content_source="a2ui:workspace",
                        default_active=True,
                    ),
                    ShellWorkbenchTab(
                        id="sources",
                        label="Sources",
                        content_source="mcp_app:gcs-browser",
                    ),
                ],
            ),
        ),
    )
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["shell"]["mode"] == "workbench-primary"
    assert dumped["shell"]["workbench"]["defaultTab"] == "compare"
    assert len(dumped["shell"]["workbench"]["tabs"]) == 2
    assert dumped["shell"]["workbench"]["tabs"][0]["contentSource"] == "a2ui:workspace"
    assert dumped["shell"]["workbench"]["tabs"][0]["defaultActive"] is True

    restored = SkillConfig.model_validate(dumped)
    assert restored.shell is not None
    assert restored.shell.workbench is not None
    assert restored.shell.workbench.default_tab == "compare"
    assert restored.shell.workbench.tabs[1].content_source == "mcp_app:gcs-browser"
    assert restored.shell.workbench.tabs[1].default_active is False


def test_skill_config_custom_mode_accepted() -> None:
    """`custom` is an accepted enum value (v1 resolves it to ChatShell on the
    frontend; the registry hook is a v6.5 follow-up)."""
    cfg = SkillConfig(
        name="custom-shell-skill",
        description="A skill declaring the custom shell mode",
        shell=SkillShell(mode="custom"),
    )
    restored = SkillConfig.model_validate(cfg.model_dump(by_alias=True))
    assert restored.shell is not None
    assert restored.shell.mode == "custom"


def test_skill_config_unknown_mode_rejected() -> None:
    """Invalid shell modes are rejected at the write boundary. The frontend
    ShellRouter still defends against unknown values for forward-compat, but
    the backend never persists an out-of-enum mode."""
    with pytest.raises(ValidationError):
        SkillShell(mode="holographic")  # type: ignore[arg-type]


def test_shell_camel_case_aliases() -> None:
    """Aliases round-trip via JSON in camelCase, matching the rest of the
    SkillConfig surface so the frontend always sees one shape."""
    shell = SkillShell(
        mode="doc-compare",
        chat=ShellChat(position="right-drawer", default_state="minimised"),
        workbench=ShellWorkbench(
            default_tab="t1",
            tabs=[ShellWorkbenchTab(id="t1", label="Tab 1", content_source="a2ui:workspace", default_active=True)],
        ),
    )
    dumped = shell.model_dump(by_alias=True)
    assert dumped["chat"]["defaultState"] == "minimised"
    assert dumped["workbench"]["defaultTab"] == "t1"
    assert dumped["workbench"]["tabs"][0]["defaultActive"] is True

    restored = SkillShell.model_validate(dumped)
    assert restored.chat.default_state == "minimised"
    assert restored.workbench is not None
    assert restored.workbench.tabs[0].default_active is True


# === /api/skills serialization (SkillResponse) ===


def test_skill_response_includes_shell_when_set() -> None:
    """The API response carries the shell block when a skill declares one."""
    from skills.routes import SkillResponse

    cfg = SkillConfig(
        name="doc-compare-skill",
        description="doc-compare experience",
        shell=SkillShell(mode="doc-compare", chat=ShellChat(position="right-drawer", default_state="minimised")),
    )
    resp = SkillResponse.from_config(cfg)
    assert resp.shell is not None
    assert resp.shell["mode"] == "doc-compare"
    assert resp.shell["chat"]["position"] == "right-drawer"


def test_skill_response_omits_shell_when_null() -> None:
    """Legacy skills serialize with shell=None — the frontend falls back to ChatShell."""
    from skills.routes import SkillResponse

    cfg = SkillConfig(name="legacy-skill", description="no shell block")
    resp = SkillResponse.from_config(cfg)
    assert resp.shell is None


# === Span attribute (Cloud Trace grouping) ===


def test_record_shell_mode_sets_span_attribute() -> None:
    """record_shell_mode tags the active span; doc-compare skill -> 'doc-compare',
    no-shell skill -> 'chat-primary'."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from skills.skill_processor import record_shell_mode

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    doc_skill = SkillConfig(name="dc", description="d", shell=SkillShell(mode="doc-compare"))
    with tracer.start_as_current_span("req"):
        mode = record_shell_mode(doc_skill)
    assert mode == "doc-compare"
    spans = exporter.get_finished_spans()
    assert spans[-1].attributes is not None
    assert spans[-1].attributes.get("shell.mode") == "doc-compare"

    exporter.clear()
    legacy = SkillConfig(name="legacy", description="d")
    with tracer.start_as_current_span("req2"):
        mode = record_shell_mode(legacy)
    assert mode == "chat-primary"
    assert exporter.get_finished_spans()[-1].attributes.get("shell.mode") == "chat-primary"
