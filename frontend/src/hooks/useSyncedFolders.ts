"use client";

/**
 * The library folder prefixes the user's GitHub sources write into — so My
 * Documents can mark those folders and not offer to rename them.
 *
 * A GitHub source places files by folder NAME; renaming a synced folder would
 * make the next sync recreate the old one. The server refuses that rename
 * (`FOLDER_SYNCED`); this list lets the menu say so up front. On a backend
 * without the route (or on any error) the list is empty and the server's
 * refusal is still shown when a rename is attempted.
 */

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";

export interface SyncedFolder {
  /** Folder name the source writes into, e.g. `docs`. */
  prefix: string;
  projectId: string;
  projectName: string;
  repo: string;
}

function toSynced(raw: unknown): SyncedFolder | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.prefix !== "string" || !r.prefix) return null;
  return {
    prefix: r.prefix,
    projectId: typeof r.projectId === "string" ? r.projectId : "",
    projectName: typeof r.projectName === "string" ? r.projectName : "",
    repo: typeof r.repo === "string" ? r.repo : "",
  };
}

export function useSyncedFolders(): SyncedFolder[] {
  const [synced, setSynced] = useState<SyncedFolder[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => subscribeDocumentsChanged(() => setTick((t) => t + 1)), []);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => fetchWithAuth("/api/proxy/api/sources/synced-folders"))
      .then(async (res) => {
        if (!res?.ok) return [];
        const body = (await res.json().catch(() => null)) as { synced?: unknown } | null;
        return Array.isArray(body?.synced) ? body.synced.map(toSynced).filter((s): s is SyncedFolder => s !== null) : [];
      })
      .catch(() => [])
      .then((list) => {
        if (!cancelled) setSynced(list);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  return synced;
}
