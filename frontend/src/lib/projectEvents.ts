"use client";

/**
 * Tiny pub/sub for "the user's projects changed" — a project was created,
 * renamed or deleted, or a document was added to / removed from one.
 *
 * Same shape as `documentEvents.ts` / `sessionEvents.ts`. Every surface that
 * shows projects (the sidebar section, the "Add to project…" menus, a project
 * home) refetches on it, so a change made in one place shows everywhere.
 * SSR-safe: both paths bail when `window` is undefined.
 */

const PROJECTS_CHANGED_EVENT = "aitana:projects-changed";

/** Fire after a successful mutation of the user's projects. */
export function notifyProjectsChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PROJECTS_CHANGED_EVENT));
}

/** Subscribe a refetch handler; returns an unsubscribe for useEffect cleanup. */
export function subscribeProjectsChanged(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(PROJECTS_CHANGED_EVENT, handler);
  return () => window.removeEventListener(PROJECTS_CHANGED_EVENT, handler);
}
