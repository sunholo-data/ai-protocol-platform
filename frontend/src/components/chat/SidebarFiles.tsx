"use client";

import { useState } from "react";
import { ChevronRight, FileText, FolderTree } from "lucide-react";
import { DocListView } from "@/components/doc-browser/DocListView";
import { UploadDropZone } from "@/components/doc-browser/UploadDropZone";
import { useRecentDocuments, type RecentDocument } from "@/hooks/useRecentDocuments";
import type { ParsedDocument } from "@/hooks/useDocBrowser";

const RECENT = 3;
/** While searching, match against this many recent files (the API's cap). */
const SEARCH_POOL = 50;

interface SidebarFilesProps {
  uid: string;
  skillId: string;
  /** The sidebar's search text. */
  query: string;
  onOpenRecent: (doc: RecentDocument) => void;
  onDocClick: (doc: ParsedDocument) => void;
}

/**
 * Files, first view: the three you used last, then "Browse all folders",
 * which opens the full folder tree (and its upload) in place. While the
 * sidebar is searching, matching files show here, from your recent files and
 * any folder you have open.
 */
export function SidebarFiles({ uid, skillId, query, onOpenRecent, onDocClick }: SidebarFilesProps) {
  const q = query.trim().toLowerCase();
  const recent = useRecentDocuments(q ? SEARCH_POOL : RECENT);
  const [browsing, setBrowsing] = useState(false);

  const shown = q
    ? recent.documents.filter((d) => d.originalFilename.toLowerCase().includes(q)).slice(0, 8)
    : recent.documents.slice(0, RECENT);

  return (
    <div data-testid="sidebar-files">
      {!q && recent.documents.length > 0 ? (
        <p className="px-2 pb-0.5 text-[11px] text-muted-foreground">Recent</p>
      ) : null}
      {recent.status === "error" ? (
        <div role="alert" className="flex items-center gap-2 px-2 py-1 text-xs text-destructive">
          <span className="flex-1">Couldn&apos;t load your recent files.</span>
          <button type="button" onClick={recent.refetch} className="shrink-0 underline underline-offset-2">
            Try again
          </button>
        </div>
      ) : null}
      {recent.status === "loading" && recent.documents.length === 0 ? (
        <div role="status" aria-label="Loading your recent files" className="space-y-1 px-1">
          {Array.from({ length: RECENT }, (_, i) => (
            <div key={i} className="h-7 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : null}
      <ul aria-label={q ? "Matching files" : "Recent files"}>
        {shown.map((d) => (
          <li key={d.id}>
            <button
              type="button"
              onClick={() => onOpenRecent(d)}
              title={d.originalFilename}
              className="flex min-h-[44px] w-full items-center gap-2 rounded px-2 text-left text-sm text-foreground/90 hover:bg-background/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:min-h-[32px]"
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 truncate">{d.originalFilename}</span>
            </button>
          </li>
        ))}
      </ul>
      {q && shown.length === 0 && recent.status === "ready" && !browsing ? (
        <p className="px-2 py-1 text-xs text-muted-foreground">No recent files match. Browse all folders to look further.</p>
      ) : null}

      <button
        type="button"
        onClick={() => setBrowsing((v) => !v)}
        aria-expanded={browsing}
        className="mt-0.5 flex min-h-[44px] w-full items-center gap-2 rounded px-2 text-left text-sm text-muted-foreground hover:bg-background/70 hover:text-foreground md:min-h-[32px]"
      >
        <FolderTree className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="flex-1">{browsing ? "Hide folders" : "Browse all folders"}</span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 transition-transform ${browsing ? "rotate-90" : ""}`} aria-hidden />
      </button>
      {browsing ? (
        <div className="mt-1 border-t border-border pt-1">
          <div className="max-h-[40vh] overflow-y-auto">
            <DocListView uid={uid} skillId={skillId} onDocClick={onDocClick} searchQuery={query} />
          </div>
          <UploadDropZone skillId={skillId} className="px-1 py-2" />
        </div>
      ) : null}
    </div>
  );
}
