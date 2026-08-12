// v6.11.0 workbench-home-and-curated-activity — the Workspace tab's "Home":
// a navigation index of the session's results. The full content lives in each
// Result tab (auto-focused when it arrives); Home is where the user returns to
// see what's available and jump to it. "Home" is a behaviour, not a new user
// term — the tab stays labelled "Workspace".
//
// Deliberately NOT a content pane: rendering a result's surface here as well as
// in its tab would double-register the A2UI mount (one mount per surfaceId).
// Home navigates; tabs carry the content + history.

"use client";

import type { A2uiArtifactEntry } from "@/providers/SurfaceRegistry";
import { WorkbenchIndex } from "./WorkbenchIndex";

export interface WorkbenchHomeProps {
  /** Session result artifacts (workbench-placed), for the index. */
  artifacts: A2uiArtifactEntry[];
  /** Focus a Result tab. */
  onOpen: (surfaceId: string) => void;
  /** The currently-open document id, if any — surfaces a "Document" jump row. */
  openDocId?: string | null;
  /** Focus the Document tab. */
  onOpenDocument?: () => void;
}

export function WorkbenchHome({ artifacts, onOpen, openDocId, onOpenDocument }: WorkbenchHomeProps) {
  const hasIndex = artifacts.length > 0 || Boolean(openDocId);

  if (!hasIndex) {
    return (
      <p className="p-4 text-sm text-muted-foreground" data-testid="home-empty">
        The assistant&apos;s results — sources, comparisons, analyses — gather here as it works. Open one to view it.
      </p>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-auto">
      {artifacts.length > 0 && (
        // Newest-first so the last relevant result leads the navigation list.
        <WorkbenchIndex artifacts={[...artifacts].reverse()} onOpen={onOpen} heading="Results" />
      )}
      {openDocId && (
        <div className="px-3 pb-3">
          <button
            type="button"
            onClick={onOpenDocument}
            className="group flex w-full items-start gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2.5 text-left transition-colors hover:border-primary/50 hover:bg-muted/30"
            data-testid="home-document-row"
          >
            <span className="mt-0.5 shrink-0 rounded-md bg-primary/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-primary">
              Document
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-foreground">Open document</span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                The document you&apos;re working with, alongside the conversation.
              </span>
            </span>
            <span aria-hidden className="mt-0.5 text-xs text-muted-foreground/50 group-hover:text-primary">
              Open ›
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
