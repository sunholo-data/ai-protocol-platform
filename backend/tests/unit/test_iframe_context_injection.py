"""Unit tests for ``adk.iframe_context`` (sprint 1.25).

Covers the pure render function (the runtime-injected
InstructionProvider is a thin wrapper around it). We assert:
  * empty state → instruction returned unchanged
  * state without mcp_app_context.* keys → unchanged
  * one mcp_app_context.* key → block appended with the structured
    content rendered as readable JSON
  * multiple keys (different servers + tools) → all appear in the
    block, sorted for deterministic output
  * key collision: state has BOTH mcp_app_context.* AND other keys →
    only the namespaced keys land in the block
"""

from __future__ import annotations

from adk.iframe_context import (
    render_instruction_with_iframe_context,
)

BASE = "You are a helpful assistant."


def test_returns_base_unchanged_when_state_is_empty():
    out = render_instruction_with_iframe_context(BASE, {})
    assert out == BASE


def test_returns_base_unchanged_when_no_namespaced_keys():
    """State has other keys but no mcp_app_context.* keys → no block
    appended (skills that don't use MCP Apps must see no overhead)."""
    state = {
        "document_ids": ["doc-1"],
        "user:preferred_locale": "en-GB",
        "app:resumed_session": True,
    }
    out = render_instruction_with_iframe_context(BASE, state)
    assert out == BASE


def test_appends_block_with_structured_content():
    state = {
        "mcp_app_context.ext-apps-map.show-map": {
            "structuredContent": {
                "viewUUID": "abc-123",
                "currentBounds": {"west": 11.4, "south": 48.0, "east": 11.7, "north": 48.2},
                "label": "Munich",
            },
            "_pushedAt": 1234567890.0,
        }
    }
    out = render_instruction_with_iframe_context(BASE, state)
    assert out.startswith(BASE)
    # The framing prose is present so the model knows this is iframe state.
    assert "Current iframe-app context" in out
    assert "NOT" in out  # security-note negative instruction present
    # The unprefixed key (server.tool) heads the section.
    assert "ext-apps-map.show-map" in out
    # The structured content's label survived the JSON pretty-print.
    assert "Munich" in out
    assert "viewUUID" in out
    assert "abc-123" in out
    # Positive usage guidance is present (model told to actively reference state).
    assert "You SHOULD reference these values" in out


def test_renders_multiple_servers_and_tools_sorted():
    state = {
        "mcp_app_context.server-z.tool-a": {"structuredContent": {"who": "z-a"}},
        "mcp_app_context.server-a.tool-b": {"structuredContent": {"who": "a-b"}},
        "mcp_app_context.server-a.tool-a": {"structuredContent": {"who": "a-a"}},
    }
    out = render_instruction_with_iframe_context(BASE, state)
    # All three appear
    assert "server-a.tool-a" in out
    assert "server-a.tool-b" in out
    assert "server-z.tool-a" in out
    # Sorted output: server-a.tool-a appears before server-a.tool-b
    # which appears before server-z.tool-a (lexicographic by server
    # then tool — gives the model a stable scan order).
    pos_aa = out.index("server-a.tool-a")
    pos_ab = out.index("server-a.tool-b")
    pos_za = out.index("server-z.tool-a")
    assert pos_aa < pos_ab < pos_za


def test_ignores_non_namespaced_keys_when_namespaced_keys_present():
    """When state has BOTH the namespace AND unrelated keys, only the
    namespaced ones land in the block — closes a soft prompt-injection
    vector where some other state key could end up in the model's
    context via this path by accident."""
    state = {
        "mcp_app_context.ext-apps-map.show-map": {"structuredContent": {"label": "Munich"}},
        "document_ids": ["doc-1"],
        "user:secret_token": "DO_NOT_LEAK",
    }
    out = render_instruction_with_iframe_context(BASE, state)
    assert "Munich" in out
    assert "doc-1" not in out
    assert "DO_NOT_LEAK" not in out


def test_handles_unserializable_value_gracefully():
    """If a state value can't be JSON-serialized, the block falls back
    to repr() rather than raising and breaking the agent's instruction
    build path entirely."""

    class WeirdValue:
        def __repr__(self) -> str:
            return "WeirdValue<sentinel>"

    state = {"mcp_app_context.foo.bar": WeirdValue()}
    out = render_instruction_with_iframe_context(BASE, state)
    assert "WeirdValue<sentinel>" in out


def test_base_instruction_is_preserved_verbatim():
    """The base instruction must appear unchanged at the top — the
    block is only appended, never inserted mid-string."""
    base_with_lines = "Line 1\n\nLine 2\n\nLine 3"
    state = {"mcp_app_context.x.y": {"structuredContent": {"a": 1}}}
    out = render_instruction_with_iframe_context(base_with_lines, state)
    assert out.startswith(base_with_lines)


def test_block_contains_positive_usage_guidance():
    """_BLOCK_TEMPLATE must include positive instructions telling the
    model to actively use the data, not just warning it to avoid
    prompt-injection confusion.

    Defensive-only framing (security note alone) causes models to treat
    the block as inert background. The positive guidance ensures the
    model references the injected state when it's relevant — regression
    for the CPH Uni AIPLA fork issue where models asked students to
    re-supply values already in context (item #29 upstream feedback).
    """
    state = {"mcp_app_context.app.state": {"structuredContent": {"x": 1}}}
    out = render_instruction_with_iframe_context(BASE, state)
    # Positive instruction must be present
    assert "You SHOULD reference these values" in out
    # Security note must still be present (defence not removed)
    assert "Security note" in out
    # Anti-pattern guidance: don't ask for values already present
    assert "Do NOT ask the user" in out
