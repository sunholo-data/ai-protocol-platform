// v6.34.0 DOC-WORKBENCH P1 — "less clicks to see the document".
//
// A generated document used to announce itself as a result card. To actually
// read the thing they had just asked for, the user opened the sidebar, found the
// Created folder and clicked the file: three deliberate actions to reach the
// document the assistant had already made.
//
// The platform already knows how to mount a document — `handleDocClick` is the
// path an upload, an import-by-reference and a sidebar click all take, and it
// pulls the workbench to the Document tab. Generation simply never called it.
//
// Two decisions are encoded here (2026-09-01):
//
//   1. ONE TAB. A created document opens as a *document*, and the artifact does
//      NOT also get a "Result" tab. Two tabs for one document is worse than
//      none — the card is a receipt, the document is the thing.
//
//   2. ...unless there is no document. A FAILED generation carries no docId, so
//      there is nothing to open; its card is the only thing standing between the
//      user and a silent non-event, and it keeps its tab (principle #8).
//
// The docId is already encoded in the surfaceId (`created_document:<docId>`, set
// by `_document_surface` in backend/adk/a2ui_document_render.py), so nothing
// here has to reach into the surface data model.
//
// Pure functions, extracted for the same reason as `workbenchFocus.ts`: the
// rule is testable without rendering the whole chat page.

/** Artifact `kind` the backend attaches to a generated document. */
export const DOCUMENT_ARTIFACT_KIND = "document";

/** Surface id prefix — mirrors `DOCUMENT_SURFACE_ID` in the backend mapping. */
export const DOCUMENT_SURFACE_PREFIX = "created_document";

/** The minimum an artifact entry must expose for the rules below. */
export interface DocumentArtifactLike {
  surfaceId: string;
  kind?: string;
  title?: string;
}

/** A created document ready to mount in the workbench. */
export interface CreatedDocumentRef {
  /** Firestore `parsed_documents` id — what `handleDocClick` keys tabs on. */
  id: string;
  /** Friendly filename, straight from the artifact title (CLAUDE.md #9). */
  filename: string;
  /** Extension without the dot, for the tab's format chip. */
  format: string;
}

/**
 * The docId inside a created-document surfaceId, or `""` when there isn't one.
 *
 * `created_document:abc-123` → `abc-123`; a bare `created_document` (the
 * failure surface) → `""`, which is what marks "nothing to open".
 */
export function documentIdFromSurfaceId(surfaceId: string): string {
  if (!surfaceId.startsWith(`${DOCUMENT_SURFACE_PREFIX}:`)) return "";
  return surfaceId.slice(DOCUMENT_SURFACE_PREFIX.length + 1).trim();
}

/** Extension of a filename, lowercased, without the dot. `""` if there is none. */
export function formatFromFilename(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot <= 0 || dot === filename.length - 1) return "";
  return filename.slice(dot + 1).toLowerCase();
}

/**
 * True when this artifact is a created document that CAN be opened.
 *
 * A failed generation is `kind: "document"` too, but carries no docId — it is
 * deliberately not openable, and keeps its card so the error is visible.
 */
export function isOpenableDocumentArtifact(a: DocumentArtifactLike): boolean {
  return a.kind === DOCUMENT_ARTIFACT_KIND && documentIdFromSurfaceId(a.surfaceId) !== "";
}

/**
 * Artifacts that should still render as workbench tabs.
 *
 * Drops openable documents (they become Document tabs instead — decision 1) and
 * keeps everything else, INCLUDING failed generations (decision 2).
 */
export function artifactsExcludingOpenableDocuments<T extends DocumentArtifactLike>(
  artifacts: readonly T[],
): T[] {
  return artifacts.filter((a) => !isOpenableDocumentArtifact(a));
}

/**
 * Documents that have arrived and not yet been opened this session.
 *
 * `seen` is **mutated** — every returned document is recorded, so a re-emission
 * of the same surface does not reopen it and does not yank the user back to a
 * document they had navigated away from. Same "never seen in this session"
 * semantics as `nextFocusedResult`, and for the same reason: `listArtifacts()`
 * drops a surface for as long as it is being re-registered, so comparing
 * against the previous render treats a blink as a fresh arrival.
 *
 * Order is preserved, so several documents created in one turn open in the
 * order they were made and the LAST one ends up focused.
 */
export function newlyCreatedDocuments(
  artifacts: readonly DocumentArtifactLike[],
  seen: Set<string>,
): CreatedDocumentRef[] {
  const out: CreatedDocumentRef[] = [];
  for (const a of artifacts) {
    if (!isOpenableDocumentArtifact(a)) continue;
    if (seen.has(a.surfaceId)) continue;
    seen.add(a.surfaceId);
    const filename = (a.title || "").trim() || "Document";
    out.push({
      id: documentIdFromSurfaceId(a.surfaceId),
      filename,
      format: formatFromFilename(filename),
    });
  }
  return out;
}
