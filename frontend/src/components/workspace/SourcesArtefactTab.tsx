// SourcesArtefactTab — the workbench "Sources" Result tab (6.11; 6.15 openable docs).
//
// Web/enterprise search sub-agents cite grounding sources; the backend
// result→A2UI transform (adk/a2ui_sources_render.py) stashes the raw list in
// the surface data model at `/sources`. Each source is
// `{title, uri, kind, bucket, object, filename}`:
//   - kind "web"  → an external link (the domain as label, redirect URI as href).
//   - kind "gcs"  → an enterprise datastore document. We render it as a clickable
//     CARD that opens the real document in the Document tab (and adds it to
//     selected docs) via `onOpenSource` → ChatShell's import-by-reference path.
//     Bytes are served behind auth; we never expose the gs:// URI as an href.

"use client";

import { useCallback, useState } from "react";

import { useSurfaceState } from "@/providers/SurfaceRegistry";

interface Source {
  title?: string;
  uri?: string;
  kind?: string;
  bucket?: string;
  object?: string;
  filename?: string;
  // The grounding snippet the answer was built on. Present for enterprise-search
  // sources; makes a link-less datastore result useful even when it can't be opened.
  snippet?: string;
}

function isOpenableDoc(src: Source): src is Source & { bucket: string; object: string } {
  return src.kind === "gcs" && !!src.bucket && !!src.object;
}

function domainOf(src: Source): string {
  const raw = (src.title || "").trim();
  if (raw) return raw;
  try {
    return new URL(src.uri || "").hostname.replace(/^www\./, "");
  } catch {
    return src.uri || "source";
  }
}

function DocGlyph() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M16 13H8M16 17H8M10 9H8" />
    </svg>
  );
}

function Spinner() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
      className="shrink-0 animate-spin text-muted-foreground/70"
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

export function SourcesArtefactTab({
  surfaceId,
  className,
  onOpenSource,
}: {
  surfaceId: string;
  className?: string;
  /** Open a gs:// source in the Document tab (parse + add to selected). Throws on failure. */
  onOpenSource?: (bucket: string, object: string) => Promise<void>;
}) {
  const state = useSurfaceState(surfaceId);
  const root = (state?.surface?.dataModel?.get("/") ?? null) as { sources?: Source[] } | null;
  const sources = (root?.sources ?? []).filter((s) => s.uri || s.title);

  // Per-source open state (never-silent: pending spinner + visible error).
  const [pendingIdx, setPendingIdx] = useState<number | null>(null);
  const [errorIdx, setErrorIdx] = useState<number | null>(null);

  const open = useCallback(
    async (i: number, src: Source) => {
      if (!onOpenSource || !isOpenableDoc(src)) return;
      setErrorIdx(null);
      setPendingIdx(i);
      try {
        await onOpenSource(src.bucket, src.object);
      } catch {
        setErrorIdx(i);
      } finally {
        setPendingIdx((cur) => (cur === i ? null : cur));
      }
    },
    [onOpenSource],
  );

  return (
    <div className={`flex h-full flex-col overflow-auto p-4 ${className ?? ""}`}>
      <div className="mb-3 flex items-baseline justify-between px-0.5">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">Sources</h2>
        <span className="text-xs text-muted-foreground">
          {sources.length} source{sources.length === 1 ? "" : "s"}
        </span>
      </div>

      {sources.length === 0 ? (
        <p className="px-0.5 text-sm text-muted-foreground">
          No sources were returned for this answer.
        </p>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {sources.map((src, i) => {
            const badge = (
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 font-mono text-[11px] text-primary">
                {i + 1}
              </span>
            );

            // Enterprise datastore document → clickable card that opens the Document tab.
            if (isOpenableDoc(src) && onOpenSource) {
              const label = (src.filename || src.title || src.object).trim();
              const isPending = pendingIdx === i;
              const isError = errorIdx === i;
              return (
                <li key={`${src.object}-${i}`}>
                  <button
                    type="button"
                    onClick={() => void open(i, src)}
                    disabled={isPending}
                    aria-label={`Open ${label} in the Document tab`}
                    className="group flex w-full items-center gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2.5 text-left transition-colors hover:border-primary/50 hover:bg-muted/30 disabled:opacity-70"
                  >
                    {badge}
                    <span className="text-muted-foreground/60 group-hover:text-primary">
                      <DocGlyph />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground group-hover:text-primary">
                        {label}
                      </span>
                      {isError ? (
                        <span className="block truncate text-xs text-destructive">
                          Couldn&apos;t open this document — you may not have access.
                        </span>
                      ) : (
                        <span className="block truncate text-xs text-muted-foreground">
                          {isPending ? "Opening…" : "Open in Document tab"}
                        </span>
                      )}
                    </span>
                    {isPending ? (
                      <Spinner />
                    ) : (
                      <span className="shrink-0 text-muted-foreground/50 transition-colors group-hover:text-primary">
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          <path d="M9 18l6-6-6-6" />
                        </svg>
                      </span>
                    )}
                  </button>
                </li>
              );
            }

            // Web source. WITH a uri → clickable external link (6.11 behaviour).
            // WITHOUT a uri (an enterprise-search result whose datastore doc has
            // no link) → a NON-clickable card showing its snippet, so it's useful
            // instead of a dead link with a raw resource-name label (6.15 Phase A+).
            const label = domainOf(src);
            const href = src.uri || "";
            const snippet = (src.snippet || "").trim();
            const inner = (
              <>
                {badge}
                <div className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground group-hover:text-primary">
                    {label}
                  </span>
                  {snippet && (
                    // Enterprise snippets can be arbitrarily large (whole
                    // grounding chunks). Cap the height and scroll WITHIN the
                    // card so a single source can't flood the list, while the
                    // full text stays readable. (Note: a plain `line-clamp-2`
                    // here is silently defeated by an adjacent `block` — the
                    // later-emitted `display:block` wins over the clamp's
                    // `display:-webkit-box`, so the clamp never engages.)
                    <span className="mt-0.5 block max-h-32 overflow-y-auto whitespace-pre-wrap pr-1 text-xs leading-relaxed text-muted-foreground">
                      {snippet}
                    </span>
                  )}
                </div>
                {href && (
                  // external-link glyph (SVG, no emoji — repo asset rule); only
                  // shown when there's actually somewhere to go.
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                    className="mt-0.5 shrink-0 text-muted-foreground/50 transition-colors group-hover:text-primary"
                  >
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <path d="M15 3h6v6" />
                    <path d="M10 14 21 3" />
                  </svg>
                )}
              </>
            );
            return (
              <li key={`${href || label}-${i}`}>
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-start gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2.5 transition-colors hover:border-primary/50 hover:bg-muted/30"
                  >
                    {inner}
                  </a>
                ) : (
                  <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2.5">
                    {inner}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
