"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SkillMessage } from "@/hooks/useSkillAgent";
import { fetchWithAuth } from "@/lib/apiClient";

interface SessionMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  avatar?: string | null;
  agent_label?: string | null;
}

/** A persisted A2UI workbench surface replayed on resume (7.5 M3). Same payload
 * the live AG-UI CUSTOM `A2UI_SURFACE` event carries, read from session state. */
export interface A2uiSurfaceReplay {
  surfaceId: string;
  messages: unknown[];
  sourceId?: string;
  artifact?: unknown;
  createdAt?: number;
}

interface GetSessionMessagesResponse {
  messages: SessionMessage[];
  session_id: string;
  a2ui_surfaces?: A2uiSurfaceReplay[];
  transcript_unavailable?: boolean;
}

interface UseSessionMessagesReturn {
  initialMessages: SkillMessage[];
  /** Persisted workbench surfaces to replay into the SurfaceRegistry on resume
   * so per-result tabs + the Workspace index return after a refresh. */
  a2uiSurfaces: A2uiSurfaceReplay[];
  isLoadingHistory: boolean;
  historyError: string | null;
  sessionGone: boolean;
  /** The conversation is listed and had turns, but its transcript is gone from
   * the ADK session store. Distinct from `sessionGone` (never existed) and from
   * a genuinely empty new chat — resuming it must explain itself rather than
   * render an unexplained blank thread. */
  transcriptUnavailable: boolean;
}

// Stranded-session-prevention (1.23) Option 1: distinguishes
// "session truly does not exist" (404) from transient errors (5xx,
// network). The chat page reads sessionGone and auto-redirects to a
// fresh URL via handleNewSession.
class SessionNotFoundError extends Error {
  constructor() {
    super("session not found");
    this.name = "SessionNotFoundError";
  }
}

let _msgCounter = 0;
function nextId(): string {
  return `hist-${++_msgCounter}`;
}

function toSkillMessage(m: SessionMessage): SkillMessage {
  return {
    id: nextId(),
    role: m.role,
    content: m.content,
    avatar: m.avatar ?? null,
    agentLabel: m.agent_label ?? null,
    // Original send time (epoch ms) so a resumed thread shows each bubble at
    // when it was sent, not when the session loaded.
    createdAt: typeof m.timestamp === "number" ? m.timestamp : undefined,
  };
}

export function useSessionMessages(sessionId: string | null): UseSessionMessagesReturn {
  const [initialMessages, setInitialMessages] = useState<SkillMessage[]>([]);
  const [a2uiSurfaces, setA2uiSurfaces] = useState<A2uiSurfaceReplay[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sessionGone, setSessionGone] = useState(false);
  const [transcriptUnavailable, setTranscriptUnavailable] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const lastSessionId = useRef<string | null>(null);

  const fetch_ = useCallback(
    (sid: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setIsLoadingHistory(true);
      setHistoryError(null);
      setSessionGone(false);
      setTranscriptUnavailable(false);

      fetchWithAuth(`/api/proxy/api/sessions/${encodeURIComponent(sid)}/messages`, {
        signal: controller.signal,
      })
        .then((res) => {
          if (res.status === 404) throw new SessionNotFoundError();
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json() as Promise<GetSessionMessagesResponse>;
        })
        .then((data) => {
          setInitialMessages(data.messages.map(toSkillMessage));
          setA2uiSurfaces(data.a2ui_surfaces ?? []);
          setTranscriptUnavailable(data.transcript_unavailable === true);
        })
        .catch((err: Error) => {
          if (err.name === "AbortError") return;
          if (err instanceof SessionNotFoundError) {
            setSessionGone(true);
            setInitialMessages([]);
            setA2uiSurfaces([]);
            return;
          }
          setHistoryError("Couldn't load previous messages — starting fresh.");
          setInitialMessages([]);
          setA2uiSurfaces([]);
        })
        .finally(() => {
          setIsLoadingHistory(false);
        });
    },
    [],
  );

  useEffect(() => {
    if (!sessionId) {
      setInitialMessages([]);
      setA2uiSurfaces([]);
      setHistoryError(null);
      setSessionGone(false);
      return;
    }

    if (sessionId === lastSessionId.current) return;
    lastSessionId.current = sessionId;

    fetch_(sessionId);
    return () => abortRef.current?.abort();
  }, [sessionId, fetch_]);

  return {
    initialMessages,
    a2uiSurfaces,
    isLoadingHistory,
    historyError,
    sessionGone,
    transcriptUnavailable,
  };
}
