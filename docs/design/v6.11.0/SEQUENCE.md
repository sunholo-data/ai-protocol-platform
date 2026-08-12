# v6.11.0 Build Sequence — Workbench Home & Curated Activity

**Gate:** 2026-07-16 live testing of the ONE front door showed the Workbench
under-delivers: a web-research turn produces useful output (news + sources) but
the Workspace stays empty, the Activity tab shows raw plumbing tool calls
(`transfer_to_agent` ×2), and "what are the sources?" can't be answered from the
UI. User direction (2026-07-16): *"make the workbench more useful — a curated,
user-friendly version of the activity for useful information (sources, formatted
tool outputs), and the Workspace should be the index page for users to reach the
tabs as needed."*

**Theme:** *Curate, don't dump.* The backend owns a **notability tier**
(`internal` / `notable` / `artifact`); the **Workspace becomes "Home"** — a
curated digest ribbon (sources, formatted outputs, handoffs) over a broadened
index of every open surface — while the **Activity tab stays the full debug
feed**. Everything rides existing protocols (A2UI surfaces + AG-UI CUSTOM
events + the SurfaceRegistry artifact model); no new format, no bespoke per-tool
React. Builds directly on the shipped search-sources grounding work.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 11.1 | [workbench-home-and-curated-activity.md](workbench-home-and-curated-activity.md) | **P1** | ~4d | 7.3 ✅, 7.5 ✅, 7.1 ✅, search-sources ✅ | Backend notability tier + `emit_digest_item` (`surfaceId="digest"`); sources as a first-class digest item from grounding chunks; Workspace→Home (digest ribbon + broadened index, threshold ≥1); Activity collapses `internal` tier; `aitana session digest` CLI. Net axiom **+8**. Flagged `NEXT_PUBLIC_ENABLE_WORKBENCH_HOME`. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 11.1 | [workbench-home-and-curated-activity.md](workbench-home-and-curated-activity.md) | Planned 2026-07-16 (P1 — ONE UX polish) |

## What ships in v6.11.0

- **11.1** — the Workspace is a curated **Home**: a friendly digest of the
  useful things the assistant did (a **Sources** card on every citable answer,
  formatted tool outputs, "Delegated to X" chips) over a one-click index of
  every open surface (Results, Document, sources). The **Activity** tab stays the
  complete, time-ordered feed with `internal` plumbing (`transfer_to_agent`)
  collapsed. Notability is decided backend-side, so the curated view is available
  to every channel and the `/sessions/{id}/activity` API. Ships behind
  `NEXT_PUBLIC_ENABLE_WORKBENCH_HOME`, verified in a real browser on dev/test.

## Dependency Graph

```
7.1 skill-delegation (Activity panel) ─┐
7.3 tool-results-as-a2ui ──────────────┤
7.5 workbench-artifacts-model ─────────┼─► 11.1 workbench-home-and-curated-activity
search-sources (grounding Sources:) ───┘
```
