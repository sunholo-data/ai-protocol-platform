"use client";

/**
 * useSessionActivity — hydrate a session's tool-call history from the backend
 * so the Activity panel survives a reload.
 *
 * The live AG-UI stream state (useSkillAgent's toolCalls) is in-memory and
 * gone on refresh. This fetches GET /api/sessions/{id}/activity, which
 * reconstructs past tool calls from the ADK session's function_call /
 * function_response events, in the same `ToolCallState` shape. ChatShell merges
 * these (by id) with the live list, so history shows immediately and new live
 * calls append.
 *
 * ACTIVITY-OBS: an action-triggered run (surface-action-run) executes through
 * the SAME ADK runner as chat, so ITS tool calls also persist to the session's
 * events — but the mount-time fetch happened before the run. Pass a `refetchKey`
 * that changes when a run settles (RUN_FINISHED / RUN_ERROR) to re-fetch and
 * pick up the freshly-persisted history, without remounting the hook.
 */

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { ToolCallState, DelegationMarkerItem } from "@/hooks/useSkillAgent";

interface ActivityResponse {
  tool_calls?: {
    id: string;
    name: string;
    status: string;
    ts: number;
    argsJson?: string | null;
    resultContent?: string | null;
  }[];
  delegations?: {
    id: string;
    target: string;
    targetDisplay: string;
    mode: string;
    ts: number;
  }[];
  session_start_ts?: number | null;
}

interface SessionActivity {
  toolCalls: ToolCallState[];
  delegations: DelegationMarkerItem[];
  /** Real session start (epoch ms) from the first event, or null. */
  sessionStartTs: number | null;
}

const EMPTY: SessionActivity = { toolCalls: [], delegations: [], sessionStartTs: null };

export function useSessionActivity(
  sessionId: string | null,
  /** Bump to force a re-fetch (e.g. after an action-triggered run settles).
   * Any changing value works — a monotonic counter is the intended use. */
  refetchKey: number = 0,
): SessionActivity {
  const [activity, setActivity] = useState<SessionActivity>(EMPTY);

  useEffect(() => {
    if (!sessionId) {
      setActivity(EMPTY);
      return;
    }
    let cancelled = false;
    fetchWithAuth(`/api/proxy/api/sessions/${encodeURIComponent(sessionId)}/activity`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: ActivityResponse) => {
        if (cancelled) return;
        setActivity({
          toolCalls: (data.tool_calls ?? []).map((t) => ({
            id: t.id,
            name: t.name,
            status: t.status === "error" ? "error" : "success",
            ts: t.ts,
            argsJson: t.argsJson ?? undefined,
            resultContent: t.resultContent ?? undefined,
          })),
          delegations: (data.delegations ?? []).map((d) => ({
            id: d.id,
            afterMessageId: null,
            parent: "",
            target: d.target,
            targetDisplay: d.targetDisplay,
            // History reconstruction has no delegate avatar (the live
            // AGENT_DELEGATION event is the only source) — resume shows root
            // avatars; per-delegate marks apply to the live session.
            avatar: null,
            mode: d.mode === "suggest" ? "suggest" : "auto",
            ts: d.ts,
          })),
          sessionStartTs: data.session_start_ts ?? null,
        });
      })
      .catch(() => {
        // Non-fatal — the panel just shows no history (live events still work).
        if (!cancelled) setActivity(EMPTY);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, refetchKey]);

  return activity;
}
