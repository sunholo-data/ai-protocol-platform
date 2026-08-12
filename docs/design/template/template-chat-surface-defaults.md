# Template Chat-Surface Defaults

**Status**: Partially implemented in platform (Sprint CHAT-SURFACE-A, 2026-06-05) — **template sync pending**. G28 + G31 + G34 + G38 shipped with 46 tests. G29 (`DefinitionList` primitive) shipped; the `JsonAsStructuredCard` routing it enables is deferred to Sprint B (audit view) where the pattern naturally lives. G32 (DocTab viewMode buttons) NOT APPLICABLE — platform's DocTab has no viewMode buttons; G32 is a fork-side cleanup item only. G33 (inline-emit suppression) deferred to Sprint B (depends on JsonAsStructuredCard).
**Priority**: P1 (this is the G22 follow-up the SEQUENCE has been waiting for)
**Estimated**: 4.5d planned; ~3h actual (most items were 1-component ports from gde-ap-agent fork; G32/G33 scope-cut as N/A or paired-with-deferred)
**Scope**: Frontend (components/chat, components/audit, components/shared, providers/AGUIProvider, lib/branding)
**Dependencies**: [template-protocol-defaults.md](./template-protocol-defaults.md) (G24 emit pattern is the source of truth for Friction 7's audit-pane fix)
**Created**: 2026-06-05
**Last Updated**: 2026-06-05
**Source items**: G28–G34 — captured by gde-ap-agent fork 2026-06-03 during AP demo polish. Resolves [SEQUENCE.md G22](./SEQUENCE.md) follow-up. G38 — captured by CPH Uni AIPLA fork (cphu/aipla-app) v1.1.0 feedback round: AGUIProvider unmounts entire subtree on every Firebase ID-token refresh; visible only to forks with cross-region backend latency.

## Implementation Status (Sprint CHAT-SURFACE-A, 2026-06-05)

| Item | Status | Files (platform) |
|------|--------|------------------|
| G28 DocumentHistoryPanel cap + collapse | ✅ Shipped (16/16 tests, 4 new G28 cases) | [DocumentHistoryPanel.tsx](../../../frontend/src/components/chat/DocumentHistoryPanel.tsx) — default-closed + `max-h-[25vh]` overflow-y-auto + count badge |
| G29 DefinitionList primitive | ✅ Shipped (9 tests) | [DefinitionList.tsx](../../../frontend/src/components/shared/DefinitionList.tsx) + [DefinitionList.test.tsx](../../../frontend/src/components/shared/__tests__/DefinitionList.test.tsx) |
| G29 JsonAsStructuredCard routing | ⏳ Deferred to Sprint B | gde-ap-agent's JsonCardBuilder is densely AP-specific (vendor_name/line_items/verdict baked in as title heuristics); a clean template port requires stripping the AP knowledge. Pairs naturally with the audit-view port |
| G31 Tabbed Workbench | ✅ Shipped (11 tests, mount-preservation contract pinned) | [Workbench.tsx](../../../frontend/src/components/chat/Workbench.tsx) + [Workbench.test.tsx](../../../frontend/src/components/chat/__tests__/Workbench.test.tsx) — `useTabBadges()` helper included |
| G32 DocTab hideViewModeButtons | ❌ NOT APPLICABLE | Platform's [DocTab.tsx](../../../frontend/src/components/doc-browser/DocTab.tsx) has no viewMode buttons; G32 is a fork-side cleanup item (gde-ap-agent added them and they conflicted with the Workbench layout) |
| G33 Inline-emit-card suppression | ⏳ Deferred to Sprint B | Pairs with JsonAsStructuredCard; without that, no inline JSON-card exists to suppress |
| G34 BrandAvatar theme + userPhotoURL | ✅ Shipped (20 tests in MessageBubble, +3 G34 cases) | [BrandAvatar.tsx](../../../frontend/src/components/chat/BrandAvatar.tsx) (theme tokens) + [MessageBubble.tsx](../../../frontend/src/components/chat/MessageBubble.tsx) (`userPhotoURL?` prop) + [ChatMessageList.tsx](../../../frontend/src/components/chat/ChatMessageList.tsx) (threading) + [chat/[...path]/page.tsx](../../../frontend/src/app/chat/[...path]/page.tsx) (passes `user.photoURL` from `useAuth`) |
| G38 AGUIProvider no token-refresh unmount | ✅ Shipped — platform's AGUIProvider already had no `tokenResolved` gate; added defensive code comment + contract test (5 tests) | [AGUIProvider.tsx](../../../frontend/src/providers/AGUIProvider.tsx) with G38 comment + [AGUIProvider.test.tsx](../../../frontend/src/providers/__tests__/AGUIProvider.test.tsx) G38 contract case |

**Validation:** `npm run quality:check` — 551/551 vitest (+26 net-new this sprint), ESLint clean, tsc clean, build clean.

**Deferred to Sprint CHAT-SURFACE-B (audit view)** — these items pair naturally with the audit-view component port from gde-ap-agent:
- G29 JsonAsStructuredCard routing (needs template-clean JsonCardBuilder)
- G33 inline-emit-card suppression / ChatConfig mode flag

## Problem Statement

The template's chat-surface components — `MessageBubble`, `DocumentHistoryPanel`,
`InspectorPanel`, the right-pane workspace ladder, `DocTab`, `BrandAvatar`, and
`JsonCardBuilder` — were designed for a **single-output single-skill demo**: one chat
bubble, one doc, one workspace card, one user. Any non-trivial fork ships:

- Many sessions per document (history panel overflows)
- Multi-stage pipelines whose payloads need ledger-style "label : value" reading
- An audit view for function-as-schema specialists
- Three concurrent right-pane surfaces (workspace, MCP App, doc) the user wants to switch between
- A consistent visual identity that includes the user's own Google photo

Each gap below is a component default that doesn't survive that bigger demo. None is
a protocol bug; all are template-componentry defaults the template seeded poorly.

**Current State:**

- **G28** [`DocumentHistoryPanel.tsx`](../../../frontend/src/components/chat/DocumentHistoryPanel.tsx) defaults `isOpen={true}` and renders its body with no `max-h` / internal scroll. A doc with 50+ sessions pushes the actual `DocumentPanel` off-screen.
- **G29** [`JsonCardBuilder.ts`](../../../frontend/src/components/chat/JsonCardBuilder.ts) renders scalar key/value pairs as `Row([Text, Text])`. A2UI v0.9's `Row` is a `flex` container with no label-column convention, so labels wrap onto two lines as the container narrows; numeric values land below their labels, breaking ledger reading.
- **G30** [`InspectorPanel.tsx`](../../../frontend/src/components/audit/InspectorPanel.tsx) `InputOutputCard` shows the tool's `result_content` on the OUTPUT side. For function-as-schema `emit_*` tools the result is a STOP-after-emit sentinel string, not the payload — judges open the audit and see *"Your structured output has been recorded. STOP. Do NOT call this tool again…"* instead of the data.
- **G31** Right-pane render uses a mutually-exclusive conditional ladder (`{expandedTab && <DocumentPanel/>}{!expandedTab && !globeContext && <WorkspaceSurfaceRegion/>}…`). Opening an MCP App unmounts the workspace card; opening a doc unmounts both. MCP App iframes remount on every switch (~200ms flash + postMessage re-init).
- **G32** [`DocTab`](../../../frontend/src/components/chat/DocTab.tsx) viewMode buttons (side / focus / minimize) were designed for the single-slot pane; after the Workbench lands they're decorative but still visible. Coupled bug: the Workbench Document tab gates on `tab.viewMode !== "minimized"` so a freshly-clicked tab (default `viewMode="minimized"`) never appears.
- **G33** [`MessageBubble.tsx`](../../../frontend/src/components/chat/MessageBubble.tsx) emits both an inline `JsonAsA2UICard` AND a workspace `A2UISurfaceMount` from a single `emit_*` tool call. Two visually-different renderings of the same payload appear — reads as a styling bug even when intentional.
- **G34** [`BrandAvatar.tsx`](../../../frontend/src/components/chat/BrandAvatar.tsx) + `MessageBubble.tsx` hardcode Tailwind colour literals (`from-teal-400 to-teal-600`, `from-amber-400 to-yellow-600`). `user.photoURL` is not threaded through the prop chain (`useAuth → chat page → ChatMessageList → MessageBubble`) so signed-in Google users see initial chips instead of their own profile photo.
- **G38** [`AGUIProvider.tsx`](../../../frontend/src/providers/AGUIProvider.tsx) `useEffect([authLoading, user, getIdToken, useTeacherAuth])` calls `setTokenResolved(false)` at the top of every run. The render path's `if (!tokenResolved) return /* loading */;` gate **unmounts** the entire subtree for the duration of the new token fetch. The effect re-runs on every silent hourly Firebase ID-token refresh and every `onAuthStateChanged` fire (tab focus, anonymous-group identity hydration). For upstream Aitana (Cloud Run + Agent Engine co-located in `europe-west1`) the GET `/api/sessions/{id}/messages` returns in ~5–50ms and the flicker is invisible; for any fork whose backend region differs (AIPLA: Cloud Run in `europe-north1`, Agent Engine in `europe-west1`, ~400ms) the chat bubbles **disappear and reappear** mid-conversation. The unmount kills `useSessionMessages` local state (history block flashes empty, refetch fires unnecessarily) and `useSkillAgent.messages` (live area goes blank).

**Impact:**

- Document tab unusable for any high-traffic doc (G28).
- Every JSON-shaped Card across the template reads "cramped and broken" because there's no label-column primitive (G29).
- Audit View — pitched as the explainability surface for multi-agent skills — is empty on the most important specialists (G30).
- User loses context on every `surface_action` because the right pane swaps wholesale; MCP App iframes pay re-init cost (G31).
- After absorbing the Workbench from this doc, viewMode buttons either don't work or break Document-tab opening (G32).
- Each `emit_*` shows the same data twice in subtly-different shapes (G33).
- Avatars clash with the theme; Google-signed-in users get initial chips instead of their photo (G34).
- Mid-conversation chat blanks-and-restores on every hourly token refresh for any fork whose backend latency exceeds human perception (G38). The race the unmount was meant to defend against (a user switching identity mid-submit) is already handled atomically by the `HttpAgent` `useMemo([skillId, token, sessionId])` rebuild; the two concerns got conflated in the original implementation.

**What works (do not touch):** AG-UI streaming, A2UI workspace surface mount itself, the SequentialAgent orchestration, the protocol layer. These frictions are template-componentry defaults around a solid protocol stack.

## Goals

**Primary Goal:** The template's chat-surface components should survive a **multi-output, multi-session, multi-surface skill** out of the box — the AP-style "extractor → validator → poster with MCP App embeds and an audit panel" demo lands without per-fork componentry rewrites.

**Success Metrics:**
- DocumentHistoryPanel with 100+ sessions still shows the document on the same screen (capped + collapsed default).
- Every scalar JSON payload in chat + audit renders ledger-aligned (label column fixed at `minmax(120px, 160px)`, numeric values monospace tabular-nums).
- Audit View for any `emit_*` tool shows the emitted payload on both INPUT and OUTPUT sides, with clear "function-as-schema" labelling.
- Right-pane workspace tabs stay mounted across `surface_action` events; MCP App iframes do not remount on tab switch.
- All starter chat-surface components use theme tokens, not hardcoded gradients; `user.photoURL` flows through to `MessageBubble`.

**Non-Goals:**
- New A2UI components in the protocol catalog. `DefinitionList` ships as a **frontend primitive** for inline JSON rendering, not as an A2UI BasicCatalog extension. Whether to upstream it to the A2UI spec is a separate question for the A2UI maintainers.
- Whole-page redesign. The Workbench replaces one specific pattern (right-pane conditional ladder); the rest of the chat layout is unchanged.
- Multi-skill audit view enhancements beyond fixing the `emit_*` empty-output bug. Broader audit UX is a follow-up.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +2 | Workbench tabs stay mounted → no MCP App iframe remount lag on `surface_action` (G31, ~200ms/switch); AGUIProvider stops unmounting subtree on hourly token refresh → no mid-chat flicker for cross-region forks (G38, ~400ms blank window) |
| 2 | EARNED TRUST | +1 | Audit pane shows real emitted payload, not STOP sentinel — explainability story actually works (G30); chat history doesn't blink-and-restore (G38) |
| 3 | SKILLS, NOT FEATURES | +1 | Better chat-surface defaults make new skills look polished without per-skill UI work |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing change |
| 5 | GRACEFUL DEGRADATION | +1 | DocumentHistoryPanel caps + scrolls instead of overflowing siblings (G28); user.photoURL falls back to initial chip when null (G34) |
| 6 | PROTOCOL OVER CUSTOM | 0 | Render-layer convention only; A2UI wire format unchanged. `DefinitionList` is a frontend primitive, not a custom protocol surface |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | +1 | Audit View is the primary observability surface for `emit_*` tools — fixing G30 makes the surface actually informative |
| 9 | SECURE BY CONSTRUCTION | 0 | No security surface change |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Client renders the same protocol payloads better; no thickening of the client's protocol logic |
| | **Net Score** | **+6** | Acceptable — proceed (was +5 before G38 bumped INSTANT FEEL +1 → +2) |

**Conflict Justifications:**
- None (no -1 scores).

## Design

### Overview

Seven coupled frontend changes: cap + collapse `DocumentHistoryPanel` (G28); ship a
`DefinitionList` primitive + a new `JsonAsStructuredCard` render path that bypasses
A2UI's column-less `Row` for inline scalar payloads (G29); fix the audit pane to show
`emit_*` args as the output (G30); replace the right-pane conditional ladder with a
persistent tabbed `Workbench` (G31); make `DocTab` viewMode buttons opt-in and decouple
the Workbench Document tab from `viewMode` (G32); harmonise inline-emit-card with
workspace-surface-card or pick one (G33); thread `user.photoURL` through the avatar
chain and replace hardcoded gradients with theme tokens (G34).

### G28 — DocumentHistoryPanel default-collapsed + capped

**File:** [`frontend/src/components/chat/DocumentHistoryPanel.tsx`](../../../frontend/src/components/chat/DocumentHistoryPanel.tsx)

```tsx
// Before
const [isOpen, setIsOpen] = useState(true);

// After
const [isOpen, setIsOpen] = useState(false);  // collapsed by default
```

Body wrapper:

```tsx
<div className="max-h-[25vh] space-y-3 overflow-y-auto">
  {/* sessions list */}
</div>
```

Header gets a count badge: `Conversations [50]`. Optional `maxHeight?: string` prop for forks that want a taller list (default `"25vh"`).

### G29 — `DefinitionList` primitive + `JsonAsStructuredCard` render path

**New files:**
- [`frontend/src/components/shared/DefinitionList.tsx`](../../../frontend/src/components/shared/DefinitionList.tsx)
- [`frontend/src/components/chat/JsonAsStructuredCard.tsx`](../../../frontend/src/components/chat/JsonAsStructuredCard.tsx)

**`DefinitionList`:**

```tsx
type DefinitionListProps = {
  items: Array<{ label: string; value: React.ReactNode; numeric?: boolean }>;
  density?: "compact" | "default";
};

export function DefinitionList({ items, density = "default" }: DefinitionListProps) {
  return (
    <dl
      className="grid gap-x-3 gap-y-1"
      style={{ gridTemplateColumns: "minmax(120px, 160px) 1fr" }}
    >
      {items.map(({ label, value, numeric }) => (
        <React.Fragment key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className={numeric ? "font-mono tabular-nums" : ""}>{value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}
```

**Render path change** in [`JsonCardBuilder.ts`](../../../frontend/src/components/chat/JsonCardBuilder.ts):
when the JSON is scalar-heavy (all values are primitives or short strings), route
through `JsonAsStructuredCard` (which uses `DefinitionList` internally) instead of the
A2UI `Row([Text, Text])` path.

**The workspace path (`A2UISurfaceMount`) is unchanged** — that's still "real A2UI on
the wire" for the protocol-purity story. The change is only at the JSON → inline-card
render layer where the template was inventing label-column behaviour A2UI's `Row`
doesn't have.

### G30 — Audit pane `emit_*` substitution

**File:** [`frontend/src/components/audit/InspectorPanel.tsx`](../../../frontend/src/components/audit/InspectorPanel.tsx)

```tsx
<InputOutputCard
  title={record.name}
  input={record.argsJson}
  output={
    record.name.startsWith("emit_") && record.argsJson
      ? record.argsJson  // for emit_* the args ARE the payload
      : record.resultContent
  }
  outputLabel={
    record.name.startsWith("emit_")
      ? "Emitted payload (function-as-schema)"
      : "Output (specialist → orchestrator)"
  }
  inputLabel={
    record.name.startsWith("emit_")
      ? "Input (orchestrator → specialist)"
      : "Input (orchestrator → specialist)"
  }
/>
```

The function-as-schema mental model is now explicit in the UI: INPUT shows what the
orchestrator handed in, OUTPUT shows the typed payload the specialist emitted.

**Generalisation (preferred):** surface a typed flag on the FunctionTool itself
(`result_is_sentinel: bool = False`) so the audit view doesn't string-match. Threaded
through the tool-record protocol so the frontend reads `record.resultIsSentinel`
instead of `record.name.startsWith("emit_")`. Lower priority; ship the name-match
fallback first.

### G31 — Tabbed `Workbench` replaces conditional ladder

**New file:** [`frontend/src/components/chat/Workbench.tsx`](../../../frontend/src/components/chat/Workbench.tsx)

```tsx
type WorkbenchTab = {
  id: string;
  label: string;
  badge?: number | string;
  render: () => React.ReactNode;
};

export function Workbench({ tabs, activeId, onChange }: {
  tabs: WorkbenchTab[];
  activeId: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <TabBar tabs={tabs} activeId={activeId} onChange={onChange} />
      {tabs.map(tab => (
        <div
          key={tab.id}
          className={tab.id === activeId ? "flex-1 overflow-auto" : "hidden"}
        >
          {tab.render()}
        </div>
      ))}
    </div>
  );
}
```

**Critical property:** every tab's `render()` runs once and stays mounted, just hidden
via `className`. MCP App iframes do not remount on tab switch; `postMessage`
handshake state is preserved.

**Wire-up in the chat page:**

```tsx
<Workbench
  activeId={activeWorkbenchTab}
  onChange={setActiveWorkbenchTab}
  tabs={[
    { id: "workspace", label: "Workspace", badge: workspaceBadge, render: () => <WorkspaceSurfaceRegion … /> },
    { id: "document", label: "Document", render: () => activeDocTab ? <DocumentPanel … /> : <EmptyDocState /> },
    { id: "vendor", label: "Vendor", badge: globeBadge, render: () => <VendorGlobePanel … /> },
    { id: "analytics", label: "Analytics", render: () => <APDashboardPanel … /> },
  ]}
/>
```

The `surface_action` handler **badges** the relevant tab (e.g. sets `globeBadge="!"`)
instead of swapping panes. User keeps control of what's visible.

### G32 — `DocTab` viewMode opt-out + Workbench Document gating

**Files:** [`frontend/src/components/chat/DocTab.tsx`](../../../frontend/src/components/chat/DocTab.tsx), [`frontend/src/components/chat/DocTabsBar.tsx`](../../../frontend/src/components/chat/DocTabsBar.tsx)

Add `hideViewModeButtons?: boolean` to `DocTab` + `DocTabsBar`, default `false`
(preserves template behaviour for forks not using the Workbench). The chat page passes
`hideViewModeButtons={isUsingWorkbench}`.

Decouple Workbench Document gating from `viewMode`:

```tsx
// Before — broke freshly-clicked tabs (default viewMode="minimized")
{ id: "document", render: () =>
    expandedTab ? <DocumentPanel … /> : <EmptyDocState /> }

// After — gate on which tab is *focused*, not on viewMode
{ id: "document", render: () =>
    activeDocTab ? <DocumentPanel doc={activeDocTab} /> : <EmptyDocState /> }
```

### G33 — Inline-emit-card harmonisation

**File:** [`frontend/src/components/chat/MessageBubble.tsx`](../../../frontend/src/components/chat/MessageBubble.tsx)

Two ship options. **Recommended:** **suppress the inline render** when a workspace
mount is also populated — single source of truth for the payload, no duplication.

```tsx
const { hasActiveWorkspaceMount } = useWorkbenchState();

return (
  <>
    {/* … message text … */}
    {jsonPart && !hasActiveWorkspaceMount && <JsonAsStructuredCard data={jsonPart} compact />}
    {/* workspace surface mount is owned by Workbench, not the bubble */}
  </>
);
```

**Alternative** (kept for forks that prefer the duplication): render a compact
**summary variant** of `JsonAsStructuredCard` with a `View full →` link to the
Workbench Workspace tab. Pick one model and ship it consistently; pass the choice via
a `<ChatConfig inlineCardMode="suppress" | "summary">` context provider.

Default: `"suppress"`.

### G34 — Avatar theme tokens + `user.photoURL` threaded

**Files:**
- [`frontend/src/components/chat/BrandAvatar.tsx`](../../../frontend/src/components/chat/BrandAvatar.tsx)
- [`frontend/src/components/chat/MessageBubble.tsx`](../../../frontend/src/components/chat/MessageBubble.tsx)
- [`frontend/src/components/chat/ChatMessageList.tsx`](../../../frontend/src/components/chat/ChatMessageList.tsx)
- [`frontend/src/app/chat/[...path]/page.tsx`](../../../frontend/src/app/chat/[...path]/page.tsx) (prop threading)

**BrandAvatar restyle (theme tokens):**

```tsx
<div className="bg-primary/5 border border-primary/20 rounded-full …">
  <AppMark className="h-5 w-5 text-primary" />
</div>
```

**User avatar:**

```tsx
type MessageBubbleProps = {
  role: "user" | "assistant";
  userInitial?: string;
  userPhotoURL?: string | null;  // NEW — threaded from useAuth
  // …
};

// Render
{role === "user" && (
  userPhotoURL
    ? <img src={userPhotoURL} className="h-8 w-8 rounded-full object-cover border border-border" />
    : <div className="bg-primary/10 text-primary …">{userInitial}</div>
)}
```

**Prop threading:** `useAuth` already returns `user.photoURL`; pass it through the chat
page → `ChatMessageList` → `MessageBubble` chain.

### G38 — AGUIProvider: don't unmount subtree on token refresh

**File:** [`frontend/src/providers/AGUIProvider.tsx`](../../../frontend/src/providers/AGUIProvider.tsx)

Track whether a token has ever resolved; gate `setTokenResolved(false)` on **initial
load only**. Subsequent refreshes fetch a new token in the background; the
`HttpAgent` `useMemo([skillId, token, sessionId])` rebuild already handles the bearer
swap atomically when the new token lands, so no request goes out unauthenticated.

```tsx
// Before
const [tokenResolved, setTokenResolved] = useState(false);

useEffect(() => {
  setTokenResolved(false);          // ← unmounts subtree on EVERY refresh
  let cancelled = false;
  (async () => {
    const token = await getIdToken();
    if (cancelled) return;
    setToken(token);
    setTokenResolved(true);
  })();
  return () => { cancelled = true; };
}, [authLoading, user, getIdToken, useTeacherAuth]);

// After
const [tokenResolved, setTokenResolved] = useState(false);
const hadTokenOnceRef = useRef(false);

useEffect(() => {
  if (!hadTokenOnceRef.current) {
    setTokenResolved(false);        // blank only on initial load
  }
  let cancelled = false;
  (async () => {
    const token = await getIdToken();
    if (cancelled) return;
    setToken(token);                // HttpAgent useMemo rebuilds atomically
    hadTokenOnceRef.current = true;
    setTokenResolved(true);
  })();
  return () => { cancelled = true; };
}, [authLoading, user, getIdToken, useTeacherAuth]);
```

**Race-defence note:** the 2026-06-03 comment block in the original effect describes
a real concern — *"a user that switches identity can submit a message in the gap and
have it sent with the old token"* — but the unmount is the wrong mitigation. The
`HttpAgent` is rebuilt from `useMemo([skillId, token, sessionId])` whenever `token`
changes; any in-flight or queued request picks up the new bearer header atomically.
Document this in the file as a code comment so the next reader doesn't re-introduce
the unmount.

**Why upstream Aitana doesn't see this:** Cloud Run + Vertex Agent Engine co-located
in `europe-west1` makes `session_service.get_session()` return in ~5–50ms. AIPLA's
data-residency decision (ADR-007) puts Cloud Run in `europe-north1` and Agent Engine
in `europe-west1` (~400ms) and the gap becomes a visible flicker. Any fork that pins
Cloud Run anywhere other than `europe-west1`, or swaps the SessionService backend
(Spanner, an external DB, an `/messages` endpoint that aggregates), hits the same
bug. The unmount is wrong even where it's invisible.

**Generalisable principle (worth a code comment in the template's provider):**
provider children should never unmount across credential refreshes. The credential is
data the provider holds, not a precondition for its consumers existing.

### CLI Surface

No new commands. Forks that re-brand chat surface colours edit `tailwind.config.ts` /
`globals.css` theme tokens — no source-code edits needed for re-theming after G34.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | `DocumentHistoryPanel` default-collapsed + max-h scroll + count badge + `maxHeight` prop (G28) | 1h |
| 2 | `DefinitionList.tsx` primitive + tests (G29 primitive) | 2h |
| 3 | `JsonAsStructuredCard.tsx` + route from `JsonCardBuilder` for scalar-heavy JSON; preserve A2UI path for nested/array JSON (G29 routing) | 3h |
| 4 | `InspectorPanel` emit_* name-match substitution + test fixtures (G30) | 1.5h |
| 5 | Generalise to `record.resultIsSentinel` flag on the tool-record protocol — typed FunctionTool annotation (G30 typed path) | 2h |
| 6 | `Workbench.tsx` + `TabBar.tsx` primitives; tests for hidden-via-className mount preservation (G31 primitive) | 3h |
| 7 | Replace chat-page conditional ladder with `Workbench` wiring; `surface_action` handler badges instead of swaps (G31 wiring) | 3h |
| 8 | `hideViewModeButtons?` prop on `DocTab` + `DocTabsBar`; Workbench Document tab gates on `activeDocTab` not `viewMode` (G32) | 1.5h |
| 9 | `MessageBubble` inline-card suppression when workspace mount is active; `ChatConfig.inlineCardMode` provider (G33) | 2h |
| 10 | `BrandAvatar` theme-token restyle; `userPhotoURL` prop threaded through chat page → list → bubble (G34) | 2h |
| 10b | `AGUIProvider`: add `hadTokenOnceRef`; gate `setTokenResolved(false)` on initial load; vitest covers (a) children stay mounted on user-ref change with stable token, (b) no extra GET `/messages` fires, (c) `HttpAgent` rebuilt with new bearer (G38) | 4h |
| 11 | Visual-regression / Vitest snapshot tests for `MessageBubble` + `Workbench` + `DefinitionList` + `InspectorPanel` | 4h |
| 12 | Manual smoke via [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md): full three-stage pipeline with workspace + MCP App + audit panels open simultaneously (chrome-devtools MCP) | 3h |
| 13 | Docs: workshop talking points addendum to [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md); template README pointers | 2h |
| 14 | `CLAUDE.md` mention of the Workbench + DefinitionList primitives | 1h |
| 15 | Resolve [SEQUENCE.md G22](./SEQUENCE.md) row → point at this doc | 0.5h |

**Total: ~35h ≈ 4.5d** (was 4d before G38).

## Testing Strategy

- **`DocumentHistoryPanel.test.tsx`** — render with 100 sessions; assert default-collapsed and that expanded body has `max-h-[25vh]` + `overflow-y-auto`.
- **`DefinitionList.test.tsx`** — render with 10 items; assert grid-template-columns is `minmax(120px, 160px) 1fr`; numeric items have `font-mono tabular-nums` class.
- **`JsonAsStructuredCard.test.tsx`** — scalar-heavy JSON routes through `DefinitionList`; nested JSON still routes through A2UI path.
- **`InspectorPanel.test.tsx`** — record with `name: "emit_invoice_extraction"`; assert OUTPUT shows `argsJson` not `resultContent`; assert label is "Emitted payload (function-as-schema)".
- **`Workbench.test.tsx`** — render with 4 tabs; switch active tab; assert non-active tabs have `display: none` (not unmounted); assert MCP App iframe `ref.current` is the same node before and after switch.
- **`MessageBubble.test.tsx`** — `userPhotoURL` present → renders `<img>`; null → renders initial chip; assert no hardcoded `from-teal-` / `from-amber-` classes survive.
- **Visual smoke**: drive the AP demo via chrome-devtools MCP per [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md):
  - Run the three-stage pipeline; verify Workbench tabs stay mounted across `surface_action` events.
  - Open Vendor Globe MCP App in `Vendor` tab; switch to `Workspace`; switch back; assert no re-init (no second `ui/initialize` postMessage).
  - Open audit view on `emit_invoice_extraction`; assert structured payload visible on both sides.
  - Sign in with a Google account; assert user photo renders in chat bubbles.

## Success Criteria

- [ ] `DocumentHistoryPanel` with 100 sessions still shows `DocumentPanel` within the viewport (G28).
- [ ] All scalar JSON payloads in chat + audit render ledger-aligned via `DefinitionList` (G29).
- [ ] Audit View for `emit_invoice_extraction` (and siblings) shows the structured payload on OUTPUT, labelled "Emitted payload (function-as-schema)" (G30).
- [ ] Switching Workbench tabs preserves MCP App iframe state — no `ui/initialize` re-handshake (G31).
- [ ] `surface_action` events badge the relevant Workbench tab; do not swap panes (G31).
- [ ] viewMode buttons hidden in Workbench mode; freshly-clicked doc tabs open the Workbench Document tab regardless of `viewMode` (G32).
- [ ] Each `emit_*` payload renders **once** (workspace surface card) by default; inline-card mode is opt-in via `ChatConfig` (G33).
- [ ] No hardcoded `from-teal-`/`from-amber-` survive in `BrandAvatar` or `MessageBubble`; `user.photoURL` from `useAuth` reaches `MessageBubble` (G34).
- [ ] Vitest covering `AGUIProvider`: with a stable token, a `user`-reference change does **not** unmount children, does **not** trigger an extra GET `/messages`, **does** rebuild `HttpAgent` with the new bearer atomically (G38).
- [ ] Visible smoke (drive via [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md)): force a token refresh mid-conversation by simulating a `user`-reference change in the AGUIProvider; assert no chat-bubble flicker (G38).
- [ ] [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) talking points 6, 7 (layout primitives, tabbed workbench) added.
- [ ] All existing tests pass; visual smoke via [aitana-frontend-verify](../../../.claude/skills/aitana-frontend-verify/SKILL.md) green.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — items G28–G34 registered here; resolves G22 follow-up row
- [template-protocol-defaults.md](./template-protocol-defaults.md) — G24 function-as-schema is the source of truth that G30's audit-pane fix renders; G26 callback-composition makes G33's "single source of truth for the payload" reliable
- [template-mcp-apps-artefacts.md](./template-mcp-apps-artefacts.md) — Workbench tabs (G31) preserve MCP App iframe state that the artefact handshake depends on; pairs with that doc's `useSandboxedIframeMessages` hook
- [template-mcp-apps-artefact-quality.md](./template-mcp-apps-artefact-quality.md) — pairs at the artefact-quality layer (theme.css, default seed state)
- [aitana-frontend-verify skill](../../../.claude/skills/aitana-frontend-verify/SKILL.md) — operating manual for the chrome-devtools MCP smoke
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — workshop tracker; absorbs G28–G34 as worked examples
