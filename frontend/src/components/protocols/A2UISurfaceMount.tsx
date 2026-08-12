// MULTI-SURFACE-A2UI — A2UISurfaceMount (v0.9 native)
//
// Renders a named A2UI surface using the SurfaceModel that the
// SurfaceRegistry keeps for `surfaceId`. The registry's per-surface
// MessageProcessor owns the model lifecycle; this component is a thin
// render of `<A2uiSurface>` plus mount registration so the dispatcher
// knows the surface exists in the DOM.
//
// Sprint 2.10 (sibling of MCP Apps' ui/update-model-context):
// subscribes to `surface.onAction` and POSTs the A2uiClientAction to
// `/api/sessions/{id}/surface-action`. The backend writes the action
// into ADK session state under
// `a2ui_surface_context.{surfaceId}.lastAction`, where the
// InstructionProvider reads it on the next agent turn. The action
// loop is OPTIONAL per skill — backend gates require
// `tool_configs.a2ui.allow_surface_context_writes: true`; without it
// the POST returns 403 and we drop silently (logged in dev).
//
// ACTION-TRIGGER M2 (sprint 1.21): opt-in `triggerOnAction` prop swaps
// the fire-and-forget POST above for the bundled write+run endpoint
// (`useActionDrivenAgent`), which both persists the action AND runs an
// agent turn that can emit a new A2UI surface in response. Default
// `false` — existing skills (and chat-driven A2UI) keep their current
// behaviour exactly.
//
// useLayoutEffect (not useEffect) for registration — completes before
// paint, so a dispatch arriving in the same tick the mount layouts
// already finds the surface in the registry.

"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { A2uiSurface } from "@a2ui/react/v0_9";
import { fetchWithAuth } from "@/lib/apiClient";
import { useActionDrivenAgent } from "@/hooks/useActionDrivenAgent";
import { emitSkillSwitchIntent } from "@/lib/skillSwitch";
import {
  type SurfacePolicy,
  useSurfaceRegistry,
  useSurfaceState,
} from "@/providers/SurfaceRegistry";

export interface A2UISurfaceMountProps {
  surfaceId: string;
  /** Override individual policy fields; merged onto the default for `surfaceId`. */
  policy?: Partial<SurfacePolicy>;
  /** Tailwind / layout classes for the mount's outer div. */
  className?: string;
  /**
   * Current chat session id. Required for the sprint-2.10 action POST
   * (the endpoint URL embeds it). When `null` (no session yet — fresh
   * chat before first send), action dispatch is skipped — there's
   * nowhere to write the namespaced state. The frontend's snapshot
   * push path still works through `forwardedProps`.
   */
  sessionId?: string | null;
  /**
   * Skill id — required when `triggerOnAction` is `true`, because the
   * action-triggered-run endpoint is scoped to the skill. Ignored when
   * `triggerOnAction` is false (default), so the existing chat-driven
   * surface mounts in `<ChatShell>` don't need to thread it.
   */
  skillId?: string;
  /**
   * ACTION-TRIGGER M2 (sprint 1.21). Default `false` preserves the
   * current fire-and-forget `surface-action` POST behaviour. When
   * `true`, the click drives a full agent turn via
   * `useActionDrivenAgent` instead — the action is persisted server-side
   * (same `EventActions(state_delta)` write) AND the agent runs and
   * streams AG-UI events that can update the rendered surface. The
   * caller is expected to have set `tool_configs.a2ui.allow_action_triggered_runs:
   * true` on the skill (the backend returns 403 otherwise and the
   * surface stays in its last-rendered state).
   */
  triggerOnAction?: boolean;
  /**
   * Handler for `chat:send` surface actions — a surface Button can ask the app
   * to post a chat message (e.g. a diff card's "Explain this difference", whose
   * action carries a ready-built prompt in `context.prompt`). Routed here rather
   * than through `surface-action[-run]` because the intent is a chat turn whose
   * text reply belongs in the chat thread, not a surface re-render. When absent,
   * `chat:send` actions are dropped (logged in dev).
   */
  onChatMessage?: (text: string) => void;
}

export function A2UISurfaceMount({
  surfaceId,
  policy,
  className,
  sessionId,
  skillId,
  triggerOnAction = false,
  onChatMessage,
}: A2UISurfaceMountProps) {
  const ref = useRef<HTMLDivElement>(null);
  const registry = useSurfaceRegistry();
  const state = useSurfaceState(surfaceId);

  useLayoutEffect(() => {
    registry.register(surfaceId, ref, policy);
    return () => {
      registry.unregister(surfaceId);
    };
  }, [surfaceId, policy, registry]);

  // ACTION-TRIGGER M2: useActionDrivenAgent is always instantiated (hooks
  // must run unconditionally); we just gate which dispatch path the
  // action subscription uses. The hook itself is cheap — it just
  // captures sessionId/skillId/registry refs into a callback. When
  // `triggerOnAction` is false (default), the callback is never invoked.
  // `skillId` may be empty for chat-driven mounts; the action-triggered
  // branch is also gated on `skillId.length > 0` so accidental
  // misconfigurations never POST to a malformed URL.
  const { triggerAction } = useActionDrivenAgent({
    skillId: skillId ?? "",
    sessionId: sessionId ?? "",
  });

  // Click-spam guard. An action-triggered run is a FULL agent turn (LLM
  // call(s) + a re-emitted surface). Without a guard, rapid clicks fire N
  // concurrent surface-action-run POSTs that race N surface updates and
  // multiply rate-limit pressure. We drop clicks while a run is in flight.
  //
  // The guard is a REF so it flips synchronously inside the action callback
  // (state updates are async — a same-tick double-click could slip past a
  // state-only check). But a silent drop reads as a dead button: with no
  // visual change, the user assumes the first click missed and clicks again.
  // So we mirror the ref into `isRunning` state purely for rendering — it
  // drives the "Working…" overlay below that dims the surface and blocks
  // pointer events, making the busy state obvious. Ref = correctness (the
  // real guard), state = feedback (the visible cue). Both flip together.
  const actionInFlightRef = useRef(false);
  const [isRunning, setIsRunning] = useState(false);
  // NEVER SILENT (#8): a failed action-triggered run (RUN_ERROR / gate reject /
  // network) MUST show the user something, not just console.warn. This is the
  // visible outcome of a submit (e.g. the obligation elicitation "Run the
  // analysis" button) — never a dead grey surface.
  const [actionError, setActionError] = useState<string | null>(null);
  // Terminal SUCCESS state (#8 never-silent): after a run completes, a one-shot
  // (`triggerOnAction`) surface must NOT silently revert to a live re-clickable
  // button. A confirm/handoff card whose result renders elsewhere (Workbench)
  // otherwise reads as "nothing happened" and gets clicked again (the 2026-07-14
  // obligation-handoff report). Freeze it + show a persistent "Sent" badge.
  const [hasCompleted, setHasCompleted] = useState(false);

  // Subscribe to surface actions and route each one through the
  // configured dispatch path. Re-subscribes whenever the SurfaceModel
  // identity changes (clearSurface → new createSurface).
  useEffect(() => {
    if (!state?.surface) return;
    if (!sessionId) return;
    const sub = state.surface.onAction.subscribe(async (action) => {
      // chat:send — a surface Button asks to post a chat message (e.g. a diff
      // card's "Explain this difference", whose action carries a ready-built
      // prompt in `context.prompt`). Route to the chat composer so the agent's
      // TEXT reply lands in the chat thread. surface-action-run is wrong for
      // this: its output only re-renders the surface, never chat text.
      if (String(action.name) === "chat:send") {
        const prompt = (action.context as { prompt?: unknown } | undefined)?.prompt;
        if (typeof prompt === "string" && prompt.trim() && onChatMessage) {
          onChatMessage(prompt.trim());
        } else if (process.env.NODE_ENV !== "production") {
          console.warn(
            `[A2UISurfaceMount] chat:send dropped on "${surfaceId}" — no onChatMessage handler or empty prompt`,
          );
        }
        return;
      }
      // confirm_delegation — the user Proceeded on a handoff card (8.2). This is
      // a full SWITCH, not a surface-action-run: hand the intent to ChatShell,
      // which captures the outstanding request + documents and navigates to the
      // specialist on the same thread (context carries). NEVER-SILENT: show an
      // immediate working state; the navigation itself is the terminal feedback.
      if (String(action.name) === "confirm_delegation") {
        const ctx = action.context as { target_skill_id?: unknown } | undefined;
        const target = ctx && typeof ctx.target_skill_id === "string" ? ctx.target_skill_id : "";
        if (target) {
          setActionError(null);
          setIsRunning(true);
          emitSkillSwitchIntent({ targetSkillId: target });
          return;
        }
        // No target id to switch to — fall through to the run path (legacy
        // surface-action-run handling) rather than silently dropping the click.
      }
      // Per-action routing (tool-results-as-a2ui / 7.3 M3): an action whose
      // name uses the `run:` convention drives a full agent turn
      // (surface-action-run) even when the mount's default is fire-and-forget.
      // This lets a single surface mix client actions (fire-and-forget, e.g. a
      // filter) with agent-run actions (e.g. a diff row's "Explain this
      // difference") without the mount being all-or-nothing. `triggerOnAction`
      // stays as a mount-level override that forces every action to the run
      // path (existing behaviour).
      const wantsRun = triggerOnAction || String(action.name).startsWith("run:");
      if (wantsRun) {
        // ACTION-TRIGGER M2: skill id is required for the bundled
        // write+run endpoint URL. Drop silently in dev when missing —
        // the design-doc fork that opts in must thread skillId.
        if (!skillId) {
          // NEVER SILENT (#8): a run-path action with no skillId can't POST —
          // show the user why instead of a dead button that greys and resolves.
          setActionError(
            "This form isn't fully wired (missing skill context). Reload the page and try again.",
          );
          if (process.env.NODE_ENV !== "production") {
            console.warn(
              `[A2UISurfaceMount] triggerOnAction=true but skillId is missing for surface "${surfaceId}"; skipping`,
            );
          }
          return;
        }
        // Drop the click if a run is already in flight (see actionInFlightRef).
        if (actionInFlightRef.current) {
          if (process.env.NODE_ENV !== "production") {
            console.info(
              `[A2UISurfaceMount] action ignored — a run is already in flight for surface "${surfaceId}"`,
            );
          }
          return;
        }
        actionInFlightRef.current = true;
        setActionError(null);
        setHasCompleted(false);
        setIsRunning(true);
        try {
          await triggerAction(surfaceId, {
            name: action.name,
            sourceComponentId: action.sourceComponentId,
            timestamp: action.timestamp,
            context: action.context,
          });
          // Reached only if the run did NOT reject — mark a visible terminal
          // state so the click has an unmistakable, persistent consequence.
          setHasCompleted(true);
        } catch (err) {
          // NEVER SILENT (#8): triggerAction rejects on RUN_ERROR (and its
          // gate-reject / network fallbacks resolve — those surface separately).
          // Show the failure to the USER, not just console.warn.
          const message =
            err instanceof Error && err.message
              ? err.message
              : "The run failed. Check the Activity tab, or try again.";
          setActionError(message);
          if (process.env.NODE_ENV !== "production") {
            console.warn(
              `[A2UISurfaceMount] action-triggered run failed for surface "${surfaceId}":`,
              err,
            );
          }
        } finally {
          actionInFlightRef.current = false;
          setIsRunning(false);
        }
        return;
      }

      // Default (current behaviour): fire-and-forget POST to the plain
      // surface-action endpoint. Backend persists the action under
      // `a2ui_surface_context.{surfaceId}.lastAction`; the agent reads
      // it on the next chat turn.
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}/surface-action`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              surfaceId,
              action: {
                name: action.name,
                sourceComponentId: action.sourceComponentId,
                timestamp: action.timestamp,
                context: action.context,
              },
            }),
          },
        );
        if (!res.ok && process.env.NODE_ENV !== "production") {
          // 403 is the expected response when the skill hasn't opted in;
          // we log but don't surface to the user.
          const detail = await res.text().catch(() => "");
          console.info(
            `[A2UISurfaceMount] surface-action POST returned ${res.status} for surface "${surfaceId}"`,
            detail,
          );
        }
      } catch (err) {
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            `[A2UISurfaceMount] surface-action POST failed for surface "${surfaceId}":`,
            err,
          );
        }
      }
    });
    return () => sub.unsubscribe();
  }, [state?.surface, surfaceId, sessionId, triggerOnAction, triggerAction, skillId, onChatMessage]);

  return (
    <div ref={ref} className={className} data-surface={surfaceId}>
      {state?.surface && (
        <div className="relative">
          {/* Dim + disable the surface while an action-triggered run is in
              flight. `pointer-events-none` is belt-and-braces on top of the
              ref guard: it stops a stray click reaching the button DOM at all
              (the SDK renders the Button; we can't reach into it to disable
              it, so we gate interaction at the wrapper). */}
          <div
            aria-busy={isRunning}
            className={
              isRunning || (hasCompleted && triggerOnAction)
                ? "pointer-events-none opacity-60 transition-opacity"
                : "transition-opacity"
            }
          >
            <A2uiSurface surface={state.surface} />
          </div>
          {isRunning && (
            <div
              className="pointer-events-none absolute inset-0 flex items-center justify-center"
              role="status"
              aria-live="polite"
              data-testid="a2ui-surface-running"
            >
              <span className="inline-flex items-center gap-2 rounded-full border bg-background/90 px-3 py-1 text-xs font-medium text-muted-foreground shadow-sm backdrop-blur-sm">
                <svg
                  className="h-3 w-3 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                  aria-hidden="true"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                Working…
              </span>
            </div>
          )}
          {actionError && (
            <div
              role="alert"
              data-testid="a2ui-surface-error"
              className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
            >
              <span aria-hidden="true">⚠</span>
              <span>{actionError}</span>
            </div>
          )}
          {hasCompleted && !isRunning && !actionError && (
            <div
              role="status"
              data-testid="a2ui-surface-done"
              className="mt-2 flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400"
            >
              <span aria-hidden="true">✓</span>
              <span>Sent — the response appears below, and in the Workspace and Activity.</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
