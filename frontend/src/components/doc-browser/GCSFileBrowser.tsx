"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { DocumentPreview } from "@/components/document/DocumentPreview";

// Files we can render a hover preview for (PDF page 1 or an image).
const PREVIEWABLE_EXT = /\.(pdf|png|jpe?g|gif|webp|bmp|tiff?)$/i;

interface GCSBucketEntry {
  name: string;
  size: number;
  contentType?: string | null;
  updated?: number | null;
  isPrefix: boolean;
}

interface GCSBucketListResponse {
  entries: GCSBucketEntry[];
  nextPageToken?: string | null;
  prefix: string;
}

interface GCSFileBrowserProps {
  bucket: string;
  /** Object-name prefix to start at (e.g. "PPAs/longform/"). */
  rootPath?: string;
  /** Click handler — fires with the fully-qualified object name + bucket. */
  onPick: (bucket: string, objectName: string, label: string) => void;
}

/**
 * GCSFileBrowser (v6.4.0 4.5 SKILL-ONBOARDING M4 — lean Aitana implementation).
 *
 * Single-level directory listing of a configured GCS bucket prefix. User
 * clicks files to import; sub-prefixes (folders) are expandable inline
 * (clicking a folder navigates the breadcrumb into it).
 *
 * NOT a verbatim port of gde-ap-agent's GCSFileBrowser — that one depends
 * on a useGCSBucket hook + GCSBucketInput sub-component + documentEvents
 * subscriber. This Aitana version is purposely lean for the Friday demo:
 * one fetch, flat-list inside a single-prefix view, click navigates. A
 * proper folder-tree expansion + import-by-reference flow can land in v6.5.
 */
export function GCSFileBrowser({ bucket, rootPath = "", onPick }: GCSFileBrowserProps) {
  const [prefix, setPrefix] = useState(rootPath);
  const [entries, setEntries] = useState<GCSBucketEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // v6.5.0 BUCKET-FILES: the backend list endpoint is folder-scoped + paginated
  // (delimiter="/", nextPageToken). `append=false` (first page / folder change)
  // replaces; `append=true` ("Load more") appends — so folders with >100 files
  // are fully browsable instead of silently truncating at the first page.
  const fetchPage = useCallback(
    (p: string, token: string | null, append: boolean) => {
      let cancelled = false;
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      const tokenParam = token ? `&pageToken=${encodeURIComponent(token)}` : "";
      fetchWithAuth(
        `/api/proxy/api/buckets/${encodeURIComponent(bucket)}/list?prefix=${encodeURIComponent(p)}&limit=100${tokenParam}`,
      )
        .then(async (res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = (await res.json()) as GCSBucketListResponse;
          if (cancelled) return;
          setEntries((prev) => (append ? [...prev, ...data.entries] : data.entries));
          setNextToken(data.nextPageToken ?? null);
          setLoading(false);
          setLoadingMore(false);
        })
        .catch((e) => {
          if (!cancelled) {
            setError(String(e?.message ?? e));
            setLoading(false);
            setLoadingMore(false);
          }
        });
      return () => {
        cancelled = true;
      };
    },
    [bucket],
  );

  // Folder change (prefix) → fresh first page. Token resets implicitly because
  // append=false and we re-fetch from the start.
  useEffect(() => {
    setNextToken(null);
    return fetchPage(prefix, null, false);
  }, [prefix, fetchPage]);

  // Breadcrumb segments: ["PPAs", "longform"] for prefix "PPAs/longform/".
  const segments = prefix.split("/").filter(Boolean);

  return (
    <div className="flex flex-col gap-1 text-xs">
      {prefix && (
        <button
          type="button"
          onClick={() => {
            // Climb one level — strip the last segment plus its trailing slash.
            const newSegments = segments.slice(0, -1);
            const newPrefix = newSegments.length > 0 ? `${newSegments.join("/")}/` : "";
            setPrefix(newPrefix);
          }}
          className="flex items-center gap-1 px-1 py-0.5 text-left font-mono text-[10px] uppercase tracking-wider text-muted-foreground hover:text-foreground"
        >
          ← {segments[segments.length - 1] || "(root)"}
        </button>
      )}

      {loading && (
        <div className="px-1 py-2 text-muted-foreground">Loading…</div>
      )}

      {!loading && error && (
        <div className="px-1 py-2 text-destructive">{error}</div>
      )}

      {!loading && !error && entries.length === 0 && (
        <div className="px-1 py-2 text-muted-foreground italic">(empty)</div>
      )}

      <ul className="space-y-0.5">
        {entries.map((entry) => {
          // Display name: strip the prefix so users see "longform/" not "PPAs/longform/".
          const display = entry.name.startsWith(prefix)
            ? entry.name.slice(prefix.length)
            : entry.name;
          if (!display) return null;
          if (entry.isPrefix) {
            return (
              <li key={entry.name}>
                <button
                  type="button"
                  onClick={() => setPrefix(entry.name)}
                  className="flex w-full items-center gap-1 truncate rounded px-1 py-0.5 text-left text-foreground hover:bg-muted/50"
                >
                  <FolderIcon /> {display.replace(/\/$/, "")}
                </button>
              </li>
            );
          }
          const fileButton = (
            <button
              type="button"
              onClick={() => onPick(bucket, entry.name, display)}
              className="flex w-full items-center gap-1 truncate rounded px-1 py-0.5 text-left text-foreground hover:bg-muted/50"
            >
              <FileIcon /> {display}
            </button>
          );
          return (
            <li key={entry.name}>
              {PREVIEWABLE_EXT.test(entry.name) ? (
                <DocumentPreview
                  source={{ kind: "bucket", bucket, object: entry.name }}
                  label={display}
                >
                  {fileButton}
                </DocumentPreview>
              ) : (
                fileButton
              )}
            </li>
          );
        })}
      </ul>

      {!loading && !error && nextToken && (
        <button
          type="button"
          onClick={() => fetchPage(prefix, nextToken, true)}
          disabled={loadingMore}
          className="mt-1 rounded px-1 py-1 text-left text-[11px] font-medium text-primary hover:bg-muted/50 disabled:opacity-60"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}

function FolderIcon() {
  return (
    <svg
      className="h-3 w-3 shrink-0 text-primary/70"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <path d="M2 4h4l1.5 2H14v7H2z" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg
      className="h-3 w-3 shrink-0 text-muted-foreground"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <path d="M4 2h6l3 3v9H4z" />
      <path d="M10 2v3h3" />
    </svg>
  );
}
