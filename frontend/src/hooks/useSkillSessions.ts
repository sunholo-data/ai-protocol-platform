"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeSessionsChanged } from "@/lib/sessionEvents";

export interface ChatSessionSummary {
  session_id: string;
  document_ids: string[];
  skill_id: string;
  owner_uid: string;
  title: string | null;
  turn_count: number;
  first_message_at: string;
  last_message_at: string;
  archived_at: string | null;
  is_owner: boolean;
  /** Friendly name of the agent this conversation was with. Null when the
   * skill can't be resolved — render no chip rather than a raw UUID. */
  skill_label?: string | null;
  /** v6.23.0 B5 — this conversation's stored messages are gone; opening it
   * shows "no longer available". Surfaced in the list so a user learns that
   * BEFORE clicking. All known cases predate the 2026-08-05 session-sweep fix. */
  transcript_lost?: boolean;
}

interface ListSessionsResponse {
  sessions: ChatSessionSummary[];
  next_cursor: string | null;
}

interface UseSkillSessionsReturn {
  sessions: ChatSessionSummary[];
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * The caller's past conversations, newest first.
 *
 * Cross-skill by DEFAULT. Switching agent via the top bar starts a new session
 * on the new skill, so one sitting that moved between agents is split into one
 * session per skill. Scoping this list to the current skill therefore showed
 * the user only the fragment belonging to wherever they were standing — a real
 * 7-turn sitting across two agents read as "it didn't record my session"
 * (2026-08-05). Every row carries `skill_label` so the list can say which agent
 * each conversation was with.
 *
 * @param skillFilter Pass a skillId to narrow to one agent; null for all.
 */
export function useSkillSessions(skillFilter: string | null): UseSkillSessionsReturn {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetch_ = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);

    const qs = skillFilter ? `?skill_id=${encodeURIComponent(skillFilter)}` : "";
    fetchWithAuth(`/api/proxy/api/sessions${qs}`, {
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
          setError("Failed to load sessions");
        }
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [skillFilter]);

  useEffect(() => {
    fetch_();
    return () => abortRef.current?.abort();
  }, [fetch_]);

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
