"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeSessionsChanged } from "@/lib/sessionEvents";

export interface ChatSessionSummary {
  session_id: string;
  document_ids: string[];
  skill_id: string;
  owner_uid: string;
  access_control: Record<string, unknown>;
  title: string | null;
  turn_count: number;
  first_message_at: string;
  last_message_at: string;
  archived_at: string | null;
  is_owner: boolean;
  can_fork: boolean;
}

export type SessionFilter = "mine" | "team" | "all";

interface ListSessionsResponse {
  sessions: ChatSessionSummary[];
  next_cursor: string | null;
}

interface UseDocumentSessionsReturn {
  sessions: ChatSessionSummary[];
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useDocumentSessions(
  docId: string | null,
  filter: SessionFilter = "all"
): UseDocumentSessionsReturn {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetch_ = useCallback(() => {
    if (!docId) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);

    fetchWithAuth(`/api/proxy/api/documents/${encodeURIComponent(docId)}/sessions?filter=${filter}`, {
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ListSessionsResponse>;
      })
      .then((data) => {
        setSessions(data.sessions);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setError("Failed to load chat history");
        }
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [docId, filter]);

  // Fetch on mount and when docId/filter change
  useEffect(() => {
    fetch_();
    return () => abortRef.current?.abort();
  }, [fetch_]);

  // Refetch on window focus (SWR-style)
  useEffect(() => {
    const onFocus = () => fetch_();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [fetch_]);

  // Cross-panel sync: when ANY session list (skill-level or per-document)
  // mutates, both hooks refetch. See lib/sessionEvents.ts.
  useEffect(() => subscribeSessionsChanged(fetch_), [fetch_]);

  return { sessions, isLoading, error, refetch: fetch_ };
}
