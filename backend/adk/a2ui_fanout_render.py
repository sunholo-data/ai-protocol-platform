"""The fan-out board — a live A2UI workbench tab for parallel skill fan-out.

FANOUT M2. See docs/design/v6.31.0/parallel-skill-fanout.md.

## Why this is NOT a registered result→A2UI mapping

`backend/adk/CLAUDE.md`'s golden rule is "don't invent, register a mapping in
`a2ui_result_render`". That rule governs **results**: a transform from a typed
tool result to a surface, emitted once when the tool returns.

A fan-out board is **progress**. It is drawn before any result exists, updated
each time a branch starts or finishes, and is finished by the time the parent's
own turn ends — so there is no typed result to map and no single moment to map
it at. It therefore emits directly through `tracker.emit_a2ui_surface`, which is
the SAME wire path a mapping ends up on (`A2UI_SURFACE` CUSTOM →
`WorkspaceA2uiEventRouter` → `SurfaceRegistry`), with the same Basic catalog and
no bespoke React. This is a narrow, deliberate exception for progress surfaces.

## What makes it work (measured 2026-09-07, not assumed)

1. The emitter is composed into the **PARENT's** tool callbacks. Trap 1 in
   `backend/adk/CLAUDE.md` — never emit from inside an `AgentTool` sub-agent's
   own callbacks, where the tracker ContextVar is unbound — still holds, and is
   not violated here: the parent's callbacks run in the request's own context.
   `asyncio.create_task` copies that context, so the binding survives into the
   tasks ADK gathers for the parallel calls (0 NULL-tracker hits across 6
   emissions from 3 concurrent branches).
2. `stream_agui_events` drains queued surface events on a 1s timer
   (`AGUI_DRAIN_SECONDS`), independent of ADK's event cadence — so a branch that
   finishes mid-fan-out reaches the wire immediately, rather than waiting for
   ADK's next event. Without that (fixed 2026-09-06, 92757af6) the whole board
   would arrive in one burst at the end and the streaming would be a lie.

## Catalog note

A2UI v0.9 **Basic** has no `Table` and no `Accordion` — a table is a `Column` of
`Row`s. Per `agent-protocols/references/a2ui-v0.9-basic-catalog.md`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID

# Status → the glyph shown on the branch card. Text rather than `Icon`, because
# the Basic catalog's Icon names are renderer-specific and a missing name
# renders as nothing — an invisible status is worse than a plain character.
_GLYPH = {"running": "⏳", "done": "✅", "failed": "⚠️", "timeout": "⚠️"}


@dataclass
class Branch:
    """One fan-out branch: an objective, its state, and how long it took."""

    objective: str
    status: str = "running"  # running | done | failed | timeout
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    error: str = ""

    @property
    def elapsed(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    def detail(self) -> str:
        """The line under the objective. Always says something — a card with a
        blank subtitle reads as broken rather than as in-progress."""
        secs = f"{self.elapsed:.1f}s"
        if self.status == "running":
            return f"running · {secs}"
        if self.status == "done":
            return f"done in {secs}"
        if self.status == "timeout":
            return f"timed out after {secs}"
        # A failure MUST carry its reason: a merge over 2 of 3 branches reads as
        # a complete answer, so the missing third has to be visible (rule #8).
        return f"failed after {secs} — {self.error or 'no reason given'}"


def summary_line(branches: list[Branch]) -> str:
    """`3 branches · 2 done · 1 running · 12.1s` — the header's one-glance state."""
    if not branches:
        # The PLANNING state, and it is not cosmetic. Measured live on dev
        # 2026-09-07: the parent spent 27s of a 92s turn deciding the
        # decomposition before it issued a single branch call. Without a board
        # drawn at turn start, that is 27 seconds in which the workbench tab
        # does not exist — a blank pane while the agent is plainly working,
        # which is the rule-#8 violation this whole milestone exists to remove.
        return "planning the research…"
    done = sum(1 for b in branches if b.status == "done")
    running = sum(1 for b in branches if b.status == "running")
    failed = sum(1 for b in branches if b.status in ("failed", "timeout"))
    parts = [f"{len(branches)} branch{'es' if len(branches) != 1 else ''}"]
    if done:
        parts.append(f"{done} done")
    if running:
        parts.append(f"{running} running")
    if failed:
        parts.append(f"{failed} failed")
    span = max(b.elapsed for b in branches)
    parts.append(f"{span:.1f}s")
    return " · ".join(parts)


def board_components(branches: list[Branch], *, merged: str = "") -> list[dict[str, Any]]:
    """Flatten the board to A2UI v0.9 components.

    v0.9 composes by ID REFERENCE — `children`/`child` are id strings, never
    nested objects — and the tree root must be `id: "root"`.
    """
    comps: list[dict[str, Any]] = []
    children: list[str] = []

    comps.append({"id": "title", "component": "Text", "text": "Research fan-out", "variant": "h2"})
    comps.append({"id": "summary", "component": "Text", "text": summary_line(branches)})
    comps.append({"id": "head", "component": "Column", "children": ["title", "summary"]})
    children.append("head")
    comps.append({"id": "rule-1", "component": "Divider"})
    children.append("rule-1")

    card_ids: list[str] = []
    for i, b in enumerate(branches):
        gid, oid, did = f"g-{i}", f"o-{i}", f"d-{i}"
        comps.append({"id": gid, "component": "Text", "text": _GLYPH.get(b.status, "•")})
        comps.append({"id": oid, "component": "Text", "text": b.objective})
        comps.append({"id": did, "component": "Text", "text": b.detail()})
        comps.append({"id": f"body-{i}", "component": "Column", "children": [oid, did]})
        comps.append({"id": f"row-{i}", "component": "Row", "children": [gid, f"body-{i}"]})
        comps.append({"id": f"card-{i}", "component": "Card", "child": f"row-{i}"})
        card_ids.append(f"card-{i}")

    comps.append({"id": "branches", "component": "List", "children": card_ids})
    children.append("branches")

    # The merged brief only appears once it exists. An empty "Merged brief"
    # heading sitting above nothing for twelve seconds reads as a failure.
    if merged:
        comps.append({"id": "rule-2", "component": "Divider"})
        comps.append({"id": "merged-h", "component": "Text", "text": "Merged brief", "variant": "h3"})
        comps.append({"id": "merged", "component": "Text", "text": merged})
        children.extend(["rule-2", "merged-h", "merged"])

    comps.append({"id": "root", "component": "Column", "children": children})
    return comps


def board_messages(surface_id: str, branches: list[Branch], *, merged: str = "") -> list[dict[str, Any]]:
    """The A2UI messages for one board update.

    `createSurface` is repeated on every update on purpose: the frontend
    `SurfaceRegistry.appendMessages` auto-synthesises it and is idempotent, so
    an update that arrives first (a reconnect, a re-delivery) still renders
    rather than landing on a surface that does not exist yet.
    """
    return [
        {"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": board_components(branches, merged=merged),
            },
        },
    ]
