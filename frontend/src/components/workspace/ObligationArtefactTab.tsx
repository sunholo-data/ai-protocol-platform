// ObligationArtefactTab — mounts the M1 Obligation Analysis MCP-App artefact
// for a `map_ppa_obligations` success surface (artifact kind
// "obligation-analysis"), booting it with the REAL extracted scenario instead
// of the static DemoSolar demo asset (PPA-OBLIGATION 7.6 M3).
//
// How the payload reaches the artefact:
//   1. The map_ppa_obligations result → A2UI transform emits the full wire
//      payload as the surface's data model (`updateDataModel value.payload`).
//   2. This component reads it via `useSurfaceState(surfaceId)` and, once the
//      artefact finishes its `ui/initialize` handshake (StaticArtefactFrame's
//      `onInitialized`), forwards it as a `ui/obligation/payload` notification.
//   3. The artefact uses the injected payload (dropping its demo placeholder
//      banner); absent an injection it falls back to the static demo asset.
//
// Scenario save/restore (7.5 artefact state):
//   - The artefact emits `obligation.recompute { scenario }` on every what-if
//     change; we stash the scenario keyed by surfaceId so it survives a
//     workbench tab switch / re-mount (the inactive tab unmounts), and mirror
//     it into the surface data model so it's part of the 7.5 artefact-state
//     slot (readable by the agent; rehydratable when the backend stash carries
//     it). On the next mount we inject it back as `savedScenario`.
//   - "Reset to extracted" in the artefact emits `obligation.reset`; we clear
//     the stash so the next mount boots the canonical extracted payload.
//
// Reviewed settings (design open question 2):
//   - The artefact's "Reviewed settings" panel lets a human override policy
//     knobs / obligation prices and flip their provenance to "reviewed". That
//     overlay rides the SAME `obligation.recompute` host-bridge context (a
//     `reviewed` field alongside `scenario`) and is persisted as a second
//     data-model dimension (`rootData.reviewed`). On mount it is forwarded back
//     as `reviewedSettings` in `ui/obligation/payload` so the review survives a
//     hard refresh. It is INDEPENDENT of the what-if scenario: a what-if reset
//     keeps the review; the panel's own "reset to extracted/default" clears it.
//
// Hard-refresh persistence (stash-update hook):
//   - When a `sessionId` is provided, every change is ALSO persisted to the
//     backend via `POST /api/sessions/{id}/surface-data` (debounced — what-if
//     drags emit a recompute per tick). The backend merges the full data-model
//     root ({payload, scenario?, reviewed?}) into the 7.5 rehydration stash as
//     a `clientDataModel` block; the session-history GET replays it as a
//     trailing updateDataModel message, so after a hard refresh `rootData`
//     below carries the edits and the artefact boots with them. A what-if reset
//     re-posts the root without the scenario slot (canonical what-if
//     rehydrates) while preserving any review. Best-effort: a failed POST only
//     costs cross-refresh persistence, never the live session.

"use client";

import { useCallback, useEffect, useRef } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import {
  StaticArtefactFrame,
  type StaticArtefactFrameHandle,
} from "@/components/workspace/StaticArtefactFrame";
import {
  useSurfaceRegistry,
  useSurfaceState,
  type A2uiV09Message,
} from "@/providers/SurfaceRegistry";

/** Path segment after `/artefacts/` — matches the deployed artefact dir. */
const ARTEFACT_PATH = "ppa-obligation-analysis/v1";

/** Engine-placement fallback flag (7.6 M3, design axiom 5 GRACEFUL DEGRADATION).
 *  When `NEXT_PUBLIC_OBLIGATION_ENGINE_URL` is set, the artefact runs the
 *  IDENTICAL `sunholo/deontic/api.ail` boundary against an `ailang serve-api`
 *  sidecar at that URL instead of the in-browser WASM engine — a pure config
 *  change (no artefact rebuild). Empty/unset → the default in-browser WASM
 *  placement (client-side what-if, no egress). Read at call time (not module
 *  load) so a test / server-side render can vary it. The sidecar itself is not
 *  part of this milestone; this is the flag + wire boundary. */
function serveApiEngineUrl(): string {
  return (process.env.NEXT_PUBLIC_OBLIGATION_ENGINE_URL || "").trim();
}

/** Origin of the mcp-sandbox proxy (StaticArtefactFrame wants origin, not the
 *  full sandbox.html URL). Defaults to the local dev port. */
const SANDBOX_ORIGIN = (() => {
  const url = process.env.NEXT_PUBLIC_MCP_SANDBOX_URL || "http://localhost:3457/sandbox.html";
  try {
    return new URL(url).origin;
  } catch {
    return "http://localhost:3457";
  }
})();

/** Within-session scenario store, keyed by artifact surfaceId. Survives a
 *  workbench tab switch / re-mount (the inactive tab unmounts). Module-level so
 *  it outlives the component instance but is cleared on a full page reload. */
const scenarioStore = new Map<string, unknown>();

/** Within-session REVIEWED-SETTINGS store (design open question 2), keyed by
 *  surfaceId. The reviewer's chosen policy-knob / obligation-price overrides —
 *  an independent persistence dimension from the what-if scenario: a what-if
 *  reset never clears a review, and vice versa. */
const reviewedStore = new Map<string, unknown>();

function isNonEmptyOverlay(r: unknown): boolean {
  return !!r && typeof r === "object" && Object.keys(r as Record<string, unknown>).length > 0;
}

/** Test hook — reset the module-level scenario + reviewed stores between cases. */
export function __resetObligationScenarioStore(): void {
  scenarioStore.clear();
  reviewedStore.clear();
}

let _writeSeq = 0;

/** Debounce for the backend stash-update POST — an FM drag emits a recompute
 *  per tick; one write per pause is plenty for cross-refresh persistence. */
const SURFACE_DATA_DEBOUNCE_MS = 750;

export interface ObligationArtefactTabProps {
  /** The `obligation_analysis:{doc_id}` artifact surface id. */
  surfaceId: string;
  className?: string;
  /** Host theme forwarded to the artefact's init handshake. */
  hostTheme?: "light" | "dark";
  /** Chat session backing this workbench — enables the backend stash-update
   *  POST so scenario edits survive a hard refresh. Absent → in-session
   *  persistence only (tab switches), same as before the hook existed. */
  sessionId?: string | null;
}

export function ObligationArtefactTab({
  surfaceId,
  className,
  hostTheme,
  sessionId,
}: ObligationArtefactTabProps) {
  const state = useSurfaceState(surfaceId);
  const registry = useSurfaceRegistry();
  const frameRef = useRef<StaticArtefactFrameHandle | null>(null);
  const initializedRef = useRef(false);
  const lastSentPayloadRef = useRef<unknown>(null);

  // The transform injected the wire payload as the surface data model root.
  const rootData = (state?.surface?.dataModel?.get("/") ?? null) as
    | { payload?: unknown; scenario?: unknown; reviewed?: unknown }
    | null;
  const payload = rootData?.payload ?? null;
  const rehydratedScenario = rootData?.scenario ?? null;
  const rehydratedReviewed = rootData?.reviewed ?? null;

  const sendPayload = useCallback(() => {
    if (!frameRef.current || !payload) return;
    const savedScenario = scenarioStore.get(surfaceId) ?? rehydratedScenario ?? null;
    const savedReviewed = reviewedStore.get(surfaceId) ?? rehydratedReviewed ?? null;
    const engineUrl = serveApiEngineUrl();
    frameRef.current.sendNotification("ui/obligation/payload", {
      payload,
      ...(savedScenario ? { savedScenario } : {}),
      // Rehydrate a prior human review (design open question 2) so the reviewed
      // knobs/prices + their "reviewed" provenance survive a hard refresh.
      ...(isNonEmptyOverlay(savedReviewed) ? { reviewedSettings: savedReviewed } : {}),
      // Engine placement (design axiom 5): forward the serve-api URL when the
      // fallback flag is set; absent → the artefact uses in-browser WASM.
      ...(engineUrl ? { engine: { mode: "serve-api", url: engineUrl } } : {}),
    });
    lastSentPayloadRef.current = payload;
  }, [payload, rehydratedScenario, rehydratedReviewed, surfaceId]);

  // Push the payload ONLY after the artefact's init handshake completes (an
  // earlier postMessage would race the proxy/artefact bring-up and be dropped).
  // This effect only resends when a NEW payload arrives on the same, already-
  // initialised surface (e.g. a re-analysis of the same document).
  useEffect(() => {
    if (initializedRef.current && payload && payload !== lastSentPayloadRef.current) {
      sendPayload();
    }
  }, [payload, sendPayload]);

  // ── Backend stash-update hook (hard-refresh persistence) ──────────────────
  // Debounced POST of the full data-model root to /surface-data. The pending
  // body lives in a ref so an unmount (tab switch) can flush the last edit
  // instead of dropping it.
  const pendingBodyRef = useRef<string | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushSurfaceData = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const body = pendingBodyRef.current;
    if (!body || !sessionId) return;
    pendingBodyRef.current = null;
    fetchWithAuth(`/api/proxy/api/sessions/${encodeURIComponent(sessionId)}/surface-data`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    })
      .then((res) => {
        if (!res.ok && process.env.NODE_ENV !== "production") {
          console.warn(
            `[ObligationArtefactTab] surface-data POST returned ${res.status} for surface "${surfaceId}"`,
          );
        }
      })
      .catch((err) => {
        if (process.env.NODE_ENV !== "production") {
          console.warn(`[ObligationArtefactTab] surface-data POST failed for surface "${surfaceId}":`, err);
        }
      });
  }, [sessionId, surfaceId]);

  const queueSurfaceData = useCallback(
    (dataModel: unknown) => {
      if (!sessionId) return;
      pendingBodyRef.current = JSON.stringify({ surfaceId, dataModel });
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
      flushTimerRef.current = setTimeout(flushSurfaceData, SURFACE_DATA_DEBOUNCE_MS);
    },
    [sessionId, surfaceId, flushSurfaceData],
  );

  // Flush the last pending edit on unmount (tab switch / navigation) so it
  // isn't lost to the debounce window.
  useEffect(() => flushSurfaceData, [flushSurfaceData]);

  // Last reviewed overlay the artefact reported — so a what-if reset (which
  // carries no reviewed field) can preserve the review when it re-persists.
  const lastReviewedRef = useRef<unknown>(null);
  // Last `view` (the AI-legible "what's on screen" summary the artefact emits)
  // — preserved across the reset event, which carries no view, so the agent's
  // copy of the on-screen result isn't briefly dropped on a reset.
  const lastViewRef = useRef<unknown>(null);

  // Persist BOTH artefact-state dimensions — the what-if scenario and the
  // reviewed-settings overlay — as one data-model root. `scenario === null`
  // drops the scenario slot (what-if reset); an empty reviewed overlay is
  // omitted (an empty overlay is the identity, so rehydration boots the base).
  const persistState = useCallback(
    (scenario: unknown, reviewed: unknown, view: unknown) => {
      if (scenario == null) scenarioStore.delete(surfaceId);
      else scenarioStore.set(surfaceId, scenario);
      reviewedStore.set(surfaceId, reviewed);

      const root: Record<string, unknown> = { payload };
      if (scenario != null) root.scenario = scenario;
      if (isNonEmptyOverlay(reviewed)) root.reviewed = reviewed;

      // Persist the INPUTS to the backend stash so the edit survives a hard
      // refresh. `view` is a derived summary (recomputed on boot) — kept out of
      // the stash, but mirrored to the agent below.
      queueSurfaceData(root);
      // Mirror into the surface data model (7.5 artefact-state slot), ALSO
      // carrying `view` — the computed settlement the artefact is currently
      // SHOWING. readA2uiSurfaceState snapshots this every turn into
      // a2ui_surface_state, so the agent sees the on-screen result and can
      // advise on it (7.9). Best-effort — a failure only costs the
      // agent-visible/rehydration copy, not the in-session store.
      const mirrorRoot = view ? { ...root, view } : root;
      try {
        registry.appendMessages(
          surfaceId,
          [{ version: "v0.9", updateDataModel: { surfaceId, value: mirrorRoot } } as A2uiV09Message],
          `obligation-state:${surfaceId}:${(_writeSeq += 1)}`,
        );
      } catch {
        /* best-effort */
      }
    },
    [payload, registry, surfaceId, queueSurfaceData],
  );

  const handleUpdateModelContext = useCallback(
    (structuredContent: Record<string, unknown>) => {
      const kind = structuredContent.kind;
      if (kind === "obligation.recompute") {
        // The artefact reports both dimensions on every recompute (a reviewed
        // edit or a what-if drag both land here).
        const reviewed =
          "reviewed" in structuredContent && structuredContent.reviewed ? structuredContent.reviewed : {};
        lastReviewedRef.current = reviewed;
        const scenario = "scenario" in structuredContent ? structuredContent.scenario : null;
        // `view` is the AI-legible on-screen summary (net result, per-obligation
        // penalties, active policy). Forward it to the agent-visible mirror.
        const view = "view" in structuredContent ? structuredContent.view : null;
        lastViewRef.current = view;
        persistState(scenario, reviewed, view);
      } else if (kind === "obligation.reset") {
        // What-if reset: drop the scenario but KEEP the reviewed overlay and the
        // last on-screen view (a recompute fired just before this with the fresh
        // baseline view, so lastViewRef already holds the post-reset result).
        persistState(null, lastReviewedRef.current, lastViewRef.current);
      }
    },
    [persistState],
  );

  const handleInitialized = useCallback(() => {
    initializedRef.current = true;
    sendPayload();
  }, [sendPayload]);

  if (!payload) {
    return (
      <div
        data-testid="obligation-artefact-preparing"
        className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground"
      >
        Preparing the obligation analysis…
      </div>
    );
  }

  return (
    <StaticArtefactFrame
      ref={frameRef}
      sandboxOrigin={SANDBOX_ORIGIN}
      artefactPath={ARTEFACT_PATH}
      hostContext={hostTheme ? { theme: hostTheme } : undefined}
      onInitialized={handleInitialized}
      onUpdateModelContext={handleUpdateModelContext}
      className={className}
      title="Obligation Analysis"
    />
  );
}
