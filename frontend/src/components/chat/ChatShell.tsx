"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChatMessageListWithSurfaces } from "@/components/chat/ChatMessageList";
import type { DocTabData } from "@/components/doc-browser/DocTab";
import { DocListView } from "@/components/doc-browser/DocListView";
import { DocTabsBar } from "@/components/doc-browser/DocTabsBar";
import { useVoiceConfig } from "@/hooks/useVoiceConfig";
import { cancelVoicePlayback } from "@/hooks/useAutoReadAloud";
import { VoiceComposerControls } from "@/components/chat/VoiceComposerControls";
import { AutoReadToggle } from "@/components/chat/AutoReadToggle";
import { UploadDropZone } from "@/components/doc-browser/UploadDropZone";
import { AccountPanel } from "@/components/navigation/AccountPanel";
import { SkillWelcome } from "@/components/chat/SkillWelcome";
import { SkillArt } from "@/components/chat/SkillArt";
import { useReplyReadyNotification } from "@/hooks/useReplyReadyNotification";
import { StreamErrorBanner } from "@/components/chat/StreamErrorBanner";
import { useRegisterSidebarToggle } from "@/components/shells/ShellActionsContext";
import type { ParsedDocument } from "@/hooks/useDocBrowser";
import type { User } from "@/lib/firebase";
import {
  useSkillAgent,
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
import { RecentConversations } from "@/components/chat/RecentConversations";
import { useRecentDocuments, type RecentDocument } from "@/hooks/useRecentDocuments";
import { SkillSessionPanel } from "@/components/chat/SkillSessionPanel";
import { SharedSessionsSection } from "@/components/chat/SharedSessionsSection";
import DocumentHistoryPanel from "@/components/chat/DocumentHistoryPanel";
import { SidebarSection } from "@/components/chat/SidebarSection";
import { InContextBadge } from "@/components/chat/InContextBadge";
import { Workbench, type WorkbenchTab } from "@/components/chat/Workbench";
import { WorkbenchHome } from "@/components/chat/WorkbenchHome";
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
import { WorkspaceAgentTools } from "./WorkspaceAgentTools";
import { useAGUIAgent } from "@/providers/AGUIProvider";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { CompareLauncher, type CompareDocIdentity } from "@/components/workspace/CompareLauncher";
import type { ActionRunActivitySink } from "@/hooks/useActionDrivenAgent";
import { ObligationArtefactTab } from "@/components/workspace/ObligationArtefactTab";
import { SourcesArtefactTab } from "@/components/workspace/SourcesArtefactTab";
import { HelpArtefactTab } from "@/components/workspace/HelpArtefactTab";
import { setHelpOpener } from "@/lib/helpViewer";
import { ClausesArtefactTab } from "@/components/workspace/ClausesArtefactTab";
import { SeriesArtefactTab } from "@/components/workspace/SeriesArtefactTab";
import { shouldShowCompareLauncher } from "@/lib/compareLauncher";
import {
  artifactsExcludingOpenableDocuments,
  newlyCreatedDocuments,
  type CreatedDocumentRef,
} from "@/lib/createdDocument";
import { forgetFocusedResult, nextFocusedResult } from "@/lib/workbenchFocus";
import { DocumentPanel } from "@/components/document/DocumentPanel";
import { LatencyHUD } from "@/components/dev/LatencyHUD";
import { ProjectsSection } from "@/components/projects/ProjectsSection";
import { ProjectChatsList } from "@/components/projects/ProjectChatsList";
import {
  SessionProjectMenu,
  SessionProjectNotice,
  type SessionProjectFeedback,
} from "@/components/projects/SessionProjectMenu";
import { YourInstructionsCard } from "@/components/projects/YourInstructionsCard";
import { useMyInstructions } from "@/hooks/useMyInstructions";
import { useProject, useProjectDocuments, useProjectSessions, type ProjectDocument, type ProjectSummary } from "@/hooks/useProjects";
import { projectChatHref, useProjectChatHref } from "@/hooks/useProjectChatHref";
import { setActiveProject } from "@/lib/activeProject";
import { ContextBanner, type ActiveProjectContext } from "@/components/chat/ContextBanner";

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
/** localStorage key for the sidebar's open/closed preference. */
const SIDEBAR_OPEN_KEY = "aitana.sidebar.open";
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

/**
 * While the chat has a Help result tab, help links in chat answers open there
 * (lib/helpViewer): expand the workbench and focus the tab. Inside the provider
 * for `useArtifacts`; renders nothing.
 */
function HelpOpenerBridge({ onOpen }: { onOpen: (surfaceId: string) => void }) {
  const helpSurfaceId =
    useArtifacts().find((a) => a.kind === "help" && a.placement !== "chat")?.surfaceId ?? null;
  useEffect(() => {
    if (!helpSurfaceId) return;
    setHelpOpener(() => onOpen(helpSurfaceId));
    return () => setHelpOpener(null);
  }, [helpSurfaceId, onOpen]);
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
/** A project document in the shape the recent-documents picker reads. */
function projectDocAsRecent(d: ProjectDocument): RecentDocument {
  return {
    id: d.id,
    originalFilename: d.originalFilename,
    sourceFormat: d.sourceFormat,
    parseStatus: d.parseStatus ?? "parsed",
    folderId: d.folderId ?? null,
    createdAt: d.createdAt ?? null,
    updatedAt: d.updatedAt ?? null,
    lastUsedAt: d.lastUsedAt ?? d.updatedAt ?? d.createdAt ?? "",
    lastSessionId: d.lastSessionId ?? null,
    lastSessionTitle: d.lastSessionTitle ?? null,
    projectIds: d.projectIds,
  };
}

function WorkbenchPane({
  onOpenCreatedDocument,
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
  onOpenRecentDocument,
  projectId = null,
  projectName = null,
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
  /** DOC-WORKBENCH P1 — mount a newly generated document in the Document tab
   * instead of leaving the user to find it in the sidebar. See
   * lib/createdDocument.ts for the two rules (one tab; failures keep a card). */
  onOpenCreatedDocument?: (doc: CreatedDocumentRef) => void;
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
  /** Open one of the user's own recent documents in the Document tab. When
   * set, the picker and the Compare launcher offer the user's recent documents
   * in place of the skill's shared demo examples. */
  onOpenRecentDocument?: (doc: RecentDocument) => void;
  /** The project the chat is in: the picker shows ITS documents, not all recent ones. */
  projectId?: string | null;
  projectName?: string | null;
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
  // DOC-WORKBENCH P1 — an openable created document becomes a DOCUMENT tab, so
  // it must not also appear as a "Result" tab (one tab per document). A FAILED
  // generation has no docId, is therefore not openable, and deliberately keeps
  // its card so the error is visible (NEVER-SILENT #8).
  const workbenchArtifacts = artifactsExcludingOpenableDocuments(
    artifacts.filter((a) => a.placement !== "chat"),
  );
  const hasArtifacts = workbenchArtifacts.length > 0;
  const workspaceHasContent = Boolean(workspaceSurface?.surface);
  // v6.4.0 4.5 SKILL-ONBOARDING M2 → v6.23.0 WORKSPACE-HOME-PERSISTENCE.
  // The picker used to be an onboarding affordance that a first message
  // (isFreshChat) or any agent surface switched off. a tester's UAT point was
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
  // The user's own documents, most recently worked with first. They replace
  // the skill's shared demo examples in the picker and the launcher — a
  // returning user picks up their own work (customer feedback 2026-10-20:
  // "the documents under 'Pick a document' are not documents I have worked on").
  const allRecentDocs = useRecentDocuments();
  // In a project, "Pick up where you left off" is the project's documents —
  // the chat sees exactly these, so the picker should too.
  const projectDocs = useProjectDocuments(projectId);
  const recentDocs = useMemo(
    () =>
      projectId
        ? {
            documents: projectDocs.documents.map(projectDocAsRecent),
            status: projectDocs.status === "idle" ? ("ready" as const) : projectDocs.status,
            refetch: projectDocs.retry,
          }
        : allRecentDocs,
    [projectId, projectDocs.documents, projectDocs.status, projectDocs.retry, allRecentDocs],
  );
  const recentEnabled = Boolean(onOpenRecentDocument);
  // In a project the picker is the project's documents, on any skill — not
  // only skills that ship example documents.
  const hasPickerContent =
    welcomeExamples.length > 0 || welcomePrompts.length > 0 || (Boolean(projectId) && recentEnabled);
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
  // v6.23.0 B4 — "Workspace tab sometimes needs a second click" (a tester; Mark had
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

  // DOC-WORKBENCH P1 — a newly created document MOUNTS ITSELF. `handleDocClick`
  // (via onOpenCreatedDocument) adds the Document tab and pulls the workbench to
  // it, which is the same path an upload takes — so "create a document" ends
  // with the document on screen rather than a card telling the user where to go
  // look for it.
  //
  // Reads `artifacts`, not `workbenchArtifacts`: the filter above has already
  // removed exactly the entries this effect exists to act on.
  //
  // The seen-set is per-session and only grows, matching `nextFocusedResult` —
  // a surface that blinks out during re-registration must not read as a fresh
  // arrival and reopen a document the user has navigated away from.
  const openedDocumentIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!onOpenCreatedDocument) return;
    for (const doc of newlyCreatedDocuments(artifacts, openedDocumentIdsRef.current)) {
      onOpenCreatedDocument(doc);
    }
  }, [artifacts, onOpenCreatedDocument]);

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
      ) : a.kind === "help" ? (
        // Guide hits from knowledge_search: the cited help page itself, rendered
        // like /help from the app's own build. See HelpArtefactTab.
        <HelpArtefactTab surfaceId={a.surfaceId} className="h-full" />
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
  // whole bug — a tester, 2026-08-06 UAT, raised 4×.
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
            recentDocuments={recentEnabled ? recentDocs.documents : undefined}
            onCompareViaChat={(text) => onSurfaceChatMessage?.(text)}
            onAnalyzeViaChat={(text) => onSurfaceChatMessage?.(text)}
            onOpenDocument={(identity) => {
              // A recent document isn't an open tab yet — open it, rather than
              // focusing a tab that doesn't exist (a dead click).
              const recent =
                "doc_id" in identity && !docTabs.some((t) => t.id === identity.doc_id)
                  ? recentDocs.documents.find((d) => d.id === identity.doc_id)
                  : undefined;
              if (recent && onOpenRecentDocument) onOpenRecentDocument(recent);
              else onOpenLauncherDoc?.(identity);
            }}
            activitySink={activitySink}
          />
        )}
        {showPicker && (
          <SkillExamplesPicker
            examples={welcomeExamples}
            onPickExample={onPickExample}
            prompts={welcomePrompts}
            onPickPrompt={onPickPrompt}
            recent={recentEnabled ? recentDocs.documents : undefined}
            recentStatus={recentDocs.status}
            onRetryRecent={recentDocs.refetch}
            onOpenRecent={onOpenRecentDocument}
            onResumeSession={onSelectSession}
            recentHeading={
              projectId
                ? {
                    eyebrow: "This project",
                    title: projectName ? `${projectName}: documents` : "Project documents",
                    description: "What chats in this project can read. Open one to ask about it, or jump back into the conversation you had.",
                  }
                : undefined
            }
            recentEmpty={
              projectId ? (
                <>
                  No documents in this project yet.{" "}
                  <Link href={`/projects/${encodeURIComponent(projectId)}`} className="text-primary underline underline-offset-2">
                    Add some in project settings
                  </Link>
                  .
                </>
              ) : undefined
            }
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
            <DocumentPanel docId={activeTabId} onSurfaceChatMessage={onSurfaceChatMessage} activitySink={activitySink} />
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
    completedReplyId,
    sendMessage: sendMessageRaw,
    isLoading,
    error,
    clearError,
    stop,
  } = useSkillAgent();
  // Projects §6.3: the project this thread is scoped to. Every turn carries it
  // (forwardedProps.project_id) — through this one wrapper, so no send path can
  // forget it. A ref keeps the wrapper's identity stable across scope changes.
  const [projectScopeId, setProjectScopeId] = useState<string | null>(null);
  const projectScopeRef = useRef<string | null>(null);
  useEffect(() => {
    projectScopeRef.current = projectScopeId;
  }, [projectScopeId]);
  const sendMessage = useCallback(
    (text: string, opts?: { documentIds?: string[]; resumedSession?: boolean; projectId?: string | null }) =>
      sendMessageRaw(text, { ...opts, projectId: opts?.projectId ?? projectScopeRef.current }),
    [sendMessageRaw],
  );
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
    slug: skillSlug,
  } = useSkillMeta(skillId);
  // PPA-OBLIGATION 7.6 M3: which workbench-launcher affordances the active
  // skill supports, derived from its tool set. demo-doc-compare → compare;
  // demo-ppa-expert → analyze obligations.
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
  // "Your instructions" (projects §6.5): the banner says when they're on.
  const myInstructions = useMyInstructions();
  const instructionsOn = myInstructions.status === "ready" && myInstructions.instructions.trim().length > 0;
  const [draft, setDraft] = useState("");
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const composerVoice = useVoiceConfig(skillId);
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
  // 2026-06-11 polish: sidebar default-collapsed so a first visit gives chat
  // the full row. The user's choice is a workspace-wide preference (not
  // per-skill), remembered in localStorage — it used to live in
  // sessionStorage, so a user who opened the sidebar to find their past
  // conversations had to find the toggle again on every visit (customer
  // feedback 2026-10-20: "I cannot easily access and continue an existing
  // chat"). Storage can throw (Safari private mode) — fall back to the default.
  const [showDocBrowser, setShowDocBrowser] = useState(false);
  useEffect(() => {
    try {
      if (window.localStorage.getItem(SIDEBAR_OPEN_KEY) === "1") setShowDocBrowser(true);
    } catch {
      /* storage unavailable — keep the default */
    }
  }, []);
  const toggleDocBrowser = useCallback(() => {
    setShowDocBrowser((v) => {
      const next = !v;
      try {
        window.localStorage.setItem(SIDEBAR_OPEN_KEY, next ? "1" : "0");
      } catch {
        /* storage unavailable — the toggle still works for this page */
      }
      return next;
    });
  }, []);
  // Open the sidebar from elsewhere (the landing's "All conversations") and
  // remember it, exactly like the header toggle.
  const openDocBrowser = useCallback(() => {
    setShowDocBrowser(true);
    try {
      window.localStorage.setItem(SIDEBAR_OPEN_KEY, "1");
    } catch {
      /* storage unavailable */
    }
  }, []);
  // The header (ShellChrome → SkillsBar) owns the sidebar button; this shell
  // owns the state.
  useRegisterSidebarToggle(showDocBrowser, toggleDocBrowser);

  // The phone sidebar is a sheet over the page: Escape closes it, like any
  // overlay.
  useEffect(() => {
    if (!showDocBrowser) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && window.matchMedia?.("(max-width: 767px)").matches) toggleDocBrowser();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [showDocBrowser, toggleDocBrowser]);
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

  // MOBILE (below `md`): chat and workbench can't share a 390px row, so they
  // swap behind a Chat | Workbench tab bar instead of splitting. Desktop
  // ignores this state entirely — every class it drives is `max-md:`-scoped.
  // Anything that focuses a workbench tab (a new result, an opened doc, an
  // action run) brings the workbench forward — repo principle #7 — and
  // sending a message brings chat back so the reply is on screen.
  const [mobilePane, setMobilePane] = useState<"chat" | "workbench">("chat");
  const focusWorkbenchTab = useCallback((id: string) => {
    setWorkbenchTabId(id);
    setMobilePane("workbench");
  }, []);
  // A help link clicked in chat: show the Help tab even if the workbench is folded.
  const openHelpTab = useCallback(
    (id: string) => {
      setWorkbenchCollapsed(false);
      focusWorkbenchTab(id);
    },
    [focusWorkbenchTab],
  );

  // Session routing: read ?session= from URL, allow programmatic navigation
  const sessionId = searchParams.get("session");
  useEffect(() => {
    setVoiceBusy(false);
    setVoiceNotice(null);
    cancelVoicePlayback();
  }, [skillId, sessionId]);

  // Effective thread id — the value threaded to the surface mounts
  // (sessionId ?? agentSessionId). The confirm→switch below keys off it.
  const effectiveSessionId = sessionId ?? agentSessionId;

  const { initialMessages, a2uiSurfaces, historyError, sessionGone, transcriptUnavailable } =
    useSessionMessages(sessionId);
  const { tabs: sessionDocTabs, projectId: sessionProjectId } = useSessionDocuments(sessionId);
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
  const {
    sessions,
    isLoading: sessionsLoading,
    error: sessionsError,
    refetch: refetchSessions,
    hasMore: sessionsHasMore,
    isLoadingMore: sessionsLoadingMore,
    loadMoreFailed: sessionsLoadMoreFailed,
    loadMore: loadMoreSessions,
  } = useSkillSessions(sessionSkillFilter);

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

  // Projects §6.3 — where the scope comes from besides `?project=` (below):
  // a resumed chat takes its row's project (or none); a fresh scoped chat keeps
  // its scope through the URL writeback even before the row says so; leaving a
  // session for a new chat drops it.
  // A chat opened FROM a project's list stays in that project even when it
  // only belongs by having used a project document (no projectId on its row):
  // the user is working in the project, and the next turn makes it explicit.
  const scopeForResumeRef = useRef<{ sid: string; pid: string } | null>(null);
  useEffect(() => {
    if (!sessionId || sessionProjectId === undefined) return;
    const opened = scopeForResumeRef.current;
    if (sessionProjectId) setProjectScopeId(sessionProjectId);
    else if (opened && opened.sid === sessionId) setProjectScopeId(opened.pid);
    else if (enteredViaResume) setProjectScopeId(null);
  }, [sessionId, sessionProjectId, enteredViaResume]);
  const prevSessionIdRef = useRef(sessionId);
  useEffect(() => {
    const prev = prevSessionIdRef.current;
    prevSessionIdRef.current = sessionId;
    if (prev && !sessionId && !searchParams.get("project")) setProjectScopeId(null);
  }, [sessionId, searchParams]);

  // v6.4.0 INTERNAL-SHELL M1: auto-collapse sidebar on first user message of
  // a fresh chat so the chat + workbench own the screen during a run.
  // Fires exactly once per session-start (isFreshChat true → false). Skipped
  // on resume; manual reopens stick.
  //
  // PHONES ONLY since 2026-10-20. There the sidebar is a sheet over the page,
  // so it must get out of the way. On desktop it is a column the user opened
  // on purpose — closing it on every new chat fought the remembered
  // preference and hid their history again the moment they started working.
  const isFreshChat = messages.length === 0 && sessionId === null;
  const prevFreshChatRef = useRef(isFreshChat);
  useEffect(() => {
    if (
      prevFreshChatRef.current &&
      !isFreshChat &&
      !enteredViaResume &&
      window.matchMedia?.("(max-width: 767px)").matches
    ) {
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
    if (!text || isLoading || error || voiceBusy) return;
    cancelVoicePlayback();
    setVoiceNotice(null);
    lastUserMessageRef.current = text;
    setDraft("");
    setMobilePane("chat");
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
      setMobilePane("chat");
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

  // A shared thread (e.g. an agent's) belongs to the agent it was started on.
  // Open it THERE: replying from another skill's page would run the turn on
  // that skill with the thread owner's access, which may not include it.
  const handleSelectSharedSession = useCallback(
    (sid: string, sessionSkillId: string) => {
      if (sessionSkillId && sessionSkillId !== skillId) {
        setEnteredViaResume(true);
        router.push(`/chat/${encodeURIComponent(sessionSkillId)}?session=${encodeURIComponent(sid)}`);
        return;
      }
      handleSelectSession(sid);
    },
    [skillId, router, handleSelectSession],
  );

  // "New chat" stays in the project the user is in (leaving is the banner's
  // ✕): it re-enters through `?project=`, the same door as a project link.
  const handleNewSession = useCallback(() => {
    setEnteredViaResume(false);
    const keep = projectScopeRef.current;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("session");
    if (keep) params.set("project", keep);
    else setProjectScopeId(null);
    const qs = params.toString();
    router.replace(qs ? `${pathPrefix}?${qs}` : pathPrefix);
  }, [router, pathPrefix, searchParams]);

  // Leave the project: a plain, unscoped new chat.
  const handleLeaveProject = useCallback(() => {
    setProjectScopeId(null);
    projectScopeRef.current = null;
    scopeForResumeRef.current = null;
    setEnteredViaResume(false);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("session");
    params.delete("project");
    params.delete("ask");
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
    focusWorkbenchTab("document");
  }, [focusWorkbenchTab]);

  // DOC-WORKBENCH P1: a document the agent just generated mounts itself in the
  // workbench. Same shape as `handleUploadedDocOpen` below — handleDocClick only
  // reads id/originalFilename/sourceFormat, so the lightweight ref built from the
  // artifact (its surfaceId carries the docId, its title the filename) is enough.
  const handleOpenCreatedDocument = useCallback(
    (doc: CreatedDocumentRef) => {
      handleDocClick({
        id: doc.id,
        originalFilename: doc.filename,
        sourceFormat: doc.format,
      } as ParsedDocument);
    },
    [handleDocClick],
  );

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

  // A recent document is already the user's own parsed record — no import,
  // just mount it like a sidebar click.
  const handleOpenRecentDocument = useCallback(
    (doc: RecentDocument) => {
      handleDocClick({
        id: doc.id,
        originalFilename: doc.originalFilename,
        sourceFormat: doc.sourceFormat,
      } as ParsedDocument);
    },
    [handleDocClick],
  );

  // `?doc=<id>&docName=…&docFormat=…` — a document opened from somewhere with
  // no workbench of its own (a project home) arrives here and mounts as an
  // included tab, exactly like a sidebar click. The params are then dropped so
  // a reload or the session writeback doesn't re-open it.
  const docParam = searchParams.get("doc");
  useEffect(() => {
    if (!docParam) return;
    handleDocClick({
      id: docParam,
      originalFilename: searchParams.get("docName") || "Document",
      sourceFormat: searchParams.get("docFormat") || "",
    } as ParsedDocument);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("doc");
    params.delete("docName");
    params.delete("docFormat");
    const qs = params.toString();
    router.replace(qs ? `${pathPrefix}?${qs}` : pathPrefix);
    // Only when a new ?doc= arrives — not on every searchParams change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docParam]);

  // `?project=<id>[&ask=<text>]` — "Ask about this project…" on a project home
  // starts a fresh chat here scoped to that project, and sends the question
  // once the chat is ready. Params are dropped like `?doc=`.
  const projectParam = searchParams.get("project");
  const [pendingAsk, setPendingAsk] = useState<{ text: string; projectId: string } | null>(null);
  useEffect(() => {
    if (!projectParam) return;
    const resumingSid = searchParams.get("session");
    // With ?session= it is a chat opened from a project's list on another
    // agent: still a resume, and it stays in the project.
    if (resumingSid) scopeForResumeRef.current = { sid: resumingSid, pid: projectParam };
    else setEnteredViaResume(false);
    setProjectScopeId(projectParam);
    projectScopeRef.current = projectParam;
    const ask = (searchParams.get("ask") || "").trim();
    if (ask) setPendingAsk({ text: ask, projectId: projectParam });
    const params = new URLSearchParams(searchParams.toString());
    params.delete("project");
    params.delete("ask");
    // A link can carry ?doc= too (open a document in a project chat). Both
    // effects rewrite the URL from the same snapshot, so this one — which runs
    // after the ?doc= one — drops those as well, or the last write puts them back.
    params.delete("doc");
    params.delete("docName");
    params.delete("docFormat");
    const qs = params.toString();
    router.replace(qs ? `${pathPrefix}?${qs}` : pathPrefix);
    // Only when a new ?project= arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectParam]);
  useEffect(() => {
    if (!pendingAsk || !chatReady || isLoading) return;
    const { text, projectId } = pendingAsk;
    setPendingAsk(null);
    lastUserMessageRef.current = text;
    setMobilePane("chat");
    void sendMessage(text, { documentIds: includedDocIds, resumedSession: false, projectId });
  }, [pendingAsk, chatReady, isLoading, sendMessage, includedDocIds]);

  // A chat row's project action (⋯ → Make this a project / Move to project…).
  const [sessionProjectFeedback, setSessionProjectFeedback] = useState<SessionProjectFeedback | null>(null);
  const dismissSessionProjectFeedback = useCallback(() => setSessionProjectFeedback(null), []);
  // Moving the OPEN chat changes its scope for the next turn, too.
  useEffect(() => {
    if (sessionProjectFeedback?.kind !== "done" || !sessionId) return;
    let cancelled = false;
    void fetchWithAuth(`/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`)
      .then((res) => (res.ok ? (res.json() as Promise<{ session?: { project_id?: string | null } }>) : null))
      .then((body) => {
        if (cancelled || !body) return;
        const pid = body.session?.project_id;
        setProjectScopeId(typeof pid === "string" && pid ? pid : null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionProjectFeedback, sessionId]);

  // The scoped project, for the banner. A deleted (404) project drops the
  // scope, so the banner never claims one that the backend won't apply.
  const scopedProject = useProject(projectScopeId);
  useEffect(() => {
    if (scopedProject.status === "not_found") setProjectScopeId(null);
  }, [scopedProject.status]);
  const projectContext = useMemo<ActiveProjectContext | null>(() => {
    const p = scopedProject.project;
    if (!p || p.id !== projectScopeId) return null;
    return {
      id: p.id,
      name: p.name,
      docCount: p.docCount,
      instructionsOn: instructionsOn || p.instructions.trim().length > 0,
      searchPreparing: p.search?.status === "indexing",
    };
  }, [scopedProject.project, projectScopeId, instructionsOn]);

  // Tell the header's "New chat" which project to stay in.
  useEffect(() => {
    setActiveProject(projectContext?.id ?? null);
    return () => setActiveProject(null);
  }, [projectContext?.id]);

  // In a project, the sidebar history and the landing's recent list are the
  // PROJECT's chats (explicit ∪ derived) — the chat screen is the project.
  const projectSessions = useProjectSessions(projectScopeId);
  const handleSelectProjectSession = useCallback(
    (sid: string) => {
      const pid = projectScopeRef.current;
      if (pid) scopeForResumeRef.current = { sid, pid };
      const s = projectSessions.sessions.find((x) => x.session_id === sid);
      if (s?.skill_id && s.skill_id !== skillId) {
        setEnteredViaResume(true);
        const qs = new URLSearchParams({ session: sid });
        if (pid) qs.set("project", pid);
        router.push(`/chat/${encodeURIComponent(s.skill_id)}?${qs.toString()}`);
        return;
      }
      handleSelectSession(sid);
    },
    [projectSessions.sessions, skillId, router, handleSelectSession],
  );

  // Where a project in the sidebar opens: the chat screen, scoped, on the
  // project's assistant — or this one while skills are still loading.
  const projectHref = useProjectChatHref(user.uid);
  const chatHrefFor = useCallback(
    (p: ProjectSummary) => projectHref(p) ?? projectChatHref(pathPrefix, p.id),
    [projectHref, pathPrefix],
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
        focusWorkbenchTab("document");
      }
    },
    [handleImportByReference, focusWorkbenchTab],
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

  // A dropped stream (e.g. the phone suspended the app mid-answer) may still
  // complete on the server — reload the conversation from history to see it.
  // Full navigation on purpose: the history fetch runs on load.
  const reloadConversation = effectiveSessionId
    ? () => {
        const url = new URL(window.location.href);
        url.searchParams.set("session", effectiveSessionId);
        window.location.assign(url.toString());
      }
    : undefined;

  // A backgrounded desktop tab gets "Reply ready · in <skill>" when the turn
  // finishes (no message text — it lands on lock screens).
  useReplyReadyNotification({
    isLoading,
    hasError: error !== null,
    skillName: displayName,
    sessionId: effectiveSessionId,
  });

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
      {/* One card, the way native chat apps draw it: the text on top, the
          tools in a row underneath — attach on the left, send on the right.
          A tap anywhere on the card focuses the text, so on a phone a single
          tap brings the keyboard up (iOS opens it only on a real tap). */}
      <form
        className="cursor-text rounded-[var(--radius)] border border-input bg-background p-1.5 transition-colors focus-within:border-ring"
        onClick={(e) => {
          if (e.target === e.currentTarget) draftRef.current?.focus();
        }}
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend();
        }}
      >
        {/* 2026-08-06 acceptance testing: this was a single-line <input>, which by
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
          onChange={(e) => {
            cancelVoicePlayback();
            setDraft(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
          rows={1}
          placeholder={
            !chatReady ? "Connecting…" : projectContext ? `Ask about ${projectContext.name}…` : `Message ${displayName}…`
          }
          className="block max-h-48 min-h-[44px] w-full resize-none overflow-y-auto border-0 bg-transparent px-2.5 py-2 text-base leading-6 placeholder:text-muted-foreground focus:outline-none md:text-sm"
          // Blocked while connecting and while an error is showing (deal with
          // it first: Retry / Dismiss). NOT while a reply streams — disabling
          // blurs the box, and on a phone that drops the keyboard after every
          // message — so the next message can be typed during this one. Sending
          // is still gated: Send is swapped for Stop until the reply ends, and
          // handleSend refuses while loading.
          disabled={!chatReady || error !== null}
        />
        <div className="flex items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1">
            <UploadDropZone variant="button" skillId={skillId} onUploadComplete={handleUploadedDocOpen} />
            <VoiceComposerControls key={`${skillId}:${sessionId ?? "new"}`}
              skillId={skillId} lang={composerVoice.tts.language ?? "auto"}
              voiceInputEnabled={composerVoice.stt.enabled}
              maxDurationSeconds={composerVoice.stt.maxDurationSeconds}
              disabled={!chatReady || isLoading || error !== null}
              onTranscript={(text) => setDraft((current) => current.trim() ? `${current} ${text}` : text)}
              onBusyChange={setVoiceBusy} onNotice={setVoiceNotice} />
            {process.env.NEXT_PUBLIC_ENABLE_READ_ALOUD === "true" && composerVoice.tts.enabled && <AutoReadToggle />}
          </div>
          {isLoading ? (
            <button
              type="button"
              onClick={stop}
              aria-label="Stop"
              title="Stop (Esc)"
              className="flex h-10 w-10 items-center justify-center rounded-full border border-border text-foreground hover:bg-muted"
            >
              <span aria-hidden className="h-3 w-3 rounded-[2px] bg-current" />
            </button>
          ) : (
            <button
              type="submit"
              aria-label="Send"
              title="Send (Enter)"
              // Keep focus in the text box, so on a phone the keyboard stays up
              // between messages instead of dropping after every send.
              onMouseDown={(e) => e.preventDefault()}
              className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-40"
              disabled={!draft.trim() || inputDisabled || voiceBusy}
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 19V5M6 11l6-6 6 6" />
              </svg>
            </button>
          )}
        </div>
      </form>
      {voiceNotice && <p role="status" aria-live="polite" className="mt-1 px-2 text-xs text-muted-foreground">{voiceNotice}</p>}
    </>
  );

  // A fresh chat starts with the cursor in the composer, ready to type. On a
  // desktop that is the whole story; on iOS the keyboard only opens on a real
  // tap, which is why the composer card is one big tap target.
  useEffect(() => {
    if (isFreshChat && chatReady) draftRef.current?.focus({ preventScroll: true });
  }, [isFreshChat, chatReady, skillId]);

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
    <WorkspaceAgentTools sessionId={effectiveSessionId} skillId={skillId} running={isLoading || actionRunning} onFocus={(id) => setWorkbenchTabId(id === "workspace" ? WORKSPACE_RESULT_TAB_ID : id)} />
    <SurfaceSessionLifecycle sessionId={sessionId} />
    <WorkspaceA2uiEventRouter />
    <ArtifactPresenceReporter onPresent={setHasAnyArtifact} />
    <HelpOpenerBridge onOpen={openHelpTab} />
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
          focusWorkbenchTab("document");
        }}
        onClose={handleTabClose}
        onToggleInclude={handleTabToggleInclude}
        onToggleBrowser={toggleDocBrowser}
      />

      {/* MOBILE: the Chat | Workbench switch that replaces the side-by-side
          split below `md`. Only when the workbench has something to show —
          otherwise chat is the whole screen and a tab bar is noise. */}
      {workbenchHasContent && (
        <div className="flex shrink-0 justify-center pb-2 md:hidden">
          <div className="inline-flex rounded-full bg-muted p-1" role="tablist" aria-label="Chat or workbench">
            {(["chat", "workbench"] as const).map((pane) => (
              <button
                key={pane}
                type="button"
                role="tab"
                aria-selected={mobilePane === pane}
                onClick={() => setMobilePane(pane)}
                className={
                  "min-h-[36px] rounded-full px-5 text-sm font-medium capitalize transition-colors " +
                  (mobilePane === pane
                    ? "bg-background text-foreground ring-1 ring-border"
                    : "text-muted-foreground")
                }
              >
                {pane}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="relative flex min-h-0 flex-1" data-workspace-row>
        {showDocBrowser && (
          // MOBILE: a 256px column would leave ~130px for everything else on a
          // phone, so below `md` the sidebar floats over the row as a sheet,
          // with a backdrop that closes it.
          <button
            type="button"
            aria-label="Dismiss sidebar"
            tabIndex={-1}
            onClick={toggleDocBrowser}
            className="absolute inset-0 z-20 bg-[hsl(var(--foreground)/0.25)] md:hidden"
          />
        )}
        {showDocBrowser && (
          <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-r bg-muted/30 max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:w-[85vw] max-md:max-w-sm max-md:bg-background">
            {/* Phone sheet header: a visible way out besides the backdrop. */}
            <div className="flex min-h-[52px] shrink-0 items-center justify-between border-b px-4 md:hidden">
              <span className="text-base font-semibold text-foreground">Files and conversations</span>
              <button
                type="button"
                onClick={toggleDocBrowser}
                aria-label="Close sidebar"
                className="-mr-2 flex h-11 w-11 items-center justify-center rounded-[var(--radius)] text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <svg className="h-4 w-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            </div>
            {/* v6.6.0: order the sidebar skill-first. The skill's own
                affordances (its curated Library + anything the agent streams
                into the A2UI sidebar surface) sit at the top because they're
                the most relevant while working; the generic workspace utilities
                (chat history, then your uploads) sit below. Each section remembers
                its open/closed state across sidebar reopens (persistId). */}

            {/* Skill-related #1 — the skill's curated document library
                (v6.4.0 4.5 SKILL-ONBOARDING M4), e.g. a tenant's shared library. Click
                a file → import-by-reference, no upload. Only present when the
                skill declares welcome.bucket_browser. */}
            {skillWelcome?.bucketBrowser?.bucket && (
              <SidebarSection
                title={skillWelcome.bucketBrowser.label || "Library"}
                persistId="library"
                defaultOpen={skillWelcome.bucketBrowser.defaultOpen ?? true}
              >
                {/* Cap + scroll the library so a large folder (the tenant's PPA library
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

            {/* Chat history — ABOVE "Your files" since 2026-10-20. It used to
                sit last, under the uploads, in a five-row box, which is exactly
                where a customer looked and couldn't find their chats ("I can
                see the uploads … but I cannot easily access and continue an
                existing chat"). Getting back to earlier work is the most common
                reason to open the sidebar at all. */}
            {/* Projects (projects design §4): a named tag on documents, with a
                home for its chats. Above history — it is the way to group it.
                Always shown, so a first-time user finds "+ New project". */}
            <SidebarSection title="Projects" persistId="projects" defaultOpen={true}>
              <div className="max-h-[30vh] overflow-y-auto">
                <ProjectsSection chatHrefFor={chatHrefFor} activeProjectId={projectContext?.id ?? null} />
              </div>
            </SidebarSection>

            <SidebarSection title="Past conversations" persistId="conversations" defaultOpen={true}>
              {projectScopeId ? (
                <ProjectChatsList
                  projectName={projectContext?.name ?? null}
                  chats={projectSessions}
                  activeSessionId={sessionId}
                  onSelectSession={handleSelectProjectSession}
                  onDelete={(sid) => void handleDeleteSkillSession(sid)}
                  onShowAll={handleLeaveProject}
                  rowActions={(s) => <SessionProjectMenu session={s} onFeedback={setSessionProjectFeedback} />}
                />
              ) : (
              <div className="max-h-[40vh] overflow-y-auto">
                <SkillSessionPanel
                  sessions={sessions}
                  activeSessionId={sessionId}
                  isLoading={sessionsLoading}
                  onSelectSession={handleSelectSession}
                  onDelete={(sid) => void handleDeleteSkillSession(sid)}
                  skillFilter={sessionSkillFilter}
                  onFilterChange={setSessionSkillFilter}
                  groupByDate
                  error={sessionsError}
                  onRetry={refetchSessions}
                  hasMore={sessionsHasMore}
                  isLoadingMore={sessionsLoadingMore}
                  loadMoreFailed={sessionsLoadMoreFailed}
                  onLoadMore={loadMoreSessions}
                  rowActions={(s) => <SessionProjectMenu session={s} onFeedback={setSessionProjectFeedback} />}
                />
              </div>
              )}
              <SessionProjectNotice feedback={sessionProjectFeedback} onDismiss={dismissSessionProjectFeedback} />
            </SidebarSection>

            {/* Generic workspace utility — the user's own uploaded files. */}
            <SidebarSection title="Your files" persistId="files" defaultOpen={true} bodyClassName="">
              <div className="max-h-[30vh] overflow-y-auto">
                <DocListView uid={user.uid} skillId={skillId} onDocClick={handleDocClick} />
              </div>
              <div className="border-t">
                <UploadDropZone skillId={skillId} />
              </div>
            </SidebarSection>

            {/* Conversations owned by someone else (e.g. an agent's threads)
                that this user collaborates on — read AND reply. Renders
                nothing when there are none; a load failure shows visibly. */}
            <SharedSessionsSection
              activeSessionId={sessionId}
              onSelectSession={handleSelectSharedSession}
            />

            {/* Phones: who you are and the account actions live here, so the
                header can stay as just menu · skill · new chat. */}
            <div className="mt-auto md:hidden">
              <AccountPanel />
            </div>
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
            (workbenchVisible ? "relative flex min-w-0 flex-col" : "relative flex min-w-0 flex-1 flex-col") +
            // MOBILE: full width when showing, gone when the workbench is.
            // `!` beats the inline flex-basis the desktop split sets.
            (workbenchHasContent && mobilePane === "workbench"
              ? " max-md:hidden"
              : " max-md:!flex-1 max-md:!basis-full")
          }
          style={
            workbenchVisible
              ? { flexBasis: `${(1 - workspaceRatio) * 100}%`, flexGrow: 0, flexShrink: 1 }
              : undefined
          }
        >
          {/* The skill's illustration behind the landing — at a glance, which
              skill this is. It fades out with the first message so it never
              sits behind a transcript. Renders nothing for a skill with no art. */}
          <SkillArt slug={skillSlug} visible={isFreshChat && !enteredViaResume} />
          {/* ONE composer, in ONE place, for both the landing and the
              conversation: only what sits above it swaps. Keyed siblings keep
              React from remounting it on the first send — a remounted textarea
              loses focus, and on a phone that drops the keyboard right after
              the first message. */}
          {isFreshChat ? (
            // Fresh session — no transcript yet. Phones: the welcome fills the
            // space above the composer, which sits under the thumb. Desktop:
            // auto margins centre welcome + composer together (Claude/ChatGPT-
            // style landing). Flips to the transcript the instant the first
            // message is sent (isFreshChat→false).
            <div
              key="welcome"
              className="relative z-10 flex flex-1 flex-col items-center justify-center overflow-auto px-5 py-8 md:mt-auto md:flex-none md:px-8"
            >
              {!enteredViaResume ? (
                <SkillWelcome name={displayName} avatar={skillAvatar} intro={skillIntroMessage} />
              ) : null}
              {/* NEVER SILENT (#8): an error on the very first turn must still
                  be visible on the landing screen, not hidden with the
                  (unrendered) transcript. */}
              {error && (
                <div className="mt-6 w-full max-w-2xl">
                  <StreamErrorBanner error={error} onRetry={handleRetry} onDismiss={clearError} onReload={reloadConversation} />
                </div>
              )}
            </div>
          ) : (
            <div key="transcript" className="flex min-h-0 flex-1 flex-col">
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
            completedReplyId={completedReplyId}
            isLoading={isLoading}
            error={error}
            skillId={displayName}
            botAvatarUrl={skillAvatar}
            userInitial={userInitial}
            userDisplayName={userDisplayName}
            userPhotoURL={user.photoURL}
            instructionsOn={instructionsOn}
            projectContext={projectContext}
            onLeaveProject={handleLeaveProject}
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
                  onReload={reloadConversation}
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
            </div>
          )}
          <footer
            key="composer"
            className={
              "relative z-10 px-5 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:px-8 " +
              (isFreshChat ? "" : "border-t pt-3")
            }
          >
            {/* Same column as the transcript (max-w-3xl on a conversation,
                a little narrower on the landing). */}
            {/* A scoped chat that hasn't started yet still says which project
                it's in — the transcript's banner only appears after a send. */}
            {isFreshChat && projectContext && (
              <div className="mx-auto mb-2 w-full max-w-2xl overflow-hidden rounded-md border">
                <ContextBanner context={null} project={projectContext} onLeaveProject={handleLeaveProject} />
              </div>
            )}
            <div className={"mx-auto w-full " + (isFreshChat ? "max-w-2xl" : "max-w-3xl")}>{composer}</div>
          </footer>
          {/* Desktop quick-start: drop a document straight onto the landing.
              Phones attach with the composer's + (no drag-and-drop on a
              phone). The upload mounts the doc as included context
              (handleUploadedDocOpen) and the InContextBadge confirms it. */}
          {isFreshChat ? (
            <div key="dropzone" className="relative z-10 mx-auto mb-auto mt-8 hidden w-full max-w-2xl space-y-6 px-8 md:block">
              {/* The way back to earlier work, where the user is when they want
                  it. Absent until there is history; the sidebar holds the rest. */}
              {projectContext ? (
                <RecentConversations
                  title={`Recent chats in ${projectContext.name}`}
                  sessions={projectSessions.sessions}
                  onSelectSession={handleSelectProjectSession}
                  onShowAll={openDocBrowser}
                />
              ) : (
                !sessionSkillFilter && (
                  <RecentConversations
                    sessions={sessions}
                    onSelectSession={handleSelectSession}
                    onShowAll={openDocBrowser}
                  />
                )
              )}
              {/* The default project's instructions (projects §6.5): where every
                  chat starts, so it's noticed. Also in Settings. */}
              <YourInstructionsCard />
              <div className="flex items-center gap-4 text-sm text-muted-foreground">
                <div className="h-px flex-1 bg-border" />
                <span>Or start from a document</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <UploadDropZone skillId={skillId} onUploadComplete={handleUploadedDocOpen} className="" />
            </div>
          ) : null}
        </div>

        {/* 2026-06-11 polish: drag handle between chat and workbench.
            Drag, ←/→ (5%), Home/End (jump to min/max), Enter (50%);
            snaps at 30/50/70/100. Per-skill ratio in sessionStorage.
            Only rendered when the workbench is visible (content + not
            user-collapsed) — otherwise chat takes the full row. */}
        {workbenchVisible && (
          <div className="flex max-md:hidden">
            <WorkbenchResizeHandle ratio={workspaceRatio} onChange={setWorkspaceRatio} />
          </div>
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
            className="group flex h-full w-6 shrink-0 flex-col items-center justify-center border-l bg-muted/40 transition-colors hover:bg-muted max-md:hidden"
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
          className={
            "relative flex min-w-0 flex-col" +
            // MOBILE: the workbench is a full-screen tab, never a sliver, and
            // the desktop collapse state doesn't apply.
            (workbenchHasContent && mobilePane === "workbench"
              ? " max-md:!flex-1 max-md:!basis-full"
              : " max-md:hidden")
          }
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
            className="absolute right-1 top-1 z-10 flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground max-md:hidden"
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
          onOpenCreatedDocument={handleOpenCreatedDocument}
          // Width comes from this column; HEIGHT must too. Without min-h-0 +
          // flex-1 the pane kept its content height (shrink-0), so a long tab
          // (a help page, a big sources list) stretched the whole page instead
          // of scrolling inside the workbench.
          workbenchClassName="min-h-0 flex-1"
          onContentChange={setWorkbenchHasContent}
          activeTabId={activeTabId}
          sessionId={sessionId ?? agentSessionId}
          skillId={skillId}
          userUid={user.uid}
          onSurfaceChatMessage={handleSurfaceChatMessage}
          workbenchTabId={workbenchTabId}
          onWorkbenchTabChange={focusWorkbenchTab}
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
          onOpenRecentDocument={handleOpenRecentDocument}
          projectId={projectContext?.id ?? null}
          projectName={projectContext?.name ?? null}
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
