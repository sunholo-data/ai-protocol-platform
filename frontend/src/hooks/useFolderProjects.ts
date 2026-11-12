"use client";

/**
 * Which projects each library folder is in — for the chip on folder rows in
 * My Documents, so a folder already in a project isn't turned into a second
 * one by accident.
 *
 * `GET /api/folders/projects` → `{ folders: { folderId: FolderProjectLink[] } }`.
 * On a backend without the route (or on any error) every folder shows no chip.
 */

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";

export interface FolderProjectLink {
  projectId: string;
  projectName: string;
  /** Documents of this folder that are in the project. */
  documents: number;
  /** How the folder is linked: a GitHub source, a folder source, or documents added by hand. */
  via: "github" | "folder" | "documents";
}

export type FolderProjects = Record<string, FolderProjectLink[]>;

function toLink(raw: unknown): FolderProjectLink | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.projectId !== "string" || !r.projectId) return null;
  const via = r.via === "github" || r.via === "folder" ? r.via : "documents";
  return {
    projectId: r.projectId,
    projectName: typeof r.projectName === "string" && r.projectName ? r.projectName : "Untitled project",
    documents: typeof r.documents === "number" ? r.documents : 0,
    via,
  };
}

export function parseFolderProjects(body: unknown): FolderProjects {
  const folders = (body as { folders?: unknown } | null)?.folders;
  if (!folders || typeof folders !== "object") return {};
  const out: FolderProjects = {};
  for (const [id, rows] of Object.entries(folders as Record<string, unknown>)) {
    if (!Array.isArray(rows)) continue;
    const links = rows.map(toLink).filter((l): l is FolderProjectLink => l !== null);
    if (links.length) out[id] = links;
  }
  return out;
}

export function useFolderProjects(): FolderProjects {
  const [links, setLinks] = useState<FolderProjects>({});
  const [tick, setTick] = useState(0);

  useEffect(() => subscribeDocumentsChanged(() => setTick((t) => t + 1)), []);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => fetchWithAuth("/api/proxy/api/folders/projects"))
      .then(async (res) => (res?.ok ? parseFolderProjects(await res.json().catch(() => null)) : {}))
      .catch(() => ({}))
      .then((next) => {
        if (!cancelled) setLinks(next);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  return links;
}
