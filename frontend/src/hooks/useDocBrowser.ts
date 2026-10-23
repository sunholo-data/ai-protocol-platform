"use client";

import { firestoreTimestampToIso, getFirestoreDb } from "@/lib/firebase";
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";
import { collection, onSnapshot, query, where } from "firebase/firestore";
import { useCallback, useEffect, useRef, useState } from "react";

export type ParseStatus =
  | "pending"
  | "pending_ai_extraction"
  | "parsed"
  | "failed";

export interface Folder {
  id: string;
  name: string;
  userId: string;
  docCount: number;
  parsedCount: number;
  createdAt?: string;
  /** A folder the assistant owns (e.g. "Created"), not one the user made. */
  system?: boolean;
  /** Set when the folder is searchable — the knowledge base it feeds. */
  knowledgeBaseId?: string | null;
}

export interface ParsedDocument {
  id: string;
  originalFilename: string;
  /** "generated" when the assistant produced it; "uploaded" otherwise. */
  source?: "uploaded" | "generated";
  sourceFormat: string;
  parseStatus: ParseStatus;
  parseError: string | null;
  folderId: string;
  userId: string;
  blockCount: number | null;
  hasA2ui: boolean;
  createdAt?: string;
  /** Last write to the record — a freshness token for cached previews. */
  updatedAt?: string;
  /** The projects this document is tagged into (projects design §5). */
  projectIds?: string[];
}

interface DocBrowserState {
  folders: Folder[];
  activeFolderId: string | null;
  documents: ParsedDocument[];
  searchQuery: string;
  filteredDocuments: ParsedDocument[];
  setActiveFolderId: (id: string | null) => void;
  setSearchQuery: (q: string) => void;
  /** True while the API fallback is loading folders for the first time. Always
   * false on the Firestore path (listeners have no "loading" notion here). */
  loading: boolean;
  /** A visible-error message when the API fallback failed (NEVER SILENT). */
  error: string | null;
  /** Re-run the API fallback load. No-op on the Firestore path. */
  retry: () => void;
}

/** While any document in view is still parsing, the API fallback re-polls at
 * this interval. No polling otherwise. */
export const PENDING_POLL_MS = 4000;

const PENDING_STATUSES: ReadonlySet<ParseStatus> = new Set(["pending", "pending_ai_extraction"]);

/** The Firestore client cannot be used in this browser: no Firebase app (tailnet
 * / local mode) or an anonymous-group user with no Firebase Auth session. */
function firestoreUnavailable(): boolean {
  return !getFirestoreDb() || isAnonymousGroupAuthMode();
}

function newestFirst(a: { createdAt?: string }, b: { createdAt?: string }): number {
  return (b.createdAt ?? "").localeCompare(a.createdAt ?? "");
}

/** Pick the newest folder the USER owns, else the first (see the Firestore
 * listener below for why "Created" must not be the default view). */
function defaultFolderId(loaded: Folder[]): string | null {
  return loaded.find((f) => !f.system)?.id ?? loaded[0]?.id ?? null;
}

function mapApiFolder(raw: Record<string, unknown>, uid: string): Folder {
  return {
    id: String(raw.id ?? ""),
    name: typeof raw.name === "string" && raw.name ? raw.name : "Unnamed",
    userId: typeof raw.userId === "string" ? raw.userId : uid,
    docCount: typeof raw.docCount === "number" ? raw.docCount : 0,
    parsedCount: typeof raw.parsedCount === "number" ? raw.parsedCount : 0,
    createdAt: firestoreTimestampToIso(raw.createdAt) ?? undefined,
    system: raw.system === true,
    knowledgeBaseId: typeof raw.knowledgeBaseId === "string" && raw.knowledgeBaseId ? raw.knowledgeBaseId : null,
  };
}

function mapApiDocument(raw: Record<string, unknown>, uid: string, folderId: string): ParsedDocument {
  const id = String(raw.id ?? raw.__id ?? "");
  return {
    id,
    originalFilename: typeof raw.originalFilename === "string" && raw.originalFilename ? raw.originalFilename : id,
    sourceFormat: typeof raw.sourceFormat === "string" ? raw.sourceFormat : "",
    parseStatus: ((raw.parseStatus as string | undefined) ?? "pending") as ParseStatus,
    parseError: typeof raw.parseError === "string" ? raw.parseError : null,
    folderId: typeof raw.folderId === "string" ? raw.folderId : folderId,
    source: raw.source === "generated" ? "generated" : "uploaded",
    userId: typeof raw.userId === "string" ? raw.userId : uid,
    blockCount: typeof raw.blockCount === "number" ? raw.blockCount : null,
    hasA2ui: !!raw.a2uiComponents,
    createdAt: firestoreTimestampToIso(raw.createdAt) ?? undefined,
    updatedAt: firestoreTimestampToIso(raw.updatedAt) ?? undefined,
  };
}

async function getJson(url: string, what: string): Promise<Record<string, unknown>> {
  let res: Response;
  try {
    res = await fetchWithAuth(url);
  } catch {
    throw new Error(`Couldn't load your ${what} (network error).`);
  }
  if (!res.ok) throw new Error(`Couldn't load your ${what} (HTTP ${res.status}).`);
  return (await res.json()) as Record<string, unknown>;
}

export function useDocBrowser(uid: string): DocBrowserState {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<ParsedDocument[]>([]);
  const [searchQuery, setSearchQuery] = useState("");

  // API fallback. The Firestore client SDK needs a Firebase session; without
  // one (tailnet / local mode, anonymous groups) or when a listener is denied,
  // the library is read from the backend via /api/proxy, which authenticates
  // in every mode. On a Firebase deployment `apiMode` stays false and the
  // listeners below are the only reads — no double fetch.
  const [apiMode, setApiMode] = useState<boolean>(() => firestoreUnavailable());
  const [refreshTick, setRefreshTick] = useState(0);
  const [foldersLoaded, setFoldersLoaded] = useState(false);
  const [foldersError, setFoldersError] = useState<string | null>(null);
  const [documentsError, setDocumentsError] = useState<string | null>(null);

  const fallBackToApi = useCallback((err: unknown) => {
    const code = (err as { code?: string } | null)?.code ?? "unknown";
    console.warn(`[useDocBrowser] Firestore listener failed (${code}); falling back to the API`);
    setApiMode(true);
  }, []);

  // Subscribe to the user's folders collection
  useEffect(() => {
    if (apiMode) return;
    const db = getFirestoreDb();
    // Anonymous-group users have no Firebase Auth user — Firestore rules would
    // deny the snapshot and produce repeating permission-denied console errors.
    if (!db || !uid || isAnonymousGroupAuthMode()) return;

    const foldersCol = collection(db, "users", uid, "folders");
    const unsub = onSnapshot(foldersCol, (snap) => {
      const loaded: Folder[] = snap.docs.map((d) => {
        const data = d.data();
        return {
          id: d.id,
          name: data.name ?? "Unnamed",
          userId: data.userId ?? uid,
          docCount: data.docCount ?? 0,
          parsedCount: data.parsedCount ?? 0,
          createdAt: firestoreTimestampToIso(data.createdAt) ?? undefined,
          system: data.system === true,
          knowledgeBaseId: typeof data.knowledgeBaseId === "string" && data.knowledgeBaseId ? data.knowledgeBaseId : null,
        };
      });
      loaded.sort((a, b) => (a.createdAt ?? "") > (b.createdAt ?? "") ? -1 : 1);
      setFolders(loaded);
      // Auto-select when folders load and nothing is selected. Folders are
      // sorted newest-first, so a plain `loaded[0]` would make the system
      // "Created" folder the default view the moment the assistant generates a
      // document — showing the user the assistant's output instead of their own
      // documents. Prefer the newest folder the USER owns; fall back to
      // `loaded[0]` only when every folder is a system one.
      setActiveFolderId((prev) => prev ?? (loaded.find((f) => !f.system)?.id ?? loaded[0]?.id ?? null));
    }, fallBackToApi);

    return unsub;
  }, [uid, apiMode, fallBackToApi]);

  // Subscribe to parsed_documents for the active folder
  const prevFolderIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (apiMode) return;
    if (prevFolderIdRef.current === activeFolderId) return;
    prevFolderIdRef.current = activeFolderId;

    if (!activeFolderId) {
      setDocuments([]);
      return;
    }

    const db = getFirestoreDb();
    if (!db || !uid || isAnonymousGroupAuthMode()) return;

    const docsQ = query(
      collection(db, "parsed_documents"),
      where("userId", "==", uid),
      where("folderId", "==", activeFolderId),
    );

    const unsub = onSnapshot(docsQ, (snap) => {
      const loaded: ParsedDocument[] = snap.docs.map((d) => {
        const data = d.data();
        return {
          id: d.id,
          originalFilename: data.originalFilename ?? d.id,
          sourceFormat: data.sourceFormat ?? "",
          parseStatus: (data.parseStatus ?? "pending") as ParseStatus,
          parseError: data.parseError ?? null,
          folderId: data.folderId ?? activeFolderId,
          source: data.source === "generated" ? "generated" : "uploaded",
          userId: data.userId ?? uid,
          blockCount: data.blockCount ?? null,
          hasA2ui: !!data.a2uiComponents,
          createdAt: firestoreTimestampToIso(data.createdAt) ?? undefined,
          updatedAt: firestoreTimestampToIso(data.updatedAt) ?? undefined,
        };
      });
      loaded.sort((a, b) => (a.createdAt ?? "") > (b.createdAt ?? "") ? -1 : 1);
      setDocuments(loaded);
    }, fallBackToApi);

    return unsub;
  }, [activeFolderId, uid, apiMode, fallBackToApi]);

  // ── API fallback ──────────────────────────────────────────────────────────

  // Refetch when the library changes (uploads finishing).
  useEffect(() => {
    if (!apiMode) return;
    return subscribeDocumentsChanged(() => setRefreshTick((t) => t + 1));
  }, [apiMode]);

  // Folders.
  useEffect(() => {
    if (!apiMode) return;
    let cancelled = false;
    getJson("/api/proxy/api/folders", "folders")
      .then((body) => {
        if (cancelled) return;
        const raw = Array.isArray(body.folders) ? (body.folders as Record<string, unknown>[]) : [];
        const loaded = raw.map((f) => mapApiFolder(f, uid)).filter((f) => f.id);
        loaded.sort(newestFirst);
        setFolders(loaded);
        setFoldersError(null);
        setActiveFolderId((prev) => (prev && loaded.some((f) => f.id === prev) ? prev : defaultFolderId(loaded)));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFoldersError(err instanceof Error ? err.message : "Couldn't load your folders.");
      })
      .finally(() => {
        if (!cancelled) setFoldersLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [apiMode, uid, refreshTick]);

  // Documents in the active folder.
  useEffect(() => {
    if (!apiMode) return;
    if (!activeFolderId) {
      setDocuments([]);
      setDocumentsError(null);
      return;
    }
    let cancelled = false;
    getJson(`/api/proxy/api/folders/${encodeURIComponent(activeFolderId)}/documents`, "documents")
      .then((body) => {
        if (cancelled) return;
        const raw = Array.isArray(body.documents) ? (body.documents as Record<string, unknown>[]) : [];
        const loaded = raw.map((d) => mapApiDocument(d, uid, activeFolderId)).filter((d) => d.id);
        loaded.sort(newestFirst);
        setDocuments(loaded);
        setDocumentsError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDocumentsError(err instanceof Error ? err.message : "Couldn't load your documents.");
      });
    return () => {
      cancelled = true;
    };
  }, [apiMode, uid, activeFolderId, refreshTick]);

  // While anything in view is still parsing, poll; stop once nothing is pending.
  const hasPending = documents.some((d) => PENDING_STATUSES.has(d.parseStatus));
  useEffect(() => {
    if (!apiMode || !hasPending) return;
    const timer = setTimeout(() => setRefreshTick((t) => t + 1), PENDING_POLL_MS);
    return () => clearTimeout(timer);
  }, [apiMode, hasPending, documents]);

  const retry = useCallback(() => {
    setFoldersError(null);
    setDocumentsError(null);
    setRefreshTick((t) => t + 1);
  }, []);

  // Clear the list only when the folder actually CHANGES. Both document loaders
  // re-run on a changed id only, so clearing on a re-select of the same folder
  // (a folder row toggled open again) left it empty for good — the folder pill
  // still said "11" and no files were listed.
  const activeFolderIdRef = useRef(activeFolderId);
  activeFolderIdRef.current = activeFolderId;
  const handleSetActiveFolderId = useCallback((id: string | null) => {
    if (id === activeFolderIdRef.current) return;
    setActiveFolderId(id);
    setDocuments([]);
  }, []);

  const lower = searchQuery.toLowerCase();
  const filteredDocuments =
    searchQuery.trim() === ""
      ? documents
      : documents.filter((d) =>
          d.originalFilename.toLowerCase().includes(lower),
        );

  return {
    folders,
    activeFolderId,
    documents,
    searchQuery,
    filteredDocuments,
    setActiveFolderId: handleSetActiveFolderId,
    setSearchQuery,
    loading: apiMode && !foldersLoaded && !foldersError,
    error: apiMode ? (foldersError ?? documentsError) : null,
    retry,
  };
}
