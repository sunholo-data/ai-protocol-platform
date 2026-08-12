// ACTION-TRIGGER M2 — useActionDrivenAgent
//
// Pattern 1 closing rung. The user clicks an A2UI button → this hook POSTs to
// `/api/skills/{skillId}/sessions/{sessionId}/surface-action-run` with the
// A2uiClientAction body + a snapshot of the live A2UI surface state, then
// consumes the AG-UI SSE response and dispatches the resulting A2UI tool
// calls into the same `SurfaceRegistry` the chat path uses. Result: the
// surface re-renders exactly like it would after a chat turn — but no chat
// message was sent.
//
// Why the hook parses SSE itself instead of routing through `HttpAgent`:
// the @ag-ui/client `HttpAgent` is wired to ONE URL (the chat stream
// endpoint) and updates ONE agent's `messages`/`state` arrays. The
// action-triggered endpoint is a different URL and we explicitly do NOT
// want to write a fake chat message into the agent's `messages` array
// (that would render a stray bubble in the chat). The only thing the
// SurfaceRegistry actually needs is the `send_a2ui_json_to_client` tool
// call's `TOOL_CALL_RESULT.content`, which we parse here and feed to
// `registry.appendMessages` — the same call the chat-bubble
// `A2UISurfaceDispatcher` makes. Single point of dispatch into the
// registry; this hook just feeds it through a different ingress.
//
// Graceful HTTP 4xx fallback: when the backend gate rejects (skill not
// opted into `allow_action_triggered_runs`, missing JWT, etc.), the
// promise resolves cleanly with a `console.warn`. The surface stays in
// its last-rendered state — no broken loading spinner, no thrown error
// bubbling into React's error boundary.

"use client";

import { useCallback } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { useSurfaceRegistry } from "@/providers/SurfaceRegistry";
import type { ToolCallState, DelegationMarkerItem } from "@/hooks/useSkillAgent";

/** A2uiClientAction shape — mirrors the backend request schema. */
export interface ActionDrivenAgentAction {
  name: string;
  sourceComponentId?: string;
  timestamp?: string;
  context?: Record<string, unknown>;
}

/**
 * ACTIVITY-OBS — sink for surfacing an action-triggered run's LIVE progress.
 *
 * The action-run path parses its OWN SSE stream (see the module docstring) and,
 * unlike the chat path, has no `useSkillAgent` subscription feeding the Activity
 * panel. Without this sink the launcher button greys for several seconds with
 * ZERO live feedback and the Activity tab stays empty — the user can't tell the
 * run is happening or debug when it isn't (Mark: "we should make it obligatory
 * that we see events come through").
 *
 * ChatShell owns the Activity panel, so it constructs a stable sink and threads
 * it down to the launcher's `useActionDrivenAgent`. Every callback is optional
 * so the hook stays backward-compatible (A2UISurfaceMount, the dev page, and any
 * caller that doesn't wire a sink behave exactly as before). Tool-call ids match
 * the ADK function_call ids the backend later persists, so ChatShell can merge
 * these live entries with the `/activity` re-fetch by id (live wins).
 */
export interface ActionRunActivitySink {
  /** Upsert (by id) a tool call as it transitions running → success/error. */
  upsertToolCall?: (toolCall: ToolCallState) => void;
  /** Upsert a skill→skill delegation surfaced mid-run (AGENT_DELEGATION). */
  upsertDelegation?: (delegation: DelegationMarkerItem) => void;
  /** The run's stream has opened — mark it in-flight (live/running badge). */
  onRunStart?: () => void;
  /**
   * The run reached a terminal state (RUN_FINISHED / RUN_ERROR) or its stream
   * ended. Clears the in-flight indicator and — this is the point — signals
   * ChatShell to re-fetch GET /activity so persisted history syncs. `error` is
   * set only on RUN_ERROR (or a translated stream failure).
   */
  onRunSettled?: (outcome: { error?: string }) => void;
  /** Server-authored STAGE_PROGRESS label, if the action path emits one. */
  onStage?: (label: string) => void;
  /** Resolved model for this action turn (MODEL_RESOLVED) — the delegate is
   * often on a different model than the front door; drives the Activity header. */
  onModel?: (model: string) => void;
}

export interface UseActionDrivenAgentArgs {
  /** Skill id — used in the endpoint URL. */
  skillId: string;
  /** Session id — also used in the endpoint URL. */
  sessionId: string;
  /**
   * Optional Activity-panel sink (ACTIVITY-OBS). When provided, the hook
   * forwards the run's live tool calls / delegations / lifecycle so the
   * Activity tab shows progress exactly like a normal chat turn.
   */
  activitySink?: ActionRunActivitySink;
}

export interface UseActionDrivenAgentReturn {
  /**
   * POST the action to `surface-action-run`, consume the SSE stream, and
   * dispatch A2UI updates into the SurfaceRegistry. Resolves on
   * `RUN_FINISHED`; rejects on `RUN_ERROR`. Resolves cleanly (no throw)
   * on HTTP 4xx — the skill is not opted in and the surface stays put.
   */
  triggerAction: (
    surfaceId: string,
    action: ActionDrivenAgentAction,
  ) => Promise<void>;
}

const A2UI_TOOL_NAME = "send_a2ui_json_to_client";

interface PendingToolCall {
  name: string;
  args: string;
}

interface ParsedA2uiToolResult {
  surfaceId: string;
  messages: Record<string, unknown>[];
}

/**
 * Parse the `send_a2ui_json_to_client` tool result envelope. Matches the
 * shape produced by `backend/adk/a2ui.py::SurfaceAwareA2uiToolset` (and
 * what `MessageBubble.parseA2UIResult` expects).
 */
function parseA2uiToolResult(
  content: string,
  fallbackSurfaceId: string,
): ParsedA2uiToolResult | null {
  try {
    const parsed = JSON.parse(content) as Record<string, unknown>;
    const raw = parsed.validated_a2ui_json;
    if (raw === undefined || raw === null) return null;
    const messages = Array.isArray(raw)
      ? (raw as Record<string, unknown>[])
      : [raw as Record<string, unknown>];
    if (messages.length === 0) return null;
    const surfaceId =
      typeof parsed.surface_id === "string" && parsed.surface_id.length > 0
        ? parsed.surface_id
        : fallbackSurfaceId;
    return { surfaceId, messages };
  } catch {
    return null;
  }
}

/**
 * Consume an SSE stream from `body` and yield each parsed `data:` JSON
 * payload. Stops on stream end. Splits on `\n\n` per the SSE spec; only
 * `data:` lines are emitted (comments / event-name lines are ignored —
 * the backend's `stream_agui_events` doesn't emit them but be defensive).
 */
async function* readSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<Record<string, unknown>> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sepIdx = buffer.indexOf("\n\n");
      while (sepIdx !== -1) {
        const frame = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trimStart());
          }
        }
        if (dataLines.length > 0) {
          const payload = dataLines.join("\n");
          try {
            yield JSON.parse(payload) as Record<string, unknown>;
          } catch {
            // Malformed frame — backend should never emit one, but if it
            // does, dropping it is safer than killing the stream.
          }
        }
        sepIdx = buffer.indexOf("\n\n");
      }
    }
    // Flush a final frame that wasn't followed by a separator. The
    // backend's `stream_agui_events` always closes with `\n\n` after the
    // terminal event but tolerate the variant.
    const tail = buffer.trim();
    if (tail.length > 0) {
      for (const line of tail.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trimStart();
        try {
          yield JSON.parse(payload) as Record<string, unknown>;
        } catch {
          // ignore
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function useActionDrivenAgent({
  skillId,
  sessionId,
  activitySink,
}: UseActionDrivenAgentArgs): UseActionDrivenAgentReturn {
  const registry = useSurfaceRegistry();

  const triggerAction = useCallback(
    async (
      surfaceId: string,
      action: ActionDrivenAgentAction,
    ): Promise<void> => {
      const url = `/api/proxy/api/skills/${encodeURIComponent(
        skillId,
      )}/sessions/${encodeURIComponent(sessionId)}/surface-action-run`;

      const surfaceSnapshot = registry.readA2uiSurfaceState();
      const body = {
        surfaceId,
        action,
        forwardedProps: { a2ui_surface_state: surfaceSnapshot },
      };

      if (process.env.NODE_ENV !== "production") {
        // Submit self-diagnosis (7.8): the elicitation "Run" button's #1 failure
        // mode was the filled field values not reaching the tool. Log exactly
        // what the snapshot carries for the TRIGGERING surface, so a single real
        // click shows the truth in the console — no server round-trip, no guessing.
        const entry = surfaceSnapshot[surfaceId] as
          | { dataModel?: Record<string, unknown> }
          | undefined;
        console.info(
          `[useActionDrivenAgent] surface-action-run "${action.name}" → "${surfaceId}"`,
          {
            surfacesInSnapshot: Object.keys(surfaceSnapshot),
            triggeringDataModel: entry?.dataModel ?? "(surface NOT in snapshot!)",
          },
        );
      }

      const doPost = () =>
        fetchWithAuth(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

      let res: Response;
      try {
        res = await doPost();
      } catch (err) {
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            `[useActionDrivenAgent] network error POSTing to ${url}:`,
            err,
          );
        }
        // NEVER SILENT (#8): a network failure must be visible, not swallowed.
        // Both callers (A2UISurfaceMount, CompareLauncher) catch this and
        // render a message. The opt-in/fallback decision happens BEFORE
        // triggerAction is ever called, so reaching here is a real failure.
        throw new Error("Couldn't reach the server. Check your connection and try again.");
      }

      // Self-heal a vanished session. LOCAL_MODE sessions live in memory and
      // disappear on a backend restart; a surface that was open before the
      // restart outlives its session, so the action gate 404s ("Session not
      // found"). The page bootstraps the session only once on mount, so we
      // re-bootstrap here and retry the action exactly once. The backend
      // access gate stays intact — the client owns session lifecycle.
      if (res.status === 404) {
        if (process.env.NODE_ENV !== "production") {
          console.info(
            `[useActionDrivenAgent] session "${sessionId}" missing (404) — bootstrapping + retrying once`,
          );
        }
        try {
          await fetchWithAuth(
            `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}/bootstrap`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ skill_id: skillId }),
            },
          );
          res = await doPost();
        } catch (err) {
          if (process.env.NODE_ENV !== "production") {
            console.warn(
              `[useActionDrivenAgent] bootstrap-and-retry failed for ${url}:`,
              err,
            );
          }
          // NEVER SILENT (#8): a failed self-heal must be visible, not a silent
          // return that reads as a dead button. The caller renders this.
          throw new Error(
            "Your session expired and couldn't be restored. Reload the page and try again.",
          );
        }
      }

      if (!res.ok) {
        // NEVER SILENT (#8): a gate reject (403) / any non-OK status must be
        // VISIBLE — both callers catch and render it. The opt-in fallback is
        // decided before triggerAction is called, so a non-OK here is a real
        // failure, not the "not opted in → chat intent" path.
        const detail = await res.text().catch(() => "");
        console.warn(
          `[useActionDrivenAgent] surface-action-run returned HTTP ${res.status} for ${url}`,
          detail,
        );
        throw new Error(
          res.status === 403
            ? "This action isn't permitted for this skill (403)."
            : `The run was rejected (HTTP ${res.status}). Check the Activity tab or try again.`,
        );
      }

      if (!res.body) {
        // No stream body — backend contract violation, but resolving
        // cleanly keeps the UI alive.
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            `[useActionDrivenAgent] surface-action-run returned 200 with empty body for ${url}`,
          );
        }
        return;
      }

      // Track in-flight tool calls so we can pair TOOL_CALL_RESULT with
      // the right tool name. G41 dedup on the server guarantees at most
      // one terminal event but we still defend against double-firing on
      // the client by short-circuiting once `terminated` flips.
      const pending = new Map<string, PendingToolCall>();
      let terminated = false;
      let runError: Error | null = null;

      // ACTIVITY-OBS — mirror each tool call's live state for the Activity
      // panel. Kept alongside `pending` (which is A2UI-parse-only) so the
      // sink sees EVERY tool (extract / map / compare), not just the A2UI
      // dispatch. Ids are the ADK function_call ids, so ChatShell can merge
      // these with the persisted /activity re-fetch by id (live wins).
      const activityToolCalls = new Map<string, ToolCallState>();
      const emitToolCall = (id: string, patch: Partial<ToolCallState>): void => {
        if (!activitySink?.upsertToolCall) return;
        const prev: ToolCallState =
          activityToolCalls.get(id) ?? { id, name: "", status: "running" };
        const next: ToolCallState = { ...prev, ...patch, id };
        activityToolCalls.set(id, next);
        activitySink.upsertToolCall(next);
      };
      // onRunSettled must fire exactly once — whether via RUN_FINISHED,
      // RUN_ERROR, or an abrupt stream end (network drop mid-run). The finally
      // below is the backstop; `settled` guards against a double emit.
      let settled = false;
      const settle = (error?: string): void => {
        if (settled) return;
        settled = true;
        activitySink?.onRunSettled?.(error ? { error } : {});
      };

      // The stream is open — announce the run so the Activity tab shows a
      // live/running indicator immediately (never a silent grey button).
      activitySink?.onRunStart?.();

      try {
        for await (const event of readSSE(res.body)) {
          if (terminated) break;
          const type = event.type;
          if (typeof type !== "string") continue;

          switch (type) {
          case "TOOL_CALL_START": {
            const toolCallId = event.toolCallId;
            const toolCallName = event.toolCallName;
            if (typeof toolCallId !== "string") break;
            if (typeof toolCallName !== "string") break;
            pending.set(toolCallId, { name: toolCallName, args: "" });
            emitToolCall(toolCallId, {
              name: toolCallName,
              status: "running",
              ts: Date.now(),
            });
            break;
          }
          case "TOOL_CALL_ARGS": {
            const toolCallId = event.toolCallId;
            const delta = event.delta;
            if (typeof toolCallId !== "string") break;
            if (typeof delta !== "string") break;
            const entry = pending.get(toolCallId);
            if (entry) entry.args += delta;
            const prevArgs = activityToolCalls.get(toolCallId)?.argsJson ?? "";
            emitToolCall(toolCallId, { argsJson: prevArgs + delta });
            break;
          }
          case "TOOL_CALL_END": {
            const toolCallId = event.toolCallId;
            if (typeof toolCallId !== "string") break;
            // Only promote to success if it hasn't already errored.
            if (activityToolCalls.get(toolCallId)?.status !== "error") {
              emitToolCall(toolCallId, { status: "success" });
            }
            break;
          }
          case "TOOL_CALL_RESULT": {
            const toolCallId = event.toolCallId;
            const content = event.content;
            if (typeof toolCallId !== "string") break;
            if (typeof content !== "string") break;
            emitToolCall(toolCallId, {
              resultContent: content,
              status:
                activityToolCalls.get(toolCallId)?.status === "error"
                  ? "error"
                  : "success",
            });
            const entry = pending.get(toolCallId);
            if (!entry || entry.name !== A2UI_TOOL_NAME) break;
            const parsed = parseA2uiToolResult(content, surfaceId);
            if (!parsed) break;
            // Dispatch through the same SurfaceRegistry path the chat
            // bubble's A2UISurfaceDispatcher uses. Idempotent on tool
            // call id — strict-mode double-effects are absorbed inside
            // the registry's `consumedToolCallIds` guard.
            registry.appendMessages(
              parsed.surfaceId,
              parsed.messages,
              toolCallId,
            );
            break;
          }
          case "CUSTOM": {
            // ACTIVITY-OBS — server-authored progress customs the chat path
            // renders via useSkillAgent. The action path binds a per-request
            // LatencyTracker (surface-action-run, commit 2973b3f) so these can
            // fire here too; forward them so the Activity tab stays in step.
            if (event.name === "STAGE_PROGRESS") {
              const v = event.value as { label?: unknown } | null | undefined;
              if (v && typeof v.label === "string") activitySink?.onStage?.(v.label);
              break;
            }
            if (event.name === "MODEL_RESOLVED") {
              const v = event.value as { model?: unknown } | null | undefined;
              if (v && typeof v.model === "string" && v.model) activitySink?.onModel?.(v.model);
              break;
            }
            if (
              event.name === "AGENT_DELEGATION" &&
              event.value &&
              typeof event.value === "object"
            ) {
              const v = event.value as {
                parent?: unknown;
                target?: unknown;
                target_display?: unknown;
                mode?: unknown;
                avatar?: unknown;
              };
              const targetDisplay =
                typeof v.target_display === "string" && v.target_display
                  ? v.target_display
                  : String(v.target ?? "specialist");
              activitySink?.upsertDelegation?.({
                id: `action-deleg-${crypto.randomUUID()}`,
                afterMessageId: null,
                parent: String(v.parent ?? ""),
                target: String(v.target ?? ""),
                targetDisplay,
                avatar: typeof v.avatar === "string" && v.avatar ? v.avatar : null,
                mode: v.mode === "suggest" ? "suggest" : "auto",
                ts: Date.now(),
              });
              break;
            }
            // Model-B skills (a2ui.enabled: false — one-doc-compare,
            // one-ppa-expert) do NOT call send_a2ui_json_to_client; their
            // surface arrives as an out-of-model A2UI_SURFACE CUSTOM event
            // from the backend result→A2UI emitter. Handle it identically to
            // the chat path's WorkspaceA2uiEventRouter (ChatShell.tsx) so a
            // launcher-triggered compare / analyze-obligations actually
            // renders. Without this case the surface is silently dropped and
            // the launcher never yields (the "nothing happens" bug).
            if (event.name !== "A2UI_SURFACE") break;
            const value = event.value;
            if (!value || typeof value !== "object") break;
            const v = value as {
              surfaceId?: unknown;
              messages?: unknown;
              sourceId?: unknown;
              artifact?: unknown;
            };
            if (!Array.isArray(v.messages) || v.messages.length === 0) break;
            const dispatchSurfaceId =
              typeof v.surfaceId === "string" && v.surfaceId ? v.surfaceId : surfaceId;
            const dispatchSourceId =
              typeof v.sourceId === "string" && v.sourceId
                ? v.sourceId
                : `custom-a2ui-${dispatchSurfaceId}-${crypto.randomUUID()}`;
            const artifact =
              v.artifact && typeof v.artifact === "object" ? v.artifact : null;
            registry.appendMessages(
              dispatchSurfaceId,
              v.messages as Parameters<typeof registry.appendMessages>[1],
              dispatchSourceId,
              artifact as Parameters<typeof registry.appendMessages>[3],
            );
            break;
          }
          case "RUN_FINISHED": {
            terminated = true;
            // Resolve any still-running tool calls as success (mirrors the
            // chat path's onRunFinalized) so the Activity feed never strands
            // an orange pulse after a clean finish.
            for (const [id, tc] of activityToolCalls) {
              if (tc.status === "running") emitToolCall(id, { status: "success" });
            }
            settle();
            break;
          }
          case "RUN_ERROR": {
            terminated = true;
            const message =
              typeof event.message === "string"
                ? event.message
                : "Agent run failed";
            runError = new Error(message);
            // Flip running tool calls to error so the failure is visible in
            // the Activity tab, not just a rejected promise + console.warn.
            for (const [id, tc] of activityToolCalls) {
              if (tc.status === "running") emitToolCall(id, { status: "error" });
            }
            settle(message);
            break;
          }
          // Other events (RUN_STARTED, TEXT_MESSAGE_*, STATE_*, CUSTOM,
          // REASONING_*) are accepted but not surfaced — Pattern 1
          // surfaces don't render an inline chat bubble, and surface
          // state is delivered via the tool call above.
          default:
            break;
          }
        }
      } finally {
        // Backstop: a stream that ends without a terminal event (network
        // drop) still clears the in-flight indicator. No-op after a terminal
        // event already settled.
        settle();
      }

      if (runError) {
        throw runError;
      }
    },
    [registry, sessionId, skillId, activitySink],
  );

  return { triggerAction };
}
