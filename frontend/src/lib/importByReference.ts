// DOC-IMPORT-REF M3 — shared helper for SkillExamplesPicker.onPickExample
// and GCSFileBrowser.onPick. POSTs to /api/documents/import-by-reference
// and returns a ParsedDocument-shaped object the chat page can pass to
// handleDocClick to mount in the workbench. Replaces the 4.5 synthetic-
// chat-message hack that bypassed AILANG Parse.
//
// See docs/design/v6.4.0/document-import-by-reference.md for the cache
// cascade (L2 self-dedup → L4 sentinel-clone → L3 fresh parse).

import { fetchWithAuth } from "@/lib/apiClient";
import type { ParsedDocument } from "@/hooks/useDocBrowser";

export interface ImportByReferenceResult {
  /** A ParsedDocument ready to pass to handleDocClick for workbench mount. */
  doc: ParsedDocument;
}

export interface ImportByReferenceError {
  /** HTTP status code (0 for network errors). */
  status: number;
  /** Human-readable failure surface for toast / console. */
  message: string;
}

interface BackendResponse {
  docId: string;
  status: string;
  originalFilename: string;
  blocksCount?: number;
  storagePath?: string;
  folderId?: string | null;
  error?: string;
}

/**
 * Parse a GCS-resident document by reference via the backend route.
 * Returns either a workbench-mountable ParsedDocument or a structured
 * error. Never throws — callers can `if (!result.doc)` and surface the
 * error in a toast.
 */
export async function importByReference(
  bucket: string,
  objectName: string,
  skillId: string,
): Promise<ImportByReferenceResult | ImportByReferenceError> {
  let res: Response;
  try {
    res = await fetchWithAuth("/api/proxy/api/documents/import-by-reference", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bucket, object: objectName, skillId }),
    });
  } catch (err) {
    return { status: 0, message: err instanceof Error ? err.message : String(err) };
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // body wasn't JSON — keep the HTTP-status fallback
    }
    return { status: res.status, message: detail };
  }
  const body = (await res.json()) as BackendResponse;
  // Only treat an explicit "failed" status as a fatal import error. Other
  // statuses ("pending", "pending_ai_extraction", "parsed") are all valid
  // outcomes — mount the doc and let the workbench paint the parseStatus.
  // The Firestore listener on the document will surface block updates once
  // an async parse completes.
  if (body.status === "failed") {
    return {
      status: res.status,
      message: body.error ?? "Parse failed",
    };
  }
  // Source format from the object's extension — the backend stores it the
  // same way (see import_by_reference.py: PurePosixPath(object).suffix.lstrip(".")).
  const sourceFormat = objectName.includes(".") ? (objectName.split(".").pop() ?? "") : "";
  // Cast status to the ParseStatus union. Backend returns one of
  // "pending" | "pending_ai_extraction" | "parsed" | "failed"; we already
  // handled "failed" above.
  return {
    doc: {
      id: body.docId,
      originalFilename: body.originalFilename,
      sourceFormat,
      parseStatus: body.status as "pending" | "pending_ai_extraction" | "parsed",
      parseError: null,
      folderId: body.folderId ?? "",
      userId: "",
      blockCount: body.blocksCount ?? null,
      hasA2ui: false,
    },
  };
}

export function isImportError(
  r: ImportByReferenceResult | ImportByReferenceError,
): r is ImportByReferenceError {
  return (r as ImportByReferenceError).message !== undefined;
}
