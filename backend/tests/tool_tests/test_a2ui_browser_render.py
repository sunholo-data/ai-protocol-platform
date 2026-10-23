"""The browser pane — where the agent is, what it looks like, what it did.

Two things this file is really pinning.

**The screenshot never becomes a data: URI.** The whole point of M1/M2 is that
page imagery lives behind an authenticated route; an `Image` carrying inline
bytes would put it straight back on the wire and into the surface state.

**The offload exemption must not leak.** `tool_names` on a mapping also marks
those tools render-payload, i.e. never offloaded. That is right for the small
navigation results and wrong for the read tools — `browser_markdown` is already
bounded by the gateway's own ceiling, and `browser_extract` has no ceiling at
all, so exempting them would remove the only bound on a large page.
"""

from __future__ import annotations

import json

import pytest

import adk.a2ui_browser_render  # noqa: F401 — registers the mapping
from adk.a2ui_result_render import is_render_payload_tool, render_for
from adk.media_results import ARTEFACT_URL_PREFIX

_SHOT_ID = "browser_screenshot_e-abc_deadbeef12.png"


class _State(dict):
    """Session state that behaves like ADK's — a dict is enough here."""


class _Ctx:
    def __init__(self) -> None:
        self.state = _State()


def _mcp_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _navigate(url: str = "https://example.com/", title: str = "Example Domain") -> dict:
    return _mcp_text(f'Navigated to {url} — "{title}"')


def _screenshot(artefact_id: str = _SHOT_ID) -> dict:
    return _mcp_text(
        json.dumps(
            {
                "artefact_id": artefact_id,
                "url": f"{ARTEFACT_URL_PREFIX}/{artefact_id}",
                "mime": "image/png",
                "width": 1280,
                "height": 720,
                "bytes": 148213,
            }
        )
    )


def _components(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages or []:
        upd = m.get("updateComponents")
        if upd:
            out.extend(upd.get("components", []))
    return out


def _of_type(messages: list[dict], component: str) -> list[dict]:
    return [c for c in _components(messages) if c.get("component") == component]


def _all_text(messages: list[dict]) -> str:
    return " ".join(c.get("text", "") for c in _of_type(messages, "Text"))


# --- what the pane shows ---


def test_navigate_puts_the_url_and_title_on_the_surface():
    msgs = render_for("browser_navigate", _navigate(), _Ctx())
    text = _all_text(msgs)
    assert "https://example.com/" in text
    assert "Example Domain" in text


def test_screenshot_renders_an_image_at_the_authenticated_route():
    msgs = render_for("browser_screenshot", _screenshot(), _Ctx())
    images = _of_type(msgs, "Image")
    assert len(images) == 1
    assert images[0]["url"] == f"{ARTEFACT_URL_PREFIX}/{_SHOT_ID}"


def test_the_image_is_never_inline_bytes():
    """A data: URI would undo M1 and M2 in one line."""
    msgs = render_for("browser_screenshot", _screenshot(), _Ctx())
    rendered = json.dumps(msgs)
    assert "data:image" not in rendered
    assert "base64" not in rendered
    assert "storage.googleapis.com" not in rendered, "never a bucket URL either"


def test_the_pane_accumulates_across_calls():
    ctx = _Ctx()
    render_for("browser_navigate", _navigate(), ctx)
    msgs = render_for("browser_screenshot", _screenshot(), ctx)

    # The screenshot call must still show where we are — the surface is
    # replaced wholesale each time, so it has to redraw the URL too.
    assert "https://example.com/" in _all_text(msgs)
    assert _of_type(msgs, "Image")


def test_the_pane_shows_the_JOURNEY_not_a_tool_call_log():
    """The Activity tab already lists every tool call.

    The first version of this pane repeated it — same information, second
    place — and the reviewer said so on sight. What the Activity tab cannot
    show is which pages the browser has actually been on, in order, which is
    the thing someone opens a browser pane to see.
    """
    ctx = _Ctx()
    render_for("browser_navigate", _navigate("https://example.com/", "Example Domain"), ctx)
    render_for("browser_click", _mcp_text("Clicked 'button.accept'"), ctx)
    msgs = render_for("browser_navigate", _navigate("https://other.test/x", "Other Site"), ctx)

    text = _all_text(msgs)
    assert "Example Domain" in text
    assert "Other Site" in text
    assert "Pages visited" in text
    # A click is a tool call, not a page. It belongs in the Activity tab.
    assert "Clicked" not in text
    assert "browser_navigate" not in text, "raw tool names must not reach the surface"


def test_one_page_needs_no_journey_list():
    """A list of one is noise — the header already says where we are."""
    ctx = _Ctx()
    msgs = render_for("browser_navigate", _navigate(), ctx)
    assert "Pages visited" not in _all_text(msgs)


def test_revisiting_the_same_page_does_not_repeat_it():
    ctx = _Ctx()
    render_for("browser_navigate", _navigate(), ctx)
    render_for("browser_navigate", _navigate(), ctx)
    pages = ctx.state.get("browser_pane_pages") or []
    assert len(pages) == 1


def test_a_page_with_no_screenshot_says_so():
    """An empty slot reads as broken; the absence has to speak (CLAUDE.md #8).

    The user's report was "no screenshot was there either" — for a run where the
    screenshot genuinely never happened. The pane was right and said nothing,
    which is indistinguishable from being wrong.
    """
    msgs = render_for("browser_navigate", _navigate(), _Ctx())
    assert not _of_type(msgs, "Image")
    assert "No screenshot taken yet" in _all_text(msgs)


def test_the_pane_is_built_from_cards_not_bare_text():
    msgs = render_for("browser_screenshot", _screenshot(), _Ctx())
    assert _of_type(msgs, "Card"), "sections need structure, not a flat text dump"


def test_the_step_history_is_bounded():
    """Twenty steps on one consent banner is a real trace, not a hypothetical."""
    ctx = _Ctx()
    for i in range(120):
        render_for("browser_click", _mcp_text(f"Clicked 'e{i}'"), ctx)
    msgs = render_for("browser_screenshot", _screenshot(), ctx)

    pages = ctx.state.get("browser_pane_pages") or []
    assert len(pages) <= 25, "the journey must not grow without bound"
    assert _all_text(msgs), "still renders after the cap"


# --- the paths that must not render, or must render a notice ---


def test_a_failed_screenshot_renders_a_visible_notice_not_an_image():
    """M1's failure envelope. Never silent (CLAUDE.md #8)."""
    failed = _mcp_text(json.dumps({"error": "browser_screenshot captured an image but it could not be stored."}))
    msgs = render_for("browser_screenshot", failed, _Ctx())

    assert not _of_type(msgs, "Image"), "there is no image to show"
    assert "could not be stored" in _all_text(msgs)


def test_an_error_result_does_not_draw_a_surface():
    assert render_for("browser_navigate", {"error": "boom"}, _Ctx()) is None


def test_an_unparseable_result_declines_rather_than_half_rendering():
    assert render_for("browser_navigate", "not a dict", _Ctx()) is None


def test_a_transform_with_no_context_still_renders():
    """The CLI preview path passes no tool_context."""
    msgs = render_for("browser_screenshot", _screenshot(), None)
    assert _of_type(msgs, "Image")


# --- the registration, and the trap ---


@pytest.mark.parametrize("tool", ["browser_navigate", "browser_screenshot", "browser_click"])
def test_navigation_tools_are_render_payload_and_so_never_offloaded(tool: str):
    assert is_render_payload_tool(tool) is True


@pytest.mark.parametrize("tool", ["browser_markdown", "browser_extract", "browser_network_requests"])
def test_read_tools_are_NOT_exempted_from_the_offload(tool: str):
    """The trap. Naming these in tool_names would remove their only bound."""
    assert is_render_payload_tool(tool) is False


def test_the_mapping_is_registered_by_importing_the_composition_root():
    """The classic silent no-op, pinned.

    Every test above imports `adk.a2ui_browser_render` directly, so they would
    all pass with the module never imported in production — the mapping would
    never register, the pane would never appear, and nothing would say so.

    Run in a subprocess deliberately: the honest question is "what does a fresh
    process that imports the composition root end up with", and asking it
    in-process would mean clearing a registry the rest of the suite shares.
    """
    import subprocess
    import sys

    probe = (
        "import adk.agent;"
        "from adk.a2ui_result_render import registered_mapping_names;"
        "print('browser_activity' in registered_mapping_names())"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.stdout.strip().endswith("True"), (
        "adk/agent.py must import adk.a2ui_browser_render for its side effect. "
        f"stdout={out.stdout!r} stderr={out.stderr[-400:]!r}"
    )
