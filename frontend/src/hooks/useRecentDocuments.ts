"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";
import { subscribeSessionsChanged } from "@/lib/sessionEvents";

/** One row of `GET /api/documents/recent` — metadata only, never blocks. */
export interface RecentDocument {
  id: string;
  originalFilename: string;
  sourceFormat: string;
  parseStatus: string;
  folderId?: string | null;
  source?: "uploaded" | "generated" | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  /** When the user last worked with it: their latest conversation that used
   * it, else when it arrived in their library. */
  lastUsedAt: string;
  /** That latest conversation, when there is one — so a card can offer
   * "continue the conversation" as well as "open the document". */
  lastSessionId: string | null;
  lastSessionTitle: string | null;
  /** The projects it is tagged into (projects design §5). */
  projectIds?: string[];
}

export type RecentDocumentsStatus = "loading" | "ready" | "error";

interface UseRecentDocumentsReturn {
  documents: RecentDocument[];
  status: RecentDocumentsStatus;
  refetch: () => void;
}

/**
 * The signed-in user's documents, most recently worked with first.
 *
 * Replaces a skill's shared demo documents in the workbench picker: a returning
 * user picks up their own work instead of the same five examples everyone gets.
 * Refetches when documents or conversations change (an upload, a new chat that
 * used a document, a delete), so the list never lags what the user just did.
 */
export function useRecentDocuments(limit = 6): UseRecentDocumentsReturn {
  const [documents, setDocuments] = useState<RecentDocument[]>([]);
  const [status, setStatus] = useState<RecentDocumentsStatus>("loading");
  const abortRef = useRef<AbortController | null>(null);
  const loadedRef = useRef(false);

  const fetch_ = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    // A background refresh keeps the current list on screen instead of
    // flashing back to skeletons.
    if (!loadedRef.current) setStatus("loading");

    fetchWithAuth(`/api/proxy/api/documents/recent?limit=${limit}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<{ documents: RecentDocument[] }>;
      })
      .then((data) => {
        loadedRef.current = true;
        setDocuments(data.documents ?? []);
        setStatus("ready");
      })
      .catch((err: Error) => {
        if (err.name === "AbortError") return;
        // Keep a list we already have; only a first load can fail into "error".
        if (!loadedRef.current) setStatus("error");
      });
  }, [limit]);

  useEffect(() => {
    fetch_();
    return () => abortRef.current?.abort();
  }, [fetch_]);

  useEffect(() => subscribeDocumentsChanged(fetch_), [fetch_]);
  useEffect(() => subscribeSessionsChanged(fetch_), [fetch_]);

  return { documents, status, refetch: fetch_ };
}
