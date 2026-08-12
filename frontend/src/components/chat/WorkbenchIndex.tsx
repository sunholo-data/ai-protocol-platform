"use client";

import { useEffect, useState } from "react";
import type { A2uiArtifactEntry } from "@/providers/SurfaceRegistry";

/**
 * WorkbenchIndex — the Workspace tab's landing view (7.5 M3).
 *
 * A scannable timeline of the session's active result artifacts: kind · title ·
 * description · relative time, each a row that opens its own workbench tab. This
 * is generic chrome over `useArtifacts()` metadata — NOT A2UI and NOT keyed off
 * any specific tool — so a new renderable tool shows up here for free once its
 * mapping declares an artifact. Shown only with ≥2 artifacts (a single artifact
 * is its own tab; no index clutter).
 */

function formatRelative(ts: number, now: number): string {
  if (!ts) return "";
  const diff = Math.max(0, now - ts);
  const s = Math.floor(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(ts).toLocaleDateString();
}

export function WorkbenchIndex({
  artifacts,
  onOpen,
  heading = "Workspace",
}: {
  artifacts: A2uiArtifactEntry[];
  onOpen: (surfaceId: string) => void;
  /** Section heading — "Workspace" as the standalone index tab, "Results" when
   * embedded under the Home digest ribbon (6.11). */
  heading?: string;
}) {
  // Re-tick relative timestamps once a minute so "just now" ages naturally
  // without a render on every artifact update.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex h-full flex-col overflow-auto p-3" data-testid="workbench-index">
      <div className="mb-2 px-1">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">{heading}</h2>
        <p className="text-xs text-muted-foreground">
          {artifacts.length} result{artifacts.length === 1 ? "" : "s"} in this session — open one to view it.
        </p>
      </div>
      <ol className="flex flex-col gap-1.5">
        {artifacts.map((a) => (
          <li key={a.surfaceId}>
            <button
              type="button"
              onClick={() => onOpen(a.surfaceId)}
              className="group flex w-full items-start gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2.5 text-left transition-colors hover:border-primary/50 hover:bg-muted/30"
            >
              {/* Kind chip — a real label, not an emoji (repo asset rule). */}
              <span className="mt-0.5 shrink-0 rounded-md bg-primary/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-primary">
                {a.kind || "result"}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-foreground">
                  {a.title || a.kind || "Result"}
                </span>
                {a.description && (
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {a.description}
                  </span>
                )}
              </span>
              <span className="mt-0.5 flex shrink-0 items-center gap-2">
                <time className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">
                  {formatRelative(a.createdAt, now)}
                </time>
                <span
                  aria-hidden
                  className="text-xs text-muted-foreground/50 transition-colors group-hover:text-primary"
                >
                  Open ›
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
