/**
 * One request for many readers. Several components mount the same data hook
 * on one page (ShellChrome, and each useProjectChatHref), and each used to
 * fetch on its own: skills ×3 and clients/me ×3 per page load (2026-10-30).
 *
 * Calls with the same key within `maxAgeMs` share one promise — in flight or
 * settled. A failure is forgotten at once so the next reader retries. Keep the
 * window short for data the user edits; `forgetSharedRequests` drops entries.
 */
const entries = new Map<string, { at: number; promise: Promise<unknown> }>();

export function sharedRequest<T>(key: string, load: () => Promise<T>, maxAgeMs: number): Promise<T> {
  const now = Date.now();
  const hit = entries.get(key);
  if (hit && now - hit.at < maxAgeMs) return hit.promise as Promise<T>;
  const promise = load();
  entries.set(key, { at: now, promise });
  promise.catch(() => {
    if (entries.get(key)?.promise === promise) entries.delete(key);
  });
  return promise;
}

/** Drop shared entries whose key starts with `prefix` (all when empty). */
export function forgetSharedRequests(prefix = ""): void {
  for (const key of [...entries.keys()]) if (key.startsWith(prefix)) entries.delete(key);
}
