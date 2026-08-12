# Skill-Driven Shell Modes — Non-Chat Surfaces

**Status**: Implemented (2026-06-13, sprint SHELL-MODES)
**Priority**: P1

> **As-built reconciliation (2026-06-13).** Two design assumptions below did
> not hold in the codebase; the implementation adapted (changes confirmed
> during the sprint):
>
> 1. **Comparison renders via the A2UI `workspace` surface, not a direct
>    `SideBySideDocViewer` mount.** `one-doc-compare`'s SKILL.md already routes
>    its output to the persistent workspace surface (`toolConfigs.a2ui`), and
>    `SideBySideDocViewer` is not wired to live React state anywhere. So
>    `DocCompareShell` and `WorkbenchShell` make the **A2UI workspace surface**
>    fill the viewport (`A2UISurfaceMount surfaceId="workspace"`) rather than
>    mounting the viewer with props. `KeyDifferencesPanel` arrives as part of
>    the agent's A2UI emission, not as a separately-mounted top bar.
> 2. **The drawer chat is a lighter shared `DrawerChatPane`, not a ChatShell
>    chat-column extraction.** `ChatShell`'s chat column is entangled with ~30
>    hooks (sidebar/doc-tabs/workbench), none of which apply when chat is a
>    secondary drawer. `DrawerChatPane` (message list + composer + session-pin +
>    bootstrap) is shared by both new shells.
> 3. **`mcp_app:` / `fixed:` workbench content sources are placeholdered** (an
>    explicit "not yet wired" tab body, not a silent blank). `a2ui:` is fully
>    wired. No production skill targets `workbench-primary` yet — it is the
>    extensibility path. Full `mcp_app` iframe wiring is designed (with options +
>    a recommendation) in
>    [v6.5.0/mcp-app-workbench-tab-source.md](../v6.5.0/mcp-app-workbench-tab-source.md);
>    implementation is gated on a real consumer.
**Estimated**: ~4 days (1 sprint, single dev; post-demo Mon 2026-06-15 → Thu 2026-06-18)
**Scope**: Fullstack (backend SkillConfig schema + frontend ShellRouter + 2 new shell components)
**Dependencies**:
- v6.2.0 2.9 multi-surface-rendering ✅
- v6.2.0 2.10 a2ui-surface-context ✅
- v6.3.0 3.2 client-tenant-management ✅
- 4.3 internal-app-shell-port (ChatShell primitives — SidebarSection, SignInRequired, Workbench, EmptyTab) — must land first; this doc consumes its components
- M3 SideBySideDocViewer + KeyDifferencesPanel + ClauseExtractionCard (shipped 2026-06-08) ✅
**Created**: 2026-06-09
**Last Updated**: 2026-06-09

## Problem Statement

Mark observed (2026-06-09): the broader pattern of "UI configured via A2UI and MCP Apps" is preserved at the surface level (A2UI emits target named surfaces; MCP Apps mount via artefact-render-hook) but the **page-level shell** is hardcoded to ChatShell. There's no way for a skill to say "I'm a doc-compare experience — the SideBySideDocViewer is my primary surface, chat is a sidebar drawer, KeyDifferencesPanel fills the top". Forks that need this today have to either:

1. Cram their primary surface into a Workbench tab inside ChatShell (chat dominates the screen, primary surface is constrained to the right pane), or
2. Write a custom `app/<custom>/page.tsx` route bypassing ChatShell entirely — losing all the sidebar / sign-in / auto-collapse / multi-surface patterns 4.3 just shipped.

Neither is right. The platform should let a skill declare *what kind of shell to render at the page level* the same way A2UI lets it declare *what surface to mount within a region*. The choice is one config field, not a fork.

**Downstream signal:** Document-comparison apps (a real fork pattern Mark wants to enable) need the doc viewer to BE the experience, not a sidekick inside chat. The current `one-doc-compare` skill in Aitana is an immediate proof point — its SideBySideDocViewer currently mounts as a Workbench tab content, but a true doc-compare workflow wants the two-pane viewer filling the canvas with chat reduced to an "ask about this clause" interaction.

**Current State:**
- `frontend/src/app/chat/[...path]/page.tsx` always renders `ChatShell` — sidebar (left) + chat (middle) + workbench (right) — for every skill regardless of skill type.
- `SkillConfig` has no field describing the desired shell shape.
- A2UI surface IDs (`workspace`, `sidebar`, `modal`) are named regions WITHIN the chat shell. There's no way to say "make the workspace surface the whole page".
- MCP App iframes can mount inside a Workbench tab but can't BE the page.
- Aitana's `one-doc-compare` skill shipped 2026-06-08 with SideBySideDocViewer inside a Workbench tab — works but constrained to ~640px on a 1080p display.

**Impact:**
- **Doc-comparison forks blocked** — every fork that wants doc-primary UX has to either accept the chat-shell constraint or bypass the platform's shell entirely
- **A2UI/MCP App protocol pattern incomplete** — the "agent drives the UI" thesis stops at the surface level; the shell shape stays hardcoded
- **Downstream template story weaker** — "fork by config" only works if the fork's primary use case fits chat-primary
- **Aitana's own one-doc-compare** is the immediate beneficiary — flipping it to doc-compare mode is the validation case

## Goals

**Primary Goal:** Ship `SkillConfig.shell` schema + `ShellRouter` page-level component + two new shell modes (`doc-compare`, `workbench-primary`) so a skill can declare its page-level shape in config. Validate against `one-doc-compare` skill end-to-end.

**Success Metrics:**
- `SkillConfig.shell.mode` field accepted by backend (`chat-primary` | `doc-compare` | `workbench-primary` | `custom`); default `chat-primary` for backwards compat
- `ShellRouter` at `app/chat/[...path]/page.tsx` selects the right shell from skill config; unknown modes fall back to `chat-primary` (graceful degradation)
- `DocCompareShell` ships: two-pane SideBySideDocViewer fills viewport (≥70%); chat as right drawer (collapsed by default, slide-out on click); KeyDifferencesPanel slides in from top when comparison completes
- `WorkbenchShell` ships: Workbench tabs fill viewport; chat as left drawer (collapsed by default); workbench tab badges + animations from 4.3 still work
- `one-doc-compare` skill flipped to `doc-compare` mode → live URL `/chat/@aitana-platform/one-doc-compare` renders DocCompareShell when authed
- All four 4.3 chat-shell primitives (SidebarSection, SignInRequired, InContextBadge, EmptyTab) reused across shells — zero duplication
- ChatShell continues to work for all other skills unchanged (backwards compat assertion in tests)
- chrome-devtools MCP snapshot of `/chat/@aitana-platform/one-doc-compare` post-deploy confirms DocCompareShell renders + no console errors

**Non-Goals:**
- **Friday 2026-06-12 ONE demo** — this sprint starts post-demo Mon 2026-06-15. ONE demo runs against ChatShell (4.3 polish). DocCompareShell flip for one-doc-compare happens after the demo lands.
- **Custom shell registration API for downstream forks** — `mode: "custom"` accepted in schema but resolves to ChatShell in this sprint. Fork-registered shells defer to v6.5.
- **Voice/Gemini Live mode shell** — separate concern, defer.
- **Mobile-specific shell layouts** — responsive breakpoints inherit from each shell's own design; no separate mobile mode.
- **Shell-mode-aware analytics dashboards** — defer.
- **Hot-swappable shell modes mid-session** — the shell is selected at page load from skill config. Switching mid-session requires page reload (acceptable v1).
- **A2UI surface routing across shells** — workspace surface in DocCompareShell mounts in a different position than in ChatShell. We document the mapping; we don't unify the layout vocabulary across shells beyond surface IDs.
- **Audit/Inspector pane port** — overlaps with 4.1 M3 (deferred). DocCompareShell + WorkbenchShell expose the same audit-chip-row slot 4.3 leaves; component port still stays in 4.1.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No latency changes. Shell selected at page load; no shell-mode resolution round-trip (skill config is fetched once with the skill). |
| 2 | EARNED TRUST | +1 | DocCompareShell makes the two-pane comparison + KeyDifferencesPanel the primary visible thing — every diff carries its block_id citation prominently, not buried inside a Workbench tab. The doc-compare workflow becomes self-evidently grounded. |
| 3 | SKILLS, NOT FEATURES | +2 | One config field (`skill.shell.mode`) selects the page layout. Adding a new shell type is a frontend component + a schema enum entry — no fork required. Skills inherit shell modes automatically; the chat-primary default keeps existing skills working. Strongest axiom hit. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing changes. |
| 5 | GRACEFUL DEGRADATION | +2 | Unknown shell modes fall back to ChatShell. Each shell handles auth via the same SignInRequired panel (4.3). Each shell falls back to a single-column chat-only layout on narrow viewports (<768px). Skill config with `shell: null` continues to render ChatShell unchanged (existing skills don't break). |
| 6 | PROTOCOL OVER CUSTOM | +2 | The shell-mode declaration is the agent skill metadata itself — extends the existing Agent Skills `SKILL.md` frontmatter, no new protocol. A2UI surface IDs continue to identify named regions; the shells map those IDs to layout positions differently. Each shell renders existing protocol events; no new event types. |
| 7 | API FIRST | +1 | `SkillConfig.shell` is one additive field on the existing `/api/skills/{id}` response. Channels (Telegram, email, CLI) ignore shell mode — they only emit text + A2UI events anyway. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Span attribute `shell.mode` added to chat-page render spans so Cloud Trace can group sessions by which shell they used. Useful for understanding which shells get traction in production. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data flows, no new trust boundaries. Auth gate is the same SignInRequired panel from 4.3. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The shell is presentation; layout choice is per-skill config that the agent or skill author already controls. Bundle delta estimated <30KB gzipped across both new shells (lazy-loaded per shell mode). |
| | **Net Score** | **+10** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Standards Compliance

- **Agent Skills spec** — `SkillConfig.shell` is additive frontmatter; backwards-compat-safe per spec (unknown frontmatter fields are preserved/ignored, not rejected).
- **A2UI surface IDs** — workspace / sidebar / modal continue to identify the same semantic regions; shells map them to layout positions. No new surface IDs invented; no surface IDs renamed.
- **AG-UI** — no event-type changes; every shell consumes the same stream.
- **MCP Apps** — iframes mount in the same artefact-render-hook regions; just at different page-level positions per shell.

## CLI Surface

Per design-doc-creator skill rule 5b-bis:

- Extend `aiplatform skill push` to validate the `shell` frontmatter section (~10 LOC change — Pydantic schema validation already handles unknown frontmatter; the CLI just needs to print the resolved shell mode on push for visibility). ~0.1d.
- No new top-level subcommands.

## Design

### Overview

Three additions to enable skill-driven page-level shells:

1. **SkillConfig schema extension** — backend Pydantic + Firestore field for `shell.mode`, with backwards-compat default `chat-primary`.
2. **`ShellRouter` page-level component** — `app/chat/[...path]/page.tsx` becomes a thin router. Reads skill config, dispatches to the right shell.
3. **Two new shell components** (`DocCompareShell`, `WorkbenchShell`) that compose the 4.3 primitives (`SidebarSection`, `SignInRequired`, `InContextBadge`, `EmptyTab`, `Workbench`) into different page-level layouts.

The existing `ChatShell` (post-4.3 polish) remains the `chat-primary` shell. No changes to its behavior.

### Skill Config Schema

```yaml
# SKILL.md frontmatter — new optional section
shell:
  mode: chat-primary | doc-compare | workbench-primary | custom

  # Chat presentation per shell mode:
  chat:
    position: column | right-drawer | left-drawer | floating | hidden
    default_state: open | minimised | hidden

  # Workbench config (optional — A2UI emissions can also declare tabs at runtime):
  workbench:
    default_tab: <tab-id>                    # id of the tab to activate on mount
    tabs:                                    # statically-declared tabs (optional)
      - id: doc-compare
        label: Compare contracts
        content_source: a2ui:workspace        # binds tab content to a named A2UI surface
        default_active: true
      - id: sources
        label: Sources
        content_source: mcp_app:gcs-browser   # binds tab content to an MCP App server
```

`content_source` is the bridge between skill-declared tabs and protocol-emitted content:
- `a2ui:<surface_id>` — tab content = whatever the agent emits to that A2UI surface
- `mcp_app:<server_id>` — tab content = MCP App iframe for that server
- `fixed:<component>` — tab content = a fixed component name resolved by the shell (rare; mostly for known-good defaults)

Backwards compatibility: when `shell` is null/missing, the platform renders `ChatShell` with the current 4.3 polish.

### Backend Changes

**`backend/skills/skill_config.py`** — extend `SkillConfig` Pydantic model:

```python
class ShellChat(BaseModel):
    position: Literal["column", "right-drawer", "left-drawer", "floating", "hidden"] = "column"
    default_state: Literal["open", "minimised", "hidden"] = "open"

class ShellWorkbenchTab(BaseModel):
    id: str
    label: str
    content_source: str   # "a2ui:<surface>" | "mcp_app:<server>" | "fixed:<component>"
    default_active: bool = False

class ShellWorkbench(BaseModel):
    default_tab: str | None = None
    tabs: list[ShellWorkbenchTab] = []

class SkillShell(BaseModel):
    mode: Literal["chat-primary", "doc-compare", "workbench-primary", "custom"] = "chat-primary"
    chat: ShellChat = ShellChat()
    workbench: ShellWorkbench | None = None

class SkillConfig(BaseModel):
    # ... existing fields ...
    shell: SkillShell | None = None        # None = chat-primary defaults
```

Schema additions are nullable + default-bearing → existing skills round-trip unchanged.

**`/api/skills/{id}` response** — gains optional `shell: { ... } | null` field. No new endpoints.

**Span attribute** — `shell.mode` added to chat-page request spans via existing tenant-context middleware.

### Frontend Changes

**New page-level router:**

```tsx
// app/chat/[...path]/page.tsx — replaces direct ChatShell render
import { ChatShell } from "@/components/chat/ChatShell";
import { DocCompareShell } from "@/components/shells/DocCompareShell";
import { WorkbenchShell } from "@/components/shells/WorkbenchShell";

function ShellRouter({ skill, ...props }: ShellRouterProps) {
  const mode = skill?.shell?.mode ?? "chat-primary";
  switch (mode) {
    case "doc-compare":      return <DocCompareShell skill={skill} {...props} />;
    case "workbench-primary": return <WorkbenchShell skill={skill} {...props} />;
    case "custom":
    case "chat-primary":
    default:                 return <ChatShell skill={skill} {...props} />;
  }
}
```

**New components:**

- `src/components/shells/DocCompareShell.tsx` (~250 LOC + 6 vitest)
  - Two-pane SideBySideDocViewer fills viewport (70/30 or full when chat drawer closed)
  - Top bar: KeyDifferencesPanel slides in when a comparison completes; collapses when none active
  - Right drawer: chat (closed by default; click to expand to 380px). Uses ChatShell's chat column components (ChatMessageList, Composer) but in drawer wrapper
  - Sidebar absent (sessions/documents accessed via drawer header or doc-tabs at top)
  - Reuses: `SignInRequired`, `InContextBadge`, `EmptyTab`, `DocTabsBar`

- `src/components/shells/WorkbenchShell.tsx` (~200 LOC + 5 vitest)
  - Workbench fills viewport with skill-declared tabs (from `skill.shell.workbench.tabs[]`) OR dynamically from A2UI surface emissions
  - Tab content sources: `a2ui:workspace` mounts `<A2UISurfaceMount surfaceId="workspace"/>`; `mcp_app:<server>` mounts the MCP App iframe; `fixed:<component>` is the v6.5+ extensibility hook
  - Left drawer: chat (collapsed by default; click to expand). Same chat-column components in drawer wrapper
  - Reuses: 4.3 SidebarSection (for Documents/Sessions inside the chat drawer), SignInRequired, EmptyTab

- `src/components/chat/ChatShell.tsx` (existing, refactored from 4.3) — extracted from `app/chat/[...path]/page.tsx` into a reusable component so all three shells can compose its primitives without forking the implementation. No behavior change for chat-primary skills.

**Shared chat-drawer wrapper:**

```tsx
// src/components/shells/ChatDrawer.tsx (~80 LOC + 3 vitest)
// Wraps a thin chat surface (header + message list + composer) into a slide-out drawer.
// Used by both DocCompareShell (right) and WorkbenchShell (left).
export function ChatDrawer({ side, defaultState, ...chatProps }: ChatDrawerProps) {
  const [open, setOpen] = useState(defaultState === "open");
  // 380px when open; 48px (tab handle) when closed
  // CSS transition; ESC closes; click outside on mobile closes
}
```

### Skill Config — one-doc-compare update

```yaml
# backend/skills/templates/one-doc-compare/SKILL.md (modify frontmatter)
shell:
  mode: doc-compare
  chat:
    position: right-drawer
    default_state: minimised
```

After this lands, `/chat/@aitana-platform/one-doc-compare` renders `DocCompareShell` instead of `ChatShell` — SideBySideDocViewer fills the screen, chat is a right drawer.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET    | /api/skills/{id} | Response gains optional `shell: SkillShell | null` field | No (additive nullable) |
| PUT    | /api/skills/{id} (admin) | Accepts optional `shell` field on write | No (additive) |

### Architecture Diagram

```
ChatPage
   │
   └── Auth gate ──► not signed in: <SignInRequired/>
        │
        └── ShellRouter (reads skill.shell.mode)
              │
              ├── mode="chat-primary" (default) → <ChatShell/>           ← 4.3 layout
              │       └── Sidebar | Chat column | Workbench
              │
              ├── mode="doc-compare" → <DocCompareShell/>
              │       └── KeyDifferencesPanel (top, slides in)
              │           SideBySideDocViewer (fills viewport)
              │           ChatDrawer (right, minimised default)
              │
              ├── mode="workbench-primary" → <WorkbenchShell/>
              │       └── ChatDrawer (left, minimised default)
              │           Workbench (fills viewport)
              │             └── tabs from skill.shell.workbench.tabs
              │                 OR dynamic from A2UI surface emissions
              │
              └── mode="custom" → <ChatShell/> (placeholder; v6.5 fork-registered)

Every shell exposes the same primitives:
  - SignInRequired panel for auth gate
  - SidebarSection (where it has a sidebar)
  - InContextBadge (where it has a composer)
  - EmptyTab (where it has Workbench tabs)
  - Workbench (where it has Workbench)

Tracing: span attribute `shell.mode` added at page render
```

## Implementation Plan

Mon 2026-06-15 → Thu 2026-06-18. Single dev. Each milestone independently revert-safe.

### M1 — Schema + ShellRouter + ChatShell extraction (Mon, ~1d)
- [ ] Extend `SkillConfig` Pydantic with `shell` field + nested models (~80 LOC backend + 6 pytest)
- [ ] Update `/api/skills/{id}` response serialization to include `shell` (~5 LOC + 1 pytest)
- [ ] Frontend `SkillShell` TypeScript type matching backend Pydantic (~30 LOC)
- [ ] Extract existing ChatShell content from `app/chat/[...path]/page.tsx` into `src/components/chat/ChatShell.tsx` (~100 LOC pure-move + 0 tests — behavior unchanged)
- [ ] New `ShellRouter` component (~40 LOC + 4 vitest covering mode dispatch + unknown-mode fallback + null-shell fallback)
- [ ] `app/chat/[...path]/page.tsx` reduces to ~200 LOC (router + auth + shell selection)
- [ ] Span attribute `shell.mode` wired via existing tenant-context middleware (~10 LOC + 1 backend test)

### M2 — DocCompareShell (Tue, ~1d)
- [ ] `src/components/shells/ChatDrawer.tsx` shared drawer wrapper (~80 LOC + 3 vitest covering open/close/keyboard ESC)
- [ ] `src/components/shells/DocCompareShell.tsx` (~250 LOC + 6 vitest)
- [ ] Reuse SideBySideDocViewer + KeyDifferencesPanel (existing) at the page level
- [ ] KeyDifferencesPanel slide-in animation when comparison completes (animation only — data already flows)
- [ ] Auth gate via SignInRequired (4.3 primitive)
- [ ] Manual: render at 1080p / 1440p / 2560p — verify SideBySideDocViewer scales

### M3 — WorkbenchShell (Wed, ~1d)
- [ ] `src/components/shells/WorkbenchShell.tsx` (~200 LOC + 5 vitest)
- [ ] Tab resolution from `skill.shell.workbench.tabs[]` → render tab content per `content_source` directive:
  - `a2ui:<surface_id>` → `<A2UISurfaceMount surfaceId={...}/>`
  - `mcp_app:<server_id>` → MCP App iframe via existing artefact-render-hook plumbing
  - `fixed:<component>` → placeholder warning (v6.5 extensibility)
- [ ] Fallback: when no tabs declared, derive from A2UI surface emissions at runtime (parity with 4.3 Workbench)
- [ ] ChatDrawer on the LEFT side (vs DocCompareShell's right) — verify drawer side prop works
- [ ] SidebarSection components inside the chat drawer for Sessions/Documents/History

### M4 — Skill update + verification (Thu, ~1d)
- [ ] Update `backend/skills/templates/one-doc-compare/SKILL.md` frontmatter with `shell.mode: doc-compare` (~5 LOC + 1 backend test asserting skill loads)
- [ ] Update sprint plan markdown (this doc → sprint plan with task-list)
- [ ] Backwards-compat regression assertion: `one-ppa-expert` + `general-assistant` + every other skill continues to render ChatShell when `shell` is null (1 vitest per skill template, ~6 tests)
- [ ] Span-attribute observability: verify `shell.mode` arrives in Cloud Trace via a sample session per shell type
- [ ] chrome-devtools MCP verification post-deploy: live URL `/chat/@aitana-platform/one-doc-compare` renders DocCompareShell; SideBySideDocViewer fills viewport; chat drawer collapsed by default; click drawer → opens; KeyDifferencesPanel slides in when comparison runs
- [ ] Update `docs/talks/ai-ui-protocol-stack.md` verification log
- [ ] `aiplatform demo verify` extension: assert shell mode round-trips for at least one skill of each shell type
- [ ] Optional: render WorkbenchShell against a temporary `workspace-demo-workbench` test skill to validate the workbench-primary path end-to-end without disturbing production skills

## Migration & Rollout

**Database Migrations:** None. `SkillConfig.shell` is additive nullable; existing Firestore skill rows round-trip unchanged.

**Feature Flags:** None. Skills opt in by setting `shell.mode` in SKILL.md frontmatter.

**Rollback Plan:**
- M1: revert ShellRouter → `app/chat/[...path]/page.tsx` returns to direct ChatShell render. Skill config keeps `shell` field but it's ignored.
- M2: revert DocCompareShell → `one-doc-compare` skill flipped back to default (mode-less) renders ChatShell with the M3 doc-compare workbench tab as before.
- M3: revert WorkbenchShell → ShellRouter falls back to ChatShell for `workbench-primary` mode. No skill currently uses this mode in production, so no user-visible regression.
- M4: revert one-doc-compare skill frontmatter → reverts to ChatShell.
- Each milestone independently revert-safe.

**Environment Variables:** None.

## Testing Strategy

### Backend Tests (pytest)
- [ ] M1: `SkillConfig.shell` Pydantic round-trips through Firestore (6 tests covering null / chat-primary / doc-compare / workbench-primary / custom / unknown-mode)
- [ ] M1: `/api/skills/{id}` response includes shell when set, omits when null (2 tests)
- [ ] M4: `one-doc-compare` skill template loads with shell config; existing skills round-trip unchanged (6 tests, one per template)

### Frontend Tests (Vitest + React Testing Library)
- [ ] M1: ShellRouter dispatch table (4 tests — chat-primary / doc-compare / workbench-primary / unknown-mode-fallback)
- [ ] M1: SkillShell TypeScript type guard (1 test)
- [ ] M2: ChatDrawer open/close/ESC/click-outside (3 tests); DocCompareShell render contract + auth gate + KeyDifferencesPanel slide-in (6 tests)
- [ ] M3: WorkbenchShell tab resolution from skill config (3 tests); fallback to A2UI emissions (1 test); left-drawer rendering (1 test)
- [ ] M4: span attribute `shell.mode` round-trips (1 backend test); SkillsBar continues to render correctly for skills of each shell type (1 test)

### Manual / E2E (verified via chrome-devtools MCP via aitana-frontend-verify skill)
- [ ] Visit `/chat/@aitana-platform/one-doc-compare` → DocCompareShell renders; SideBySideDocViewer fills viewport
- [ ] Click chat-drawer toggle → drawer expands to 380px right; ESC closes
- [ ] Visit `/chat/@aitana-platform/one-ppa-expert` (chat-primary skill) → ChatShell renders unchanged from 4.3
- [ ] Cloud Trace: filter by `shell.mode = "doc-compare"` → shows the one-doc-compare session

## Security Considerations

- **No new data flows.** All shells consume the same AG-UI / A2UI / MCP App protocol events.
- **No new trust boundaries.** Auth gate is the same SignInRequired panel from 4.3; per-skill `enabled_skills` filter (4.2 M1) still applies before the shell renders.
- **Shell-mode declaration is server-trusted.** Skills are author-controlled config; an attacker can't flip a victim's shell mode without write access to the skill template (which goes through the same admin-CRUD as everything else).
- **MCP App iframe sandboxing unchanged.** WorkbenchShell mounts the same iframe via the same artefact-render-hook with the same sandbox + CSP.

## Performance Considerations

- **Bundle-size impact:** estimated <30KB gzipped across both new shells. Shells are dynamic-imported per mode, so the chat-primary path (existing skills) doesn't pay any bundle cost.
- **No new network calls.** Shell mode is read from the already-fetched skill config.
- **Span overhead:** one extra attribute per chat-page render. Negligible.

## Success Criteria

- [ ] All backend tests passing (`cd backend && make lint && make test-fast`)
- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] `aiplatform demo verify` extended with shell-mode assertion returns green
- [ ] Live URL `/chat/@aitana-platform/one-doc-compare` renders DocCompareShell after deploy
- [ ] Live URL `/chat/@aitana-platform/one-ppa-expert` renders ChatShell unchanged
- [ ] Cloud Trace shows `shell.mode` attribute on chat-page spans
- [ ] Bundle-size CI assertion passes (<200KB initial budget unchanged)
- [ ] Documentation updated: `docs/talks/ai-ui-protocol-stack.md` verification log + this doc moves to `implemented/` post-sprint
- [ ] At least one downstream-fork dry-run (e.g. an internal sketch fork) demonstrates registering a skill with `shell.mode: doc-compare` and getting the doc-compare layout without any frontend code changes

## Open Questions

- **Q1 — `mode: custom` semantics.** v1 ships `custom` as an accepted enum value that resolves to ChatShell. What does v6.5 add? Recommended path: registry hook (`registerShell(name, component)`) at app boot so forks can plug in their own shells without forking the platform. Defer to v6.5; note in the schema.
- **Q2 — A2UI surface ID semantics across shells.** When `surface_id="workspace"` is emitted, ChatShell mounts into its workspace region (right column); DocCompareShell could mount into the doc viewer top-bar; WorkbenchShell mounts into the active tab. Do we standardise surface-to-position mapping per shell, or let each shell decide? Recommended: each shell decides, documented per shell. Tradeoff: skill authors may need to know which shell their skill targets to predict where emissions appear. Live-with-it for v1; revisit if it causes confusion.
- **Q3 — Mobile responsiveness.** DocCompareShell at <768px probably needs to collapse to single-pane vertical doc viewer + chat below. Detailed mobile layout deferred to a v6.5 sprint with proper device testing. v1: drawer becomes full-screen overlay on mobile; doc viewer fills width.
- **Q4 — Skill config validation.** Should `aiplatform skill push` reject SKILL.md files where `shell.mode` references a content_source that doesn't exist (e.g. `mcp_app:nonexistent-server`)? Recommended: warn but don't reject (loose-coupling matches MCP server soft-dependency pattern already in v6). Defer hard validation to v6.5.
- **Q5 — Voice/Gemini Live mode.** Voice interactions might want a `voice-primary` shell (chat as transcript pane, big "talk" button). Out of scope this sprint; tracked as v6.5+ candidate.

## Related Documents

- [docs/design/v6.4.0/internal-app-shell-port.md](internal-app-shell-port.md) — 4.3 ships the chat-shell primitives this doc reuses across shells. Must land first.
- [docs/design/v6.4.0/multi-tenant-demo-readiness.md](multi-tenant-demo-readiness.md) — 4.2 shipped one-doc-compare skill + SideBySideDocViewer that this doc promotes to a primary surface.
- [docs/design/v6.2.0/implemented/multi-surface-rendering.md](../v6.2.0/implemented/multi-surface-rendering.md) — surface ID foundation (workspace/sidebar/modal).
- [docs/design/v6.2.0/implemented/a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — surface state ↔ agent loop.
- [docs/design/v6.0.0/implemented/skills-data-model.md](../v6.0.0/implemented/skills-data-model.md) — SkillConfig schema being extended.
- gde-ap-agent source: `<local-path>]/page.tsx` — the AP shell that proves the multi-surface protocol pattern. This doc lifts the architectural insight (skill drives shell shape) but uses Aitana primitives.
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — verification log.
