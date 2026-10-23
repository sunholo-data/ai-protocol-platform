"use client";

/**
 * Knowledge bases the user can see, keyed by id — for the "searchable folder"
 * badge and menu in My Documents.
 *
 * Knowledge bases are a private-tier backend extension. Where it isn't
 * installed (the public template) `GET /api/knowledge-bases` is a 404, and
 * `available` is false so the UI offers nothing it can't do.
 *
 * While any knowledge base is indexing, the list re-polls so the badge shows
 * live progress (NEVER SILENT: an in-flight back-fill is always observable).
 */

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";

export interface KnowledgeBaseInfo {
  kbId: string;
  name: string;
  documentCount: number;
  /** "queued" | "indexing" | "done" | undefined (never synced). */
  state?: string;
  total?: number;
  done?: number;
  failed?: number;
  /** Plain-English audience from the server ("only its owner", "groups x"). */
  audience?: string;
  canManage?: boolean;
  accessControl?: Record<string, unknown>;
}

export interface KnowledgeBasesState {
  /** null while unknown; false when the backend has no knowledge bases. */
  available: boolean | null;
  byId: Record<string, KnowledgeBaseInfo>;
  /** Same entries, in the server's order (by name) — for pickers. */
  list: KnowledgeBaseInfo[];
}

export const KB_POLL_MS = 4000;

const IN_FLIGHT = new Set(["queued", "indexing"]);

function toInfo(raw: Record<string, unknown>): KnowledgeBaseInfo {
  const summary = (raw.lastSyncSummary ?? {}) as Record<string, unknown>;
  const num = (v: unknown) => (typeof v === "number" ? v : undefined);
  return {
    kbId: String(raw.kbId ?? ""),
    name: typeof raw.name === "string" ? raw.name : "",
    documentCount: num(raw.documentCount) ?? 0,
    state: typeof summary.state === "string" ? summary.state : undefined,
    total: num(summary.total),
    done: num(summary.done),
    failed: num(summary.failed),
    audience: typeof raw.audience === "string" ? raw.audience : undefined,
    canManage: raw.canManage === true,
    accessControl:
      raw.accessControl && typeof raw.accessControl === "object" ? (raw.accessControl as Record<string, unknown>) : undefined,
  };
}

export function useKnowledgeBases(): KnowledgeBasesState {
  const [state, setState] = useState<KnowledgeBasesState>({ available: null, byId: {}, list: [] });
  const [tick, setTick] = useState(0);

  useEffect(() => subscribeDocumentsChanged(() => setTick((t) => t + 1)), []);

  useEffect(() => {
    let cancelled = false;
    // Defensive by design: this only feeds a badge, so no failure here may
    // break the document list it sits in.
    (async () => {
      try {
        const res = await fetchWithAuth("/api/proxy/api/knowledge-bases");
        if (cancelled || !res) return;
        if (res.status === 404) {
          setState({ available: false, byId: {}, list: [] });
          return;
        }
        if (!res.ok) return; // keep what we had; the badge degrades to "Searchable"
        const list = (await res.json()) as unknown;
        const byId: Record<string, KnowledgeBaseInfo> = {};
        const ordered: KnowledgeBaseInfo[] = [];
        for (const raw of Array.isArray(list) ? (list as Record<string, unknown>[]) : []) {
          const info = toInfo(raw);
          if (info.kbId) {
            byId[info.kbId] = info;
            ordered.push(info);
          }
        }
        if (!cancelled) setState({ available: true, byId, list: ordered });
      } catch {
        // network error: leave the last known state
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const indexing = Object.values(state.byId).some((kb) => kb.state && IN_FLIGHT.has(kb.state));
  useEffect(() => {
    if (!indexing) return;
    const timer = setTimeout(() => setTick((t) => t + 1), KB_POLL_MS);
    return () => clearTimeout(timer);
  }, [indexing, state]);

  return state;
}
