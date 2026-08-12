"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchWithAuth } from "@/lib/apiClient";

/**
 * Where a thumbnail's bytes come from. Both resolve to an AUTHENTICATED
 * thumbnail route that renders a PNG (PDF first page or resized image) — never
 * a public URL, so private-content previews stay behind the Firebase bearer.
 */
export type DocThumbnailSource =
  | { kind: "bucket"; bucket: string; object: string }
  | { kind: "doc"; docId: string };

const THUMBNAILABLE_EXT = /\.(pdf|png|jpe?g|gif|webp|bmp|tiff?)$/i;

function thumbnailUrl(source: DocThumbnailSource, width: number): string | null {
  if (source.kind === "bucket") {
    if (!THUMBNAILABLE_EXT.test(source.object)) return null; // skip a guaranteed 415
    return `/api/proxy/api/buckets/${encodeURIComponent(source.bucket)}/thumbnail?object=${encodeURIComponent(
      source.object,
    )}&width=${width}`;
  }
  return `/api/proxy/api/documents/${encodeURIComponent(source.docId)}/thumbnail?width=${width}`;
}

// Module-level blob-URL cache so re-hovering a document is instant (no refetch,
// no flash). fetchWithAuth forces `cache: "no-store"`, so without this every
// hover re-downloads + re-renders the thumbnail. The cache OWNS each object URL
// (we never revoke on unmount); a bounded LRU revokes evicted entries.
const _thumbCache = new Map<string, string>();
const _THUMB_CACHE_MAX = 64;

function _cacheGet(url: string): string | null {
  const hit = _thumbCache.get(url);
  if (hit) {
    // LRU touch: re-insert so it's most-recently-used.
    _thumbCache.delete(url);
    _thumbCache.set(url, hit);
  }
  return hit ?? null;
}

function _cachePut(url: string, objectUrl: string): void {
  _thumbCache.set(url, objectUrl);
  while (_thumbCache.size > _THUMB_CACHE_MAX) {
    const oldest = _thumbCache.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    const evicted = _thumbCache.get(oldest);
    _thumbCache.delete(oldest);
    if (evicted) URL.revokeObjectURL(evicted);
  }
}

/** Test-only: drop the module cache so tests don't leak thumbnails between cases. */
export function __clearThumbnailCache(): void {
  _thumbCache.clear();
}

/**
 * Fetch a document thumbnail PNG through the authenticated route and expose it
 * as an object URL. `null` while loading or on any error (403 no-access, 415
 * non-thumbnailable, network) so the caller can fall back to an icon. Results
 * are cached module-wide, so re-hovering the same document is instant.
 */
export function useDocumentThumbnail(
  source: DocThumbnailSource,
  width: number,
): { url: string | null; loading: boolean } {
  const url = thumbnailUrl(source, width);
  const cached = url ? _cacheGet(url) : null;
  const [blobUrl, setBlobUrl] = useState<string | null>(cached);
  const [loading, setLoading] = useState<boolean>(url != null && !cached);

  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }
    const hit = _cacheGet(url);
    if (hit) {
      setBlobUrl(hit);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setBlobUrl(null);
    (async () => {
      try {
        const res = await fetchWithAuth(url);
        if (cancelled) return;
        if (res.ok) {
          const blob = await res.blob();
          if (cancelled) return;
          const created = URL.createObjectURL(blob);
          _cachePut(url, created);
          setBlobUrl(created);
        }
      } catch {
        /* leave null → icon fallback */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    // No revoke on cleanup — the cache owns the object URL's lifecycle.
    return () => {
      cancelled = true;
    };
  }, [url]);

  return { url: blobUrl, loading };
}

interface DocumentThumbnailProps {
  source: DocThumbnailSource;
  /** Render width requested from the backend. Default 600. */
  width?: number;
  /** Classes for the rendered <img> (default fills + top-aligns). */
  className?: string;
  alt?: string;
}

/**
 * Standardised document preview image, used everywhere a document is shown
 * (example cards, hover previews, doc lists). Renders the authenticated
 * thumbnail as an <img>; shows a skeleton while loading and a generic doc icon
 * on failure / unsupported type.
 */
export function DocumentThumbnail({ source, width = 600, className, alt }: DocumentThumbnailProps) {
  const { url, loading } = useDocumentThumbnail(source, width);

  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={url}
        alt={alt ?? "Document preview"}
        className={cn("animate-in fade-in duration-300", className ?? "h-full w-full object-cover object-top")}
      />
    );
  }

  return (
    <div
      className={cn(
        "flex h-full w-full items-center justify-center bg-muted/30 text-muted-foreground",
        loading && "animate-pulse",
      )}
    >
      <FileText className="h-8 w-8" aria-hidden="true" />
    </div>
  );
}
