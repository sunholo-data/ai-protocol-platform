"use client";

// Media served by an authenticated backend route, shown anywhere in the app.
//
// An `<img>`, `<video>` or `<audio>` sends no Authorization header, and a
// backend-relative `/api/...` path resolves against the FRONTEND origin. So
// such media is fetched through the proxy with `fetchWithAuth` and shown from a
// `blob:` URL: the same fix `lib/a2uiAuthImages.ts` applies to A2UI surfaces
// and `DocumentViewer` to iframes. This module is that fix for plain React:
// document thumbnails, and the pictures, video and audio the agent puts in a
// chat reply (`![caption](/api/documents/<ref>/preview)`, from `show_media`).
//
// The bytes are cached module-wide (bounded LRU), so a re-render, a re-hover or
// the same picture in the lightbox does not download again. The cache OWNS each
// object URL: components never revoke on unmount; eviction does. The whole
// file is fetched before it plays (no range requests), which suits the sizes
// the backend serves (sources ≤ 50 MB, bucket previews ≤ 30 MB).

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

const PROXY_PREFIX = "/api/proxy";
const BACKEND_PATH = /^\/api\//;

/**
 * The proxy path for media on an authenticated backend route, or `null` when
 * `src` is something an element can load by itself (https, data:, blob:).
 */
export function authImagePath(src: string | null | undefined): string | null {
  if (!src || !BACKEND_PATH.test(src)) return null;
  return src.startsWith(`${PROXY_PREFIX}/`) ? src : `${PROXY_PREFIX}${src}`;
}

// The media routes a chat reply may point at: exactly what `show_media` emits.
// Model text is untrusted, so a reply cannot make the browser fetch any other
// backend route with the user's credentials.
const CHAT_MEDIA_ROUTE =
  /^\/api\/(documents\/[^/?#]+\/(thumbnail|preview)|buckets\/[^/?#]+\/(thumbnail|preview)|browser\/artefacts\/[^/?#]+|media\/[^/?#]+)(\?[^#]*)?$/;

/** True for a backend media route a chat reply may show (see `show_media`). */
export function isChatMediaPath(url: string): boolean {
  // No dot segments, plain or percent-encoded: the URL parser would resolve
  // `/api/documents/../x/thumbnail` to a different route than the one matched.
  const path = url.split("?", 1)[0];
  if (path.includes("..") || /%2e/i.test(path)) return false;
  return CHAT_MEDIA_ROUTE.test(url);
}

interface Cached {
  url: string;
  type: string;
}

const _cache = new Map<string, Cached>();
const _CACHE_MAX = 64;

function _cacheGet(key: string): Cached | null {
  const hit = _cache.get(key);
  if (hit) {
    // LRU touch: re-insert so it's most-recently-used.
    _cache.delete(key);
    _cache.set(key, hit);
  }
  return hit ?? null;
}

function _cachePut(key: string, value: Cached): void {
  _cache.set(key, value);
  while (_cache.size > _CACHE_MAX) {
    const oldest = _cache.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    const evicted = _cache.get(oldest);
    _cache.delete(oldest);
    if (evicted) URL.revokeObjectURL(evicted.url);
  }
}

/** Test-only: forget every cached file. */
export function __clearAuthImageCache(): void {
  _cache.clear();
}

/**
 * Fetch `path` (a `/api/proxy/...` route) with the user's credentials and
 * expose it as an object URL plus its MIME `type` (so a caller can pick
 * `<img>`, `<video>` or `<audio>`). `url` is `null` while loading, when `path`
 * is null, or on failure; `failed` tells a real failure (403, 404, 5xx,
 * network) from "nothing to show" so callers can say so rather than stay
 * blank. `cacheKey` defaults to `path`; pass one carrying a freshness token
 * when the same path can serve new bytes.
 */
export function useAuthImage(
  path: string | null,
  cacheKey?: string | null,
): { url: string | null; type: string; loading: boolean; failed: boolean } {
  const key = path ? (cacheKey ?? path) : null;
  const cached = key ? _cacheGet(key) : null;
  const [file, setFile] = useState<Cached | null>(cached);
  const [loading, setLoading] = useState<boolean>(path != null && !cached);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    if (!path || !key) {
      setFile(null);
      setLoading(false);
      return;
    }
    const hit = _cacheGet(key);
    if (hit) {
      setFile(hit);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFile(null);
    (async () => {
      try {
        const res = await fetchWithAuth(path);
        if (cancelled) return;
        if (res.ok) {
          const blob = await res.blob();
          if (cancelled) return;
          const created = { url: URL.createObjectURL(blob), type: blob.type || "" };
          _cachePut(key, created);
          setFile(created);
        } else {
          setFailed(true);
        }
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path, key]);

  return { url: file?.url ?? null, type: file?.type ?? "", loading, failed };
}
