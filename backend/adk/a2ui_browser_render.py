"""The browser pane — where the agent is, what the page looks like, what it did.

Registered on the proven 7.3 result→A2UI path, so there is no browser-specific
React anywhere: the A2UI SDK's Basic catalog already ships `Image`, and the
generic `A2uiSurface` renders it (CLAUDE.md #7).

WHY THE STEP HISTORY IS HERE AND NOT A NICE-TO-HAVE
Measured on a deployed environment, 2026-09-04: asked for a single screenshot of
a content-heavy news site, the agent made about twenty tool calls — markdown,
three clicks, four extractions and two interactive-element sweeps — working
through a cookie consent banner before it could see the page. Every one of those
was invisible. The user watched a spinner and then read one sentence. An agent
spending twenty steps on consent furniture is either working hard or stuck, and
without the history those look identical.

THE IMAGE IS A URL, NEVER BYTES
`adk.media_results` puts the screenshot in the private artefact store and
`tools/browser_artefact_routes` serves it behind the caller's token. The `Image`
here points at that route. A `data:` URI would put the bytes back on the wire
and into the surface state, undoing both — the test suite asserts it cannot.

THE TRAP IN `tool_names`
Naming a tool there ALSO marks it render-payload, i.e. never offloaded. That is
right for these small navigation results and wrong for the read tools:
`browser_extract` has no size ceiling at all, so exempting it would remove the
only bound on a large page. The read tools are deliberately absent, which costs
the pane a history row for reads — recorded as open question 7 on the design.

Design: `docs/design/v6.39.0/obscura-browser.md`, Phase 2 as amended 2026-09-04.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from adk.a2ui_result_render import BASIC_CATALOG_ID, WORKSPACE_SURFACE_ID, register

logger = logging.getLogger(__name__)

BROWSER_SURFACE = "browser"

# Small-result tools only. See "THE TRAP" above before adding to this list.
BROWSER_PANE_TOOLS = [
    "browser_navigate",
    "browser_screenshot",
    "browser_click",
    "browser_back",
    "browser_forward",
    "browser_reload",
]

_STATE_URL = "browser_pane_url"
_STATE_TITLE = "browser_pane_title"
_STATE_SHOT = "browser_pane_shot"
_STATE_PAGES = "browser_pane_pages"

# A browsing session that visits more than this is not going to be read row by
# row anyway; keep the most recent.
_MAX_PAGES = 25

# Obscura's navigate result: `Navigated to <url> — "<title>"`. The title half is
# optional — a page without one still tells us where we are.
_NAVIGATED_RE = re.compile(r"Navigated to (?P<url>\S+)(?:\s+[—-]\s+\"(?P<title>.*)\")?", re.DOTALL)


class _Tree:
    """Flattened A2UI v0.9 components with unique ids.

    Same shape as `a2ui_ppa_render._Tree` (the stated template) — copied rather
    than imported, so neither module's layout constrains the other. v0.9
    children are id strings, never inline objects.
    """

    def __init__(self) -> None:
        self.components: list[dict[str, Any]] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def text(self, text: str, *, variant: str | None = None) -> str:
        # Never variant="caption": the v0.9 React SDK renders <caption>, which is
        # only valid inside <table> and triggers a hydration warning.
        comp: dict[str, Any] = {"id": self._next_id("t"), "component": "Text", "text": text}
        if variant:
            comp["variant"] = variant
        self.components.append(comp)
        return comp["id"]

    def image(self, url: str, *, description: str, fit: str = "contain") -> str:
        cid = self._next_id("img")
        self.components.append({"id": cid, "component": "Image", "url": url, "description": description, "fit": fit})
        return cid

    def column(self, children: list[str]) -> str:
        cid = self._next_id("col")
        self.components.append({"id": cid, "component": "Column", "children": children})
        return cid

    def list_(self, children: list[str]) -> str:
        cid = self._next_id("list")
        self.components.append({"id": cid, "component": "List", "children": children})
        return cid

    def card(self, child: str) -> str:
        cid = self._next_id("card")
        self.components.append({"id": cid, "component": "Card", "child": child})
        return cid

    def divider(self) -> str:
        cid = self._next_id("div")
        self.components.append({"id": cid, "component": "Divider", "axis": "horizontal"})
        return cid

    def root(self, children: list[str]) -> None:
        self.components.append({"id": "root", "component": "Column", "children": children})

    def messages(self) -> list[dict[str, Any]]:
        # Built against the placeholder surface; the registry retargets it to
        # `surface=` on the way out.
        return [
            {"version": "v0.9", "createSurface": {"surfaceId": WORKSPACE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
            {"version": "v0.9", "updateComponents": {"surfaceId": WORKSPACE_SURFACE_ID, "components": self.components}},
        ]


def _mcp_text(typed_result: Any) -> str | None:
    """The first text block of an MCP CallToolResult dump, if this is one."""
    if not isinstance(typed_result, dict):
        return None
    content = typed_result.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text") or ""
    return None


def _as_json(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _state_of(tool_context: Any) -> Any:
    """Session state, or None. Fail-open: no state just means no accumulation."""
    return getattr(tool_context, "state", None) if tool_context is not None else None


def _record(state: Any, key: str, value: Any) -> None:
    if state is None:
        return
    try:
        state[key] = value
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("browser pane: could not record %s (%s)", key, exc)


def _get(state: Any, key: str, default: Any = None) -> Any:
    if state is None:
        return default
    try:
        return state.get(key, default)
    except Exception:  # pragma: no cover - defensive
        return default


def browser_activity_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """Draw the browser pane from this call plus what the session already knows."""
    if isinstance(typed_result, dict) and typed_result.get("error"):
        # The model narrates a tool error in chat; a surface would double it.
        return None

    text = _mcp_text(typed_result)
    if text is None:
        return None

    state = _state_of(tool_context)
    tool_name = _current_tool_name(typed_result, text)

    url = _get(state, _STATE_URL)
    title = _get(state, _STATE_TITLE)
    shot = _get(state, _STATE_SHOT)
    notice: str | None = None

    payload = _as_json(text)
    if payload is not None and payload.get("error"):
        # M1's failure envelope — say so rather than showing nothing.
        notice = str(payload["error"])
    elif payload is not None and payload.get("url"):
        shot = {"url": payload["url"], "width": payload.get("width"), "height": payload.get("height")}
        _record(state, _STATE_SHOT, shot)
    else:
        match = _NAVIGATED_RE.search(text)
        if match:
            url = match.group("url")
            title = match.group("title") or title
            _record(state, _STATE_URL, url)
            _record(state, _STATE_TITLE, title)

    # Only NAVIGATIONS are recorded. The workbench already has an Activity tab
    # that lists every tool call, and the first version of this pane simply
    # repeated it — same information, second place, and the user said so. What
    # the Activity tab cannot show is the JOURNEY: which pages this browser has
    # actually been on, in order, with their titles. That is browser-specific
    # and it is what someone looking at a browser pane wants to know.
    pages = list(_get(state, _STATE_PAGES, []) or [])
    if tool_name == "browser_navigate" and url:
        entry = {"url": url, "title": title or ""}
        if not pages or pages[-1].get("url") != url:
            pages.append(entry)
            pages = pages[-_MAX_PAGES:]
            _record(state, _STATE_PAGES, pages)

    return _draw(url=url, title=title, shot=shot, pages=pages, notice=notice)


def _current_tool_name(typed_result: Any, text: str) -> str:
    """Best-effort label for the step row.

    The transform signature does not carry the tool name, so it is inferred from
    the result's shape. Wrong-but-harmless: a mislabelled history row is a
    cosmetic bug, and guessing beats an empty history.
    """
    payload = _as_json(text)
    if payload is not None and (payload.get("url") or payload.get("error")):
        return "browser_screenshot"
    if _NAVIGATED_RE.search(text):
        return "browser_navigate"
    if text.startswith("Clicked"):
        return "browser_click"
    return "browser"


def _host_of(url: str) -> str:
    """`https://www.example.com/a/b?c` -> `example.com`. Falls back to the raw url."""
    try:
        host = urlparse(url).hostname or url
        return host[4:] if host.startswith("www.") else host
    except Exception:  # pragma: no cover - defensive
        return url


def _humanise(tool_name: str, text: str) -> str:
    """A step row a person can read.

    The first version of this pane listed raw tool names and truncated result
    text — `browser_navigate — Navigated to https://... — "Example Domain"`.
    That is developer output in a user-facing surface, and it read (correctly)
    as unfinished. Same information, said plainly.
    """
    flat = " ".join(text.split())
    if tool_name == "browser_navigate":
        match = _NAVIGATED_RE.search(text)
        if match:
            title = (match.group("title") or "").strip()
            host = _host_of(match.group("url"))
            return f"Opened {host}" + (f" — {title}" if title else "")
        return "Opened a page"
    if tool_name == "browser_screenshot":
        return "Took a screenshot"
    if tool_name == "browser_click":
        target = flat[len("Clicked") :].strip().strip("'\"") if flat.startswith("Clicked") else ""
        return f"Clicked {target}" if target else "Clicked an element"
    if tool_name in ("browser_back", "browser_forward", "browser_reload"):
        return {"browser_back": "Went back", "browser_forward": "Went forward", "browser_reload": "Reloaded"}[tool_name]
    return flat[:90] if flat else tool_name


def _draw(
    *,
    url: str | None,
    title: str | None,
    shot: dict | None,
    pages: list[dict],
    notice: str | None,
) -> list[dict[str, Any]]:
    tree = _Tree()
    children: list[str] = []

    # Where we are.
    header: list[str] = [tree.text(title or "Browser", variant="h2")]
    if url:
        header.append(tree.text(url, variant="body"))
    children.append(tree.card(tree.column(header)))

    # What it looks like — or WHY there is nothing to look at. An empty slot
    # here is what the user reads as "broken", so the absence has to speak
    # (CLAUDE.md #8): a screenshot that was never taken and one that failed are
    # different facts and neither is silence.
    if notice:
        children.append(
            tree.card(tree.column([tree.text("Screenshot", variant="h3"), tree.text(notice, variant="body")]))
        )
    elif shot and shot.get("url"):
        caption = f"Screenshot of {_host_of(url) if url else 'the current page'}"
        if shot.get("width") and shot.get("height"):
            caption += f" ({shot['width']}x{shot['height']})"
        # The Image's `description` is the caption (and its alt text). Rendering
        # a second Text with the same string showed it TWICE — once as alt while
        # the picture loaded, once below it. Seen on test, 2026-09-04.
        children.append(tree.card(tree.image(shot["url"], description=caption)))
    else:
        children.append(
            tree.card(
                tree.column(
                    [
                        tree.text("Screenshot", variant="h3"),
                        tree.text("No screenshot taken yet on this page.", variant="body"),
                    ]
                )
            )
        )

    # Where it has been. Deliberately NOT a tool-call log — the Activity tab
    # owns that, and duplicating it was the first version's mistake.
    if len(pages) > 1:
        # No Divider: the cards above already separate the sections, and the
        # SDK's divider is a 1px div that the flex column stretches into a grey
        # block (measured 22px in the browser). One less thing to fight.
        children.append(tree.text("Pages visited", variant="h3"))
        rows = []
        for page in pages:
            label = page.get("title") or _host_of(page.get("url", ""))
            host = _host_of(page.get("url", ""))
            rows.append(tree.text(f"{label} — {host}" if label != host else host, variant="body"))
        children.append(tree.list_(rows))

    tree.root(children)
    return tree.messages()


def _artifact_meta(typed_result: Any) -> dict[str, str]:
    return {
        "kind": "browser",
        "title": "Browser",
        "description": "What the browser is looking at, and how it got there",
    }


register(
    browser_activity_to_a2ui,
    tool_names=BROWSER_PANE_TOOLS,
    name="browser_activity",
    surface=BROWSER_SURFACE,
    artifact_meta=_artifact_meta,
)

__all__ = ["BROWSER_PANE_TOOLS", "BROWSER_SURFACE", "browser_activity_to_a2ui"]
