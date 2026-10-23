"use client";

/**
 * Folder / document management calls for "My Documents": rename a folder,
 * delete an (empty) folder, move or rename a document.
 *
 * Every call resolves to a result the caller MUST render (NEVER SILENT):
 * `{ ok: true }` or `{ ok: false, message, code? }` where `message` is the
 * server's human-readable text when it sent one. Success fires
 * `notifyDocumentsChanged()` so the API-fallback lists refetch (the Firestore
 * listeners refresh on their own).
 */

import { useCallback, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
import { FLOATING_SURFACE } from "@/lib/surfaces";

export type ActionResult =
  | { ok: true }
  | { ok: false; message: string; code?: string };

export const MENU_CONTENT_CLASS = `${FLOATING_SURFACE} min-w-[9rem] max-h-72 overflow-y-auto p-1 text-xs`;
export const MENU_ITEM_CLASS =
  "flex cursor-pointer select-none items-center rounded px-2 py-1 outline-none data-[highlighted]:bg-accent data-[state=open]:bg-accent data-[disabled]:opacity-50";

/** MIME type a dragged document row carries (payload: id, folderId, filename). */
export const DOC_DRAG_TYPE = "application/x-aitana-doc";

export interface DraggedDoc {
  id: string;
  folderId?: string;
  /** For feedback only ("Added EDP.pdf to …") — never used as an id. */
  filename?: string;
}

/** Parse a dragged document payload; null when the drag isn't one of ours.
 * Shared by folder drop targets (move) and project drop targets (add). */
export function readDragDoc(dt: DataTransfer | null): DraggedDoc | null {
  const raw = dt?.getData(DOC_DRAG_TYPE);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { id?: unknown; folderId?: unknown; filename?: unknown };
    if (typeof parsed.id !== "string" || !parsed.id) return null;
    return {
      id: parsed.id,
      folderId: typeof parsed.folderId === "string" ? parsed.folderId : undefined,
      filename: typeof parsed.filename === "string" && parsed.filename ? parsed.filename : undefined,
    };
  } catch {
    return null;
  }
}

async function readError(res: Response, fallback: string): Promise<{ message: string; code?: string }> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = body?.detail;
  if (typeof detail === "string" && detail) return { message: detail };
  if (detail && typeof detail === "object") {
    const d = detail as { code?: unknown; message?: unknown };
    return {
      message: typeof d.message === "string" && d.message ? d.message : `${fallback} (HTTP ${res.status}).`,
      code: typeof d.code === "string" ? d.code : undefined,
    };
  }
  return { message: `${fallback} (HTTP ${res.status}).` };
}

async function call(url: string, init: RequestInit, fallback: string): Promise<ActionResult> {
  let res: Response;
  try {
    res = await fetchWithAuth(url, init);
  } catch {
    return { ok: false, message: `${fallback} (network error).` };
  }
  if (!res.ok) return { ok: false, ...(await readError(res, fallback)) };
  notifyDocumentsChanged();
  return { ok: true };
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function renameFolder(folderId: string, name: string): Promise<ActionResult> {
  return call(
    `/api/proxy/api/folders/${encodeURIComponent(folderId)}`,
    { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ name }) },
    "Couldn't rename the folder",
  );
}

export function deleteFolder(folderId: string): Promise<ActionResult> {
  return call(
    `/api/proxy/api/folders/${encodeURIComponent(folderId)}`,
    { method: "DELETE" },
    "Couldn't delete the folder",
  );
}

export function moveDocument(docId: string, folderId: string, replace = false): Promise<ActionResult> {
  return call(
    `/api/proxy/api/documents/${encodeURIComponent(docId)}`,
    {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(replace ? { folder_id: folderId, replace: true } : { folder_id: folderId }),
    },
    "Couldn't move the document",
  );
}

export type RenameResult = { ok: true; name: string } | { ok: false; message: string; code?: string };

/**
 * Rename a document (its display name). The server keeps the extension: a name
 * typed without it gets it appended, so the name to show is the one it returns.
 */
export async function renameDocument(docId: string, filename: string): Promise<RenameResult> {
  const fallback = "Couldn't rename the document";
  let res: Response;
  try {
    res = await fetchWithAuth(`/api/proxy/api/documents/${encodeURIComponent(docId)}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ filename }),
    });
  } catch {
    return { ok: false, message: `${fallback} (network error).` };
  }
  if (!res.ok) return { ok: false, ...(await readError(res, fallback)) };
  const body = (await res.json().catch(() => null)) as { originalFilename?: unknown } | null;
  notifyDocumentsChanged();
  return { ok: true, name: typeof body?.originalFilename === "string" && body.originalFilename ? body.originalFilename : filename };
}

interface MoveConflict {
  docId: string;
  folderId: string;
  message: string;
}

/**
 * Move flow shared by the "Move to…" menu and drag-and-drop: pending state,
 * a visible error, and the FILENAME_EXISTS confirm (Replace retries with
 * `replace: true`; Cancel does nothing). There is deliberately no "keep both".
 */
export function useMoveDocument() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<MoveConflict | null>(null);

  const run = useCallback(async (docId: string, folderId: string, replace: boolean) => {
    setPending(true);
    setError(null);
    setConflict(null);
    const result = await moveDocument(docId, folderId, replace);
    setPending(false);
    if (result.ok) return;
    if (result.code === "FILENAME_EXISTS" && !replace) {
      setConflict({ docId, folderId, message: result.message });
    } else {
      setError(result.message);
    }
  }, []);

  const move = useCallback((docId: string, folderId: string) => run(docId, folderId, false), [run]);
  const confirmReplace = useCallback(() => {
    if (conflict) void run(conflict.docId, conflict.folderId, true);
  }, [conflict, run]);
  const cancel = useCallback(() => setConflict(null), []);
  const dismissError = useCallback(() => setError(null), []);

  return { move, pending, error, conflict, confirmReplace, cancel, dismissError };
}

/** "Make searchable": a private knowledge base fed by this folder, back-filled
 * in the background (progress shows on the folder badge). */
export function makeFolderSearchable(folderId: string): Promise<ActionResult> {
  return call(
    "/api/proxy/api/knowledge-bases",
    { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ folderId }) },
    "Couldn't make the folder searchable",
  );
}

/** "Stop searching": delete the folder's knowledge base and its index. */
export function stopFolderSearch(kbId: string): Promise<ActionResult> {
  return call(
    `/api/proxy/api/knowledge-bases/${encodeURIComponent(kbId)}`,
    { method: "DELETE" },
    "Couldn't stop searching the folder",
  );
}
