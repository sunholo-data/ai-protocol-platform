"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatMessageListWithSurfaces } from "@/components/chat/ChatMessageList";
import type { DocTabData } from "@/components/doc-browser/DocTab";
import { DocListView } from "@/components/doc-browser/DocListView";
import { DocTabsBar } from "@/components/doc-browser/DocTabsBar";
import { UploadDropZone } from "@/components/doc-browser/UploadDropZone";
import type { ParsedDocument } from "@/hooks/useDocBrowser";
import type { User } from "@/lib/firebase";
import {
  useSkillAgent,
  type StreamError,
  type ToolCallState,
  type DelegationMarkerItem,
  type CompactionNoticeItem,
} from "@/hooks/useSkillAgent";
import {
  subscribeSkillSwitchIntent,
  stashPendingSkillSwitch,
  readPendingSkillSwitch,
  clearPendingSkillSwitch,
} from "@/lib/skillSwitch";
import { ActivityPanel, type ActivityContext, type ActivityDoc } from "@/components/chat/ActivityPanel";
import { useSkillMeta } from "@/hooks/useSkillMeta";
import { useSessionMessages, type A2uiSurfaceReplay } from "@/hooks/useSessionMessages";
import { useSessionDocuments } from "@/hooks/useSessionDocuments";
import { useSessionActivity } from "@/hooks/useSessionActivity";
import { useStableThreadId } from "@/hooks/useStableThreadId";
import { fetchWithAuth } from "@/lib/apiClient";
import { importByReference, isImportError } from "@/lib/importByReference";
import { docIdentityFromToolCall, docIdentityKey } from "@/lib/docFromToolCall";
import {
  useResizableWorkspaceRatio,
  readStoredCollapsed,
  writeStoredCollapsed,
} from "@/hooks/useResizableWorkspaceRatio";
import { WorkbenchResizeHandle } from "@/components/chat/WorkbenchResizeHandle";
import { useBackendReady } from "@/hooks/useBackendReady";
import { computeIncludedDocIds } from "@/lib/docContext";
import { notifySessionsChanged, subscribeSessionsChangedDetailed } from "@/lib/sessionEvents";
import { useSkillSessions } from "@/hooks/useSkillSessions";
import { SkillSessionPanel } from "@/components/chat/SkillSessionPanel";
import DocumentHistoryPanel from "@/components/chat/DocumentHistoryPanel";
import { SidebarSection } from "@/components/chat/SidebarSection";
import { InContextBadge } from "@/components/chat/InContextBadge";
import { Workbench, type WorkbenchTab } from "@/components/chat/Workbench";
import { WorkbenchHome } from "@/components/chat/WorkbenchHome";
import { AssistantIntroBubble } from "@/components/chat/AssistantIntroBubble";
import { SkillExamplesPicker } from "@/components/chat/SkillExamplesPicker";
import { GCSFileBrowser } from "@/components/doc-browser/GCSFileBrowser";
import type { ExampleDocument, ExamplePrompt } from "@/types/skill";
import {
  type A2uiArtifact,
  type A2uiArtifactEntry,
  type A2uiV09Message,
  SurfaceRegistryProvider,
  useArtifacts,
  useClearSurfacesOnSessionChange,
  useSurfaceRegistry,
  useSurfaceState,
} from "@/providers/SurfaceRegistry";
import { useAGUIAgent } from "@/providers/AGUIProvider";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { CompareLauncher, type CompareDocIdentity } from "@/components/workspace/CompareLauncher";
import type { ActionRunActivitySink } from "@/hooks/useActionDrivenAgent";
import { ObligationArtefactTab } from "@/components/workspace/ObligationArtefactTab";
import { SourcesArtefactTab } from "@/components/workspace/SourcesArtefactTab";
import { ClausesArtefactTab } from "@/components/workspace/ClausesArtefactTab";
import { SeriesArtefactTab } from "@/components/workspace/SeriesArtefactTab";
import { shouldShowCompareLauncher } from "@/lib/compareLauncher";
import { forgetFocusedResult, nextFocusedResult } from "@/lib/workbenchFocus";
import { DocumentPanel } from "@/components/document/DocumentPanel";
import { LatencyHUD } from "@/components/dev/LatencyHUD";

/**
 * v6.23.0 WORKSPACE-HOME-PERSISTENCE — workbench tab ids for the two jobs the
 * Workspace tab used to do at once.
 *
 * `HOME_TAB_ID` is permanent furniture: the launcher, the examples/prompts
 * picker and the index of this session's results. It is never replaced by a
 * result, and it is never an auto-focus target.
 *
 * `WORKSPACE_RESULT_TAB_ID` carries the dominant `workspace` A2UI surface — the
 * one result kind that was exempt from the 7.5 artifact-tab model and therefore
 * the one that destroyed Home. Note it is deliberately NOT the surface id: the
 * surface is still `"workspace"` (one A2UISurfaceMount per surfaceId), only the
 * TAB it lives in is addressed separately, so the ids can't collide.
 */
const HOME_TAB_ID = "workspace";
const WORKSPACE_RESULT_TAB_ID = "workspace-result";

/**
 * Index row for the promoted workspace surface, so Home can jump to it the same
 * way it jumps to any other result. `surfaceId` here is the TAB id, because
 * that is what `WorkbenchHome.onOpen` hands to `onWorkbenchTabChange` — the
 * index addresses tabs, not surfaces.
 */
const WORKSPACE_RESULT_INDEX_ENTRY = {
  surfaceId: WORKSPACE_RESULT_TAB_ID,
  createdAt: 0,
  kind: "workspace",
  title: "Assistant",
  description: "The assistant's structured output for this conversation",
} as const;

/**
 * MULTI-SURFACE-A2UI M3 — chat page surface mounts.
 *
 * The chat page wraps in <SurfaceRegistryProvider> and declares mounts for
 * the four named A2UI surfaces. Each mount is conditional on having content
 * — empty surfaces don't add visible DOM. Layout intent:
 *   - workspace : displaces or sits alongside the DocumentPanel (w-1/2 region)
 *   - sidebar   : appends to the bottom of the existing aside
 *   - modal     : fixed-position overlay at page root (M4 wires the
 *                 user-gesture guard; M3 just shows it when populated)
 */
function WorkspaceSurfaceRegion({ sessionId }: { sessionId: string | null }) {
  const state = useSurfaceState("workspace");
  if (!state?.surface) return null;
  // Workspace is a flex sibling of the chat panel. Each gets `flex-1
  // min-w-0` so they share the parent row proportionally and BOTH can
  // shrink below their natural content size when the viewport is narrow.
  // `max-w-xl` caps the workspace so it doesn't dominate on wide screens;
  // the chat is the primary interaction surface and shouldn't be squeezed
  // by small dashboard content. Cap is generous (576px) — forks with
  // larger dashboards override via SurfaceRegistry policy.
  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden border-r md:max-w-xl">
      <div className="min-h-0 flex-1 overflow-auto p-3">
        <A2UISurfaceMount
          surfaceId="workspace"
          className="h-full"
          sessionId={sessionId}
        />
      </div>
    </div>
  );
}

function SidebarSurfaceRegion({ sessionId }: { sessionId: string | null }) {
  const state = useSurfaceState("sidebar");
  if (!state?.surface) return null;
  return (
    <div className="border-t px-2 py-2">
      <A2UISurfaceMount surfaceId="sidebar" sessionId={sessionId} />
    </div>
  );
}

function ModalSurfaceRegion({ sessionId }: { sessionId: string | null }) {
  const state = useSurfaceState("modal");
  if (!state?.surface) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6">
      <div className="max-w-xl rounded-lg border bg-background p-4 shadow-xl">
        <A2UISurfaceMount surfaceId="modal" sessionId={sessionId} />
      </div>
    </div>
  );
}

/**
 * MULTI-SURFACE-A2UI M4 — wires the session-id transition to surface
 * lifecycle policy. When the user starts/switches sessions, session-scoped
 * surfaces (workspace, sidebar by default) clear automatically. The hook
 * is idempotent on the same sessionId so it won't fire on render.
 */
function SurfaceSessionLifecycle({ sessionId }: { sessionId: string | null }) {
  useClearSurfacesOnSessionChange(sessionId);
  return null;
}

/**
 * Routes backend-pushed A2UI surface events into the SurfaceRegistry
 * (tool-results-as-a2ui / 7.3, Model B). MUST live INSIDE
 * `SurfaceRegistryProvider` — `useSkillAgent` runs in ChatShell's body, which
 * is ABOVE this provider in the tree, so its `useOptionalSurfaceRegistry()`
 * returns null and can't reach the registry. This component subscribes to the
 * same AG-UI agent from inside the provider and appends the `A2UI_SURFACE`
 * messages so `A2UISurfaceMount("workspace")` draws them. Renders nothing.
 */
/**
 * Reports whether ANY A2UI artifact (chat elicitation form OR workbench result)
 * exists, so ChatShell can PIN the session to the URL. The action-driven
 * obligation flow (launcher / form = surface-action-run) produces artifacts but
 * NO chat messages, so the message-gated URL pin never fired and a refresh lost
 * the history + surfaces. Must live INSIDE the provider. Renders nothing.
 */
function ArtifactPresenceReporter({ onPresent }: { onPresent: (present: boolean) => void }) {
  const present = useArtifacts().length > 0;
  useEffect(() => {
    onPresent(present);
  }, [present, onPresent]);
  return null;
}

function WorkspaceA2uiEventRouter() {
  const agent = useAGUIAgent();
  const registry = useSurfaceRegistry();
  useEffect(() => {
    // Defensive: the agent is always present under AGUIProvider in the app, but
    // some tests mock useAGUIAgent to undefined — don't crash the workbench.
    if (!agent?.subscribe) return;
    const sub = agent.subscribe({
      onCustomEvent: ({ event }: { event: { name?: unknown; value?: unknown } }) => {
        if (event?.name !== "A2UI_SURFACE" || !event.value || typeof event.value !== "object") {
          return;
        }
        const v = event.value as {
          surfaceId?: unknown;
          messages?: unknown;
          sourceId?: unknown;
          artifact?: unknown;
        };
        const surfaceId = typeof v.surfaceId === "string" && v.surfaceId ? v.surfaceId : "workspace";
        const sourceId =
          typeof v.sourceId === "string" && v.sourceId
            ? v.sourceId
            : `custom-a2ui-${surfaceId}-${crypto.randomUUID()}`;
        // 7.5: optional artifact metadata drives the workbench tab title + index.
        const artifact =
          v.artifact && typeof v.artifact === "object" ? (v.artifact as A2uiArtifact) : null;
        if (Array.isArray(v.messages) && v.messages.length > 0) {
          registry.appendMessages(surfaceId, v.messages as A2uiV09Message[], sourceId, artifact);
        }
      },
    });
    return () => sub.unsubscribe();
  }, [agent, registry]);
  return null;
}

/**
 * Resume rehydration (7.5 M3) — replays the workbench surfaces persisted in
 * session state (fetched by `useSessionMessages`) into the SurfaceRegistry so a
 * page refresh / resume brings back the per-result tabs + Workspace index
 * WITHOUT re-running any tool. Lives INSIDE the provider (like
 * `WorkspaceA2uiEventRouter`) so it can reach the registry.
 *
 * Idempotent by construction: `appendMessages` dedupes on the stashed
 * `sourceId` (the same id the live event used), so replaying a surface the live
 * stream already delivered is a no-op. `createdAt` is restored from the stash so
 * the index timeline keeps its original order. Gated on `enteredViaResume` to
 * mirror `initialMessages` (a fresh chat's surfaces are already live in the
 * registry; only a resume needs to rebuild them).
 */
function RehydrateSurfaces({
  surfaces,
  enabled,
}: {
  surfaces: A2uiSurfaceReplay[];
  enabled: boolean;
}) {
  const registry = useSurfaceRegistry();
  useEffect(() => {
    if (!enabled || surfaces.length === 0) return;
    for (const s of surfaces) {
      if (!s.surfaceId || !Array.isArray(s.messages) || s.messages.length === 0) continue;
      const sourceId = s.sourceId || `rehydrate-${s.surfaceId}`;
      const baseArtifact = s.artifact && typeof s.artifact === "object" ? (s.artifact as A2uiArtifact) : null;
      // Mark as replayed so interactive chat cards (confirm/elicitation) render
      // frozen rather than a live, misleading "Proceed" (v6.10.0).
      const artifact: A2uiArtifact = { ...(baseArtifact ?? {}), replayed: true };
      registry.appendMessages(
        s.surfaceId,
        s.messages as A2uiV09Message[],
        sourceId,
        artifact,
        typeof s.createdAt === "number" ? s.createdAt : undefined,
      );
    }
  }, [surfaces, enabled, registry]);
  return null;
}

/**
 * v6.4.0 ITERATION 2026-06-09: Workbench right pane wrapper.
 * Lives INSIDE SurfaceRegistryProvider scope so `useSurfaceState` works.
 * Renders 2 tabs (Workspace + Document) with EmptyTab fallbacks.
 */
function WorkbenchPane({
  activeTabId,
  sessionId,
  skillId,
  userUid,
  workbenchTabId,
  onWorkbenchTabChange,
  onSelectSession,
  onNewSession,
  welcomeExamples,
  welcomePrompts = [],
  isFreshChat,
  canDelegate,
  onPickExample,
  onPickPrompt,
  onOpenLauncherDoc,
  onOpenSource,
  onSurfaceChatMessage,
  compareOptedIn,
  allowCompareLauncher,
  allowObligationsLauncher,
  docTabs,
  workbenchClassName,
  onContentChange,
  toolCalls,
  delegations,
  compactions,
  isThinking,
  activityContext,
  activityDocuments,
  sessionStartTs,
  activitySink,
  actionRunning,
  actionStageLabel,
  actionError,
}: {
  activeTabId: string | null;
  sessionId: string | null;
  skillId: string;
  userUid: string;
  workbenchTabId: string;
  onWorkbenchTabChange: (id: string) => void;
  /** SKILL-DELEGATION M3b — activity feed inputs for the Activity tab. */
  toolCalls: ToolCallState[];
  delegations: DelegationMarkerItem[];
  /** COMPACTION-WIRE M4 — history compactions, rendered in the Activity feed. */
  compactions?: CompactionNoticeItem[];
  isThinking: boolean;
  /** Model + voice config for the Activity context row. */
  activityContext?: ActivityContext;
  /** Session documents → "document added" activity entries. */
  activityDocuments?: ActivityDoc[];
  /** Session start time → "Session started" activity marker. */
  sessionStartTs?: number;
  /** ACTIVITY-OBS — sink threaded into the workbench CompareLauncher so a
   * launcher-triggered run reports its live progress to the Activity tab. */
  activitySink?: ActionRunActivitySink;
  /** ACTIVITY-OBS — an action-triggered run is in flight. Drives the Activity
   * tab's live "Running…" row + auto-focus so the run is never silent. */
  actionRunning?: boolean;
  /** Stage label for the in-flight action run (Activity running row). */
  actionStageLabel?: string | null;
  /** Last action-run error (Activity error row). */
  actionError?: string | null;
  onSelectSession: (sid: string) => void;
  onNewSession: () => void;
  /** v6.4.0 4.5 SKILL-ONBOARDING M2 — skill.welcome.exampleDocuments,
   * passed through from ChatShell which resolves it via useSkillMeta. */
  welcomeExamples: ExampleDocument[];
  /** v6.12.0 — skill.welcome.examplePrompts. First-look ACTION cards showing the
   * skill's real range (market data, comparison, analysis, research), not just
   * "import a document". */
  welcomePrompts?: ExamplePrompt[];
  /** True when chat has no messages + no resumed session. v6.23.0: this is now
   * the fallback gate for skills that CANNOT delegate — a front door keeps its
   * picker up for the whole conversation instead (see `canDelegate`). */
  isFreshChat: boolean;
  /** v6.23.0 — the skill can hand off to a specialist. Only a front door keeps
   * its example tiles on Workspace Home past the first turn; a specialist would
   * be advertising routing it cannot do. */
  canDelegate: boolean;
  /** Click handler — parent fires the synthetic intent message. */
  onPickExample: (example: ExampleDocument) => void;
  /** Send an action card's prompt as a normal chat message. */
  onPickPrompt?: (prompt: string) => void;
  /** Open the launcher-picked document in the Document tab (import-by-reference
   * for a bucket `gs_url`, focus for an already-open `doc_id`). */
  onOpenLauncherDoc?: (identity: CompareDocIdentity) => void;
  /** 6.15: open an enterprise-search (gs://) source in the Document tab (parse +
   * add to selected). Throws on failure so the Sources tab can show an error. */
  onOpenSource?: (bucket: string, objectName: string) => Promise<void>;
  /** PPA-COMPARE-LAUNCHER M2 — true when the active skill opts into
   * action-triggered runs (toolConfigs.a2ui.allow_action_triggered_runs).
   * Gates whether the workbench Compare launcher renders in the empty state. */
  compareOptedIn: boolean;
  /** PPA-OBLIGATION 7.6 M3 — the active skill supports contract comparison
   * (has `compare_ppa_contracts`). Gates the launcher's "Compare contracts"
   * button. */
  allowCompareLauncher: boolean;
  /** PPA-OBLIGATION 7.6 M3 — the active skill supports obligation analysis
   * (has `map_ppa_obligations`). Gates the launcher's "Analyze obligations"
   * button. */
  allowObligationsLauncher: boolean;
  /** Open document tabs — the Compare launcher's candidate list (doc_id side)
   * and its selection seed (each tab's `included` flag). */
  docTabs: DocTabData[];
  /** `chat:send` surface action → post a chat message (diff "Explain this"). */
  onSurfaceChatMessage?: (text: string) => void;
  /** Override the Workbench's default 4-breakpoint width scale. Pass ""
   * to let the parent control the width (used when WorkbenchPane sits
   * inside the resizable chat-row introduced 2026-06-11). */
  workbenchClassName?: string;
  /** 2026-06-11 auto-fold: WorkbenchPane reports whether it has any
   * meaningful content to render (workspace surface ⊕ open doc tab ⊕
   * fresh-chat examples). Parent uses this to hide the resize handle
   * and let chat take the full row when there's nothing in the
   * workbench worth showing. Lives here because the
   * useSurfaceState("workspace") check has to run inside the
   * SurfaceRegistryProvider, which only wraps the JSX subtree. */
  onContentChange?: (hasContent: boolean) => void;
}) {
  const workspaceSurface = useSurfaceState("workspace");
  // 7.5 workbench artifacts: each tool result is its own artifact surface (with
  // metadata) → its own workbench tab. `useArtifacts()` is the reactive, ordered
  // list. A plain skill that emits to the bare `workspace` surface (no artifact
  // metadata) still gets the single Workspace tab (below) — graceful fallback.
  const artifacts = useArtifacts();
  // Chat-placement artifacts (the obligation elicitation form, 7.8 M1) render
  // inline in the chat thread (ChatPlacementForms), NOT as workbench tabs —
  // exclude them from every workbench-facing derivation below.
  const workbenchArtifacts = artifacts.filter((a) => a.placement !== "chat");
  const hasArtifacts = workbenchArtifacts.length > 0;
  const workspaceHasContent = Boolean(workspaceSurface?.surface);
  // v6.4.0 4.5 SKILL-ONBOARDING M2 → v6.23.0 WORKSPACE-HOME-PERSISTENCE.
  // The picker used to be an onboarding affordance that a first message
  // (isFreshChat) or any agent surface switched off. Dana's UAT point was
  // exactly that it should NOT be: "it would be nice to also have the list of
  // skills this assistant can do for OTHER ITERATIONS, instead of creating a
  // new chat."
  //
  // But that only holds for a FRONT DOOR. Those tiles ("Compare two PPAs", "Run
  // an obligation analysis") are delegation prompts — the door answers them by
  // handing off to a specialist. A specialist skill cannot hand off, so leaving
  // its tiles up mid-conversation would advertise work it can't route (CLAUDE.md
  // #8: never a dead end). Specialists therefore keep the ORIGINAL first-turn
  // onboarding behaviour; only skills with a delegation allow-list persist.
  const hasPickerContent = welcomeExamples.length > 0 || welcomePrompts.length > 0;
  const showPicker = hasPickerContent && (canDelegate || isFreshChat);
  // PPA-COMPARE-LAUNCHER M2: the Compare launcher renders on Workspace Home for
  // opted-in skills that own a launcher-capable tool (`optedIn` alone covers
  // elicitation-only front doors since 44c426c, which must fall through to the
  // examples/prompts picker). v6.23.0 removed its artifact/surface gates — see
  // lib/compareLauncher.ts.
  const showLauncher = shouldShowCompareLauncher({
    optedIn: compareOptedIn,
    allowCompare: allowCompareLauncher,
    allowObligations: allowObligationsLauncher,
  });

  // Every result tab id this session has already auto-focused once. Shared by
  // BOTH auto-focus effects below and released only by an explicit user close,
  // so "new" means *never seen*, not "absent from the previous render". See the
  // B4 note on the artifact effect for why that distinction is the bug fix.
  const seenArtifactIdsRef = useRef<Set<string>>(new Set());

  // Repo principle #7 — auto-focus new workbench elements. When the dominant
  // workspace surface first arrives, switch focus to ITS Result tab, once.
  // (Superseded the 2026-06-11 badge-don't-switch behaviour.)
  //
  // v6.23.0: the target moved from "workspace" (Home) to WORKSPACE_RESULT_TAB_ID.
  // Before, the surface WAS the Workspace tab, so focusing it destroyed the
  // launcher. Now Home is never the auto-focus target — which also means a tab
  // the user deliberately clicked can no longer be stolen by an arriving
  // surface (relevant to B4, "Workspace tab sometimes needs a second click").
  //
  // Same B4 blink hazard as the artifact effect below: `workspaceHasContent`
  // reads `state.surface`, which is momentarily null across a re-registration.
  // Latching "we have already focused this surface once" (rather than comparing
  // against the previous render) means a blink can't re-steal focus. The latch
  // is released only by an explicit close, via `seenArtifactIdsRef`.
  const [workspaceBadged, setWorkspaceBadged] = useState(false);
  useEffect(() => {
    if (!workspaceHasContent) return;
    // Same rule, same latch as the artifact tabs — the promoted workspace
    // surface is a result like any other.
    const focusId = nextFocusedResult([WORKSPACE_RESULT_TAB_ID], seenArtifactIdsRef.current);
    if (!focusId) return;
    if (workbenchTabId !== focusId) onWorkbenchTabChange(focusId);
    setWorkspaceBadged(false);
  }, [workspaceHasContent, workbenchTabId, onWorkbenchTabChange]);
  useEffect(() => {
    if (workbenchTabId === "workspace") setWorkspaceBadged(false);
  }, [workbenchTabId]);

  // 7.5: auto-focus the newest artifact tab when a NEW artifact surface arrives
  // (repo principle #7 — auto-focus new workbench elements). extract → extract →
  // compare fills tabs; focus tracks the latest one.
  //
  // v6.23.0 B4 — "Workspace tab sometimes needs a second click" (Dana; Mark had
  // seen it too). This ref used to be REPLACED with the current id set on every
  // run (`prevArtifactIdsRef.current = currentIds`). `listArtifacts()` only
  // returns surfaces whose `state.surface` is non-null, so an artifact that
  // blinks out for a single render — any re-registration of its surface — was
  // dropped from the ref and then read as brand NEW when it came back, stealing
  // focus a second time. If the user had clicked Home in between, their click
  // was silently undone: exactly the "it needed a second click" symptom.
  //
  // "New" must therefore mean *never seen in this session*. The rule lives in
  // lib/workbenchFocus.ts so it can be tested against the blink sequence
  // directly — see that file's header for the full mechanism.
  useEffect(() => {
    const focusId = nextFocusedResult(
      workbenchArtifacts.map((a) => a.surfaceId),
      seenArtifactIdsRef.current,
    );
    if (focusId) onWorkbenchTabChange(focusId);
  }, [workbenchArtifacts, onWorkbenchTabChange]);

  // SKILL-DELEGATION M3b: badge the Activity tab when new activity (a tool
  // call, a delegation, or a pushed activity surface) arrives while the user
  // is on another tab. Lights up, then clears the moment they open Activity.
  const activitySurface = useSurfaceState("activity");
  const [activityBadged, setActivityBadged] = useState(false);
  const prevActivityCountRef = useRef(0);
  const activityCount = toolCalls.length + delegations.length + (activitySurface?.surface ? 1 : 0);
  useEffect(() => {
    if (activityCount > prevActivityCountRef.current && workbenchTabId !== "activity") {
      setActivityBadged(true);
    }
    prevActivityCountRef.current = activityCount;
  }, [activityCount, workbenchTabId]);
  useEffect(() => {
    if (workbenchTabId === "activity") setActivityBadged(false);
  }, [workbenchTabId]);

  // ACTIVITY-OBS — auto-focus the Activity tab the moment an action-triggered
  // run starts (false→true) so the user watches its tool calls stream live
  // ("make it obligatory that we see events come through"). The run's result
  // artifact lands moments later and the artifact auto-focus effect above then
  // switches to that tab — so the flow is: click launcher → Activity (live
  // tools) → Result. Fires once per run start; if the user navigates away it
  // won't yank them back until the next run.
  const prevActionRunningRef = useRef(false);
  useEffect(() => {
    if (actionRunning && !prevActionRunningRef.current && workbenchTabId !== "activity") {
      onWorkbenchTabChange("activity");
    }
    prevActionRunningRef.current = Boolean(actionRunning);
  }, [actionRunning, workbenchTabId, onWorkbenchTabChange]);

  // Close a Result tab. Every RESULT tab is closable (generically, by the 7.5
  // artifact model — never keyed on `kind`); the FIXED structural tabs (Home,
  // Document, Activity) are not, because they're the pane's furniture rather
  // than results.
  //
  // Confirm first: a result may have cost a real BigQuery job or a paid
  // analysis and is not cheaply regenerable, so this is a destructive act.
  // `window.confirm` matches the existing idiom in this file
  // (handleDeleteSkillSession, the make-public gate) — not a new modal system.
  // The prompt names the result by its FRIENDLY title (CLAUDE.md #9), never the
  // surfaceId.
  //
  // v6.23.0: takes (surfaceId, tabId, name) rather than an A2uiArtifactEntry so
  // the promoted `workspace` surface — which has no artifact metadata, and whose
  // tab id differs from its surface id — closes through the SAME path as every
  // other result instead of getting a second special case.
  const registry = useSurfaceRegistry();
  const handleCloseResult = useCallback(
    (surfaceId: string, tabId: string, name: string) => {
      if (
        !window.confirm(
          `Close "${name}"?\n\n` +
            `This removes the result from your workbench and can't be undone. ` +
            `Getting it back means re-running the query or analysis.`,
        )
      ) {
        return;
      }
      registry.clearSurface(surfaceId);
      // Forget the auto-focus latch (B4). A CLOSE is deliberate, so if the agent
      // later re-emits this result it counts as a genuinely new arrival and
      // should take the stage — unlike the transient blink the latch exists to
      // absorb.
      forgetFocusedResult(tabId, seenArtifactIdsRef.current);
      // NEVER SILENT (#8): if the user closed the tab they were looking at,
      // focus lands on Home — never a blank pane or a tab id that no longer
      // resolves. Home always exists, so this is now always a real destination.
      if (workbenchTabId === tabId) onWorkbenchTabChange(HOME_TAB_ID);
    },
    [registry, workbenchTabId, onWorkbenchTabChange],
  );
  const handleCloseArtifact = useCallback(
    (artifact: A2uiArtifactEntry) =>
      handleCloseResult(
        artifact.surfaceId,
        artifact.surfaceId,
        artifact.title || artifact.kind || "this result",
      ),
    [handleCloseResult],
  );

  // 2026-06-11 auto-fold: report to the parent whether anything worth
  // rendering lives in the workbench. When false the parent hides the
  // resize handle and lets chat take the full row.
  // A delegation or a backend-pushed activity surface is notable enough to
  // reveal the workbench (with the Activity tab); plain tool calls are not —
  // they'd pop the pane too often and are already shown inline as chips.
  //
  // v6.23.0: `showLauncher`/`showPicker` no longer switch off after the first
  // turn, so for a skill that ships a launcher or examples the pane now stays
  // open for the whole conversation instead of auto-folding once the chat
  // starts. That is the requested behaviour — Home is only "≤1 click away" if
  // the pane is there — and the user keeps an explicit escape hatch: the
  // collapse chevron, which reveals the re-open rail (workbenchCollapsed).
  const hasNotableActivity =
    delegations.length > 0 || Boolean(activitySurface?.surface) || Boolean(actionRunning);
  const hasContent =
    workspaceHasContent ||
    hasArtifacts ||
    activeTabId !== null ||
    showLauncher ||
    showPicker ||
    hasNotableActivity;
  useEffect(() => {
    onContentChange?.(hasContent);
  }, [hasContent, onContentChange]);
  if (!hasContent) return null;
  // 7.5: one workbench tab per artifact surface (titled by its metadata),
  // rendered by the generic A2UISurfaceMount. These sit BESIDE the permanent
  // Home tab — as of v6.23.0 nothing replaces it, including the bare
  // `workspace` surface, which gets an equivalent tab of its own below.
  const artifactTabs: WorkbenchTab[] = workbenchArtifacts.map((a) => ({
    id: a.surfaceId,
    eyebrow: "Result",
    // Label is the tool/kind ("Clauses", "Comparison") — NOT the filename (that
    // duplicates the Document tabs). Full detail (filename, counts) is the
    // hover tooltip. Multiple same-kind tabs are fine — ids stay distinct.
    label: a.title || a.kind || "Result",
    tooltip: a.description || a.title,
    onClose: () => handleCloseArtifact(a),
    content:
      // 7.6 M3: a successful obligation mapping mounts the verified WASM
      // artefact (StaticArtefactFrame) with the real extracted payload, NOT the
      // generic A2UI surface. Every OTHER artifact (clauses, comparison, the
      // obligation-refusal panel) renders via the generic A2UISurfaceMount.
      a.kind === "obligation-analysis" ? (
        <ObligationArtefactTab surfaceId={a.surfaceId} className="h-full" sessionId={sessionId} />
      ) : a.kind === "sources" ? (
        // 6.11: web-search Sources render as a clean, clickable domain list
        // (Basic-catalog Text can't do links), reading the raw list from the
        // surface data model. See SourcesArtefactTab / a2ui_sources_render.py.
        // 6.15: enterprise (gs://) sources open the real document in the Document
        // tab + add to selected, via the import-by-reference path.
        <SourcesArtefactTab surfaceId={a.surfaceId} className="h-full" onOpenSource={onOpenSource} />
      ) : a.kind === "clauses" ? (
        // 6.11: PPA clause extraction renders as a professional table with
        // confidence badges, from the surface data model. See ClausesArtefactTab.
        <ClausesArtefactTab surfaceId={a.surfaceId} className="h-full" />
      ) : a.kind === "prices" ? (
        // 6.12 PRICES-WORKSPACE M4: a market price query renders as a chart +
        // sortable table + CSV, read from the DECLARED SERIES envelope in the
        // surface data model. The tab holds no ENTSO-E knowledge — any
        // dataset-shaped tool that declares `x`/`y` reuses it, so this branch is
        // keyed on the artifact `kind`, never on the tool name.
        // See SeriesArtefactTab / a2ui_entsoe_render.py.
        <SeriesArtefactTab surfaceId={a.surfaceId} className="h-full" />
      ) : (
        <div className="h-full p-3">
          <A2UISurfaceMount
            surfaceId={a.surfaceId}
            className="h-full"
            sessionId={sessionId}
            skillId={skillId}
            onChatMessage={onSurfaceChatMessage}
          />
        </div>
      ),
    emptyBody: a.description || "This result's view will appear here.",
  }));
  // v6.23.0 WORKSPACE-HOME-PERSISTENCE — the dominant `workspace` A2UI surface
  // gets its own Result tab, exactly like a clauses/prices/sources artifact.
  //
  // This is the special case being DELETED, not added: every other result kind
  // already lived in `artifactTabs` via the 7.5 model. The bare `workspace`
  // surface was the sole exemption, and it was the one that overwrote Home. It
  // can't join `artifactTabs` literally (it carries no artifact metadata, so
  // `listArtifacts()` never returns it), so it gets an equivalent tab built the
  // same way and appended to the same list.
  //
  // Naming: eyebrow "Result" puts it in the same family as the artifact tabs;
  // "Assistant" is the label the old code already used for this content
  // (`eyebrow: workspaceHasContent ? "Assistant" : "Home"`), kept so the tab
  // strip reads as a friendly name rather than a surface id (CLAUDE.md #9).
  const workspaceResultTab: WorkbenchTab | null = workspaceSurface?.surface
    ? {
        id: WORKSPACE_RESULT_TAB_ID,
        eyebrow: "Result",
        label: "Assistant",
        tooltip: "The assistant's structured output for this conversation",
        onClose: () => handleCloseResult("workspace", WORKSPACE_RESULT_TAB_ID, "Assistant"),
        content: (
          <div className="h-full p-3">
            <A2UISurfaceMount
              surfaceId="workspace"
              className="h-full"
              sessionId={sessionId}
              skillId={skillId}
              onChatMessage={onSurfaceChatMessage}
            />
          </div>
        ),
        emptyBody: "This result's view will appear here.",
      }
    : null;

  // Home content. v6.11.0 made this a curated index; v6.23.0 makes it PERMANENT
  // and COMPOSED rather than a fallback chain. Launcher, picker and results
  // index stack — they answer different questions ("what can I start", "what
  // examples are there", "what have I got") and a user needs all three
  // throughout a conversation, not one at a time.
  //
  // Critically: the dominant workspace surface is NOT in here any more. That
  // single condition (`showHome = !workspaceHasContent && hasArtifacts`) was the
  // whole bug — Dana, 2026-08-06 UAT, raised 4×.
  const showIndex = hasArtifacts || Boolean(activeTabId);
  const homeContent =
    showLauncher || showPicker || showIndex ? (
      <div className="flex h-full min-h-0 flex-col gap-1 overflow-auto">
        {showLauncher && (
          <CompareLauncher
            sessionId={sessionId}
            skillId={skillId}
            optedIn={compareOptedIn}
            allowCompare={allowCompareLauncher}
            allowObligations={allowObligationsLauncher}
            docTabs={docTabs}
            exampleDocuments={welcomeExamples}
            onCompareViaChat={(text) => onSurfaceChatMessage?.(text)}
            onAnalyzeViaChat={(text) => onSurfaceChatMessage?.(text)}
            onOpenDocument={onOpenLauncherDoc}
            activitySink={activitySink}
          />
        )}
        {showPicker && (
          <SkillExamplesPicker
            examples={welcomeExamples}
            onPickExample={onPickExample}
            prompts={welcomePrompts}
            onPickPrompt={onPickPrompt}
          />
        )}
        {showIndex && (
          <WorkbenchHome
            artifacts={workspaceResultTab ? [...workbenchArtifacts, WORKSPACE_RESULT_INDEX_ENTRY] : workbenchArtifacts}
            onOpen={onWorkbenchTabChange}
            openDocId={activeTabId}
            onOpenDocument={() => onWorkbenchTabChange("document")}
          />
        )}
      </div>
    ) : null;
  const workspaceEmptyBody =
    "The assistant's structured outputs — clause cards, comparisons, charts — appear here as it works on your question.";

  // The Home tab. Permanent furniture: never closable, never an auto-focus
  // target, never replaced by a result. Tab stays labelled "Workspace" (v6.11.0
  // OQ4) so ONE doesn't have to re-learn a name they already know; the eyebrow
  // carries the distinction from the Result tabs beside it.
  const homeTab: WorkbenchTab = {
    id: HOME_TAB_ID,
    eyebrow: "Home",
    label: "Workspace",
    badged: workspaceBadged,
    content: homeContent,
    emptyBody: workspaceEmptyBody,
  };

  const tabs: WorkbenchTab[] = [
    homeTab,
    ...artifactTabs,
    ...(workspaceResultTab ? [workspaceResultTab] : []),
    {
      id: "document",
      label: "Document",
      content: activeTabId ? (
        <div className="flex h-full flex-col">
          <div className="min-h-0 flex-1 overflow-auto">
            <DocumentPanel docId={activeTabId} />
          </div>
          <DocumentHistoryPanel
            documentId={activeTabId}
            activeSessionId={sessionId}
            currentUserUid={userUid}
            onSelectSession={onSelectSession}
            onNewSession={onNewSession}
            onDeleteActive={onNewSession}
          />
        </div>
      ) : null,
      emptyBody:
        "Click a document in the sidebar to read it here alongside the conversation.",
    },
    {
      id: "activity",
      eyebrow: "Live",
      label: "Activity",
      badged: activityBadged,
      content: (
        <ActivityPanel
          toolCalls={toolCalls}
          delegations={delegations}
          isThinking={isThinking}
          sessionId={sessionId}
          context={activityContext}
          documents={activityDocuments}
          sessionStartTs={sessionStartTs}
          isRunning={actionRunning}
          runStageLabel={actionStageLabel}
          runError={actionError}
          compactions={compactions}
        />
      ),
    },
  ];
  return (
    <Workbench
      tabs={tabs}
      activeTabId={workbenchTabId}
      onActiveTabChange={onWorkbenchTabChange}
      className={workbenchClassName}
    />
  );
}

function StreamErrorBanner({
  error,
  onRetry,
  onDismiss,
}: {
  error: StreamError;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  // Rate-limit / quota renders amber ("wait & retry") to visually distinguish a
  // key/quota issue from a red "the demo is broken" error.
  const amber = error.kind === "rate_limited";
  const boxTone = amber
    ? "border-amber-500/40 bg-amber-500/10 text-amber-700"
    : "border-destructive/40 bg-destructive/10 text-destructive";
  const btnTone = amber
    ? "border-amber-500/40 hover:bg-amber-500/20"
    : "border-destructive/40 hover:bg-destructive/20";
  return (
    <div className={`inline-block max-w-[80%] space-y-2 rounded-md border px-3 py-2 text-sm ${boxTone}`}>
      <p>{error.message}</p>
      <div className="flex gap-2">
        {error.retryable && (
          <button
            type="button"
            onClick={onRetry}
            className={`rounded border px-2 py-0.5 text-xs ${btnTone}`}
          >
            Try again
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="rounded border border-destructive/20 px-2 py-0.5 text-xs text-destructive/70 hover:bg-destructive/10"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

export function ChatShell({
  skillId,
  pathPrefix,
  user,
}: {
  skillId: string;
  pathPrefix: string;
  user: User;
}) {
  const {
    sessionId: agentSessionId,
    messages,
    toolCalls,
    thinkingContent,
    isThinking,
    stageLabel,
    delegations,
    compactions,
    tidyingUp,
    resolvedModel,
    sendMessage,
    isLoading,
    error,
    clearError,
    stop,
  } = useSkillAgent();
  const {
    displayName,
    avatar: skillAvatar,
    mcpServerIds,
    welcome: skillWelcome,
    initialMessage: skillInitialMessage,
    model: skillModel,
    voice: skillVoice,
    a2ui: skillA2ui,
    tools: skillTools,
    canDelegate: skillCanDelegate,
    loading: skillMetaLoading,
  } = useSkillMeta(skillId);
  // PPA-OBLIGATION 7.6 M3: which workbench-launcher affordances the active
  // skill supports, derived from its tool set. one-doc-compare → compare;
  // one-ppa-expert → analyze obligations.
  const allowCompareLauncher = (skillTools ?? []).includes("compare_ppa_contracts");
  const allowObligationsLauncher = (skillTools ?? []).includes("map_ppa_obligations");

  // Activity context row + "Session started" marker. sessionStartTs is the time
  // this session view opened (approximate on reload until activity history is
  // persisted from the backend). `activityDocuments` is derived below, once
  // sessionDocTabs is resolved.
  const [mountTs] = useState(() => Date.now());
  // Model shown in the Activity header. Defaults to the skill's static config
  // (`skillModel`) but is overridden by the model that ACTUALLY ran the most
  // recent turn (MODEL_RESOLVED) — a router thinking tier on the chat path
  // (`resolvedModel`) or a confirm→switch delegate on the action path
  // (`activitySink.onModel`). Reset on session change (below) so a resumed
  // thread falls back to config until its first live turn reports a model (8.2).
  const [runtimeModel, setRuntimeModel] = useState<string | null>(null);
  useEffect(() => {
    if (resolvedModel) setRuntimeModel(resolvedModel);
  }, [resolvedModel]);
  const activityContext: ActivityContext = {
    modelTier: runtimeModel ?? skillModel,
    voice: skillVoice,
  };

  // 2026-06-11 cold-start UX: surface a "Connecting…" banner + disable
  // the input until BOTH the skill metadata is loaded AND the backend
  // sidecar is reachable. Without this, users land on a freshly-rolled-
  // out revision, see a familiar-looking chat shell, type a question,
  // and hit a timeout / RUN_ERROR before the agent path is warm. The
  // backend probe lives in useBackendReady (polls /api/proxy/health
  // with backoff until 200).
  const { ready: backendReady } = useBackendReady();
  const chatReady = !skillMetaLoading && backendReady;
  // v6.4.0 4.5 SKILL-ONBOARDING M3: source the intro from welcome.introMessage
  // first, fallback to legacy initialMessage. Empty/null → no intro bubble.
  const skillIntroMessage =
    (skillWelcome?.introMessage && skillWelcome.introMessage.trim()) ||
    (skillInitialMessage && skillInitialMessage.trim()) ||
    null;
  const searchParams = useSearchParams();
  const router = useRouter();
  const [draft, setDraft] = useState("");
  // 2026-08-06 UAT: auto-grow the composer with its content, so a wrapped
  // multi-line prompt is fully visible rather than scrolling inside one line.
  // Reset to `auto` first — scrollHeight only ever GROWS while an explicit
  // height is set, so without the reset the box could never shrink back after
  // a send. `max-h-48` on the element caps the growth; overflow scrolls past it.
  const draftRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = draftRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);
  // 2026-06-11 polish: sidebar default-collapsed + per-tab persistence.
  // Most demo sessions don't need the sessions/docs list visible at all;
  // start hidden so chat takes the full row. User can re-open via the
  // DocTabsBar toggle button (top-left of the chat header). Persisted
  // globally (not per-skill) in sessionStorage — sidebar visibility is
  // a workspace-wide preference, not a per-skill one.
  const [showDocBrowser, setShowDocBrowser] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.sessionStorage.getItem("aitana.sidebar.open");
    if (stored === "1") setShowDocBrowser(true);
  }, []);
  const toggleDocBrowser = useCallback(() => {
    setShowDocBrowser((v) => {
      const next = !v;
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem("aitana.sidebar.open", next ? "1" : "0");
      }
      return next;
    });
  }, []);
  const [openTabs, setOpenTabs] = useState<DocTabData[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  // v6.4.0 ITERATION 2026-06-09: Workbench is the right pane that holds
  // the document + workspace surfaces. Default starts on "workspace" so
  // the agent's outputs land in the visible tab as the user chats;
  // auto-switches to "document" when the user opens a doc from sidebar.
  const [workbenchTabId, setWorkbenchTabId] = useState<string>("workspace");
  const lastUserMessageRef = useRef<string>("");

  // 2026-06-11 polish: chat↔workbench split ratio, drag-resizable +
  // sessionStorage-persisted per-skill. The handle component lives in
  // components/chat/WorkbenchResizeHandle.tsx. RATIO_MAX=1.0 hides the
  // chat entirely (workspace full-bleed); RATIO_MIN=0.3 caps the
  // workbench at 30% of the row.
  const { ratio: workspaceRatio, setRatio: setWorkspaceRatio } =
    useResizableWorkspaceRatio(skillId);

  // 2026-06-11 auto-fold: WorkbenchPane reports whether it has content
  // (workspace surface ⊕ open doc tab ⊕ fresh-chat examples). When
  // false, hide the resize handle and let chat flex-1 across the row.
  // Default true so the first paint isn't a layout flash on skills that
  // do have content — the callback will flip to false within one render
  // if there's truly nothing.
  const [workbenchHasContent, setWorkbenchHasContent] = useState(true);
  // Any A2UI artifact present (chat form / workbench result) — reported by
  // ArtifactPresenceReporter inside the provider. Used to PIN the session URL
  // for the action-driven obligation flow (no chat messages → previously never
  // pinned → refresh lost history + surfaces).
  const [hasAnyArtifact, setHasAnyArtifact] = useState(false);

  // 2026-06-11 user-driven collapse: distinct from auto-fold. Even when
  // the workbench HAS content (a doc tab open, an A2UI surface mounted),
  // the user can click a button to hide it. Persisted per-skill so the
  // preference survives navigation within a tab. Collapsed → render a
  // thin vertical strip with an expand chevron so the user always sees
  // a way back.
  const [workbenchCollapsed, setWorkbenchCollapsed] = useState(false);
  useEffect(() => {
    setWorkbenchCollapsed(readStoredCollapsed(skillId));
  }, [skillId]);
  const toggleWorkbenchCollapsed = useCallback(() => {
    setWorkbenchCollapsed((v) => {
      const next = !v;
      writeStoredCollapsed(skillId, next);
      return next;
    });
  }, [skillId]);

  // Combined "is the workbench currently visible" — visible when there's
  // content AND the user hasn't collapsed it. When collapsed, the thin
  // expand strip renders instead.
  const workbenchVisible = workbenchHasContent && !workbenchCollapsed;

  // Session routing: read ?session= from URL, allow programmatic navigation
  const sessionId = searchParams.get("session");

  // Effective thread id — the value threaded to the surface mounts
  // (sessionId ?? agentSessionId). The confirm→switch below keys off it.
  const effectiveSessionId = sessionId ?? agentSessionId;

  const { initialMessages, a2uiSurfaces, historyError, sessionGone, transcriptUnavailable } =
    useSessionMessages(sessionId);
  const { tabs: sessionDocTabs } = useSessionDocuments(sessionId);
  // Session documents → "document added" entries in the Activity feed.
  const activityDocuments: ActivityDoc[] = (sessionDocTabs ?? []).map((t) => ({
    id: t.id,
    name: t.filename,
  }));
  // ACTIVITY-OBS — live progress from an action-triggered run (launcher
  // "Compare contracts" / "Analyze obligations"). Unlike a chat turn, that run
  // is driven by useActionDrivenAgent inside the launcher, which has no
  // useSkillAgent subscription feeding the Activity panel. ChatShell owns the
  // panel, so it holds the run's live state here and passes a stable sink down
  // to the launcher's hook. `activityNonce` bumps when a run settles so the
  // useSessionActivity re-fetch below syncs the now-persisted history.
  const [actionToolCalls, setActionToolCalls] = useState<ToolCallState[]>([]);
  const [actionDelegations, setActionDelegations] = useState<DelegationMarkerItem[]>([]);
  const [actionRunning, setActionRunning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionStageLabel, setActionStageLabel] = useState<string | null>(null);
  const [activityNonce, setActivityNonce] = useState(0);

  // State setters are stable, so the sink is stable for the run's lifetime —
  // important, because it's a dep of useActionDrivenAgent's triggerAction.
  const activitySink = useMemo<ActionRunActivitySink>(
    () => ({
      upsertToolCall: (tc) =>
        setActionToolCalls((prev) => {
          const i = prev.findIndex((p) => p.id === tc.id);
          if (i === -1) return [...prev, tc];
          const next = prev.slice();
          next[i] = { ...next[i], ...tc };
          return next;
        }),
      upsertDelegation: (d) =>
        setActionDelegations((prev) =>
          prev.some((p) => p.id === d.id) ? prev : [...prev, d],
        ),
      onRunStart: () => {
        setActionRunning(true);
        setActionError(null);
      },
      onRunSettled: ({ error }) => {
        setActionRunning(false);
        setActionStageLabel(null);
        if (error) setActionError(error);
        // Re-sync persisted /activity — the run's tool calls are now in the
        // ADK session (it ran through the same runner as chat).
        setActivityNonce((n) => n + 1);
      },
      onStage: (label) => setActionStageLabel(label),
      onModel: (model) => setRuntimeModel(model),
    }),
    [],
  );

  // Clear the action-run feed when the session changes (new chat / switch
  // thread). Mirrors useSkillAgent resetting its delegations on threadId change:
  // runtime action events don't belong to a different conversation.
  const activitySessionKey = sessionId ?? agentSessionId;
  useEffect(() => {
    setActionToolCalls([]);
    setActionDelegations([]);
    setActionRunning(false);
    setActionError(null);
    setActionStageLabel(null);
    setRuntimeModel(null);
  }, [activitySessionKey]);

  // Tool-call history from the backend (persists across reload), merged with the
  // live stream by id (live wins) so the Activity feed survives a refresh.
  // `activityNonce` re-fetches after an action run settles so its now-persisted
  // tool calls sync (their ids match the live action ids → the merge dedupes).
  const {
    toolCalls: historyToolCalls,
    delegations: historyDelegations,
    sessionStartTs: historyStartTs,
  } = useSessionActivity(sessionId ?? agentSessionId, activityNonce);
  // Precedence: chat-live > action-run-live > persisted history, deduped by id.
  const liveToolCallIds = new Set(toolCalls.map((t) => t.id));
  const actionToolCallsOnly = actionToolCalls.filter((a) => !liveToolCallIds.has(a.id));
  const actionToolCallIds = new Set(actionToolCallsOnly.map((a) => a.id));
  const mergedToolCalls = [
    ...toolCalls,
    ...actionToolCallsOnly,
    ...historyToolCalls.filter(
      (h) => !liveToolCallIds.has(h.id) && !actionToolCallIds.has(h.id),
    ),
  ];
  const liveDelegationIds = new Set(delegations.map((d) => d.id));
  const actionDelegationsOnly = actionDelegations.filter(
    (a) => !liveDelegationIds.has(a.id),
  );
  const actionDelegationIds = new Set(actionDelegationsOnly.map((a) => a.id));
  const mergedDelegations = [
    ...delegations,
    ...actionDelegationsOnly,
    ...historyDelegations.filter(
      (h) => !liveDelegationIds.has(h.id) && !actionDelegationIds.has(h.id),
    ),
  ];
  // Prefer the real session start (first event) from history; fall back to when
  // this view mounted for a brand-new session with no events yet.
  const sessionStartTs = historyStartTs ?? mountTs;
  // Past conversations are CROSS-SKILL by default (null filter). Switching
  // agent via the top bar starts a new session on the new skill, so scoping
  // this list to the current skill showed only the fragment belonging to
  // whichever agent the user happened to be standing on — a sitting spread
  // across two agents read as "it didn't record my session" (2026-08-05).
  const [sessionSkillFilter, setSessionSkillFilter] = useState<string | null>(null);
  const { sessions, isLoading: sessionsLoading } = useSkillSessions(sessionSkillFilter);

  // Tracks whether the user reached this chat by clicking a conversation
  // thread (resume) vs starting a fresh chat. Backend uses this flag to
  // decide whether to eagerly inline document content into the LLM
  // request (resume → yes, fresh → standard tool-discovery flow).
  // Initial value: ?session= was already in the URL on mount = resume.
  // Updated by handleSelectSession (true) and handleNewSession (false);
  // intentionally NOT set by the URL-writeback effect that runs after a
  // fresh chat's first message — that's not a resume.
  const [enteredViaResume, setEnteredViaResume] = useState<boolean>(
    () => sessionId !== null,
  );

  // v6.4.0 INTERNAL-SHELL M1: auto-collapse sidebar on first user message of
  // a fresh chat so the chat + workbench own the screen during a run.
  // Fires exactly once per session-start (isFreshChat true → false). Skipped
  // on resume; manual reopens stick.
  const isFreshChat = messages.length === 0 && sessionId === null;
  const prevFreshChatRef = useRef(isFreshChat);
  useEffect(() => {
    if (prevFreshChatRef.current && !isFreshChat && !enteredViaResume) {
      setShowDocBrowser(false);
    }
    prevFreshChatRef.current = isFreshChat;
  }, [isFreshChat, enteredViaResume]);

  // Auto-switch Workbench to Document tab when the user opens a doc.
  useEffect(() => {
    if (activeTabId) setWorkbenchTabId("document");
  }, [activeTabId]);

  // When the URL points at an existing session and we've resolved its
  // documentIds, mount those tabs (with `included: true`) so the user lands
  // on the same workspace they had during the original conversation. Only
  // fires once per session-load — `lastSyncedSessionId` ref guards against
  // wiping subsequent tab edits the user makes inside the same session.
  const lastSyncedSessionId = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId) {
      // Cleared back to a fresh chat — drop the ref so revisiting the same
      // session later still hydrates its tabs.
      lastSyncedSessionId.current = null;
      return;
    }
    if (sessionDocTabs === null) return;
    if (lastSyncedSessionId.current === sessionId) return;
    lastSyncedSessionId.current = sessionId;
    setOpenTabs(sessionDocTabs);
    setActiveTabId(sessionDocTabs[0]?.id ?? null);
  }, [sessionId, sessionDocTabs]);

  const navigateToSession = useCallback(
    (sid: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("session", sid);
      router.replace(`${pathPrefix}?${params.toString()}`);
    },
    [router, pathPrefix, searchParams],
  );

  // Pin the URL to the agent's session id once a fresh chat has produced its
  // first user message. Without this the ChatSessionIndex row exists in
  // Firestore but the URL never reflects it, so a refresh starts a new chat
  // and the existing session looks "lost". Skip when the URL already has a
  // session — the resume path is already pointing at the right id.
  useEffect(() => {
    if (!sessionId && agentSessionId && (messages.length > 0 || hasAnyArtifact)) {
      navigateToSession(agentSessionId);
    }
  }, [sessionId, agentSessionId, messages.length, hasAnyArtifact, navigateToSession]);

  // Fire-and-forget bootstrap: pre-create the ChatSessionIndex + ADK session
  // before the first agent turn so iframe context pushes (ui/update-model-context)
  // that arrive immediately after mount don't 404. Idempotent on the backend —
  // resumed sessions already have an index and the call is a no-op.
  const bootstrappedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!agentSessionId || bootstrappedRef.current === agentSessionId) return;
    bootstrappedRef.current = agentSessionId;
    void fetchWithAuth(`/api/proxy/api/sessions/${agentSessionId}/bootstrap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_id: skillId }),
    }).catch(() => {
      // bootstrap is best-effort; silently swallow errors so the chat isn't broken
    });
  }, [agentSessionId, skillId]);

  const userInitial = (user.displayName ?? user.email ?? "U").charAt(0).toUpperCase();
  const userDisplayName = user.displayName ?? user.email ?? "You";

  // Documents currently included in agent context. Every open tab defaults to
  // included; users uncheck the box on a tab to exclude it without closing it.
  // Derivation extracted to lib/docContext.ts so the multi-doc contract is
  // unit-testable independently of the chat-page render tree
  // (multi-doc-context-fix.md / 1.22 D2).
  const includedDocIds = computeIncludedDocIds(openTabs);

  // ── Confirm→SWITCH (8.2, full-switch semantics) ──────────────────────────
  // FRONT-DOOR side. When the user Proceeds on a confirm_delegation card,
  // A2UISurfaceMount emits an intent (it knows the target skill; we own the
  // transcript). We capture the outstanding request (last user message across
  // history + live), stash it with the in-context documents, and navigate to the
  // specialist on the SAME thread — so the ADK session, its history, and its
  // documents carry over natively (use_thread_id_as_session_id). Doing the pick
  // "for them," exactly like a manual skill-menu selection. A ref holds the
  // click-time snapshot so the subscription doesn't churn on every streamed token.
  const switchCtxRef = useRef({ messages, initialMessages, includedDocIds, effectiveSessionId });
  switchCtxRef.current = { messages, initialMessages, includedDocIds, effectiveSessionId };
  useEffect(() => {
    return subscribeSkillSwitchIntent(({ targetSkillId }) => {
      const { messages: msgs, initialMessages: hist, includedDocIds: docs, effectiveSessionId: thread } =
        switchCtxRef.current;
      if (!targetSkillId || !thread) return;
      const lastUser =
        [...msgs].reverse().find((m) => m.role === "user")?.content ??
        [...hist].reverse().find((m) => m.role === "user")?.content ??
        "";
      stashPendingSkillSwitch({ threadId: thread, targetSkillId, prompt: lastUser, documentIds: docs });
      router.push(
        `/chat/${encodeURIComponent(targetSkillId)}?session=${encodeURIComponent(thread)}`,
      );
    });
  }, [router]);

  // SPECIALIST side. On arrival, if a stash targets THIS skill + thread, re-issue
  // the outstanding request through the NORMAL chat path (so the reply streams
  // like any other turn) and stay active for the rest of the session. One-shot.
  const switchConsumedRef = useRef(false);
  useEffect(() => {
    if (switchConsumedRef.current) return;
    const pending = readPendingSkillSwitch();
    if (!pending) return;
    if (pending.threadId !== effectiveSessionId || pending.targetSkillId !== skillId) return;
    switchConsumedRef.current = true;
    clearPendingSkillSwitch();
    if (!pending.prompt.trim()) return; // switched with nothing to ask — just land here
    void sendMessage(pending.prompt, {
      documentIds: pending.documentIds,
      resumedSession: true,
    });
  }, [effectiveSessionId, skillId, sendMessage]);

  async function handleSend() {
    const text = draft.trim();
    if (!text || isLoading || error) return;
    lastUserMessageRef.current = text;
    setDraft("");
    await sendMessage(text, {
      documentIds: includedDocIds,
      resumedSession: enteredViaResume,
    });
  }

  const handleRetry = useCallback(() => {
    const text = lastUserMessageRef.current;
    if (!text) { clearError(); return; }
    clearError();
    void sendMessage(text, {
      documentIds: includedDocIds,
      resumedSession: enteredViaResume,
    });
  }, [clearError, sendMessage, includedDocIds, enteredViaResume]);

  // A `chat:send` surface action (e.g. a diff card's "Explain this difference")
  // posts a chat message so the agent's reply lands in the chat thread — same
  // doc context as the composer. See A2UISurfaceMount onChatMessage.
  const handleSurfaceChatMessage = useCallback(
    (text: string) => {
      if (!text.trim() || isLoading) return;
      lastUserMessageRef.current = text;
      void sendMessage(text, {
        documentIds: includedDocIds,
        resumedSession: enteredViaResume,
      });
    },
    [sendMessage, includedDocIds, enteredViaResume, isLoading],
  );

  // MCP App iframe → notification adapter → synthetic chat turn. Stable
  // identity (not an inline arrow) so it doesn't defeat MessageBubble's
  // React.memo on every SSE token — deps change only on doc-context/resume
  // changes, never per streamed token.
  const handleMcpChatMessage = useCallback(
    (text: string) => {
      void sendMessage(text, {
        documentIds: includedDocIds,
        resumedSession: enteredViaResume,
      });
    },
    [sendMessage, includedDocIds, enteredViaResume],
  );

  // Loop/spam guard for A2UI actions. An action (e.g. a form submit) fires an
  // agent turn; the agent re-emits the surface, and a chat re-render can
  // re-fire the SAME action — an unbounded loop that hammers the LLM (a single
  // submit spun hundreds of turns / millions of tokens). Unlike handleSend,
  // this path had no in-flight gate. We (a) never dispatch while a run is
  // already in flight, and (b) drop an identical action repeated within a
  // window that outlasts a normal turn. A human cannot resubmit the same form
  // with identical data in a few seconds, so only machine-driven repeats are
  // suppressed. Refs (not state) keep handleAction's identity stable — a
  // changing onAction prop is itself part of what drove the churn.
  const isLoadingRef = useRef(isLoading);
  useEffect(() => {
    isLoadingRef.current = isLoading;
  }, [isLoading]);
  const lastActionSendRef = useRef<{ key: string; at: number } | null>(null);

  const handleAction = useCallback(
    (event: { actionName: string; context: Record<string, unknown> }) => {
      const key = `${event.actionName}:${JSON.stringify(event.context)}`;
      const now = Date.now();
      const prev = lastActionSendRef.current;
      if (
        isLoadingRef.current ||
        (prev && prev.key === key && now - prev.at < 8000)
      ) {
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            `[ChatShell] dropped A2UI action "${event.actionName}" — loop guard ` +
              `(inFlight=${isLoadingRef.current})`,
          );
        }
        return;
      }
      lastActionSendRef.current = { key, at: now };
      void sendMessage(
        `[a2ui:${event.actionName}] ${JSON.stringify(event.context)}`,
        { documentIds: includedDocIds, resumedSession: enteredViaResume },
      );
    },
    [sendMessage, includedDocIds, enteredViaResume],
  );

  // Wraps navigateToSession with the resume signal so we differentiate
  // explicit thread clicks from the URL writeback that happens after a
  // fresh chat's first message.
  const handleSelectSession = useCallback(
    (sid: string) => {
      setEnteredViaResume(true);
      navigateToSession(sid);
    },
    [navigateToSession],
  );

  const handleNewSession = useCallback(() => {
    setEnteredViaResume(false);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("session");
    const qs = params.toString();
    router.replace(qs ? `${pathPrefix}?${qs}` : pathPrefix);
  }, [router, pathPrefix, searchParams]);

  // Defensive auto-clear: when ANY mutation site reports a deletion via the
  // sessions-changed bus and that id matches the URL session we're showing,
  // navigate to a fresh chat even if the originating handler missed the
  // active-session check (e.g. stale closure props on a detached panel).
  // See docs/design/v6.1.0/implemented/session-delete-ui.md.
  useEffect(() => {
    return subscribeSessionsChangedDetailed((detail) => {
      if (detail.deletedSessionId && detail.deletedSessionId === sessionId) {
        handleNewSession();
      }
    });
  }, [sessionId, handleNewSession]);

  // Stranded-session-prevention (1.23) Option 1: GET /messages returned 404,
  // meaning ?session=X points at a session the backend no longer has. Drop
  // ?session= from the URL so useStableThreadId mints a fresh UUID before
  // the next outbound POST. One-shot — handleNewSession clears sessionId,
  // which resets sessionGone via the hook on the next effect cycle.
  useEffect(() => {
    if (sessionGone && sessionId) {
      handleNewSession();
    }
  }, [sessionGone, sessionId, handleNewSession]);

  const handleDeleteSkillSession = useCallback(
    async (sid: string) => {
      // Mirrors DocumentHistoryPanel.handleDelete: confirm + DELETE +
      // dispatch sessions-changed (which both useSkillSessions and any
      // mounted useDocumentSessions listen for, so both panels reconcile)
      // + clear URL if the deleted session is active.
      if (
        !window.confirm(
          "Delete this conversation? This can't be undone from the UI.",
        )
      ) {
        return;
      }
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/sessions/${encodeURIComponent(sid)}`,
          { method: "DELETE" },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        notifySessionsChanged({ deletedSessionId: sid });
        if (sid === sessionId) {
          handleNewSession();
        }
      } catch {
        // Backend rejected. Reconcile via the same bus.
        notifySessionsChanged();
      }
    },
    [sessionId, handleNewSession],
  );

  const handleDocClick = useCallback((doc: ParsedDocument) => {
    setOpenTabs((prev) => {
      if (prev.find((t) => t.id === doc.id)) return prev;
      return [
        ...prev,
        { id: doc.id, filename: doc.originalFilename, format: doc.sourceFormat, included: true },
      ];
    });
    setActiveTabId(doc.id);
    // 2026-06-11: always pull the workbench to the Document tab on a
    // doc-click. Same rationale as the DocTabsBar onSelect wrapper —
    // covers the case where activeTabId is already this doc (no state
    // change → activeTabId-change useEffect doesn't fire).
    setWorkbenchTabId("document");
  }, []);

  // Quick-start upload (landing screen): when a doc is uploaded from the
  // fresh-chat dropzone, mount it as an INCLUDED tab straight away — same path a
  // sidebar doc-click takes — so the user can type their first question and the
  // agent already has the document as context ("drag a doc in and start work").
  // The composer's InContextBadge then shows "Will process: <file>" as visible
  // confirmation. handleDocClick only reads id/originalFilename/sourceFormat, so
  // a lightweight doc built from the upload result is sufficient here.
  const handleUploadedDocOpen = useCallback(
    (docId: string, filename: string) => {
      const sourceFormat = filename.includes(".") ? filename.split(".").pop()!.toLowerCase() : "";
      handleDocClick({
        id: docId,
        originalFilename: filename,
        sourceFormat,
        parseStatus: "pending",
        parseError: null,
        folderId: "",
        userId: "",
        blockCount: null,
        hasA2ui: false,
      });
    },
    [handleDocClick],
  );

  // DOC-IMPORT-REF M3: shared handler for the picker + GCSFileBrowser. POSTs
  // to /api/documents/import-by-reference (via the lib helper), then mounts
  // the returned doc in the workbench via handleDocClick — same path uploads
  // take. Replaces the 4.5 synthetic-chat-message hack that delegated to
  // the agent's bucket tools (which returned raw bytes, not parsed blocks).
  const handleImportByReference = useCallback(
    async (bucket: string, objectName: string): Promise<void> => {
      const result = await importByReference(bucket, objectName, skillId);
      if (isImportError(result)) {
        console.error(`import-by-reference failed: ${result.message}`);
        return;
      }
      handleDocClick(result.doc);
    },
    [skillId, handleDocClick],
  );

  // 6.15: open an enterprise-search (gs://) source in the Document tab. Same
  // import-by-reference path as a bucket-menu pick, but THROWS on failure so the
  // Sources tab can render a visible error (NEVER-SILENT) instead of a dead click.
  const handleOpenSourceDoc = useCallback(
    async (bucket: string, objectName: string): Promise<void> => {
      const result = await importByReference(bucket, objectName, skillId);
      if (isImportError(result)) {
        throw new Error(result.message);
      }
      handleDocClick(result.doc);
    },
    [skillId, handleDocClick],
  );

  // Open a launcher-picked contract in the Document tab (parity with the sidebar
  // Library / examples picker). A bucket `gs_url` → import-by-reference (parses +
  // opens + focuses a tab); an already-open `doc_id` → just focus its tab.
  const handleOpenDocFromLauncher = useCallback(
    (identity: CompareDocIdentity) => {
      if ("gs_url" in identity) {
        const m = identity.gs_url.match(/^gs:\/\/([^/]+)\/(.+)$/);
        if (m) void handleImportByReference(m[1], m[2]);
      } else {
        setActiveTabId(identity.doc_id);
        setWorkbenchTabId("document");
      }
    },
    [handleImportByReference],
  );

  // #11: when the AI reads/analyses a document via a tool (get_document_content,
  // extract_ppa_clauses, map_ppa_obligations), open it in the document bar —
  // parity with a manual pick, via the same import-by-reference path. Deduped so
  // a doc is opened once even as its args re-render / the tool is re-called.
  const aiOpenedDocsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const tc of toolCalls) {
      const identity = docIdentityFromToolCall(tc.name, tc.argsJson);
      if (!identity) continue;
      const key = docIdentityKey(identity);
      if (aiOpenedDocsRef.current.has(key)) continue;
      aiOpenedDocsRef.current.add(key);
      handleOpenDocFromLauncher(identity);
    }
  }, [toolCalls, handleOpenDocFromLauncher]);

  const handleTabClose = useCallback((id: string) => {
    setOpenTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeTabId === id) setActiveTabId(next[next.length - 1]?.id ?? null);
      return next;
    });
  }, [activeTabId]);

  const handleTabToggleInclude = useCallback((id: string) => {
    setOpenTabs((prev) =>
      prev.map((t) => (t.id === id ? { ...t, included: !t.included } : t)),
    );
  }, []);

  const inputDisabled = isLoading || error !== null || !chatReady;

  // The composer (connecting banner + in-context badge + input row). Extracted
  // so it can render pinned at the bottom during a conversation OR centered on
  // the landing screen of a fresh session (below).
  const composer = (
    <>
      {/* 2026-06-11 cold-start UX: surface a "Connecting…" banner whenever the
          agent isn't safe to talk to yet — skill metadata still loading OR
          backend cold-start in flight. Disables the input at the same time. */}
      {!chatReady && (
        <div
          className="mb-2 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
          role="status"
          aria-live="polite"
        >
          <svg className="h-3 w-3 animate-spin shrink-0" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
            <path d="M12 2 a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" fill="none" />
          </svg>
          <span>
            {skillMetaLoading
              ? "Loading skill…"
              : "Connecting to assistant… you can start typing in a moment."}
          </span>
        </div>
      )}
      {/* COMPACTION-LATENCY M2 — the answer is done and a compaction is running.
          Without this the composer simply re-enables mid-housekeeping with no
          explanation; NEVER SILENT (#8) means saying what the system is doing,
          not just getting out of the way. */}
      {tidyingUp && (
        <div
          className="mb-2 flex items-center gap-2 text-xs text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-500/70" />
          <span>Tidying up the conversation history — you can keep typing.</span>
        </div>
      )}
      {/* v6.4.0 INTERNAL-SHELL M3: in-context caption — disambiguates multi-doc
          state so the user always knows which files the agent will see next
          turn. Renders nothing when no docs are included. */}
      <InContextBadge openTabs={openTabs} includedDocIds={includedDocIds} />
      <form
        // items-end: as the textarea grows the Send/Stop button stays aligned
        // with the LAST line rather than stretching to the full box height.
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend();
        }}
      >
        {/* 2026-08-06 UAT (ONE): this was a single-line <input>, which by
            definition never wraps — a long prompt scrolled sideways and the
            user could not see what they had typed. Reported independently by
            two users as the #1 complaint. A textarea wraps; `rows={1}` +
            the auto-grow effect below keep the one-line resting height so the
            composer doesn't get taller for short messages. Enter sends,
            Shift+Enter inserts a newline (the chat idiom) — without that
            handler a textarea would swallow Enter and never submit. */}
        <textarea
          ref={draftRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
          rows={1}
          placeholder={chatReady ? "Message…" : "Connecting…"}
          className="max-h-48 flex-1 resize-none overflow-y-auto rounded-md border px-3 py-2 text-sm"
          disabled={inputDisabled}
        />
        {isLoading ? (
          <button type="button" onClick={stop} className="rounded-md border px-3 py-2 text-sm">
            Stop
          </button>
        ) : (
          <button
            type="submit"
            className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"
            disabled={!draft.trim() || inputDisabled}
          >
            Send
          </button>
        )}
      </form>
    </>
  );

  // Esc cancels an in-flight run — perceived-snappiness affordance from
  // ttft-instrumentation.md M2. Bound at document level because the
  // text input is disabled while isLoading (no keydown fires there).
  // No-op when no run is in flight; lets browser handle Esc otherwise.
  useEffect(() => {
    if (!isLoading) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        stop();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isLoading, stop]);

  return (
    <SurfaceRegistryProvider>
    <SurfaceSessionLifecycle sessionId={sessionId} />
    <WorkspaceA2uiEventRouter />
    <ArtifactPresenceReporter onPresent={setHasAnyArtifact} />
    <RehydrateSurfaces surfaces={a2uiSurfaces} enabled={enteredViaResume} />
    {/* SkillsBar (top nav) lifted to ShellChrome in ShellRouter so every shell
        mode shares it — see components/shells/ShellChrome.tsx. This shell body
        fills the height remaining below that bar. */}
    <main className="flex min-h-0 flex-1 flex-col">
      <DocTabsBar
        tabs={openTabs}
        // 2026-06-11: explicitly switch the workbench to the Document
        // tab on every doc-tab click. The activeTabId-change useEffect
        // below already covers the case where the activeTabId actually
        // CHANGES (click doc A → click doc B), but if the user clicks
        // the already-active doc in the tab strip — e.g. after manually
        // switching to the Workspace tab — React skips the setState
        // and the effect never fires. Doing it explicitly here makes
        // the click reliably bring the user to the doc preview no
        // matter the prior workbench tab state.
        activeTabId={activeTabId}
        showBrowser={showDocBrowser}
        onSelect={(id) => {
          setActiveTabId(id);
          setWorkbenchTabId("document");
        }}
        onClose={handleTabClose}
        onToggleInclude={handleTabToggleInclude}
        onToggleBrowser={toggleDocBrowser}
      />

      <div className="flex min-h-0 flex-1" data-workspace-row>
        {showDocBrowser && (
          <aside className="flex w-64 shrink-0 flex-col overflow-hidden border-r bg-muted/30">
            {/* v6.6.0: order the sidebar skill-first. The skill's own
                affordances (its curated Library + anything the agent streams
                into the A2UI sidebar surface) sit at the top because they're
                the most relevant while working; the generic workspace utilities
                (your uploads, chat history) sit below. Each section remembers
                its open/closed state across sidebar reopens (persistId). */}

            {/* Skill-related #1 — the skill's curated document library
                (v6.4.0 4.5 SKILL-ONBOARDING M4), e.g. ONE's PPA library. Click
                a file → import-by-reference, no upload. Only present when the
                skill declares welcome.bucket_browser. */}
            {skillWelcome?.bucketBrowser?.bucket && (
              <SidebarSection
                title={skillWelcome.bucketBrowser.label || "Library"}
                persistId="library"
                defaultOpen={skillWelcome.bucketBrowser.defaultOpen ?? true}
              >
                {/* Cap + scroll the library so a large folder (ONE's PPA library
                    has 50+ files) can't push "Your files" / "Past conversations"
                    off-screen — the whole point of a sidebar is that every
                    section stays reachable (2026-07-16 report). */}
                <div className="max-h-[45vh] overflow-y-auto">
                  <GCSFileBrowser
                    bucket={skillWelcome.bucketBrowser.bucket ?? ""}
                    rootPath={skillWelcome.bucketBrowser.rootPath ?? ""}
                    onPick={(bucket, objectName, _label) => {
                      void handleImportByReference(bucket, objectName);
                    }}
                  />
                </div>
              </SidebarSection>
            )}

            {/* Skill-related #2 — A2UI sidebar surface. A skill can route its
                A2UI to the `sidebar` surface (toolConfigs.a2ui.defaultSurface),
                so the agent streams live UI here. Autohides when empty. Placed
                high so AI-streamed content is prominent. */}
            <SidebarSurfaceRegion sessionId={sessionId ?? agentSessionId} />

            {/* Generic workspace utility — the user's own uploaded files. */}
            <SidebarSection title="Your files" persistId="files" defaultOpen={true} bodyClassName="">
              <div className="max-h-[30vh] overflow-y-auto">
                <DocListView uid={user.uid} onDocClick={handleDocClick} />
              </div>
              <div className="border-t">
                <UploadDropZone skillId={skillId} />
              </div>
            </SidebarSection>

            {/* Generic workspace utility — chat history. Lowest priority while
                working, so it sits at the bottom. */}
            <SidebarSection title="Past conversations" persistId="conversations" defaultOpen={true}>
              <div className="max-h-40 overflow-y-auto">
                <SkillSessionPanel
                  sessions={sessions}
                  activeSessionId={sessionId}
                  isLoading={sessionsLoading}
                  onSelectSession={handleSelectSession}
                  onDelete={(sid) => void handleDeleteSkillSession(sid)}
                  skillFilter={sessionSkillFilter}
                  onFilterChange={setSessionSkillFilter}
                />
              </div>
            </SidebarSection>
          </aside>
        )}

        {/* v6.4.0 ITERATION 2026-06-09: Chat is the middle column,
            Workbench is the right pane. Replaces the prior conditional
            ladder where DocumentPanel took the middle slot when a doc
            was open (chat pushed right) and WorkspaceSurfaceRegion took
            it when the agent emitted a surface. Now the right pane is
            ALWAYS the Workbench with Document + Workspace tabs.

            2026-06-11 polish: chat width tracks (1 - workspaceRatio) so
            the WorkbenchResizeHandle below can drag chat ↔ workbench
            live, with per-skill sessionStorage persistence. */}
        <div
          className={
            workbenchVisible ? "flex min-w-0 flex-col" : "flex min-w-0 flex-1 flex-col"
          }
          style={
            workbenchVisible
              ? { flexBasis: `${(1 - workspaceRatio) * 100}%`, flexGrow: 0, flexShrink: 1 }
              : undefined
          }
        >
          {isFreshChat ? (
            // Fresh session — no transcript yet. Center the greeting + composer
            // vertically (ChatGPT/Claude-style landing) instead of pinning the
            // input to the bottom of an empty pane. Flips to the transcript
            // layout the instant the first message is sent (isFreshChat→false).
            <div className="flex flex-1 flex-col items-center justify-center overflow-auto p-4">
              <div className="w-full max-w-2xl space-y-6">
                {skillIntroMessage && !enteredViaResume && (
                  <AssistantIntroBubble content={skillIntroMessage} skillName={displayName} />
                )}
                {/* NEVER SILENT (#8): an error on the very first turn must still
                    be visible on the landing screen, not hidden with the
                    (unrendered) transcript. */}
                {error && (
                  <StreamErrorBanner error={error} onRetry={handleRetry} onDismiss={clearError} />
                )}
                {composer}
                {/* Quick-start upload — the same drag-and-drop that lives in the
                    sidebar, surfaced right under the landing composer so a user
                    can drop a document and immediately start working on it. The
                    upload mounts the doc as included context (handleUploadedDocOpen)
                    and the composer's InContextBadge confirms it. Fresh-session
                    only: during a conversation the composer pins to the bottom and
                    the sidebar upload is enough. */}
                <div className="space-y-1.5">
                  <div className="flex items-center gap-3 text-[11px] uppercase tracking-wide text-muted-foreground/70">
                    <div className="h-px flex-1 bg-border" />
                    <span>or start from a document</span>
                    <div className="h-px flex-1 bg-border" />
                  </div>
                  <div className="rounded-lg border bg-muted/20">
                    <UploadDropZone skillId={skillId} onUploadComplete={handleUploadedDocOpen} />
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <>
          <ChatMessageListWithSurfaces
            messages={messages}
            introMessage={
              // Only show on truly-fresh chat — skip on resume.
              !enteredViaResume ? skillIntroMessage : null
            }
            skillDisplayName={displayName}
            // initialMessages are the persisted history fetched by
            // useSessionMessages(sessionId). They're only relevant when the
            // user RESUMED an existing session — in that case the live
            // `messages` array is empty until the first new turn, so the
            // history fills the gap. When the URL is written back mid-chat
            // (fresh chat → server assigns sessionId → URL update), the
            // history fetch returns the same messages that are ALREADY in
            // live `messages` — duplicating every bubble. `enteredViaResume`
            // distinguishes the two cases.
            initialMessages={enteredViaResume ? initialMessages : undefined}
            historyError={historyError}
            transcriptUnavailable={enteredViaResume && transcriptUnavailable}
            toolCalls={toolCalls}
            thinkingContent={thinkingContent}
            isThinking={isThinking}
            isLoading={isLoading}
            error={error}
            skillId={displayName}
            botAvatarUrl={skillAvatar}
            userInitial={userInitial}
            userDisplayName={userDisplayName}
            userPhotoURL={user.photoURL}
            stageLabel={stageLabel}
            delegations={delegations}
            onAction={handleAction}
            mcpServerIds={mcpServerIds}
            sessionId={sessionId ?? agentSessionId}
            onChatMessage={handleMcpChatMessage}
            errorBanner={
              error ? (
                <StreamErrorBanner
                  error={error}
                  onRetry={handleRetry}
                  onDismiss={clearError}
                />
              ) : undefined
            }
            // Chat-placement A2UI surfaces (the obligation elicitation form /
            // result cards, 7.8) are INTERLEAVED into the transcript by creation
            // time inside ChatMessageListWithSurfaces (reads the registry) — a
            // message sent after a form appears below it, in chronological
            // order. `formSkillId` is the REAL skill id the forms' surface-
            // action-run is scoped to (ChatMessageList's `skillId` above is the
            // display label passed to MessageBubble).
            formSkillId={skillId}
          />

          <footer className="border-t p-3">{composer}</footer>
            </>
          )}
        </div>

        {/* 2026-06-11 polish: drag handle between chat and workbench.
            Drag, ←/→ (5%), Home/End (jump to min/max), Enter (50%);
            snaps at 30/50/70/100. Per-skill ratio in sessionStorage.
            Only rendered when the workbench is visible (content + not
            user-collapsed) — otherwise chat takes the full row. */}
        {workbenchVisible && (
          <WorkbenchResizeHandle ratio={workspaceRatio} onChange={setWorkspaceRatio} />
        )}

        {/* 2026-06-11 user-driven collapse: when content exists but the
            user has explicitly hidden the workbench, render a thin
            vertical strip on the right edge with a chevron the user
            can click to bring the workbench back. Without this strip
            a collapsed workbench would look like the auto-folded case
            and the user couldn't tell where to click to restore it. */}
        {workbenchHasContent && workbenchCollapsed && (
          <button
            type="button"
            onClick={toggleWorkbenchCollapsed}
            className="group flex h-full w-6 shrink-0 flex-col items-center justify-center border-l bg-muted/40 transition-colors hover:bg-muted"
            aria-label="Expand workbench"
            title="Expand workbench"
          >
            <svg
              className="h-3 w-3 text-muted-foreground group-hover:text-foreground"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="8 2 4 6 8 10" />
            </svg>
            <span className="mt-2 font-mono text-[9px] uppercase tracking-wider text-muted-foreground [writing-mode:vertical-rl] [transform:rotate(180deg)]">
              Workbench
            </span>
          </button>
        )}

        {/* v6.4.0 ITERATION 2026-06-09: Workbench right pane — always
            visible. WorkbenchPane is a child component so its
            useSurfaceState hook runs INSIDE the SurfaceRegistryProvider
            scope (the provider wraps ChatShell's JSX above).

            v6.4.0 4.5 SKILL-ONBOARDING M2 — also receives the active
            skill's welcome.exampleDocuments/examplePrompts so the
            Workspace Home can render SkillExamplesPicker. v6.23.0 dropped
            the isFreshChat prop: the picker is permanent on Home now, not
            a first-turn-only affordance.

            2026-06-11: wrapped in a div whose flex-basis tracks
            workspaceRatio so the WorkbenchResizeHandle above can drive
            the live split. workbenchClassName="" overrides the default
            md:w-[520px]…2xl:w-[760px] breakpoint scale inside Workbench
            since width is now parent-driven. */}
        <div
          className="relative flex min-w-0 flex-col"
          style={
            workbenchVisible
              ? { flexBasis: `${workspaceRatio * 100}%`, flexGrow: 0, flexShrink: 1 }
              : { flexBasis: 0, flexGrow: 0, flexShrink: 0 }
          }
        >
        {/* 2026-06-11 explicit collapse: small chevron in the top-right
            corner of the workbench. Sits absolute so it overlays the
            Workbench's own tab strip without disturbing the layout. */}
        {workbenchVisible && (
          <button
            type="button"
            onClick={toggleWorkbenchCollapsed}
            className="absolute right-1 top-1 z-10 flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Collapse workbench"
            title="Collapse workbench"
          >
            <svg
              className="h-3 w-3"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="4 2 8 6 4 10" />
            </svg>
          </button>
        )}
        <WorkbenchPane
          workbenchClassName=""
          onContentChange={setWorkbenchHasContent}
          activeTabId={activeTabId}
          sessionId={sessionId ?? agentSessionId}
          skillId={skillId}
          userUid={user.uid}
          onSurfaceChatMessage={handleSurfaceChatMessage}
          workbenchTabId={workbenchTabId}
          onWorkbenchTabChange={setWorkbenchTabId}
          toolCalls={mergedToolCalls}
          delegations={mergedDelegations}
          compactions={compactions}
          isThinking={isThinking}
          activityContext={activityContext}
          activityDocuments={activityDocuments}
          sessionStartTs={sessionStartTs}
          activitySink={activitySink}
          actionRunning={actionRunning}
          actionStageLabel={actionStageLabel}
          actionError={actionError}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          welcomeExamples={skillWelcome?.exampleDocuments ?? []}
          welcomePrompts={skillWelcome?.examplePrompts ?? []}
          isFreshChat={isFreshChat}
          canDelegate={skillCanDelegate}
          compareOptedIn={skillA2ui?.allowActionTriggeredRuns ?? false}
          allowCompareLauncher={allowCompareLauncher}
          allowObligationsLauncher={allowObligationsLauncher}
          docTabs={openTabs}
          onPickExample={(example) => {
            // DOC-IMPORT-REF M3: POST to /api/documents/import-by-reference
            // and mount the parsed doc in the workbench via handleDocClick.
            // Replaces the 4.5 synthetic-chat-message hack that bypassed
            // AILANG Parse and made the LLM stare at raw bytes.
            void handleImportByReference(example.bucket, example.object);
          }}
          onPickPrompt={handleSurfaceChatMessage}
          onOpenLauncherDoc={handleOpenDocFromLauncher}
          onOpenSource={handleOpenSourceDoc}
        />
        </div>
      </div>
      <LatencyHUD />
      {/* MULTI-SURFACE-A2UI M3: modal surface mount — fixed-position
          overlay at page root. Only visible when populated; M4 will wire
          the user-gesture guard so the agent can't pop one unprompted. */}
      <ModalSurfaceRegion sessionId={sessionId ?? agentSessionId} />
    </main>
    </SurfaceRegistryProvider>
  );
}
