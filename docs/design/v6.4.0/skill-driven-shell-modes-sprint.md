# Sprint Plan — Skill-Driven Shell Modes (SHELL-MODES)

**Design doc:** [skill-driven-shell-modes.md](skill-driven-shell-modes.md)
**Sprint key:** `SHELL-MODES`
**Window:** Mon 2026-06-15 → Thu 2026-06-18 (4 days, single dev)
**Scope:** Fullstack — backend `SkillConfig.shell` schema + frontend `ShellRouter` + 2 new shell components
**Status:** Planned (created 2026-06-13)

## Goal

Let a skill declare its **page-level shell shape** in config (`SkillConfig.shell.mode`) the same way A2UI lets it declare which surface to mount within a region. Ship `chat-primary` (default, unchanged), `doc-compare`, and `workbench-primary` shells behind a `ShellRouter`. Validate by flipping `one-doc-compare` to `doc-compare` so the SideBySideDocViewer fills the canvas with chat as a drawer.

## Velocity basis

Last 7 days: 82 commits, +25.5k LOC (solo). The ~1000-LOC / 4-milestone estimate is comfortably within recent throughput; the timeline risk is **not** volume, it's the M1 refactor (below).

## Critical-path risk — read before starting

The design doc assumed `app/chat/[...path]/page.tsx` was ~627 LOC. **It is now 1102 LOC** — this week's `chat-polish` commits added significantly to it. M1's "extract ChatShell into a reusable component" is therefore a larger, behavior-preserving refactor of the page that **every** skill renders. Mitigations baked into M1:

- Extract first as a **pure move** (no behavior change), keeping all hooks/handlers wired identically.
- M1 does **not** ship until existing chat flows render unchanged: full frontend test suite green + a manual smoke of `one-ppa-expert` (chat-primary) confirming no regression.
- **Hard checkpoint after M1** before building M2/M3 on top of the extracted shell.

If the extraction proves hairy, the fallback is to wrap (not move) — `ChatShell` becomes a thin component that renders the existing page body — buying the router seam without the full untangle. Note that in the plan as the M1 escape hatch.

## Milestones

| M | Title | Scope | Est | Depends |
|---|-------|-------|-----|---------|
| M1 | Schema + ChatShell extraction + ShellRouter | fullstack | ~1.5d | — |
| M2 | DocCompareShell + ChatDrawer | frontend | ~1d | M1 |
| M3 | WorkbenchShell | frontend | ~1d | M1 |
| M4 | one-doc-compare flip + verification | fullstack | ~0.5d | M2 (M3 for full coverage) |

M2 and M3 are independent once M1 lands and could parallelize; default is M2 → M3 (M2 is the validated path that M4 demos).

### M1 — Schema + ShellRouter + ChatShell extraction (Mon, ~1.5d)
**Backend**
- `SkillConfig.shell` Pydantic: `SkillShell` (`mode` Literal, default `chat-primary`) + `ShellChat` + `ShellWorkbench` + `ShellWorkbenchTab`, nullable + default-bearing.
- `/api/skills/{id}` serialization includes `shell` when set, omits/nulls otherwise.
- Span attribute `shell.mode` via existing tenant-context middleware.

**Frontend**
- `SkillShell` TS type in `frontend/src/types/skill.ts` mirroring Pydantic.
- Extract ChatShell body from `page.tsx` (1102 LOC) into `src/components/chat/ChatShell.tsx` — behavior-preserving.
- New `src/components/shells/ShellRouter.tsx` — dispatch on `skill.shell.mode`; unknown/`custom`/null → ChatShell.
- `page.tsx` reduces to auth gate + ShellRouter.

**Acceptance**
- `SkillConfig.shell` round-trips Firestore + `/api/skills/{id}` (6 pytest: null/chat-primary/doc-compare/workbench-primary/custom/unknown-mode).
- ShellRouter dispatch table (4 vitest incl. unknown-mode + null-shell fallback).
- **Backwards-compat: full frontend suite + existing chat tests pass unchanged; `one-ppa-expert` renders ChatShell identically (manual smoke).**
- `shell.mode` span attribute present (1 backend test).
- `cd frontend && npm run quality:check` + `cd backend && make lint && make test-fast` green.

### M2 — DocCompareShell + ChatDrawer (Tue, ~1d)
- `src/components/shells/ChatDrawer.tsx` (~80 LOC) — slide-out drawer, `side` prop (left/right), open/close, ESC, 380px open / 48px handle.
- `src/components/shells/DocCompareShell.tsx` (~250 LOC) — SideBySideDocViewer fills ≥70% viewport; KeyDifferencesPanel slides in on completion; chat as right drawer (minimised default); reuses SignInRequired, InContextBadge, EmptyTab, DocTabsBar.
- **Acceptance:** ChatDrawer open/close/ESC (3 vitest); DocCompareShell render + auth gate + KeyDiff slide-in (6 vitest); manual render at 1080p/1440p/2560p; quality:check green.

### M3 — WorkbenchShell (Wed, ~1d)
- `src/components/shells/WorkbenchShell.tsx` (~200 LOC) — Workbench fills viewport; tab resolution from `skill.shell.workbench.tabs[]` by `content_source` (`a2ui:<id>` → A2UISurfaceMount; `mcp_app:<id>` → iframe via artefact-render-hook; `fixed:<c>` → v6.5 placeholder warning); fallback to A2UI surface emissions when no tabs declared; ChatDrawer on the **left**.
- **Acceptance:** tab resolution from config (3 vitest); A2UI-emission fallback (1 vitest); left-drawer render (1 vitest); quality:check green.

### M4 — one-doc-compare flip + verification (Thu, ~0.5d)
- `backend/skills/templates/one-doc-compare/SKILL.md` frontmatter: `shell.mode: doc-compare`, chat right-drawer/minimised (+1 backend test asserting skill loads).
- Backwards-compat regression: every other skill template still renders ChatShell when `shell` null (≤6 vitest).
- chrome-devtools MCP (via `aitana-frontend-verify`): live `/chat/@aitana-platform/one-doc-compare` → DocCompareShell, viewer fills viewport, drawer collapsed→opens on click, KeyDiff slides in; `/chat/@aitana-platform/one-ppa-expert` → ChatShell unchanged.
- Cloud Trace shows `shell.mode` on chat-page spans.
- Move design doc to `implemented/`; update `docs/talks/ai-ui-protocol-stack.md` verification log.

## Out of scope (per design doc non-goals)
`mode: custom` resolves to ChatShell (v6.5 registry hook); voice-primary shell; bespoke mobile layouts (v1: drawer → full-screen overlay <768px); hard `content_source` validation in `aiplatform skill push` (warn-only).

## Quality gates
- Per milestone: `cd frontend && npm run quality:check:fast` (M1 also `make test-fast` backend).
- Sprint close: `cd frontend && npm run quality:check` + `cd backend && make lint && make test-fast` + chrome-devtools live verification.
