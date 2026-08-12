"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { DocTabData } from "@/components/doc-browser/DocTab";

interface SessionResponse {
  session: {
    document_ids?: string[];
  };
}

interface DocumentMetaResponse {
  id?: string;
  originalFilename?: string;
  sourceFormat?: string;
}

interface UseSessionDocumentsReturn {
  tabs: DocTabData[] | null;
  isLoading: boolean;
}

/**
 * Resolve the open-tab list for a chat session: fetch the session's
 * `documentIds` and hydrate each one to its filename + format so the chat
 * page can mount tabs that match the conversation we're resuming.
 *
 * Returns `null` when no session is active or while loading the first page —
 * callers should treat `null` as "leave tabs alone" and `[]` as "session has
 * no docs". Tabs are returned with `included: true` (default for resumed
 * sessions; user can still toggle off per tab).
 */
export function useSessionDocuments(sessionId: string | null): UseSessionDocumentsReturn {
  const [tabs, setTabs] = useState<DocTabData[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const lastSessionId = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setTabs(null);
      lastSessionId.current = null;
      return;
    }
    if (sessionId === lastSessionId.current) return;
    lastSessionId.current = sessionId;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    setTabs(null);

    void (async () => {
      try {
        const sessionRes = await fetchWithAuth(
          `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`,
          { signal: controller.signal },
        );
        if (!sessionRes.ok) throw new Error(`session HTTP ${sessionRes.status}`);
        const sessionData = (await sessionRes.json()) as SessionResponse;
        const docIds = sessionData.session?.document_ids ?? [];
        if (controller.signal.aborted) return;
        if (docIds.length === 0) {
          setTabs([]);
          return;
        }
        const docResponses = await Promise.all(
          docIds.map((id) =>
            fetchWithAuth(`/api/proxy/api/documents/${encodeURIComponent(id)}`, {
              signal: controller.signal,
            })
              .then((res) =>
                res.ok ? (res.json() as Promise<DocumentMetaResponse>) : null,
              )
              .catch(() => null),
          ),
        );
        if (controller.signal.aborted) return;
        const resolved: DocTabData[] = docResponses
          .map((data, i) => {
            const id = docIds[i];
            if (!id) return null;
            return {
              id,
              filename: data?.originalFilename ?? id,
              format: data?.sourceFormat ?? "",
              included: true,
            };
          })
          .filter((t): t is DocTabData => t !== null);
        setTabs(resolved);
      } catch (err: unknown) {
        if ((err as Error).name !== "AbortError") {
          // Failure here is non-fatal — chat still works without tabs.
          setTabs([]);
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false);
      }
    })();

    return () => abortRef.current?.abort();
  }, [sessionId]);

  return { tabs, isLoading };
}
