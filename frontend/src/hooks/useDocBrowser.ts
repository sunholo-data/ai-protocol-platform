"use client";

import { firestoreTimestampToIso, getFirestoreDb } from "@/lib/firebase";
import { isAnonymousGroupAuthMode } from "@/lib/anonymousGroupAuth";
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
}

export interface ParsedDocument {
  id: string;
  originalFilename: string;
  sourceFormat: string;
  parseStatus: ParseStatus;
  parseError: string | null;
  folderId: string;
  userId: string;
  blockCount: number | null;
  hasA2ui: boolean;
  createdAt?: string;
}

interface DocBrowserState {
  folders: Folder[];
  activeFolderId: string | null;
  documents: ParsedDocument[];
  searchQuery: string;
  filteredDocuments: ParsedDocument[];
  setActiveFolderId: (id: string | null) => void;
  setSearchQuery: (q: string) => void;
}

export function useDocBrowser(uid: string): DocBrowserState {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<ParsedDocument[]>([]);
  const [searchQuery, setSearchQuery] = useState("");

  // Subscribe to the user's folders collection
  useEffect(() => {
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
        };
      });
      loaded.sort((a, b) => (a.createdAt ?? "") > (b.createdAt ?? "") ? -1 : 1);
      setFolders(loaded);
      // Auto-select first folder when folders load and nothing is selected
      setActiveFolderId((prev) => prev ?? (loaded[0]?.id ?? null));
    });

    return unsub;
  }, [uid]);

  // Subscribe to parsed_documents for the active folder
  const prevFolderIdRef = useRef<string | null>(null);
  useEffect(() => {
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
          userId: data.userId ?? uid,
          blockCount: data.blockCount ?? null,
          hasA2ui: !!data.a2uiComponents,
          createdAt: firestoreTimestampToIso(data.createdAt) ?? undefined,
        };
      });
      loaded.sort((a, b) => (a.createdAt ?? "") > (b.createdAt ?? "") ? -1 : 1);
      setDocuments(loaded);
    });

    return unsub;
  }, [activeFolderId, uid]);

  const handleSetActiveFolderId = useCallback((id: string | null) => {
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
  };
}
