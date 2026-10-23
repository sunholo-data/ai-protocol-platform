"use client";

/**
 * Projects: a named tag on the user's documents, with a home for its chats and
 * instructions (docs/design/private/projects.md).
 *
 * One shared store backs every surface that lists projects — the sidebar
 * section, each document's "Add to project…" menu, the list page — so the
 * dozens of document rows in "Your files" share ONE `GET /api/projects` instead
 * of fetching per row. It refetches on `notifyProjectsChanged()`, which every
 * mutation below fires on success.
 *
 * Every mutation resolves to a result the caller MUST render (NEVER SILENT
 * #8): `{ ok: true, … }` or `{ ok: false, message, code? }` carrying the
 * server's human-readable text when it sent one.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeToAuthState } from "@/lib/firebase";
import { notifyDocumentsChanged, subscribeDocumentsChanged } from "@/lib/documentEvents";
import { notifyProjectsChanged, subscribeProjectsChanged } from "@/lib/projectEvents";
import { notifySessionsChanged, subscribeSessionsChanged } from "@/lib/sessionEvents";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

const API = "/api/proxy/api/projects";
const JSON_HEADERS = { "Content-Type": "application/json" };

/** One row of `GET /api/projects`. */
export interface ProjectSummary {
  id: string;
  name: string;
  docCount: number;
  chatCount: number;
  lastOpenedAt: string | null;
  /** The skill "chat in this project" opens on; null → the user's default. */
  defaultSkillId?: string | null;
}

/** `GET /api/projects/{id}` — the summary plus what the home edits. */
/** Meaning-based search over the project's documents (indexed automatically
 * when they're added). Absent where the platform has no search (the public
 * template) or before the backend reports it — then nothing is shown. */
export interface ProjectSearchStatus {
  indexed: number;
  /** Documents that can never be searched (no readable text, failed parse). */
  failed: number;
  total: number;
  status: "ready" | "indexing" | "unavailable";
}

export interface Project extends ProjectSummary {
  instructions: string;
  defaultSkillId: string | null;
  search?: ProjectSearchStatus | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

/** A document tagged into a project — metadata only, never blocks. */
export interface ProjectDocument {
  id: string;
  originalFilename: string;
  sourceFormat: string;
  parseStatus?: string;
  folderId?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  lastUsedAt?: string | null;
  lastSessionId?: string | null;
  lastSessionTitle?: string | null;
  projectIds?: string[];
}

/** A project chat: explicit (started in / moved into the project) or derived
 * (it used one of the project's documents — `linkedDocument` says which). */
export interface ProjectSession extends ChatSessionSummary {
  membership?: "explicit" | "derived";
  linkedDocument?: { id: string; filename: string } | null;
}

export type Failure = { ok: false; message: string; code?: string };
export type ProjectResult<T = object> = ({ ok: true } & T) | Failure;

export interface AddDocumentsOutcome {
  added: string[];
  already: string[];
  failed: Array<{ id: string; filename?: string; reason?: string }>;
}

// ---------------------------------------------------------------------------
// Wire helpers
// ---------------------------------------------------------------------------

async function readError(res: Response, fallback: string): Promise<Failure> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = body?.detail;
  if (typeof detail === "string" && detail) return { ok: false, message: detail };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; message?: unknown };
    return {
      ok: false,
      message: typeof d.message === "string" && d.message ? d.message : `${fallback} (HTTP ${res.status}).`,
      code: typeof d.code === "string" ? d.code : undefined,
    };
  }
  // FastAPI's 422 is a list of field errors — not user text.
  return { ok: false, message: `${fallback} (HTTP ${res.status}).` };
}

async function request(url: string, init: RequestInit, fallback: string): Promise<{ ok: true; body: unknown } | Failure> {
  let res: Response;
  try {
    res = await fetchWithAuth(url, init);
  } catch {
    return { ok: false, message: `${fallback} (network error).` };
  }
  if (!res.ok) return readError(res, fallback);
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  return { ok: true, body };
}

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** Tolerant of a `{project: …}` envelope and of snake_case keys, so a small
 * drift in the backend's response shape degrades to a field default rather
 * than a crash. */
export function normalizeProject(raw: unknown): Project | null {
  if (!raw || typeof raw !== "object") return null;
  const o = ((raw as { project?: unknown }).project ?? raw) as Record<string, unknown>;
  const id = str(o.id) ?? str(o.projectId) ?? str(o.project_id);
  if (!id) return null;
  return {
    id,
    name: str(o.name) ?? "Untitled project",
    docCount: num(o.docCount ?? o.doc_count),
    chatCount: num(o.chatCount ?? o.chat_count),
    lastOpenedAt: str(o.lastOpenedAt) ?? str(o.last_opened_at),
    instructions: typeof o.instructions === "string" ? o.instructions : "",
    defaultSkillId: str(o.defaultSkillId) ?? str(o.default_skill_id),
    createdAt: str(o.createdAt) ?? str(o.created_at),
    updatedAt: str(o.updatedAt) ?? str(o.updated_at),
    search: normalizeSearch(o.search),
  };
}

function normalizeSearch(raw: unknown): ProjectSearchStatus | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const status = o.status;
  if (status !== "ready" && status !== "indexing" && status !== "unavailable") return null;
  return { indexed: num(o.indexed), failed: num(o.failed), total: num(o.total), status };
}

function byRecent(a: ProjectSummary, b: ProjectSummary): number {
  return (b.lastOpenedAt ?? "").localeCompare(a.lastOpenedAt ?? "");
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export interface CreateProjectInput {
  name: string;
  instructions?: string;
  /** E4: tag that folder's current documents (a one-off snapshot). */
  fromFolderId?: string;
  /** E3: tag these documents. */
  docIds?: string[];
  /** A pre-filled name (E3/E4): on a clash the server appends " (2)", " (3)", …
   * instead of answering 409 PROJECT_EXISTS. */
  uniqueName?: boolean;
  /** E5 "Make this a project": tag the documents this chat used and make the
   * chat a member of the new project. */
  fromSessionId?: string;
}

function readOutcome(raw: unknown): AddDocumentsOutcome {
  const b = (raw ?? {}) as Partial<AddDocumentsOutcome>;
  return {
    added: Array.isArray(b.added) ? b.added : [],
    already: Array.isArray(b.already) ? b.already : [],
    failed: Array.isArray(b.failed) ? b.failed : [],
  };
}

export type CreatedProject = {
  project: Project;
  /** What happened to `docIds` / `fromFolderId` documents; null when none were asked for. */
  documents: AddDocumentsOutcome | null;
};

export async function createProject(input: CreateProjectInput): Promise<ProjectResult<CreatedProject>> {
  const r = await request(
    API,
    { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(input) },
    "Couldn't create the project",
  );
  if (!r.ok) return r;
  const project = normalizeProject(r.body);
  if (!project) return { ok: false, message: "The project was created but the reply was unreadable. Refresh to see it." };
  const docs = (r.body as { documents?: unknown } | null)?.documents;
  notifyProjectsChanged();
  if (input.fromFolderId || input.docIds?.length || input.fromSessionId) notifyDocumentsChanged();
  if (input.fromSessionId) notifySessionsChanged();
  return { ok: true, project, documents: docs ? readOutcome(docs) : null };
}

/**
 * Create from a PRE-FILLED name (E3 "+ New project…", E4 "Start a project
 * from this folder"): the server picks a free name (" (2)", …) rather than
 * fail — the user didn't type the name, so a 409 would be our fault. A name
 * the user typed (E1) goes through `createProject` and shows the 409 text.
 */
export function createProjectWithFreeName(input: CreateProjectInput): Promise<ProjectResult<CreatedProject>> {
  return createProject({ ...input, name: input.name.trim(), uniqueName: true });
}

export async function updateProject(
  id: string,
  patch: { name?: string; instructions?: string; defaultSkillId?: string | null },
): Promise<ProjectResult<{ project: Project | null }>> {
  const r = await request(
    `${API}/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(patch) },
    "Couldn't save the project",
  );
  if (!r.ok) return r;
  notifyProjectsChanged();
  return { ok: true, project: normalizeProject(r.body) };
}

export async function deleteProject(id: string): Promise<ProjectResult> {
  const r = await request(`${API}/${encodeURIComponent(id)}`, { method: "DELETE" }, "Couldn't delete the project");
  if (!r.ok) return r;
  notifyProjectsChanged();
  notifyDocumentsChanged();
  return { ok: true };
}

export async function addDocumentsToProject(
  id: string,
  docIds: string[],
): Promise<ProjectResult<AddDocumentsOutcome>> {
  const r = await request(
    `${API}/${encodeURIComponent(id)}/documents`,
    { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ doc_ids: docIds }) },
    "Couldn't add to the project",
  );
  if (!r.ok) return r;
  const outcome = readOutcome(r.body);
  if (outcome.added.length) {
    notifyProjectsChanged();
    notifyDocumentsChanged();
  }
  return { ok: true, ...outcome };
}

/**
 * E5 "Make this a project": a new project named after the chat (a free name —
 * the user didn't type it), holding the documents the chat used, with the chat
 * itself as an explicit member.
 */
export function createProjectFromSession(sessionId: string, name: string): Promise<ProjectResult<CreatedProject>> {
  return createProject({ name: name.trim() || "New project", fromSessionId: sessionId, uniqueName: true });
}

/** "Move to project…": put a chat into a project, or (null) out of any. */
export async function moveSessionToProject(sessionId: string, projectId: string | null): Promise<ProjectResult> {
  const r = await request(
    `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`,
    { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) },
    projectId ? "Couldn't move the chat" : "Couldn't take the chat out of the project",
  );
  if (!r.ok) return r;
  notifySessionsChanged();
  notifyProjectsChanged();
  return { ok: true };
}

export async function removeDocumentFromProject(id: string, docId: string): Promise<ProjectResult> {
  const r = await request(
    `${API}/${encodeURIComponent(id)}/documents/${encodeURIComponent(docId)}`,
    { method: "DELETE" },
    "Couldn't remove it from the project",
  );
  if (!r.ok) return r;
  notifyProjectsChanged();
  notifyDocumentsChanged();
  return { ok: true };
}

// ---------------------------------------------------------------------------
// The shared list store
// ---------------------------------------------------------------------------

export type ProjectsStatus = "loading" | "ready" | "error";

interface StoreState {
  projects: ProjectSummary[];
  status: ProjectsStatus;
}

let state: StoreState = { projects: [], status: "loading" };
let loaded = false;
let inFlight: Promise<void> | null = null;
let eventUnsub: (() => void) | null = null;
const listeners = new Set<() => void>();

function emit(next: StoreState) {
  state = next;
  for (const l of listeners) l();
}

function load(): Promise<void> {
  if (inFlight) return inFlight;
  // A background refresh keeps the list on screen; only a first load shows
  // the loading state, and only a first load can fail into "error".
  if (!loaded && state.status !== "loading") emit({ ...state, status: "loading" });
  inFlight = (async () => {
    try {
      const res = await fetchWithAuth(API);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as { projects?: unknown };
      const projects = (Array.isArray(body?.projects) ? body.projects : [])
        .map(normalizeProject)
        .filter((p): p is Project => p !== null)
        .sort(byRecent);
      loaded = true;
      emit({ projects, status: "ready" });
    } catch {
      if (!loaded) emit({ projects: [], status: "error" });
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}


// The store outlives a sign-out: without this, the next person to sign in on
// the same tab would briefly see the previous user's projects until a refetch.
let authUid: string | null | undefined;
let authUnsub: (() => void) | null = null;
function watchAuth() {
  if (authUnsub) return;
  authUnsub = subscribeToAuthState((user) => {
    const uid = user?.uid ?? null;
    if (authUid !== undefined && uid !== authUid) {
      loaded = false;
      inFlight = null;
      emit({ projects: [], status: "loading" });
      if (uid && listeners.size > 0) void load();
    }
    authUid = uid;
  });
}

function subscribe(listener: () => void): () => void {
  watchAuth();
  listeners.add(listener);
  if (listeners.size === 1) {
    eventUnsub = subscribeProjectsChanged(() => void load());
    if (!loaded) void load();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      eventUnsub?.();
      eventUnsub = null;
    }
  };
}

const getSnapshot = () => state;

/** Test-only: forget everything so each test starts from a cold store. */
export function __resetProjectsStore(): void {
  authUnsub?.();
  authUnsub = null;
  authUid = undefined;
  state = { projects: [], status: "loading" };
  loaded = false;
  inFlight = null;
  eventUnsub?.();
  eventUnsub = null;
  listeners.clear();
}

/** The signed-in user's projects, most recently opened first. */
export function useProjects(): { projects: ProjectSummary[]; status: ProjectsStatus; retry: () => void } {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const retry = useCallback(() => {
    loaded = false;
    void load();
  }, []);
  return { projects: snap.projects, status: snap.status, retry };
}

// ---------------------------------------------------------------------------
// One project's home
// ---------------------------------------------------------------------------

export type ProjectStatus = "idle" | "loading" | "ready" | "error" | "not_found";

/** One project by id. A null id (no project in scope) fetches nothing: "idle". */
export function useProject(id: string | null): { project: Project | null; status: ProjectStatus; retry: () => void } {
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState<ProjectStatus>(id ? "loading" : "idle");
  const loadedRef = useRef(false);

  const fetch_ = useCallback(async () => {
    if (!id) {
      setProject(null);
      setStatus("idle");
      return;
    }
    if (!loadedRef.current) setStatus("loading");
    try {
      const res = await fetchWithAuth(`${API}/${encodeURIComponent(id)}`);
      if (res.status === 404) {
        setProject(null);
        setStatus("not_found");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const p = normalizeProject(await res.json());
      if (!p) throw new Error("unreadable");
      loadedRef.current = true;
      setProject(p);
      setStatus("ready");
    } catch {
      if (!loadedRef.current) setStatus("error");
    }
  }, [id]);

  useEffect(() => {
    // A different project: never show the previous one's name meanwhile.
    loadedRef.current = false;
    setProject(null);
    void fetch_();
  }, [fetch_]);
  useEffect(() => subscribeProjectsChanged(() => void fetch_()), [fetch_]);

  return { project, status, retry: fetch_ };
}

interface Paged<T> {
  items: T[];
  status: "idle" | "loading" | "ready" | "error";
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreFailed: boolean;
  loadMore: () => void;
  retry: () => void;
}

function usePaged<T extends object>(
  url: string | null,
  key: "documents" | "sessions",
  idOf: (item: T) => string,
  subscriptions: Array<(h: () => void) => () => void>,
): Paged<T> {
  const [items, setItems] = useState<T[]>([]);
  // "idle": no url (e.g. a chat that isn't in a project) — nothing to fetch.
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">(url ? "loading" : "idle");
  const [cursor, setCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreFailed, setLoadMoreFailed] = useState(false);
  const loadedRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const fetchFirst = useCallback(() => {
    abortRef.current?.abort();
    if (!url) {
      setItems([]);
      setCursor(null);
      setStatus("idle");
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    if (!loadedRef.current) setStatus("loading");
    fetchWithAuth(url, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Record<string, unknown>>;
      })
      .then((body) => {
        loadedRef.current = true;
        const list = Array.isArray(body?.[key]) ? (body[key] as T[]) : [];
        setItems(list);
        setCursor(typeof body?.next_cursor === "string" && body.next_cursor ? body.next_cursor : null);
        setLoadMoreFailed(false);
        setStatus("ready");
      })
      .catch((err: Error) => {
        if (err.name === "AbortError") return;
        if (!loadedRef.current) setStatus("error");
      });
  }, [url, key]);

  const loadMore = useCallback(() => {
    if (!url || !cursor || isLoadingMore) return;
    setIsLoadingMore(true);
    setLoadMoreFailed(false);
    const sep = url.includes("?") ? "&" : "?";
    fetchWithAuth(`${url}${sep}cursor=${encodeURIComponent(cursor)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Record<string, unknown>>;
      })
      .then((body) => {
        const more = Array.isArray(body?.[key]) ? (body[key] as T[]) : [];
        setItems((prev) => {
          const seen = new Set(prev.map(idOf));
          return [...prev, ...more.filter((m) => !seen.has(idOf(m)))];
        });
        setCursor(typeof body?.next_cursor === "string" && body.next_cursor ? body.next_cursor : null);
      })
      .catch(() => setLoadMoreFailed(true))
      .finally(() => setIsLoadingMore(false));
  }, [url, key, cursor, isLoadingMore, idOf]);

  useEffect(() => {
    // A different list (another project): never show the previous one's rows.
    loadedRef.current = false;
    setItems([]);
    setCursor(null);
    fetchFirst();
    return () => abortRef.current?.abort();
  }, [fetchFirst]);

  useEffect(() => {
    const unsubs = subscriptions.map((sub) => sub(fetchFirst));
    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the subscription list is static per hook
  }, [fetchFirst]);

  return { items, status, hasMore: cursor !== null, isLoadingMore, loadMoreFailed, loadMore, retry: fetchFirst };
}

const docId = (d: ProjectDocument) => d.id;
const sessionId = (s: ProjectSession) => s.session_id;

/** The project's documents, newest first, metadata only. `null` → idle. */
export function useProjectDocuments(id: string | null) {
  const paged = usePaged<ProjectDocument>(id ? `${API}/${encodeURIComponent(id)}/documents` : null, "documents", docId, [
    subscribeProjectsChanged,
    subscribeDocumentsChanged,
  ]);
  return { ...paged, documents: paged.items };
}

/** The project's chats: explicit ∪ derived, newest first. `null` → idle. */
export function useProjectSessions(id: string | null) {
  const paged = usePaged<ProjectSession>(
    id ? `${API}/${encodeURIComponent(id)}/sessions?page_size=30` : null,
    "sessions",
    sessionId,
    [subscribeProjectsChanged, subscribeSessionsChanged],
  );
  // Tolerate a snake_case `linked_document` from the backend.
  const sessions = paged.items.map((s) => {
    const raw = s as ProjectSession & { linked_document?: { id: string; filename: string } | null };
    return raw.linkedDocument === undefined && raw.linked_document !== undefined
      ? { ...raw, linkedDocument: raw.linked_document }
      : raw;
  });
  return { ...paged, sessions };
}
