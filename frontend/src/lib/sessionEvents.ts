"use client";

/**
 * Tiny pub/sub for "the session list has changed" events.
 *
 * Two panels render session lists from separate hooks: `useSkillSessions`
 * (skill-level left sidebar) and `useDocumentSessions` (per-document
 * Conversations panel under each doc). Each owns its own React state,
 * so without coordination a delete or rename in one panel leaves the
 * other showing stale data.
 *
 * Both hooks subscribe to ``aitana:sessions-changed``; any code that
 * mutates session state (DELETE, PATCH rename, future create-from-CLI)
 * calls ``notifySessionsChanged()`` AFTER the backend commit succeeds.
 *
 * SSR-safe: the dispatch / subscribe paths bail early when ``window``
 * is undefined (Next.js server render).
 */

const SESSIONS_CHANGED_EVENT = "aitana:sessions-changed";

export interface SessionsChangedDetail {
  /** When set, identifies the session that was just deleted. The chat
   * page's `ChatShell` listens for this and clears the URL when the
   * deleted id matches the currently-viewed session — defense in depth
   * against stale closure props in per-panel delete handlers. */
  deletedSessionId?: string;
}

/** Fire after a successful session mutation so all listening hooks refetch. */
export function notifySessionsChanged(detail?: SessionsChangedDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<SessionsChangedDetail>(SESSIONS_CHANGED_EVENT, {
      detail: detail ?? {},
    }),
  );
}

/**
 * Subscribe a refetch handler. Returns an unsubscribe function suitable
 * for a useEffect cleanup. Handler runs synchronously when the event
 * fires (window CustomEvent semantics).
 *
 * The handler shape ignores the event payload by default; consumers that
 * need the ``deletedSessionId`` payload should subscribe with a typed
 * handler via ``window.addEventListener`` directly — see ``ChatShell``.
 */
export function subscribeSessionsChanged(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(SESSIONS_CHANGED_EVENT, handler);
  return () => window.removeEventListener(SESSIONS_CHANGED_EVENT, handler);
}

/** Subscribe with the typed event payload (for the ChatShell active-session
 * auto-clear path). Same SSR safety + cleanup signature as above. */
export function subscribeSessionsChangedDetailed(
  handler: (detail: SessionsChangedDetail) => void,
): () => void {
  if (typeof window === "undefined") return () => {};
  const wrapped = (e: Event) => {
    const detail =
      (e as CustomEvent<SessionsChangedDetail>).detail ?? ({} as SessionsChangedDetail);
    handler(detail);
  };
  window.addEventListener(SESSIONS_CHANGED_EVENT, wrapped);
  return () => window.removeEventListener(SESSIONS_CHANGED_EVENT, wrapped);
}
