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
  /** Friendly name of the conversation's owner (e.g. "Agent" for a machine
   * principal's thread shared with a supervisor). Null/absent when unknown or
   * from an older backend — render no owner rather than a raw uid (CLAUDE.md #9). */
  owner_name?: string | null;
  /** True when the caller may send messages in this conversation (owner or
   * collaborator). Absent on older backends — treat as false. */
  can_reply?: boolean;
  /** The project this chat was started in or moved into (projects §6.3).
   * Null/absent: not in a project explicitly. */
  project_id?: string | null;
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
  /** True when the backend has older conversations beyond the loaded pages. */
  hasMore: boolean;
  isLoadingMore: boolean;
  /** The last "load more" failed; the loaded list is still valid. */
  loadMoreFailed: boolean;
  /** Append the next page of older conversations. No-op when !hasMore. */
  loadMore: () => void;
}

/** One page. The list used to stop at the backend default of 20 with no way
 * to reach anything older, so a user's history simply ran out. */
const PAGE_SIZE = 30;

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
  const qs = skillFilter ? `&skill_id=${encodeURIComponent(skillFilter)}` : "";
  return useSessionList(`/api/proxy/api/sessions?page_size=${PAGE_SIZE}${qs}`);
}

/**
 * Conversations OWNED BY OTHERS where the caller is a collaborator — e.g. a
 * supervisor reading and replying in a machine principal's threads.
 * Newest first. Rows carry `owner_name` so the list says whose thread it is.
 * Most users have none; the sidebar hides the section when the list is empty.
 */
export function useSharedSessions(): UseSkillSessionsReturn {
  return useSessionList(`/api/proxy/api/sessions?shared=true&page_size=${PAGE_SIZE}`);
}

function useSessionList(url: string): UseSkillSessionsReturn {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreFailed, setLoadMoreFailed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const moreAbortRef = useRef<AbortController | null>(null);

  const fetch_ = useCallback(() => {
    abortRef.current?.abort();
    // A refresh replaces the list from the first page, so an in-flight
    // "load more" would append onto the wrong base.
    moreAbortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);
    setLoadMoreFailed(false);

    fetchWithAuth(url, {
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ListSessionsResponse>;
      })
      .then((data) => {
        setSessions(data.sessions ?? []);
        setNextCursor(data.next_cursor ?? null);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setError("Failed to load sessions");
        }
      })
      .finally(() => {
        // Only the latest request may clear the loading flag — an aborted
        // predecessor resolving late must not end a newer request's loading.
        if (abortRef.current === controller) setIsLoading(false);
      });
  }, [url]);

  const loadMore = useCallback(() => {
    if (!nextCursor || moreAbortRef.current) return;
    const controller = new AbortController();
    moreAbortRef.current = controller;
    setIsLoadingMore(true);
    setLoadMoreFailed(false);
    fetchWithAuth(`${url}&cursor=${encodeURIComponent(nextCursor)}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ListSessionsResponse>;
      })
      .then((data) => {
        setSessions((prev) => {
          const seen = new Set(prev.map((s) => s.session_id));
          return [...prev, ...(data.sessions ?? []).filter((s) => !seen.has(s.session_id))];
        });
        setNextCursor(data.next_cursor ?? null);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") setLoadMoreFailed(true);
      })
      .finally(() => {
        if (moreAbortRef.current === controller) {
          moreAbortRef.current = null;
          setIsLoadingMore(false);
        }
      });
  }, [url, nextCursor]);

  useEffect(() => {
    fetch_();
    return () => {
      abortRef.current?.abort();
      moreAbortRef.current?.abort();
    };
  }, [fetch_]);

  useEffect(() => {
    const onFocus = () => fetch_();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [fetch_]);

  // Cross-panel sync: when ANY session list (skill-level or per-document)
  // mutates, both hooks refetch. See lib/sessionEvents.ts.
  useEffect(() => subscribeSessionsChanged(fetch_), [fetch_]);

  return {
    sessions,
    isLoading,
    error,
    refetch: fetch_,
    hasMore: nextCursor !== null,
    isLoadingMore,
    loadMoreFailed,
    loadMore,
  };
}
