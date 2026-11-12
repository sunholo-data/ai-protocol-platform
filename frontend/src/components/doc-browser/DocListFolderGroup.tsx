"use client";

import { type ReactNode, useState } from "react";
import type { FolderProjectLink } from "@/hooks/useFolderProjects";
import type { SyncedFolder } from "@/hooks/useSyncedFolders";
import { FolderProjectsMenu } from "./FolderProjectsMenu";
import { folderSegments } from "./folderTree";

/** True when `path` is the folder a GitHub source writes into (not one under it). */
export function isSyncRoot(synced: SyncedFolder | null | undefined, path: string): boolean {
  if (!synced) return false;
  return folderSegments(path).join("/").toLocaleLowerCase() === folderSegments(synced.prefix).join("/").toLocaleLowerCase();
}

/** Small GitHub mark on the top folder a source keeps in sync. */
export function SyncedBadge({ synced }: { synced: SyncedFolder }) {
  const text = `Synced from GitHub${synced.repo ? ` (${synced.repo})` : ""} into ${synced.projectName} · can't be renamed`;
  return (
    <svg
      role="img"
      aria-label={text}
      viewBox="0 0 16 16"
      fill="currentColor"
      className="h-3 w-3 shrink-0 text-muted-foreground"
      data-testid="folder-synced-badge"
    >
      <title>{text}</title>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

interface DocListFolderGroupProps {
  /** The path segment, e.g. `flying-fish`. */
  label: string;
  /** The full path, shown as a tooltip. */
  path: string;
  /** Documents in all the subfolders below. */
  docCount: number;
  defaultOpen?: boolean;
  /** The GitHub source this path belongs to, if any. */
  syncedBy?: SyncedFolder | null;
  /** Projects the subfolders are in. */
  projects?: FolderProjectLink[];
  /** The subfolders. */
  children: ReactNode;
}

/**
 * A folder that exists only as the parent of other folders — an uploaded
 * folder with no loose files of its own. It holds no documents, so it has
 * no actions; it just expands to its subfolders.
 */
export function DocListFolderGroup({
  label,
  path,
  docCount,
  defaultOpen = false,
  syncedBy,
  projects = [],
  children,
}: DocListFolderGroupProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded">
      <div className="group/folder flex items-center">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          title={path}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded px-2 py-1.5 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          aria-expanded={open}
        >
          <svg
            className={`h-3 w-3 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
            viewBox="0 0 12 12"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M4 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <svg className="h-3.5 w-3.5 shrink-0 text-amber-500" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M1.5 3.5A1 1 0 012.5 2.5h3.879a1 1 0 01.707.293L8.5 4.5H13.5a1 1 0 011 1V12a1 1 0 01-1 1h-11a1 1 0 01-1-1V3.5z" />
          </svg>
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{label}</span>
          {syncedBy && isSyncRoot(syncedBy, path) && <SyncedBadge synced={syncedBy} />}
          <span
            className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
            title={`${docCount} document${docCount === 1 ? "" : "s"} in subfolders`}
          >
            {docCount}
          </span>
        </button>
        <FolderProjectsMenu projects={projects} folderLabel={label} />
      </div>
      {open && <div className="ml-5 mt-0.5 space-y-0.5">{children}</div>}
    </div>
  );
}
