/**
 * The one place that builds a document-preview URL.
 *
 * A restyle or an edit re-ingests the document IN PLACE — same doc-id, same
 * URL — which is what makes iterating feel like editing. It also means the
 * preview URL is a lie about cacheability: the bytes behind it change while the
 * URL does not.
 *
 * Both preview consumers already re-ran their fetch on `parsedAt`, so the React
 * half of this was right. The HTTP half was not: the backend served
 * `Cache-Control: private, max-age=300` on a URL that never varied, so the
 * refetch was answered from the browser's own cache and redrew the PRE-restyle
 * bytes for up to five minutes. Reported 2026-09-04 as "I apply the formatting,
 * the page refreshes, and nothing changes" — and the restyle had in fact worked
 * every time.
 *
 * Putting the freshness token in the URL fixes it at the layer where the
 * staleness actually lives, and lets the backend keep caching (it only grants
 * `max-age` to a versioned request; a bare one must revalidate).
 */
export function documentPreviewUrl(docId: string, freshness: string): string {
  const base = `/api/proxy/api/documents/${encodeURIComponent(docId)}/preview`;
  // No token yet (a document still parsing has no `parsedAt`) — ask for the
  // uncached variant rather than inventing a version that could get pinned.
  return freshness ? `${base}?v=${encodeURIComponent(freshness)}` : base;
}
