"use client";

import { firestoreTimestampToIso, getFirestoreDb } from "@/lib/firebase";
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
import {
  doc as fsDoc,
  onSnapshot,
  type DocumentData,
} from "firebase/firestore";
import { useEffect, useState } from "react";
import type { Block } from "@/components/document/BlocksRenderer";
import type { ParseStatus } from "@/hooks/useDocBrowser";

export interface DocSummary {
  totalBlocks: number;
  headings: number;
  tables: number;
  images: number;
  changes: number;
}

export interface DocumentDetail {
  id: string;
  originalFilename: string;
  sourceFormat: string;
  parseStatus: ParseStatus;
  parseError: string | null;
  sourceUrl: string | null;
  parsedAt: string | null;
  summary: DocSummary | null;
  blocks: Block[];
}

interface UseDocumentReturn {
  doc: DocumentDetail | null;
  isLoading: boolean;
  error: string | null;
}

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
    parsedAt: firestoreTimestampToIso(data.parsedAt),
    summary,
    blocks: Array.isArray(data.blocks) ? (data.blocks as Block[]) : [],
  };
}

export function useDocument(docId: string | null): UseDocumentReturn {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!docId) {
      setDoc(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    const db = getFirestoreDb();
    if (!db || isAnonymousGroupAuthMode()) {
      setError("Document preview unavailable.");
      setIsLoading(false);
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
        console.error("[useDocument] snapshot error", err);
        setError("Document preview unavailable.");
        setIsLoading(false);
      },
    );

    return unsub;
  }, [docId]);

  return { doc, isLoading, error };
}
