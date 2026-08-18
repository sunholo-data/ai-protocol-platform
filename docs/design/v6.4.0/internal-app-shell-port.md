# Internal App Shell — Port chat layout iterations from gde-ap-agent

**Status**: Planned
**Priority**: P1
**Estimated**: ~3 days (1 sprint, single dev; runs in parallel with 4.2 M4 CLI work)
**Scope**: Frontend (heavy) + Backend (none — protocol surfaces all exist)
**Dependencies**:
- v6.2.0 2.9 multi-surface-rendering ✅
- v6.2.0 2.10 a2ui-surface-context ✅
- v6.2.0 2.13 artefact-render-hook ✅
- ACTION-TRIGGER M1–M3 (2026-06-08) ✅
- M3.5 landing polish (2026-06-09, today) ✅
- Existing Aitana `Workbench`, `SkillSessionPanel`, `DocumentHistoryPanel`, `SurfaceRegistryProvider`, `useStableThreadId`, `subscribeSessionsChangedDetailed` ✅
**Created**: 2026-06-09
**Last Updated**: 2026-06-09

## Problem Statement

M3.5 made the **homepage** demo-quality — Hero, ProtocolStripe, OneHeroVisual landed on the live URL this morning. But once a visitor clicks "Ask the PPA expert" and arrives at the chat shell, the polish stops. The internal app at `/chat/@aitana-platform/...` is functional but lacks the compositional UX that makes gde-ap-agent's chat shell feel deliberate.

Aitana already has the **building blocks**: `Workbench` (191 LOC, identical API to gde-ap-agent's), `SkillSessionPanel`, `DocumentHistoryPanel`, `SurfaceRegistryProvider`, `useStableThreadId`, `subscribeSessionsChangedDetailed`, `WorkspaceSurfaceRegion` / `SidebarSurfaceRegion` / `ModalSurfaceRegion` (basic versions), `StreamErrorBanner`. What's missing is the **composition pattern** — multi-section collapsible sidebar, sidebar auto-collapse on first message, animated tab badges, `InContextBadge` "Will process: file.pdf" caption, `SignInRequired` panel that stays on the chat URL, contextual `EmptyTab` fallbacks, and workspace-surface mounting INTO a Workbench tab rather than as a flex sibling.

gde-ap-agent's `app/chat/[...path]/page.tsx` is **1795 LOC** vs Aitana's **627 LOC** — the ~1100 LOC delta is exactly where the UX iterations live. Most of those iterations are AP-pipeline-specific *content* (`InvoiceHeroCard`, `VendorKgPanel`, `APDashboardPanel`, `mergeEmittedInvoicePayload`) that stay fork-side. What we port is the *layout architecture* + ~6 generic compositional patterns.

**Current State** (verified by reading both chat pages):
- Aitana sidebar: single `showDocBrowser: boolean` toggle → entire browser hidden or visible. No per-section collapse. No auto-collapse on first user message.
- Aitana Workbench tab badge: solid `bg-primary` dot only. No animate-ping halo. Active underline is static. Tab activation has no fade-in.
- Aitana Workbench default width: caller-controlled via `className` prop with no documented breakpoint guidance. gde-ap-agent ships `md:w-[520px] xl:w-[640px] 2xl:w-[760px] [@media(min-width:2000px)]:w-[860px]` as the canonical 4-breakpoint scale.
- Aitana sign-in gate: `useEffect → router.replace("/")` on unauth → user dumped at homepage with no explanation, can't bookmark a chat URL for post-sign-in.
- Aitana chat header: no "Will process: file.pdf" caption when documents are in context. Multi-doc state is ambiguous if the user has 3 tabs open and 1 unchecked.
- Aitana Workbench tabs: caller-defined content. No contextual `EmptyTab` pattern documented in upstream → empty tabs render as blank panels.
- Aitana workspace surface: mounts as a flex sibling of chat (`max-w-xl`). gde-ap-agent's hybrid: mounts INTO a Workbench tab when one is active so MCP App iframes persist across A2UI surface updates.

**Impact:**
- **Demo polish ceiling** — the homepage is now demo-quality; the internal app is the next polish surface. Fri 2026-06-12 ONE demo will spend most of its time IN the chat, not on the landing.
- **Croatia July 2026 workshop** — same protocol-stack story benefits from a chat shell that holds its shape across the conversation.
- **Forks downstream** — every public-template consumer inherits whatever shell Aitana ships. Iterations baked in here propagate via `aitana-template-publish`.
- **Per-deploy branding (M1) compounded by per-deploy layout** — ONE deployment gets ONE branding; both deployments get a polished shell.

## Goals

**Primary Goal:** Port six compositional UX patterns from gde-ap-agent's chat shell into Aitana's `/chat/[...path]/page.tsx` + `Workbench.tsx` + sidebar — verified against the live `platform-frontend` URL via chrome-devtools MCP — by Thursday EOD so Friday demo runs against a polished internal shell.

**Success Metrics:**
- Sidebar refactored to multi-section collapsible (`<details>` + chevron per Sessions / Documents / History) with auto-collapse on first user message of a fresh chat
- Workbench tab badges show animate-ping halo + zoom-in active underline; tab activation fades in (`animate-in fade-in duration-200`)
- Default Workbench width scales across 4 breakpoints (520/640/760/860px) when no `className` is provided
- Sign-in gate renders `SignInRequired` panel on the chat URL (no silent redirect to `/`) so post-sign-in lands directly back in the chat
- `InContextBadge` shows "Will process: file.pdf" or "Will process 3 documents on next turn" above the chat input whenever `includedDocIds.length > 0`
- Contextual `EmptyTab` ("No X yet — pick from sidebar") renders inside every Workbench tab when its content is null/empty
- Workspace surface mounts INTO an active Workbench tab when one is present; falls back to flex-sibling when no Workbench tab is mounted (backwards compat with existing layouts)
- Aitana chat page LOC grows by ~300–400 lines (not 1100 — AP-pipeline-specific content stays out); test count grows by ~20
- chrome-devtools MCP snapshot of `/chat/@aitana-platform/one-doc-compare` post-deploy shows: collapsible sidebar sections, animated tab badges, no console errors

**Non-Goals:**
- **AP-pipeline-specific content** — `InvoiceHeroCard`, `APPipelineSteps`, `SampleInvoicePicker`, `VendorKgPanel`, `APDashboardPanel`, `mergeEmittedInvoicePayload`, the `app:emitted:invoice` state-key dance — all stay in gde-ap-agent
- **Audit/Inspector pane component port** — overlaps with 4.1 M3 (deferred). This sprint wires the SLOT (chip-row position above chat + slide-out region on the right) but ports no audit components. 4.1 M3 ships the components if/when Mark wants them
- **DocTabsBar viewMode states** (minimized/side/focus per tab) — defer to v6.5
- **Hash-driven URLs** (`#audit=docparse`) — defer to v6.5 with the audit-pane port
- **`SampleInvoicePicker`-style generalised "skill home" empty state** — useful but bigger scope; defer to v6.5
- **Tier 2 workbench artefact polish** (`ClauseExtractionCard` / `SideBySideDocViewer` / `KeyDifferencesPanel` Tailwind tweaks) — separate concern, defer to optional M3.6 post-demo
- **New A2UI primitives, MCP App spec changes, new protocol events** — zero new protocol surface; everything renders existing AG-UI / A2UI / MCP App events

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Persistent Workbench tabs already prevent MCP App iframe remount; this sprint adds animate-ping badge + fade-in tab activation so side updates register without the user needing to switch. Auto-collapse sidebar on first message keeps chat + workbench in view during the active phase of a turn. |
| 2 | EARNED TRUST | +1 | `InContextBadge` ("Will process: file.pdf") makes multi-doc context unambiguous — the user always knows what the agent will see. `SignInRequired` panel explains *why* auth is needed instead of silently redirecting. |
| 3 | SKILLS, NOT FEATURES | 0 | No new skill abstractions; the shell is infrastructure. Skills inherit the polish automatically. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing changes. |
| 5 | GRACEFUL DEGRADATION | +2 | Contextual `EmptyTab` on every Workbench tab. `StreamErrorBanner` already has retry-when-retryable. Sidebar sections degrade independently (collapsing one doesn't affect others). Sign-in failure stays on the URL so post-sign-in lands cleanly. Race guards on state fetches (already in gde-ap-agent — port the pattern). |
| 6 | PROTOCOL OVER CUSTOM | +2 | Zero new protocols. Every visible thing renders an existing AG-UI / A2UI / MCP App event. Sidebar sections wrap existing panels (`SkillSessionPanel`, `DocumentHistoryPanel`). Workbench tabs wrap existing A2UI surface mounts. Workspace-into-tab pattern uses the existing `A2UISurfaceMount` API. |
| 7 | API FIRST | 0 | Frontend-only. No new endpoints. Channels (Telegram, email, CLI) unaffected. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Inherits AG-UI / Cloud Trace coverage; adds no new instrumentation surfaces. Race-guard fixes a class of "stale data after session switch" bugs that previously required heavyweight Cloud Trace forensics to diagnose. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data flows, no new trust boundaries. `SignInRequired` panel is read-only chrome before auth completes. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All compositional patterns are presentation over protocol events — no business logic moves to the client. Bundle delta estimated <15KB gzipped. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Standards Compliance

- **No new protocols** — every patch renders existing AG-UI / A2UI / MCP App events. The workspace-into-tab pattern is a layout choice over `A2UISurfaceMount`.
- **HTML semantics** — `<details>`/`<summary>` for collapsible sidebar sections is native HTML; no library, no JS toggle handler needed, accessible by default (screen-reader expand/collapse, keyboard Enter/Space).
- **ARIA roles** — existing `Workbench` already does `role="tablist"` + `role="tab"` + `role="tabpanel"` + `aria-selected` + `aria-controls`. We add nothing; we enhance what's there.
- **No new dependencies** — uses existing `tailwindcss-animate` for fade-in + animate-ping.

## CLI Surface

Per design-doc-creator skill rule 5b-bis. This sprint is pure frontend with no new developer-facing API. **Skip CLI commands.** The existing `aiplatform demo verify` (from 4.2 M4) covers post-deploy smoke; chrome-devtools MCP via the `aitana-frontend-verify` skill covers live UX checks.

## Design

### Overview

Port six compositional patterns from gde-ap-agent's `app/chat/[...path]/page.tsx` into Aitana's. AP-pipeline-specific content stays fork-side. Generalise where the pattern was domain-locked (e.g. `EmptyTab` body strings flow through skill metadata). The Workbench component itself is already at parity — small polish patches (badge halo + active underline animation + width defaults) finish the job.

### Six Compositional Patterns

#### Pattern 1 — Multi-section collapsible sidebar (`SidebarSection`)

Replaces Aitana's `showDocBrowser: boolean` single toggle for the entire browser.

```tsx
// src/components/chat/SidebarSection.tsx  (~50 LOC)
export function SidebarSection({
  title,
  defaultOpen = true,
  badge,
  action,
  bodyClassName,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  badge?: React.ReactNode;
  action?: React.ReactNode;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <details open={defaultOpen} className="group border-b border-border">
      <summary className="flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground/60 hover:text-muted-foreground">
        <SectionChevron />
        <span className="flex-1 truncate">{title}</span>
        {badge}
        {action}
      </summary>
      <div className={bodyClassName ?? "px-3 pb-3 pt-1"}>{children}</div>
    </details>
  );
}
```

Refactor sidebar into three sections wrapping existing panels:
- **Sessions** — wraps `SkillSessionPanel` (already exists). `defaultOpen: true`.
- **Documents** — wraps `DocListView` + `UploadDropZone`. `defaultOpen: true`.
- **History** — wraps `DocumentHistoryPanel`. `defaultOpen: false` (compact by default).

`showDocBrowser` boolean stays as the master visibility toggle (mobile + first-message auto-collapse), but inside the open browser the user gets per-section control.

#### Pattern 2 — Auto-collapse sidebar on first user message

```tsx
// in ChatShell — already has isFreshChat computed
const prevFreshChatRef = useRef(isFreshChat);
useEffect(() => {
  if (prevFreshChatRef.current && !isFreshChat && !enteredViaResume) {
    setShowDocBrowser(false);
  }
  prevFreshChatRef.current = isFreshChat;
}, [isFreshChat, enteredViaResume]);
```

Fires exactly once per session-start (transition `isFreshChat: true → false`). Doesn't fire on resume. Manual reopens stick.

#### Pattern 3 — Workbench badge polish + width defaults + fade-in

Three small patches to existing `Workbench.tsx`:

**Badge halo** (replaces solid dot):
```tsx
{tab.badged && !isActive && (
  <span aria-label="new content" className="relative ml-0.5 flex h-1.5 w-1.5 shrink-0 items-center justify-center">
    <span className="absolute inline-flex h-2.5 w-2.5 animate-ping rounded-full bg-primary/40" />
    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
  </span>
)}
```

**Active underline animation:**
```tsx
{isActive && (
  <span aria-hidden className="absolute inset-x-2 -bottom-px h-0.5 origin-center animate-in fade-in zoom-in-x-50 rounded-t-sm bg-primary duration-200" />
)}
```

**Tab body fade-in:**
```tsx
className={cn(
  "h-full w-full overflow-auto",
  isActive ? "animate-in fade-in duration-200" : "hidden",
)}
```

**Default width scale** — bake into the wrapping `<div>` when no `className` is provided:
```tsx
const defaultClassName = "md:w-[520px] xl:w-[640px] 2xl:w-[760px] [@media(min-width:2000px)]:w-[860px]";
// merged: className ?? defaultClassName
```

#### Pattern 4 — `SignInRequired` panel (replaces silent redirect)

Aitana current (line 120 of chat page):
```tsx
useEffect(() => {
  if (!loading && !user) router.replace("/");
}, [loading, user, router]);
```

Replace with:
```tsx
function SignInRequired({ skillName }: { skillName?: string }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="max-w-md space-y-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Sign-in required</p>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground md:text-3xl">
          You need to sign in to {skillName ? `open ${skillName}` : "open this chat"}.
        </h1>
        <p className="text-sm text-muted-foreground">
          Sessions, document history, and audit traces are scoped to your account. Sign in to continue — you&apos;ll land straight back here.
        </p>
      </div>
      <SignInButton />
      <Link href="/" className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground">
        ← Back to homepage
      </Link>
    </main>
  );
}

// in ChatPage:
if (!loading && !user) return <SignInRequired />;
```

Stays on the chat URL so post-sign-in Firebase auth re-renders straight into the chat the user wanted.

#### Pattern 5 — `InContextBadge` caption

```tsx
function InContextBadge({ openTabs, includedDocIds }: { openTabs: DocTabData[]; includedDocIds: string[] }) {
  if (includedDocIds.length === 0) return null;
  const includedTabs = openTabs.filter((t) => includedDocIds.includes(t.id));
  const label = includedTabs.length === 1
    ? `Will process: ${includedTabs[0].filename}`
    : `Will process ${includedTabs.length} documents on next turn`;
  return (
    <div className="mb-2 flex items-center gap-2 px-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
      <span className="h-1.5 w-1.5 rounded-full bg-primary/70" aria-hidden />
      <span className="truncate">{label}</span>
    </div>
  );
}
```

Renders above the chat input, only when `includedDocIds.length > 0`.

#### Pattern 6 — Contextual `EmptyTab` + workspace-into-Workbench-tab

```tsx
// src/components/chat/EmptyTab.tsx  (~20 LOC)
export function EmptyTab({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 py-16 text-center">
      <h3 className="text-lg font-semibold tracking-tight text-foreground">{title}</h3>
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">{body}</p>
    </div>
  );
}
```

Used inside each Workbench tab's `content` when the underlying data is null. Each skill supplies tab labels via skill metadata; empty bodies use a sensible default ("No content yet — pick from sidebar to populate") and a per-tab override field on `tools_enabled.workbench_tabs[].empty_body` if richer copy is needed.

**Workspace-into-tab pattern** — when a Workbench is present and one of its tabs is named `"workspace"`, mount the workspace A2UI surface INTO that tab body instead of into the flex-sibling region. Falls back to the existing `WorkspaceSurfaceRegion` flex sibling when no Workbench tab claims the workspace. This is the iframe-stability fix — the workspace surface no longer remounts when the user switches tabs.

### Frontend Changes

**New Components:**
- `src/components/chat/SidebarSection.tsx` (~50 LOC + 4 tests)
- `src/components/chat/SignInRequired.tsx` (~40 LOC + 2 tests)
- `src/components/chat/InContextBadge.tsx` (~25 LOC + 3 tests)
- `src/components/chat/EmptyTab.tsx` (~20 LOC + 2 tests)

**Modified Components:**
- `src/components/chat/Workbench.tsx` — 3 polish patches (~30 LOC change + 4 new tests). Badge halo, active underline animation, tab body fade-in, default width scale. Extend `WorkbenchTab` interface with optional `emptyBody?: string` field; Workbench renders `<EmptyTab>` when `content` is null and `emptyBody` is set.
- `src/app/chat/[...path]/page.tsx` — sidebar refactor + auto-collapse hook + `SignInRequired` swap + `InContextBadge` insertion (~80 LOC change + 6 new tests).

**State Management:**
- New ref `prevFreshChatRef` in `ChatShell` for auto-collapse
- Sidebar section open/closed state lives in the DOM (`<details open>`) — no React state needed
- No new contexts or providers

**Backwards compatibility:**
- `Workbench` `className` prop still wins over default width scale (existing callers unaffected)
- `WorkbenchTab.content` may now be `null` or `React.ReactNode` (already typed as `React.ReactNode`, no change)
- Sidebar refactor preserves `showDocBrowser` master toggle — mobile and auto-collapse behavior unchanged for callers that haven't adopted sections

### Backend Changes

**None.** Every pattern composes existing protocol surfaces.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| —      | —        | No API changes | — |

### Architecture Diagram

```
ChatPage
   │
   ├── Auth gate ──► not signed in: <SignInRequired skillName={…} />
   │
   └── ChatPageInner (AGUIProvider wrapping)
         │
         └── ChatShell
               ├── ContextBanner (existing)
               ├── SkillsBar (existing, top)
               │
               ├── (when showDocBrowser) Sidebar
               │     ├── <SidebarSection title="Sessions">      → <SkillSessionPanel/>
               │     ├── <SidebarSection title="Documents">     → <DocListView/> + <UploadDropZone/>
               │     ├── <SidebarSection title="History">       → <DocumentHistoryPanel/>
               │     └── <SidebarSurfaceRegion/>                → A2UI sidebar surface mount
               │
               ├── Chat column
               │     ├── DocTabsBar (existing)
               │     ├── <ChatMessageList/>
               │     ├── <InContextBadge openTabs includedDocIds/>      ← NEW
               │     └── <Composer/>
               │
               ├── (when no workbench tab claims workspace) <WorkspaceSurfaceRegion/>    ← fallback
               │
               └── Workbench (with `className="md:w-[520px] xl:w-[640px] 2xl:w-[760px] [@media(min-width:2000px)]:w-[860px]"` default)
                     ├── Tab "workspace" → <A2UISurfaceMount surfaceId="workspace"/>      ← preferred mount
                     ├── Tab "document"  → <DocumentPanel/> or <EmptyTab title="No document open"/>
                     ├── Tab "<skill-defined>" → skill content or <EmptyTab title="No X yet"/>
                     └── (per-skill more tabs)

Auto-collapse: isFreshChat=true → first user message → setShowDocBrowser(false)
                (once per session-start, skipped on resume)
```

## Implementation Plan

### M1 — SidebarShell (Wed, ~1d)
- [ ] Port `SidebarSection.tsx` from gde-ap-agent (verbatim — already generic) (~50 LOC + 4 vitest)
- [ ] Refactor `app/chat/[...path]/page.tsx` sidebar to wrap existing panels in 3 `<SidebarSection>` instances (Sessions / Documents / History) (~40 LOC change + 2 vitest)
- [ ] Add `prevFreshChatRef` auto-collapse effect (~10 LOC + 2 vitest)
- [ ] Manual: verify sidebar sections expand/collapse independently; auto-collapse fires on first message of fresh chat; sticks on resume

### M2 — Workbench polish (Wed PM, ~0.5d)
- [ ] Patch Workbench badge to halo+dot animation (~15 LOC change + 2 vitest)
- [ ] Patch active underline to animated zoom-in (~5 LOC change + 1 vitest)
- [ ] Patch tab body to fade-in on activation (~5 LOC change + 1 vitest)
- [ ] Add default 4-breakpoint width scale to Workbench (use when `className` not provided) (~10 LOC + 1 vitest)
- [ ] Update `WorkbenchTab` interface to include optional `emptyBody?: string` for contextual empty state
- [ ] `EmptyTab.tsx` component (~20 LOC + 2 vitest)

### M3 — Chat-shell ergonomics (Thu AM, ~0.5d)
- [ ] `SignInRequired.tsx` (~40 LOC + 2 vitest)
- [ ] Replace silent `router.replace("/")` with `<SignInRequired/>` render in ChatPage (~5 LOC change + 1 vitest)
- [ ] `InContextBadge.tsx` (~25 LOC + 3 vitest)
- [ ] Insert `InContextBadge` above the chat input in ChatShell (~5 LOC change + 1 vitest)

### M4 — Workspace-into-tab mount + verify (Thu PM, ~0.5d)
- [ ] Extend Workbench surface-mount detection — when a tab id `"workspace"` is present, mount `<A2UISurfaceMount surfaceId="workspace"/>` INTO the tab body (~30 LOC chat-page change + 3 vitest)
- [ ] Backwards-compat fallback: when no workspace tab, existing `WorkspaceSurfaceRegion` flex-sibling continues to render
- [ ] Race-guard pattern audit — add `cancelled` flag to any existing async state-fetch effects that lack one (grep `fetchWithAuth(.*sessions.*state`) (~20 LOC + 2 vitest)
- [ ] chrome-devtools MCP verification: `/chat/@aitana-platform/one-doc-compare` after deploy. Snapshot shows: collapsible sidebar sections, animated tab badges, workspace surface inside Workbench tab when emitted, no console errors

### M5 — Optional polish (deferred, no critical-path commitment)
- [ ] Audit chip-row slot wiring (no audit component port — just the slot position above the chat thread) — defer to v6.5 with the audit-pane port
- [ ] Hash-driven inspector URLs (`#audit=key`) — defer to v6.5
- [ ] DocTabsBar per-tab viewMode (minimized/side/focus) — defer to v6.5
- [ ] `SampleInvoicePicker`-style generalised skill-home empty state — defer to v6.5

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None. Every pattern is additive; existing chat works through every milestone independently.

**Rollback Plan:**
- M1: revert `SidebarSection` refactor → sidebar returns to single toggle. Manual reopens lost; nothing broken.
- M2: revert Workbench patches → solid-dot badges + static underline + no fade-in. Functional behavior unchanged.
- M3: revert `SignInRequired` → silent redirect resumes. UX regression but no breakage.
- M3: revert `InContextBadge` insertion → multi-doc context becomes ambiguous again (the original state).
- M4: revert workspace-into-tab → workspace surface returns to flex sibling. MCP App iframe remounts on tab switch resume.
- Each milestone independently revert-safe.

**Environment Variables:** None.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] M1: `SidebarSection` (4 tests covering open/closed toggle, badge slot, action slot, default-open prop); page.tsx sidebar refactor render contract (2 tests); auto-collapse effect (2 tests covering fresh-chat transition + resume no-op)
- [ ] M2: Workbench badge halo (2 tests); active underline animation (1 test); fade-in activation (1 test); default-width-scale fallback (1 test); `EmptyTab` render (2 tests covering populated body + default body)
- [ ] M3: `SignInRequired` panel (2 tests covering skillName variant + no-skillName default); `InContextBadge` (3 tests covering 0/1/N docs); ChatPage `SignInRequired` swap (1 test asserting no router.replace fires unauth)
- [ ] M4: workspace-into-tab mount detection (3 tests covering tab present, tab absent, multiple tabs); race-guard cancelled-flag audit (2 tests on any newly-protected effects)

### Backend Tests (pytest)
None — no backend changes.

### Manual / E2E (verified via chrome-devtools MCP via aitana-frontend-verify skill)
- [ ] Visit `/chat/@aitana-platform/one-doc-compare` unauth → `SignInRequired` panel renders; URL stays as the chat URL; `SignInButton` present
- [ ] Sign in → ChatShell renders; sidebar has 3 collapsible sections
- [ ] Collapse Documents section → Sessions and History remain visible and functional
- [ ] Send first user message → sidebar auto-collapses; tab badges animate when content arrives in inactive tabs
- [ ] Open a document tab → `InContextBadge` shows "Will process: <filename>" above the input
- [ ] Open 3 document tabs → `InContextBadge` shows "Will process 3 documents on next turn"
- [ ] Workbench at various viewport widths (1080p / 1440p / 2560p ultrawide) → width scales through 4 breakpoints
- [ ] Switch tabs while an MCP App iframe is mounted → no remount flash (verify by watching `console.log` from sandbox proxy or by network panel — no new sandbox request)

## Security Considerations

- **No new data flows.** `SignInRequired` is read-only chrome before auth completes. Sidebar refactor wraps existing panels; per-section access control inherits from the wrapped panels.
- **No new trust boundaries.** Workspace-into-tab mounts the same `A2UISurfaceMount` component; the iframe sandbox + artefact-render-hook defence-in-depth (v6.2.0 2.13) still applies.
- **No PII leaked through new chrome.** `InContextBadge` shows filenames the user already sees in the doc tabs — same scope.

## Performance Considerations

- **Bundle-size impact**: estimated <15KB gzipped across all new components. New components are presentation-only.
- **No new network calls.** Sidebar collapse is DOM-native (`<details>`); auto-collapse fires once per session.
- **MCP App iframe stability** — workspace-into-tab pattern *reduces* network/CPU vs current flex-sibling-during-conditional-render pattern (no remount on tab switch).
- **No TTFT impact.** First token continues to stream from the existing AG-UI path.

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] `make demo-verify` returns green for /chat routes after deploy
- [ ] Bundle-size CI assertion passes (<200KB initial budget unchanged)
- [ ] Live URL `/chat/@aitana-platform/one-doc-compare` renders ChatShell with 3 collapsible sidebar sections, animated tab badges, fade-in tab activation
- [ ] Unauthenticated visitor to `/chat/...` sees `SignInRequired` panel (not silent redirect)
- [ ] Authenticated visitor with 1+ docs open sees `InContextBadge` above input
- [ ] Workbench at 1080p shows 640px width; at 1440p shows 760px width; at 2560p ultrawide shows 860px width
- [ ] chrome-devtools MCP run via `aitana-frontend-verify` skill: no console errors
- [ ] Zero AP-pipeline-specific copy/components/state leaked into upstream (CI grep assertion or manual review)
- [ ] Documentation updated: `docs/talks/ai-ui-protocol-stack.md` verification log

## Open Questions

- **Q1 — Sidebar section order.** Sessions → Documents → History (recommended) vs Documents → Sessions → History. AP-fork uses Sessions first; ONE deployment may want Documents first since the doc-compare workflow is the centerpiece. Tactical call. Default: Sessions first; deployments override if needed.
- **Q2 — Audit chip-row slot in M5.** Wire the empty slot now (zero-cost: just an empty `<div data-slot="audit-chip-row"/>` above the chat thread) so 4.1 M3's later port lands cleanly, or skip the slot entirely until 4.1 ships? Recommend: skip — keeps this sprint smaller; the 4.1 port can add the slot when it adds the components.
- **Q3 — `emptyBody` field on `WorkbenchTab`.** Add the field now (additive, backwards-compat) or push the empty state into each consumer's content render? Recommend: add the field — central pattern, single test surface, downstream forks benefit.
- **Q4 — Workspace-into-tab vs flex-sibling default.** If a skill defines no `"workspace"` tab on its Workbench, do we keep the flex-sibling mount as the default behavior (more visible, may overlap chat) or default to nothing-rendered (cleaner, requires skill to opt in)? Recommend: keep flex-sibling default (backwards compat; existing skills don't break).

## Related Documents

- [docs/design/v6.4.0/fork-visual-demo-pullback.md](fork-visual-demo-pullback.md) — sister sprint; covers landing + audit-pane components. This doc deliberately reuses 4.1's audit-pane slot if/when it lands.
- the deployment's demo-readiness doc (deployment-private) — ONE-content sprint that instantiates the workbench artefacts this shell hosts.
- [docs/design/v6.2.0/implemented/multi-surface-rendering.md](../v6.2.0/implemented/multi-surface-rendering.md) — surface mount foundation
- [docs/design/v6.2.0/implemented/a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — workspace ↔ agent state loop
- [docs/design/v6.2.0/implemented/artefact-render-hook.md](../v6.2.0/implemented/artefact-render-hook.md) — defence-in-depth for MCP App iframes
- gde-ap-agent source: `<local-path>]/page.tsx` (1795 LOC) — patterns being ported
- gde-ap-agent Workbench: `<local-path>` — already at parity; minor polish only
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — verification log
