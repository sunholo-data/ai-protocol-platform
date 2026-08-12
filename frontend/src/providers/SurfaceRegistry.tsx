// MULTI-SURFACE-A2UI — SurfaceRegistry (v0.9 native, post-M5)
//
// Maps `surfaceId → mount point + per-surface MessageProcessor + SurfaceModel`,
// with per-surface persistence policy. The registry consumes the A2UI v0.9
// wire format directly (array of {version, createSurface | updateComponents |
// updateDataModel | deleteSurface} messages) and feeds it to a per-surface
// MessageProcessor from @a2ui/web_core/v0_9.
//
// **Why one MessageProcessor per surface** (not one global):
//   - Lifecycle alignment: clearing a session-scoped surface == disposing
//     its processor. Cross-surface bleed is impossible by construction.
//   - The SDK's MessageProcessor + SurfaceGroupModel is designed for the
//     single-app, single-session use case; we have multiple independent
//     surfaces with different persistence scopes (workspace = session,
//     modal = turn). Per-surface processors model that directly.
//
// **Auto-createSurface** (Gemini sometimes skips createSurface):
//   - When appendMessages receives updateComponents/updateDataModel for a
//     surface that hasn't seen createSurface yet, we synthesize one with
//     basicCatalog. Logs a dev warning so we can spot LLM regressions.

"use client";

import {
  createContext,
  type ReactNode,
  type RefObject,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";
import {
  MessageProcessor,
  type A2uiMessage,
  type SurfaceModel,
} from "@a2ui/web_core/v0_9";
import {
  basicCatalog,
  type ReactComponentImplementation,
} from "@a2ui/react/v0_9";

// ─── Public types ───────────────────────────────────────────────────────────

export interface SurfacePolicy {
  /**
   * - `turn-scoped`     — cleared after the current run finishes (chat default).
   * - `session-scoped`  — cleared on session change (workspace/sidebar default).
   * - `indefinite`      — survives session changes (admin opt-in for sidebar).
   */
  persistence: "turn-scoped" | "session-scoped" | "indefinite";
  /** Modal-style surfaces refuse agent-initiated emits without a user gesture. */
  requiresUserGesture: boolean;
}

/** One A2UI v0.9 message, opaque to the registry — passed straight to the SDK. */
export type A2uiV09Message = Record<string, unknown>;

/**
 * Optional metadata a backend emission attaches to a surface (7.5 workbench
 * artifacts): drives the workbench tab title + the Workspace index/timeline.
 */
export interface A2uiArtifact {
  kind?: string;
  /** For `kind: "elicitation"` chat cards — distinguishes a compact confirm from
   * the taller field form so the frontend can size each appropriately (8.2). */
  elicitationKind?: "confirm" | "confirm_with_fields";
  /** True when this surface was rehydrated from session history (not emitted live
   * this turn). Interactive chat cards (confirm/elicitation) render FROZEN when
   * replayed — the action already happened / the session has moved on, so a live
   * "Proceed" would be a lie (v6.10.0). */
  replayed?: boolean;
  title?: string;
  description?: string;
  /**
   * Where this artifact renders. Default (`undefined` / `"workbench"`) → its own
   * workbench tab. `"chat"` → rendered inline in the chat thread as an
   * interactive `A2UISurfaceMount` (the obligation elicitation form, 7.8 M1) — a
   * result that needs structured input from the user acts where they're reading,
   * per CLAUDE.md "user interaction ... renders as A2UI in the chat area via the
   * surface-action loop".
   */
  placement?: "chat" | "workbench";
}

/** One entry in `listArtifacts()` — an active artifact surface + its metadata. */
export interface A2uiArtifactEntry extends A2uiArtifact {
  surfaceId: string;
  /** `Date.now()` at first content — orders the workbench tabs + index. */
  createdAt: number;
}

export interface SurfaceState {
  /**
   * Active v0.9 SurfaceModel for this surface, or `null` if no createSurface
   * has been processed (or after clear/delete). Consumers pass this straight
   * to `<A2uiSurface surface={state.surface} />`.
   */
  surface: SurfaceModel<ReactComponentImplementation> | null;
  /** `Date.now()` at the most recent message processed. */
  lastUpdatedAt: number;
  /** Tool call id that produced the most recent batch — for audit + dedupe. */
  sourceToolCallId: string | null;
  /** Artifact metadata (7.5), or `null` for a plain surface (e.g. `workspace`). */
  artifact: A2uiArtifact | null;
  /** `Date.now()` at first content on this surface — stable across updates. */
  createdAt: number;
}

export interface SurfaceRegistryAPI {
  /**
   * Register a DOM mount for `surfaceId`. Idempotent for the SAME ref;
   * a DIFFERENT ref logs `console.error` and is refused (Frankenstein
   * layouts shouldn't crash the app — they should just lose the surface).
   */
  register(
    surfaceId: string,
    mountRef: RefObject<HTMLDivElement | null>,
    policyOverride?: Partial<SurfacePolicy>,
  ): void;
  unregister(surfaceId: string): void;
  getMount(surfaceId: string): RefObject<HTMLDivElement | null> | null;
  /**
   * Returns the policy for a surface. Falls back to the registered default
   * (with override applied), or — if the surface isn't in `DEFAULT_SURFACES`
   * AND has never been registered — the `chat` default (safe choice).
   */
  getPolicy(surfaceId: string): SurfacePolicy;
  /**
   * Feed an array of v0.9 A2UI messages to the per-surface MessageProcessor.
   * If the first messages reference a surfaceId that has never seen a
   * createSurface, synthesize one using basicCatalog (dev warning emitted).
   * Idempotent on `sourceToolCallId` — replaying the same tool call's
   * messages is a no-op (protects against React-strict-mode double-effects).
   */
  appendMessages(
    surfaceId: string,
    messages: A2uiV09Message[],
    sourceToolCallId: string,
    artifact?: A2uiArtifact | null,
    createdAtOverride?: number,
  ): void;
  /**
   * Every active artifact surface (has content AND artifact metadata), ordered
   * by first-seen `createdAt` — for deriving workbench tabs + the Workspace
   * index (7.5). Plain surfaces (e.g. `workspace`, no metadata) are excluded.
   */
  listArtifacts(): A2uiArtifactEntry[];
  /**
   * Snapshot every active surface's current state as
   * `{[surfaceId]: {catalogId, dataModel}}` for outbound transport
   * (e.g. `forwardedProps.a2ui_surface_state` on the next runAgent
   * POST). Sprint 2.10 — closes the workspace → agent context loop.
   * Returns an empty object when no surfaces are active; callers should
   * omit the wire slot rather than send `{}`.
   */
  readA2uiSurfaceState(): Record<string, { catalogId: string; dataModel: unknown }>;
  /**
   * Drop the surface + processor for `surfaceId`; subscribers re-render with
   * `null`. This is also the REMOVAL path for a user-closed workbench artifact
   * tab: the entry's `state` goes null, so `listArtifacts()` stops returning it
   * and the tab is genuinely gone (artifactCount decrements) — not hidden.
   *
   * Deliberately NOT a tombstone, and deliberately NOT a hard `entries.delete`:
   *   - No tombstone — `consumedToolCallIds` is cleared, so the SAME surfaceId
   *     re-registers and renders normally if the identical query is re-run.
   *   - No map delete — `subscribeState` listeners are held ON the entry object.
   *     Deleting it would orphan every live `useSurfaceState(id)` subscriber
   *     against a stale entry, and a re-register would notify a different one.
   *     The residual entry is inert (null state/processor, default policy), so
   *     it is observably indistinguishable from never having existed.
   */
  clearSurface(surfaceId: string): void;
  /**
   * Bulk-clear surfaces matching a persistence scope. Used to clear
   * session-scoped surfaces when the AG-UI session changes. Returns the
   * count of surfaces actually cleared.
   */
  clearByPersistence(scope: SurfacePolicy["persistence"]): number;
  /** Current state for `surfaceId`, or `null` if never set / cleared. */
  getState(surfaceId: string): SurfaceState | null;
}

// ─── Default surface table ──────────────────────────────────────────────────

export const DEFAULT_SURFACES: Record<string, SurfacePolicy> = {
  chat: {
    persistence: "turn-scoped",
    requiresUserGesture: false,
  },
  workspace: {
    persistence: "session-scoped",
    requiresUserGesture: false,
  },
  sidebar: {
    persistence: "session-scoped",
    requiresUserGesture: false,
  },
  modal: {
    persistence: "turn-scoped",
    requiresUserGesture: true,
  },
};

function defaultPolicyFor(surfaceId: string): SurfacePolicy {
  return DEFAULT_SURFACES[surfaceId] ?? DEFAULT_SURFACES.chat;
}

// ─── Internal store ─────────────────────────────────────────────────────────

interface SurfaceEntry {
  mountRef: RefObject<HTMLDivElement | null> | null;
  policy: SurfacePolicy;
  state: SurfaceState | null;
  /** Per-surface MessageProcessor — owns the SurfaceModel lifecycle. */
  processor: MessageProcessor<ReactComponentImplementation> | null;
  /** Tool-call ids already consumed — protects against strict-mode double-dispatch. */
  consumedToolCallIds: Set<string>;
  /** Per-surface listeners — fired only when THIS surface's state changes. */
  listeners: Set<() => void>;
  /** Bumped on every mutation so `useSyncExternalStore` snapshots are stable. */
  version: number;
}

function makeProcessor(): MessageProcessor<ReactComponentImplementation> {
  return new MessageProcessor<ReactComponentImplementation>([basicCatalog]);
}

class SurfaceStore {
  private entries = new Map<string, SurfaceEntry>();
  /** Global listeners — for `useSurfaceMount` re-runs on register/unregister. */
  private mountListeners = new Map<string, Set<() => void>>();
  private mountVersions = new Map<string, number>();
  /** Global "any surface state changed" signal — drives `useArtifacts()` (7.5),
   * which re-derives the workbench tab list whenever an artifact arrives/clears. */
  private artifactsListeners = new Set<() => void>();
  private artifactsVersion = 0;

  private ensureEntry(surfaceId: string): SurfaceEntry {
    let entry = this.entries.get(surfaceId);
    if (!entry) {
      entry = {
        mountRef: null,
        policy: defaultPolicyFor(surfaceId),
        state: null,
        processor: null,
        consumedToolCallIds: new Set(),
        listeners: new Set(),
        version: 0,
      };
      this.entries.set(surfaceId, entry);
    }
    return entry;
  }

  private notifyState(entry: SurfaceEntry) {
    entry.version += 1;
    for (const listener of entry.listeners) listener();
    // Any state change may add/remove an artifact → wake `useArtifacts()`.
    this.artifactsVersion += 1;
    for (const listener of this.artifactsListeners) listener();
  }

  subscribeArtifacts(listener: () => void): () => void {
    this.artifactsListeners.add(listener);
    return () => {
      this.artifactsListeners.delete(listener);
    };
  }

  getArtifactsVersion(): number {
    return this.artifactsVersion;
  }

  private notifyMount(surfaceId: string) {
    this.mountVersions.set(
      surfaceId,
      (this.mountVersions.get(surfaceId) ?? 0) + 1,
    );
    const set = this.mountListeners.get(surfaceId);
    if (set) for (const listener of set) listener();
  }

  // — Mount lifecycle —

  register(
    surfaceId: string,
    mountRef: RefObject<HTMLDivElement | null>,
    policyOverride?: Partial<SurfacePolicy>,
  ): void {
    const entry = this.ensureEntry(surfaceId);
    if (entry.mountRef && entry.mountRef !== mountRef) {
      console.error(
        `[SurfaceRegistry] refusing to register surface "${surfaceId}" — ` +
          `it's already mounted by a different ref. Two A2UISurfaceMount ` +
          `components with the same surfaceId are not allowed.`,
      );
      return;
    }
    const wasMounted = entry.mountRef !== null;
    entry.mountRef = mountRef;
    entry.policy = {
      ...defaultPolicyFor(surfaceId),
      ...(policyOverride ?? {}),
    };
    if (!wasMounted) this.notifyMount(surfaceId);
  }

  unregister(surfaceId: string): void {
    const entry = this.entries.get(surfaceId);
    if (!entry) return;
    entry.mountRef = null;
    this.notifyMount(surfaceId);
  }

  getMount(surfaceId: string): RefObject<HTMLDivElement | null> | null {
    return this.entries.get(surfaceId)?.mountRef ?? null;
  }

  getPolicy(surfaceId: string): SurfacePolicy {
    const entry = this.entries.get(surfaceId);
    if (entry) return entry.policy;
    return defaultPolicyFor(surfaceId);
  }

  // — State mutation —

  appendMessages(
    surfaceId: string,
    messages: A2uiV09Message[],
    sourceToolCallId: string,
    artifact?: A2uiArtifact | null,
    createdAtOverride?: number,
  ): void {
    if (messages.length === 0) return;
    const entry = this.ensureEntry(surfaceId);

    // Strict-mode double-effect protection — same tool call's messages
    // arriving twice would re-run createSurface (the SDK throws on
    // "Surface already exists"). Idempotent on tool-call id.
    if (entry.consumedToolCallIds.has(sourceToolCallId)) return;
    entry.consumedToolCallIds.add(sourceToolCallId);

    if (!entry.processor) {
      entry.processor = makeProcessor();
    }
    const processor = entry.processor;

    const surfaceExists = processor.model.getSurface(surfaceId) !== undefined;

    // Re-declared surface. web_core throws A2uiStateError("Surface already
    // exists") if a batch carries a createSurface for a surfaceId the processor
    // already holds — but agents legitimately re-emit a full A2UI batch on each
    // turn, and our click-counter fixture seeds the surface so the very first
    // agent turn re-declares it. Left unhandled the throw was caught below and
    // the WHOLE batch dropped, freezing the demo on turn 2. In v0.9
    // createSurface only declares surfaceId/catalogId/theme (components + data
    // arrive via updateComponents/updateDataModel), so for an existing surface
    // the createSurface is redundant: strip it and apply the remaining updates,
    // preserving the surface and its data model.
    let toProcess = messages;
    if (surfaceExists) {
      toProcess = messages.filter((m) => !("createSurface" in m));
      if (toProcess.length === 0) return; // nothing but a redundant re-create
    }

    // Auto-createSurface: if the first message isn't a createSurface AND
    // the processor has no surface for this id, synthesize one. The SDK
    // throws A2uiStateError("Surface not found") otherwise; the registry's
    // job is to keep the demo robust against LLM message-ordering drift.
    const firstHasCreate = "createSurface" in toProcess[0];
    if (!firstHasCreate && !surfaceExists) {
      if (process.env.NODE_ENV !== "production") {
        console.warn(
          `[SurfaceRegistry] surface "${surfaceId}" received update without ` +
            `prior createSurface — auto-creating with basicCatalog. ` +
            `Skill prompt should emit createSurface first.`,
        );
      }
      processor.processMessages([
        {
          version: "v0.9",
          createSurface: {
            surfaceId,
            catalogId: basicCatalog.id,
          },
        } as A2uiMessage,
      ]);
    }

    try {
      // SDK already validated structurally on the backend; cast at the
      // boundary since our wire-format type is wider than the SDK's union.
      processor.processMessages(toProcess as unknown as A2uiMessage[]);
    } catch (err) {
      if (process.env.NODE_ENV !== "production") {
        console.error(
          `[SurfaceRegistry] processMessages failed for surface "${surfaceId}":`,
          err,
        );
      }
      return;
    }

    const surface = processor.model.getSurface(surfaceId) ?? null;
    const now = Date.now();
    entry.state = {
      surface,
      lastUpdatedAt: now,
      sourceToolCallId,
      // Keep the FIRST createdAt (stable ordering across updates); keep the
      // latest non-null artifact metadata (a re-emit may refine title/desc).
      // `createdAtOverride` lets resume-rehydration restore the ORIGINAL emit
      // time (from session state) so the Workspace index/timeline keeps its
      // order after a refresh instead of collapsing to "all just now".
      createdAt: entry.state?.createdAt ?? createdAtOverride ?? now,
      artifact: artifact ?? entry.state?.artifact ?? null,
    };
    this.notifyState(entry);
  }

  listArtifacts(): A2uiArtifactEntry[] {
    const out: A2uiArtifactEntry[] = [];
    for (const [id, entry] of this.entries) {
      const st = entry.state;
      if (!st?.surface || !st.artifact) continue; // only surfaces WITH metadata
      out.push({ surfaceId: id, createdAt: st.createdAt, ...st.artifact });
    }
    return out.sort((a, b) => a.createdAt - b.createdAt);
  }

  readA2uiSurfaceState(): Record<string, { catalogId: string; dataModel: unknown }> {
    // Walk every entry with a live SurfaceModel and snapshot
    // `dataModel.get('/')` (the root data model value). Skips entries
    // without a surface — they have nothing the agent can read yet.
    const snapshot: Record<string, { catalogId: string; dataModel: unknown }> = {};
    for (const [id, entry] of this.entries) {
      const surface = entry.state?.surface;
      if (!surface) continue;
      let dataModelValue: unknown;
      try {
        dataModelValue = surface.dataModel.get("/");
      } catch {
        // Defensive — a corrupt dataModel shouldn't break sendMessage.
        dataModelValue = undefined;
      }
      snapshot[id] = {
        catalogId: surface.catalog.id,
        dataModel: dataModelValue,
      };
    }
    return snapshot;
  }

  clearSurface(surfaceId: string): void {
    const entry = this.entries.get(surfaceId);
    if (!entry) return;
    if (entry.state === null && entry.processor === null) return;
    if (entry.state?.surface) {
      try {
        entry.state.surface.dispose();
      } catch {
        // best-effort dispose — SurfaceModel.dispose may not exist in all builds
      }
    }
    entry.processor = null;
    entry.state = null;
    entry.consumedToolCallIds.clear();
    this.notifyState(entry);
  }

  clearByPersistence(scope: SurfacePolicy["persistence"]): number {
    let cleared = 0;
    for (const [id, entry] of this.entries) {
      if (entry.policy.persistence !== scope) continue;
      if (entry.state === null && entry.processor === null) continue;
      if (entry.state?.surface) {
        try {
          entry.state.surface.dispose();
        } catch {
          // best-effort
        }
      }
      entry.processor = null;
      entry.state = null;
      entry.consumedToolCallIds.clear();
      this.notifyState(entry);
      cleared += 1;
      if (process.env.NODE_ENV !== "production") {
        console.info(
          `[SurfaceRegistry] clearByPersistence(${scope}) cleared surface "${id}"`,
        );
      }
    }
    return cleared;
  }

  getState(surfaceId: string): SurfaceState | null {
    return this.entries.get(surfaceId)?.state ?? null;
  }

  // — Subscriptions for useSyncExternalStore —

  subscribeState(surfaceId: string, listener: () => void): () => void {
    const entry = this.ensureEntry(surfaceId);
    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
    };
  }

  getStateVersion(surfaceId: string): number {
    return this.entries.get(surfaceId)?.version ?? 0;
  }

  subscribeMount(surfaceId: string, listener: () => void): () => void {
    let set = this.mountListeners.get(surfaceId);
    if (!set) {
      set = new Set();
      this.mountListeners.set(surfaceId, set);
    }
    set.add(listener);
    return () => {
      set!.delete(listener);
    };
  }

  getMountVersion(surfaceId: string): number {
    return this.mountVersions.get(surfaceId) ?? 0;
  }
}

// ─── Context ────────────────────────────────────────────────────────────────

interface SurfaceRegistryContextValue {
  api: SurfaceRegistryAPI;
  store: SurfaceStore;
}

const SurfaceRegistryContext =
  createContext<SurfaceRegistryContextValue | null>(null);

export function SurfaceRegistryProvider({ children }: { children: ReactNode }) {
  const storeRef = useRef<SurfaceStore | null>(null);
  if (storeRef.current === null) storeRef.current = new SurfaceStore();
  const store = storeRef.current;

  const api = useMemo<SurfaceRegistryAPI>(
    () => ({
      register: (id, ref, override) => store.register(id, ref, override),
      unregister: (id) => store.unregister(id),
      getMount: (id) => store.getMount(id),
      getPolicy: (id) => store.getPolicy(id),
      appendMessages: (id, msgs, src, artifact, createdAtOverride) =>
        store.appendMessages(id, msgs, src, artifact, createdAtOverride),
      listArtifacts: () => store.listArtifacts(),
      readA2uiSurfaceState: () => store.readA2uiSurfaceState(),
      clearSurface: (id) => store.clearSurface(id),
      clearByPersistence: (scope) => store.clearByPersistence(scope),
      getState: (id) => store.getState(id),
    }),
    [store],
  );

  const value = useMemo(() => ({ api, store }), [api, store]);

  return (
    <SurfaceRegistryContext.Provider value={value}>
      {children}
    </SurfaceRegistryContext.Provider>
  );
}

function useSurfaceRegistryContext(): SurfaceRegistryContextValue {
  const value = useContext(SurfaceRegistryContext);
  if (!value) {
    throw new Error(
      "useSurfaceRegistry must be used within a SurfaceRegistryProvider",
    );
  }
  return value;
}

// ─── Public hooks ───────────────────────────────────────────────────────────

export function useSurfaceRegistry(): SurfaceRegistryAPI {
  return useSurfaceRegistryContext().api;
}

/**
 * Soft variant of `useSurfaceRegistry` — returns `null` when called
 * outside a `SurfaceRegistryProvider` instead of throwing. Used by
 * generic hooks (e.g. `useSkillAgent`) that need to read surface state
 * if available but are also used in contexts (tests, embeds) without
 * the provider mounted. Don't use this in app components — there it's
 * a bug if the provider is missing, and the throw above is correct.
 */
export function useOptionalSurfaceRegistry(): SurfaceRegistryAPI | null {
  const value = useContext(SurfaceRegistryContext);
  return value?.api ?? null;
}

/**
 * Subscribes to mount-lifecycle changes for `surfaceId`. Returns the registered
 * ref, or `null` if no `A2UISurfaceMount` exists for the id yet.
 */
export function useSurfaceMount(
  surfaceId: string,
): RefObject<HTMLDivElement | null> | null {
  const { store } = useSurfaceRegistryContext();

  const subscribe = useCallback(
    (listener: () => void) => store.subscribeMount(surfaceId, listener),
    [store, surfaceId],
  );
  useSyncExternalStore(
    subscribe,
    () => store.getMountVersion(surfaceId),
    () => store.getMountVersion(surfaceId),
  );
  return store.getMount(surfaceId);
}

/**
 * Clears all `session-scoped` surfaces (workspace, sidebar by default)
 * whenever `sessionId` changes.
 */
export function useClearSurfacesOnSessionChange(
  sessionId: string | null,
): void {
  const registry = useSurfaceRegistry();
  const previousRef = useRef<string | null>(null);
  useEffect(() => {
    if (sessionId === previousRef.current) return;
    if (previousRef.current !== null) {
      // A new conversation wipes ALL of the old session's transient surfaces —
      // both `session-scoped` (workspace/sidebar) AND `turn-scoped`, which is the
      // fallback policy for backend-pushed chat surfaces like the elicitation /
      // handoff-confirm cards. Clearing only session-scoped left those cards
      // leaking into the fresh conversation (2026-07-14 report). Only explicit
      // `indefinite` surfaces (admin opt-in) survive a session change.
      registry.clearByPersistence("turn-scoped");
      registry.clearByPersistence("session-scoped");
    }
    previousRef.current = sessionId;
  }, [sessionId, registry]);
}

/**
 * Subscribes to state changes for `surfaceId`. Only re-renders the calling
 * component when THIS surface's state changes.
 */
export function useSurfaceState(surfaceId: string): SurfaceState | null {
  const { store } = useSurfaceRegistryContext();
  const subscribe = useCallback(
    (listener: () => void) => store.subscribeState(surfaceId, listener),
    [store, surfaceId],
  );
  useSyncExternalStore(
    subscribe,
    () => store.getStateVersion(surfaceId),
    () => store.getStateVersion(surfaceId),
  );
  return store.getState(surfaceId);
}

/**
 * Reactive list of active artifact surfaces (7.5) — re-renders whenever an
 * artifact arrives or clears. Drives the workbench's dynamic per-result tabs +
 * the Workspace index. Memoized on the version so the array identity is stable
 * between changes (safe as a dependency / for `.map` in render).
 */
export function useArtifacts(): A2uiArtifactEntry[] {
  const { store } = useSurfaceRegistryContext();
  const subscribe = useCallback(
    (listener: () => void) => store.subscribeArtifacts(listener),
    [store],
  );
  const version = useSyncExternalStore(
    subscribe,
    () => store.getArtifactsVersion(),
    () => store.getArtifactsVersion(),
  );
  // Re-derive only when the version changes (not every render). `version` isn't
  // read inside — it's the invalidation key (listArtifacts() returns a fresh
  // array each call, so memoizing on version keeps the reference stable).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => store.listArtifacts(), [store, version]);
}
