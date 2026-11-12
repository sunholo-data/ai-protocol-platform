"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import type { FolderProjectLink } from "@/hooks/useFolderProjects";
import { navigateTo, useOptionalRouter } from "@/lib/useOptionalRouter";
import { MENU_CONTENT_CLASS, MENU_ITEM_CLASS } from "./folderActions";

/** How the folder is in the project, shown on the row itself. A tooltip is not
 * enough: it is cut off in a narrow sidebar, and a bare count beside a project
 * the folder is not a source of read as a mystery (seen 2026-11-04). */
// Two lines: the project, then how it holds this folder.
const PROJECT_ITEM_CLASS = MENU_ITEM_CLASS.replace("items-center", "flex-col items-start");

export function linkDescription(link: FolderProjectLink): string {
  const n = link.documents;
  const docs = `${n} document${n === 1 ? "" : "s"}`;
  if (link.via === "github") return `Synced from GitHub · ${docs}`;
  if (link.via === "folder") return `Follows this folder · ${docs}`;
  return `${docs} added by hand`;
}

/**
 * "Projects" section of a folder's ⋯ menu: one item per project the folder
 * (with its subfolders) is in, each opening that project. Renders nothing
 * when the folder is in no project.
 */
export function FolderProjectsMenuItems({ projects }: { projects: FolderProjectLink[] }) {
  const router = useOptionalRouter();
  if (projects.length === 0) return null;
  return (
    <>
      <DropdownMenu.Label className="px-2 pb-0.5 pt-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {projects.length === 1 ? "Project" : "Projects"}
      </DropdownMenu.Label>
      {projects.map((p) => {
        const how = linkDescription(p);
        return (
          <DropdownMenu.Item
            key={p.projectId}
            title={`Open ${p.projectName}`}
            className={PROJECT_ITEM_CLASS}
            onSelect={() => navigateTo(router, `/projects/${encodeURIComponent(p.projectId)}`)}
          >
            <span className="w-full truncate font-medium text-foreground">{p.projectName}</span>
            <span className="w-full truncate text-[11px] text-muted-foreground">{how}</span>
          </DropdownMenu.Item>
        );
      })}
      <DropdownMenu.Separator className="my-1 h-px bg-border" />
    </>
  );
}

/** A ⋯ menu holding only the project links — for rows with no other actions. */
export function FolderProjectsMenu({ projects, folderLabel }: { projects: FolderProjectLink[]; folderLabel: string }) {
  if (projects.length === 0) return null;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Projects with ‘${folderLabel}’`}
        className="ml-0.5 shrink-0 rounded px-1 py-0.5 text-xs text-muted-foreground opacity-0 hover:bg-accent focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring group-hover/folder:opacity-100 data-[state=open]:opacity-100"
      >
        ⋯
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" className={MENU_CONTENT_CLASS}>
          <FolderProjectsMenuItems projects={projects} />
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
