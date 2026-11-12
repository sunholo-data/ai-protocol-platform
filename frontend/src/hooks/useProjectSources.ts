/**
 * A project's sources: GitHub folders and library folders it stays in step with
 * (docs/design/private/github-sources.md). Read-mostly: the list refetches after
 * every add, remove and sync, and on `notifyProjectsChanged()`.
 *
 * A sync runs inside one request within a server-side time budget; a run that
 * reaches it reports `partial` with `counts.remaining`, and `syncSourceUntilDone`
 * calls again until nothing remains (§15), reporting progress between calls.
 *
 * So a sync request answers only when its run ends: wait longer than the server
 * may take (SYNC_ANSWER_TIMEOUT_MS), not the usual minute. A sync whose answer is
 * lost anyway carries on on the server; the row follows it (ProjectSources).
 */

import { useCallback, useEffect, useState } from "react";
import { notifyProjectsChanged, subscribeProjectsChanged } from "@/lib/projectEvents";
import { NO_ANSWER_CODES, request, type ProjectResult } from "./useProjects";

const API = "/api/proxy/api/projects";
/** A sync that keeps stopping at the time limit is not looped forever. */
const MAX_CONTINUATIONS = 20;
/** How long one sync request is waited for. The server stops starting files at
 * its budget (backend sources/engine.py DEFAULT_BUDGET_SECONDS, 240 s), but a
 * file already being read finishes, and a scanned PDF read page by page can take
 * minutes; the run's lease allows budget + 120 s. */
export const SYNC_ANSWER_TIMEOUT_MS = 7 * 60_000;

/** True when a request got no answer — it may still be running on the server. */
export function noAnswer(r: { ok: boolean; code?: string }): boolean {
  return !r.ok && NO_ANSWER_CODES.has(r.code ?? "");
}

export interface SourceFileNote {
  path: string;
  reason?: string;
  error?: string;
}

export interface SourceReport {
  status: string;
  dryRun?: boolean;
  commit?: string;
  message?: string;
  error?: { code?: string; message?: string };
  counts: Record<string, number>;
  skipped?: SourceFileNote[];
  failed?: SourceFileNote[];
  parseFailed?: SourceFileNote[];
  indexFailed?: SourceFileNote[];
  removalsHeld?: string[];
  plan?: { adopt?: string[]; add?: string[]; update?: string[]; remove?: string[] };
  finishedAt?: string;
}

export interface ProjectSource {
  id: string;
  type: "github" | "folder";
  repo?: string;
  ref?: string;
  path?: string;
  folderPrefix?: string;
  folderName?: string;
  credentialRef?: string;
  status: string;
  syncedCommit?: string;
  syncedAt?: string;
  lastReport?: SourceReport | null;
  /** While a sync runs: files handled so far of those it is storing. */
  progress?: { done: number; total: number } | null;
  /** The project's AI may commit to this source (project-source-write.md §4). */
  writable?: boolean;
  /** Present when writing could be offered to this person: whether it can be
   * turned on now, or where the writer GitHub App still has to be installed. */
  write?: SourceWriteAccess;
}

export interface SourceWriteAccess {
  canWrite: boolean;
  installUrl?: string;
  problem?: string;
}

export type SourcesStatus = "loading" | "ready" | "error";

/** GitHub access this person may use, and the repositories it reaches
 * (`owner/name` patterns). The server picks the one that covers a repository. */
export interface SourceCredential {
  ref: string;
  repos: string[];
}

export function useProjectSources(projectId: string): {
  sources: ProjectSource[];
  credentials: SourceCredential[];
  /** Where this person could let the AI write (the writer App decides per repository). */
  writers: SourceCredential[];
  status: SourcesStatus;
  reload: () => void;
} {
  const [sources, setSources] = useState<ProjectSource[]>([]);
  const [credentials, setCredentials] = useState<SourceCredential[]>([]);
  const [writers, setWriters] = useState<SourceCredential[]>([]);
  const [status, setStatus] = useState<SourcesStatus>("loading");

  const load = useCallback(async () => {
    const r = await request(`${API}/${encodeURIComponent(projectId)}/sources`, {}, "Couldn't load sources");
    if (!r.ok) {
      setStatus("error");
      return;
    }
    const body = (r.body ?? {}) as {
      sources?: ProjectSource[];
      credentials?: SourceCredential[];
      writers?: SourceCredential[];
    };
    setSources(Array.isArray(body.sources) ? body.sources : []);
    setCredentials(Array.isArray(body.credentials) ? body.credentials : []);
    setWriters(Array.isArray(body.writers) ? body.writers : []);
    setStatus("ready");
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => subscribeProjectsChanged(() => void load()), [load]);

  return { sources, credentials, writers, status, reload: () => void load() };
}

export interface NewGitHubSource {
  type: "github";
  repo: string;
  path: string;
  ref: string;
  /** Empty: the server uses whichever of your credentials covers the repository. */
  credentialRef?: string;
}

export interface NewFolderSource {
  type: "folder";
  folderId: string;
}

export async function addSource(
  projectId: string,
  input: NewGitHubSource | NewFolderSource,
): Promise<ProjectResult<{ source: ProjectSource }>> {
  const body =
    input.type === "folder"
      ? { type: "folder", folder_id: input.folderId }
      : { type: "github", repo: input.repo, path: input.path, ref: input.ref || "main", credential_ref: input.credentialRef ?? "" };
  const r = await request(
    `${API}/${encodeURIComponent(projectId)}/sources`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    "Couldn't add the source",
  );
  return r.ok ? { ok: true, source: r.body as ProjectSource } : r;
}

/** One source, as it is now — read while a sync runs to follow its progress. */
export async function getSource(projectId: string, sourceId: string): Promise<ProjectResult<{ source: ProjectSource }>> {
  const r = await request(
    `${API}/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
    {},
    "Couldn't read the source",
  );
  return r.ok ? { ok: true, source: r.body as ProjectSource } : r;
}

/** Let the project's AI commit to this source, or stop it. */
export async function setSourceWritable(
  projectId: string,
  sourceId: string,
  writable: boolean,
): Promise<ProjectResult<{ source: ProjectSource }>> {
  const r = await request(
    `${API}/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ writable }) },
    writable ? "Couldn't turn on writing" : "Couldn't turn off writing",
  );
  return r.ok ? { ok: true, source: r.body as ProjectSource } : r;
}

export async function removeSource(projectId: string, sourceId: string): Promise<ProjectResult> {
  const r = await request(
    `${API}/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" },
    "Couldn't remove the source",
  );
  return r.ok ? { ok: true } : r;
}

export interface SyncOptions {
  dryRun?: boolean;
  confirmRemovals?: boolean;
  force?: boolean;
}

export async function syncSource(
  projectId: string,
  sourceId: string,
  opts: SyncOptions = {},
): Promise<ProjectResult<{ report: SourceReport }>> {
  const r = await request(
    `${API}/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/sync`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run: Boolean(opts.dryRun),
        confirm_removals: Boolean(opts.confirmRemovals),
        force: Boolean(opts.force),
      }),
    },
    opts.dryRun ? "Couldn't preview the sync" : "Couldn't sync",
    { timeoutMs: SYNC_ANSWER_TIMEOUT_MS },
  );
  return r.ok ? { ok: true, report: r.body as SourceReport } : r;
}

/** Sync, continuing after each time-limited run until nothing remains.
 * `onProgress` gets every intermediate report. */
export async function syncSourceUntilDone(
  projectId: string,
  sourceId: string,
  opts: SyncOptions,
  onProgress: (report: SourceReport) => void,
): Promise<ProjectResult<{ report: SourceReport }>> {
  let last: ProjectResult<{ report: SourceReport }> = { ok: false, message: "Couldn't sync." };
  let first = true;
  for (let i = 0; i < MAX_CONTINUATIONS; i++) {
    last = await syncSource(projectId, sourceId, first ? opts : { ...opts, force: false });
    first = false;
    if (!last.ok || opts.dryRun || !(last.report.counts?.remaining > 0)) break;
    onProgress(last.report);
  }
  if (last.ok && !opts.dryRun) notifyProjectsChanged();
  return last;
}
