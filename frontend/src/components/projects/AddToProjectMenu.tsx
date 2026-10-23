"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import { MENU_CONTENT_CLASS, MENU_ITEM_CLASS } from "@/components/doc-browser/folderActions";
import { addDocumentsToProject, removeDocumentFromProject, useProjects } from "@/hooks/useProjects";

/** What happened, for the caller to render under the document row (#8). */
export type ProjectMenuFeedback =
  | { kind: "pending"; message: string }
  | { kind: "done"; message: string; projectId?: string }
  | { kind: "error"; message: string };

interface AddToProjectMenuProps {
  docId: string;
  filename: string;
  /** The projects this document is already in (ticked). */
  projectIds: readonly string[];
  onFeedback: (feedback: ProjectMenuFeedback) => void;
  /** "+ New project…" (E3): the caller shows the inline name field. */
  onNewProject: () => void;
}

/**
 * "Add to project…" — a submenu of a document's ⋯ menu (projects design §6.1).
 * Each project is a tick: ticking adds the document (it stays in its folder),
 * unticking removes it (the document itself is never deleted). The last row
 * creates a project with this document in it (E3). Must render inside a Radix
 * `DropdownMenu.Content`.
 */
export function AddToProjectMenu(props: AddToProjectMenuProps) {
  return (
    <DropdownMenu.Sub>
      <DropdownMenu.SubTrigger className={MENU_ITEM_CLASS}>
        <span className="flex-1">Add to project…</span>
        <span aria-hidden className="pl-2 text-muted-foreground">›</span>
      </DropdownMenu.SubTrigger>
      <DropdownMenu.Portal>
        <DropdownMenu.SubContent className={MENU_CONTENT_CLASS} sideOffset={4} onClick={(e) => e.stopPropagation()}>
          {/* Mounted only while the submenu is open, so a list of 50 documents
              doesn't fetch projects until someone actually asks for them. */}
          <ProjectChoices {...props} />
        </DropdownMenu.SubContent>
      </DropdownMenu.Portal>
    </DropdownMenu.Sub>
  );
}

function ProjectChoices({ docId, filename, projectIds, onFeedback, onNewProject }: AddToProjectMenuProps) {
  const { projects, status, retry } = useProjects();

  async function toggle(projectId: string, name: string, inProject: boolean) {
    if (inProject) {
      onFeedback({ kind: "pending", message: `Removing from ${name}…` });
      const r = await removeDocumentFromProject(projectId, docId);
      onFeedback(r.ok ? { kind: "done", message: `Removed from ${name} — the document is kept.` } : { kind: "error", message: r.message });
      return;
    }
    onFeedback({ kind: "pending", message: `Adding to ${name}…` });
    const r = await addDocumentsToProject(projectId, [docId]);
    if (!r.ok) {
      onFeedback({ kind: "error", message: r.message });
    } else if (r.failed.length > 0) {
      const reason = r.failed[0].reason;
      onFeedback({ kind: "error", message: reason ? `Couldn't add ${filename}: ${reason}` : `Couldn't add ${filename} to ${name}.` });
    } else {
      onFeedback({
        kind: "done",
        message: r.added.length > 0 ? `Added to ${name}` : `Already in ${name}`,
        projectId,
      });
    }
  }

  return (
    <>
      {status === "loading" && projects.length === 0 && (
        <DropdownMenu.Label className="px-2 py-1 text-[10px] text-muted-foreground">Loading projects…</DropdownMenu.Label>
      )}
      {status === "error" && (
        <DropdownMenu.Item
          className={`${MENU_ITEM_CLASS} text-destructive`}
          onSelect={(e) => {
            e.preventDefault();
            retry();
          }}
        >
          Couldn&apos;t load projects — Retry
        </DropdownMenu.Item>
      )}
      {projects.map((p) => {
        const inProject = projectIds.includes(p.id);
        return (
          <DropdownMenu.CheckboxItem
            key={p.id}
            className={`${MENU_ITEM_CLASS} gap-1.5`}
            checked={inProject}
            onSelect={() => void toggle(p.id, p.name, inProject)}
            aria-label={inProject ? `Remove from ${p.name}` : `Add to ${p.name}`}
          >
            <span className="inline-flex w-3 shrink-0 justify-center">
              <DropdownMenu.ItemIndicator>
                <Check className="h-3 w-3" aria-hidden />
              </DropdownMenu.ItemIndicator>
            </span>
            <span className="min-w-0 flex-1 truncate">{p.name}</span>
          </DropdownMenu.CheckboxItem>
        );
      })}
      {projects.length > 0 && <DropdownMenu.Separator className="my-1 h-px bg-border" />}
      <DropdownMenu.Item className={MENU_ITEM_CLASS} onSelect={onNewProject}>
        + New project…
      </DropdownMenu.Item>
    </>
  );
}
