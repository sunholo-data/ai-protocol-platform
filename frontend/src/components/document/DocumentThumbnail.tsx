"use client";

import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { __clearAuthImageCache, useAuthImage } from "@/lib/authImage";

/**
 * Where a thumbnail's bytes come from. Both resolve to an AUTHENTICATED
 * thumbnail route that renders a PNG (PDF first page or resized image) — never
 * a public URL, so private-content previews stay behind the Firebase bearer.
 */
export type DocThumbnailSource =
  | { kind: "bucket"; bucket: string; object: string }
  | {
      kind: "doc";
      docId: string;
      /** Source format when the caller already knows it (e.g. a list row). A
       * known non-thumbnailable format skips the request (no guaranteed 415). */
      format?: string;
      /** Freshness token (e.g. `updatedAt`) — part of the client cache key so a
       * re-uploaded / re-parsed document doesn't show a stale thumbnail. */
      version?: string | null;
    };

const THUMBNAILABLE_EXT = /\.(pdf|png|jpe?g|gif|webp|bmp|tiff?)$/i;
/** Formats the backend thumbnailer renders (`tools/documents/thumbnail.py`
 * THUMBNAILABLE_EXTS) — PDF page 1 or a resized image. */
const THUMBNAILABLE_FORMAT = /^(pdf|png|jpe?g|gif|webp|bmp|tiff?)$/i;

/** True when the backend can render a thumbnail for this source format / ext. */
export function isThumbnailableFormat(format: string | null | undefined): boolean {
  return !!format && THUMBNAILABLE_FORMAT.test(format.replace(/^\./, ""));
}

function thumbnailUrl(source: DocThumbnailSource, width: number): string | null {
  if (source.kind === "bucket") {
    if (!THUMBNAILABLE_EXT.test(source.object)) return null; // skip a guaranteed 415
    return `/api/proxy/api/buckets/${encodeURIComponent(source.bucket)}/thumbnail?object=${encodeURIComponent(
      source.object,
    )}&width=${width}`;
  }
  if (source.format !== undefined && !isThumbnailableFormat(source.format)) return null;
  return `/api/proxy/api/documents/${encodeURIComponent(source.docId)}/thumbnail?width=${width}`;
}

/** Client cache key: the URL plus the doc's freshness token, when known. */
function cacheKey(source: DocThumbnailSource, url: string | null): string | null {
  if (!url) return null;
  return source.kind === "doc" && source.version ? `${url}#${source.version}` : url;
}

/** Test-only: drop the shared image cache so tests don't leak thumbnails between cases. */
export function __clearThumbnailCache(): void {
  __clearAuthImageCache();
}

/**
 * Fetch a document thumbnail PNG through the authenticated route and expose it
 * as an object URL. `url` is `null` while loading, for an unsupported format,
 * or on any error; `failed` distinguishes a real failure (403 no-access, 404,
 * 5xx, network) from "nothing to show" so callers can say so (NEVER SILENT).
 * Cached module-wide by `lib/authImage` (keyed by URL + version), so
 * re-hovering the same document is instant.
 */
export function useDocumentThumbnail(
  source: DocThumbnailSource,
  width: number,
): { url: string | null; loading: boolean; failed: boolean } {
  const url = thumbnailUrl(source, width);
  return useAuthImage(url, cacheKey(source, url));
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
 * thumbnail as an <img>; shows a skeleton while loading, a generic doc icon
 * for an unsupported type, and the icon plus "Preview unavailable" when the
 * fetch failed.
 */
export function DocumentThumbnail({ source, width = 600, className, alt }: DocumentThumbnailProps) {
  const { url, loading, failed } = useDocumentThumbnail(source, width);

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
        "flex h-full w-full flex-col items-center justify-center gap-1 bg-muted/30 text-muted-foreground",
        loading && "animate-pulse",
      )}
    >
      <FileText className="h-8 w-8" aria-hidden="true" />
      {failed ? <span className="text-[11px]">Preview unavailable</span> : null}
    </div>
  );
}
