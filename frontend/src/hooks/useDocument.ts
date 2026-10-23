"use client";

import { firestoreTimestampToIso, getFirestoreDb } from "@/lib/firebase";
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeDocumentsChanged } from "@/lib/documentEvents";
import {
  doc as fsDoc,
  onSnapshot,
  type DocumentData,
} from "firebase/firestore";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Block } from "@/components/document/BlocksRenderer";
import type { ParseStatus } from "@/hooks/useDocBrowser";

export interface DocSummary {
  totalBlocks: number;
  headings: number;
  tables: number;
  images: number;
  changes: number;
}

/**
 * What last styled this document, as recorded by the backend at ingest time.
 *
 * Friendly name only (CLAUDE.md #9) — `generatedFrom.templateDocId` is the
 * backend's addressing and never reaches the UI. Null for a document that was
 * uploaded or generated without a template.
 */
export interface DocumentTemplate {
  /** Empty for a record written before the filename was stored — the document
   *  IS styled, we just cannot name the template. Say that, rather than
   *  claiming it is unstyled. */
  filename: string;
  /** The converter's own verdict, so the UI can distinguish "applied, nothing
   *  to carry" from "did not apply". */
  applied: boolean;
  partsCarried: number;
}

export interface DocumentDetail {
  id: string;
  originalFilename: string;
  sourceFormat: string;
  parseStatus: ParseStatus;
  parseError: string | null;
  sourceUrl: string | null;
  parsedAt: string | null;
  updatedAt: string | null;
  summary: DocSummary | null;
  blocks: Block[];
  /** Which template this document is currently styled from, if any. */
  template: DocumentTemplate | null;
}

interface UseDocumentReturn {
  doc: DocumentDetail | null;
  isLoading: boolean;
  /** A visible message when the document can't be shown (NEVER SILENT). */
  error: string | null;
  /** Re-run the load — after an error, or after an edit. On the Firestore path it re-subscribes. */
  retry: () => void;
}

function mapTemplate(data: DocumentData): DocumentTemplate | null {
  const gen = data.generatedFrom as Record<string, unknown> | undefined;
  const filename = typeof gen?.templateFilename === "string" ? gen.templateFilename : "";
  // Records written before templateFilename existed carry only the doc-id. That
  // document really is styled from a template, so reporting "no template" would
  // be wrong — but a UUID is not a name a human can use (CLAUDE.md #9). Report
  // the state, leave the name empty, and let the panel word it.
  const hasTemplate = Boolean(filename) || Boolean(gen?.templateDocId);
  if (!hasTemplate) return null;
  return {
    filename,
    applied: gen?.referenceDocApplied !== false,
    partsCarried: typeof gen?.templatePartsCarried === "number" ? gen.templatePartsCarried : 0,
  };
}

/** While the document is still parsing, the API fallback re-polls at this
 * interval. No polling once it is parsed or failed. */
export const DOCUMENT_POLL_MS = 3000;

const PENDING_STATUSES: ReadonlySet<ParseStatus> = new Set(["pending", "pending_ai_extraction"]);

/**
 * Normalise a timestamp to `Date.toISOString()` form. Firestore hands back a
 * Timestamp (or the ISO string the pipeline wrote); the API hands back an ISO
 * string serialised by Python (`+00:00`, microseconds). Routing both through
 * here makes the two read paths produce byte-identical values.
 */
function toIso(value: unknown): string | null {
  const raw = firestoreTimestampToIso(value);
  if (!raw) return null;
  const t = Date.parse(raw);
  return Number.isNaN(t) ? raw : new Date(t).toISOString();
}

/** One mapper for both read paths (Firestore snapshot data and the API JSON). */
function mapDoc(id: string, data: DocumentData): DocumentDetail {
  const summary =
    typeof data.blockCount === "number"
      ? {
          totalBlocks: data.blockCount ?? 0,
          headings:
            typeof data.headingCount === "number" ? data.headingCount : 0,
          tables: typeof data.tableCount === "number" ? data.tableCount : 0,
          images: typeof data.imageCount === "number" ? data.imageCount : 0,
          changes: typeof data.changeCount === "number" ? data.changeCount : 0,
        }
      : null;

  return {
    id: (data.id as string) ?? id,
    originalFilename: (data.originalFilename as string) ?? "",
    sourceFormat: (data.sourceFormat as string) ?? "",
    parseStatus: (data.parseStatus as ParseStatus) ?? "pending",
    parseError: (data.parseError as string | null) ?? null,
    sourceUrl: (data.sourceUrl as string | null) ?? null,
    parsedAt: toIso(data.parsedAt),
    updatedAt: toIso(data.updatedAt),
    summary,
    blocks: Array.isArray(data.blocks) ? (data.blocks as Block[]) : [],
    template: mapTemplate(data),
  };
}

/** HTTP status → the message the viewer shows. */
function apiErrorMessage(status: number): string {
  if (status === 404) return "Document not found.";
  if (status === 403) return "You don't have access to this document.";
  return `Couldn't load this document (HTTP ${status}).`;
}

/**
 * Live view of one parsed document.
 *
 * Firestore `onSnapshot` when the browser has a Firebase session. Without one
 * (tailnet / local mode, anonymous groups) or when the listener is denied, the
 * record is read from the backend via `/api/proxy/api/documents/{id}` — the
 * owner-checked route, which authenticates in every mode — refetched on the
 * documentEvents bus and polled only while the parse is still pending.
 */
export function useDocument(docId: string | null): UseDocumentReturn {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiMode, setApiMode] = useState<boolean>(
    () => !getFirestoreDb() || isAnonymousGroupAuthMode(),
  );
  const [refreshTick, setRefreshTick] = useState(0);
  // Which document the API fallback has on screen — a poll of the same doc
  // keeps it visible instead of flashing the loading state.
  const apiLoadedIdRef = useRef<string | null>(null);

  // A new document starts back on the preferred path.
  useEffect(() => {
    setApiMode(!getFirestoreDb() || isAnonymousGroupAuthMode());
  }, [docId]);

  // ── Firestore path ────────────────────────────────────────────────────────
  useEffect(() => {
    if (apiMode) return;
    if (!docId) {
      setDoc(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const db = getFirestoreDb();
    if (!db || isAnonymousGroupAuthMode()) {
      setApiMode(true);
      return;
    }

    setIsLoading(true);
    setError(null);

    const ref = fsDoc(db, "parsed_documents", docId);
    const unsub = onSnapshot(
      ref,
      (snap) => {
        if (!snap.exists()) {
          setDoc(null);
          setError("Document not found.");
          setIsLoading(false);
          return;
        }
        setDoc(mapDoc(snap.id, snap.data()));
        setError(null);
        setIsLoading(false);
      },
      (err) => {
        const code = (err as { code?: string } | null)?.code ?? "unknown";
        console.warn(`[useDocument] Firestore listener failed (${code}); falling back to the API`);
        setApiMode(true);
      },
    );

    return unsub;
  }, [docId, apiMode, refreshTick]);

  // ── API fallback ──────────────────────────────────────────────────────────

  // Refetch when the library changes (a re-parse or upload finishing).
  useEffect(() => {
    if (!apiMode || !docId) return;
    return subscribeDocumentsChanged(() => setRefreshTick((t) => t + 1));
  }, [apiMode, docId]);

  useEffect(() => {
    if (!apiMode) return;
    if (!docId) {
      setDoc(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    if (apiLoadedIdRef.current !== docId) {
      apiLoadedIdRef.current = null;
      setDoc(null);
      setIsLoading(true);
    }
    (async () => {
      let res: Response;
      try {
        res = await fetchWithAuth(`/api/proxy/api/documents/${encodeURIComponent(docId)}`);
      } catch {
        if (!cancelled) {
          setError("Couldn't load this document (network error).");
          setIsLoading(false);
        }
        return;
      }
      if (cancelled) return;
      if (!res.ok) {
        apiLoadedIdRef.current = null;
        setDoc(null);
        setError(apiErrorMessage(res.status));
        setIsLoading(false);
        return;
      }
      try {
        const body = (await res.json()) as DocumentData;
        if (cancelled) return;
        setDoc(mapDoc(docId, body));
        apiLoadedIdRef.current = docId;
        setError(null);
      } catch {
        if (!cancelled) setError("Couldn't read this document (bad response).");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiMode, docId, refreshTick]);

  // Poll while the parse is still running; stop once it settles.
  const pending = !!doc && PENDING_STATUSES.has(doc.parseStatus);
  useEffect(() => {
    if (!apiMode || !pending || error) return;
    const timer = setTimeout(() => setRefreshTick((t) => t + 1), DOCUMENT_POLL_MS);
    return () => clearTimeout(timer);
  }, [apiMode, pending, error, doc]);

  const retry = useCallback(() => {
    setError(null);
    setRefreshTick((t) => t + 1);
  }, []);

  return { doc, isLoading, error, retry };
}
