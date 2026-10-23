"use client";

/**
 * Tiny pub/sub for "the user's document library has changed" events.
 *
 * `useDocBrowser` normally sees new folders/documents through Firestore
 * listeners. When the browser has no Firebase session (tailnet / local mode,
 * anonymous groups) it falls back to the backend API, which has no push — so
 * anything that changes the library (an upload finishing, a folder batch
 * finishing) calls `notifyDocumentsChanged()` and the fallback refetches.
 *
 * Same shape as `sessionEvents.ts`. SSR-safe: both paths bail when `window`
 * is undefined.
 */

const DOCUMENTS_CHANGED_EVENT = "aitana:documents-changed";

/** Fire after a successful mutation of the user's documents/folders. */
export function notifyDocumentsChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(DOCUMENTS_CHANGED_EVENT));
}

/** Subscribe a refetch handler; returns an unsubscribe for useEffect cleanup. */
export function subscribeDocumentsChanged(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(DOCUMENTS_CHANGED_EVENT, handler);
  return () => window.removeEventListener(DOCUMENTS_CHANGED_EVENT, handler);
}
