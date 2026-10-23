"use client";

import { useEffect, useId, useRef, useState } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import type { Folder, ParsedDocument } from "@/hooks/useDocBrowser";
import type { KnowledgeBaseInfo } from "@/hooks/useKnowledgeBases";
import { DocListItem } from "./DocListItem";
import {
  DOC_DRAG_TYPE,
  MENU_CONTENT_CLASS,
  MENU_ITEM_CLASS,
  deleteFolder,
  makeFolderSearchable,
  readDragDoc,
  renameFolder,
  stopFolderSearch,
  useMoveDocument,
} from "./folderActions";
import { MoveFeedback } from "./MoveFeedback";
import { AddToSkillPanel, SearchAccessPanel } from "./FolderSearchPanels";
import { createProjectWithFreeName } from "@/hooks/useProjects";
import { navigateTo, useOptionalRouter } from "@/lib/useOptionalRouter";

interface DocListFolderProps {
  folder: Folder;
  documents: ParsedDocument[];
  isActive: boolean;
  onSelect: (folderId: string) => void;
  onDocClick: (doc: ParsedDocument) => void;
  /** Every folder the user has — the "Move to…" targets for this folder's documents. */
  folders?: Folder[];
  /** Called after the server confirmed the folder was deleted. */
  onDeleted?: (folderId: string) => void;
  /** The backend supports knowledge bases ("Make searchable"). */
  searchAvailable?: boolean;
  /** This folder's knowledge base, when it is searchable and listed. */
  knowledgeBase?: KnowledgeBaseInfo;
  /** The signed-in user (to list the skills they can add this folder to). */
  uid?: string;
  /** The skill the user is chatting with — offered first in "Add to a skill". */
  currentSkillId?: string;
}

/** Badge text + tooltip for a searchable folder. */
export function searchBadge(kb: KnowledgeBaseInfo | undefined): { text: string; title: string } {
  if (!kb) return { text: "Searchable", title: "This folder is searchable" };
  const failed = kb.failed ? ` · ${kb.failed} could not be indexed` : "";
  if (kb.state === "queued") return { text: "Indexing…", title: "Getting ready to index this folder" };
  if (kb.state === "indexing") {
    const progress = kb.total ? `${kb.done ?? 0}/${kb.total}` : "…";
    return { text: `Indexing ${progress}`, title: `Indexing this folder for search${failed}` };
  }
  return {
    text: `Searchable · ${kb.documentCount}`,
    title: `${kb.documentCount} document${kb.documentCount === 1 ? "" : "s"} searchable as "${kb.name}"${failed}`,
  };
}

export function DocListFolder({
  folder,
  documents,
  isActive,
  onSelect,
  onDocClick,
  folders = [],
  onDeleted,
  searchAvailable = false,
  knowledgeBase,
  uid,
  currentSkillId,
}: DocListFolderProps) {
  const [open, setOpen] = useState(isActive);
  const nameId = useId();

  // Rename
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(folder.name);
  const [renaming, setRenaming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const keepFocusRef = useRef(false);

  // Delete
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);

  // "Start a project from this folder" (projects design §4 E4).
  const router = useOptionalRouter();
  const [startingProject, setStartingProject] = useState(false);

  // Searchable folder (knowledge base)
  const [searchPending, setSearchPending] = useState<"make" | "stop" | null>(null);
  const [confirmingStop, setConfirmingStop] = useState(false);
  const [searchPanel, setSearchPanel] = useState<"add" | "access" | null>(null);
  const kbId = folder.knowledgeBaseId ?? null;

  // Drop target
  const [dragOver, setDragOver] = useState(false);
  const move = useMoveDocument();
  const canManage = !folder.system;

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function toggle() {
    if (!open) onSelect(folder.id);
    setOpen((v) => !v);
  }

  function startRename() {
    keepFocusRef.current = true;
    setDraft(folder.name);
    setActionError(null);
    setConfirmingDelete(false);
    setEditing(true);
  }

  function cancelRename() {
    setEditing(false);
    setActionError(null);
  }

  async function saveRename() {
    const name = draft.trim();
    if (!name) {
      setActionError("A folder name can't be empty.");
      return;
    }
    if (name === folder.name) {
      cancelRename();
      return;
    }
    setRenaming(true);
    setActionError(null);
    const result = await renameFolder(folder.id, name);
    setRenaming(false);
    if (result.ok) setEditing(false);
    else setActionError(result.message);
  }

  async function confirmDelete() {
    setDeleting(true);
    setActionError(null);
    const result = await deleteFolder(folder.id);
    setDeleting(false);
    setConfirmingDelete(false);
    if (result.ok) onDeleted?.(folder.id);
    else setActionError(result.message);
  }

  /** A one-off snapshot: the folder's documents today join the project; later
   * uploads don't. The name is pre-filled from the folder, so a clash gets
   * " (2)" rather than an error. Lands on the new project's home. */
  async function startProject() {
    setStartingProject(true);
    setActionError(null);
    const result = await createProjectWithFreeName({ name: folder.name, fromFolderId: folder.id });
    setStartingProject(false);
    if (!result.ok) {
      setActionError(result.message);
      return;
    }
    navigateTo(router, `/projects/${encodeURIComponent(result.project.id)}`);
  }

  async function makeSearchable() {
    setSearchPending("make");
    setActionError(null);
    const result = await makeFolderSearchable(folder.id);
    setSearchPending(null);
    if (!result.ok) setActionError(result.message);
  }

  async function confirmStopSearching() {
    if (!kbId) return;
    setSearchPending("stop");
    setActionError(null);
    const result = await stopFolderSearch(kbId);
    setSearchPending(null);
    setConfirmingStop(false);
    if (!result.ok) setActionError(result.message);
  }

  const acceptsDrops = canManage;

  function handleDragOver(e: React.DragEvent) {
    if (!acceptsDrops || !Array.from(e.dataTransfer?.types ?? []).includes(DOC_DRAG_TYPE)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    if (!dragOver) setDragOver(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragOver(false);
  }

  function handleDrop(e: React.DragEvent) {
    setDragOver(false);
    if (!acceptsDrops) return;
    const dragged = readDragDoc(e.dataTransfer);
    if (!dragged) return;
    e.preventDefault();
    if (dragged.folderId === folder.id) return; // dropped back where it came from
    void move.move(dragged.id, folder.id);
  }

  const moveTargets = folders.filter((f) => !f.system);

  return (
    <div
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-drop-target={dragOver ? "true" : undefined}
      className={`rounded ${dragOver ? "bg-accent ring-1 ring-primary" : ""}`}
    >
      <div className="group/folder flex items-center">
        {editing ? (
          <div className="flex w-full items-center gap-1.5 px-2 py-1">
            <input
              ref={inputRef}
              aria-label="Folder name"
              aria-describedby={`${nameId}-hint`}
              value={draft}
              disabled={renaming}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); void saveRename(); }
                else if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
              }}
              className="min-w-0 flex-1 rounded border bg-background px-1.5 py-0.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
            />
            <span id={`${nameId}-hint`} className="shrink-0 text-[10px] text-muted-foreground">
              {renaming ? "Saving…" : "Enter ↵ / Esc"}
            </span>
          </div>
        ) : (
          <button
            type="button"
            onClick={toggle}
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
            <svg
              className="h-3.5 w-3.5 shrink-0 text-amber-500"
              viewBox="0 0 16 16"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M1.5 3.5A1 1 0 012.5 2.5h3.879a1 1 0 01.707.293L8.5 4.5H13.5a1 1 0 011 1V12a1 1 0 01-1 1h-11a1 1 0 01-1-1V3.5z" />
            </svg>
            <span id={nameId} className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
              {folder.name}
            </span>
            {folder.system && (
              <span
                className="shrink-0 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300"
                title="Documents the assistant created for you"
              >
                Created
              </span>
            )}
            {(kbId || searchPending === "make") && (
              <span
                className="shrink-0 rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-300"
                title={kbId ? searchBadge(knowledgeBase).title : "Making this folder searchable"}
                data-testid="folder-search-badge"
              >
                {kbId ? searchBadge(knowledgeBase).text : "Making searchable…"}
              </span>
            )}
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {folder.docCount}
            </span>
          </button>
        )}

        {canManage && !editing && (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger
              aria-label="Folder actions"
              aria-describedby={nameId}
              className="ml-0.5 shrink-0 rounded px-1 py-0.5 text-xs text-muted-foreground opacity-0 hover:bg-accent focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring group-hover/folder:opacity-100 data-[state=open]:opacity-100"
            >
              ⋯
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                className={MENU_CONTENT_CLASS}
                onCloseAutoFocus={(e) => {
                  // Rename moves focus into the input; don't yank it back.
                  if (keepFocusRef.current) {
                    e.preventDefault();
                    keepFocusRef.current = false;
                  }
                }}
              >
                <DropdownMenu.Item className={MENU_ITEM_CLASS} onSelect={startRename}>
                  Rename
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  className={MENU_ITEM_CLASS}
                  disabled={startingProject}
                  onSelect={() => void startProject()}
                >
                  Start a project from this folder
                </DropdownMenu.Item>
                {searchAvailable && !kbId && (
                  <DropdownMenu.Item
                    className={MENU_ITEM_CLASS}
                    disabled={searchPending !== null}
                    onSelect={() => void makeSearchable()}
                  >
                    Make searchable
                  </DropdownMenu.Item>
                )}
                {kbId && knowledgeBase && (
                  <DropdownMenu.Item
                    className={MENU_ITEM_CLASS}
                    onSelect={() => {
                      setActionError(null);
                      setSearchPanel("add");
                    }}
                  >
                    Add to a skill…
                  </DropdownMenu.Item>
                )}
                {kbId && knowledgeBase?.canManage && (
                  <DropdownMenu.Item
                    className={MENU_ITEM_CLASS}
                    onSelect={() => {
                      setActionError(null);
                      setSearchPanel("access");
                    }}
                  >
                    Who can search…
                  </DropdownMenu.Item>
                )}
                {kbId && (
                  <DropdownMenu.Item
                    className={MENU_ITEM_CLASS}
                    disabled={searchPending !== null}
                    onSelect={() => {
                      setActionError(null);
                      setConfirmingStop(true);
                    }}
                  >
                    Stop searching
                  </DropdownMenu.Item>
                )}
                <DropdownMenu.Item
                  className={`${MENU_ITEM_CLASS} text-destructive`}
                  onSelect={() => {
                    setActionError(null);
                    setConfirmingDelete(true);
                  }}
                >
                  Delete
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        )}
      </div>

      {confirmingDelete && (
        <div
          role="alertdialog"
          aria-label="Delete folder?"
          className="mx-2 mb-1 rounded border border-destructive/40 px-2 py-1 text-[10px] leading-tight"
        >
          <p className="text-foreground">Delete folder &lsquo;{folder.name}&rsquo;?</p>
          <div className="mt-1 flex gap-1.5">
            <button
              type="button"
              onClick={() => void confirmDelete()}
              disabled={deleting}
              className="rounded border border-destructive/40 px-1.5 py-0.5 text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              disabled={deleting}
              className="rounded border px-1.5 py-0.5 text-muted-foreground hover:bg-accent disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {searchPanel === "add" && knowledgeBase && (
        <AddToSkillPanel
          kb={knowledgeBase}
          folderName={folder.name}
          uid={uid}
          currentSkillId={currentSkillId}
          onClose={() => setSearchPanel(null)}
        />
      )}
      {searchPanel === "access" && knowledgeBase && (
        <SearchAccessPanel kb={knowledgeBase} folderName={folder.name} onClose={() => setSearchPanel(null)} />
      )}

      {confirmingStop && (
        <div
          role="alertdialog"
          aria-label="Stop searching this folder?"
          className="mx-2 mb-1 rounded border px-2 py-1 text-[10px] leading-tight"
        >
          <p className="text-foreground">
            Stop searching &lsquo;{folder.name}&rsquo;? Its search index is deleted; skills using it lose access. The
            documents stay.
          </p>
          <div className="mt-1 flex gap-1.5">
            <button
              type="button"
              onClick={() => void confirmStopSearching()}
              disabled={searchPending === "stop"}
              className="rounded border px-1.5 py-0.5 text-foreground hover:bg-accent disabled:opacity-50"
            >
              {searchPending === "stop" ? "Stopping…" : "Stop searching"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingStop(false)}
              disabled={searchPending === "stop"}
              className="rounded border px-1.5 py-0.5 text-muted-foreground hover:bg-accent disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {startingProject && (
        <p role="status" className="px-2 pb-1 text-[10px] leading-tight text-muted-foreground">
          Starting a project from &lsquo;{folder.name}&rsquo;…
        </p>
      )}

      {actionError && (
        <div role="alert" className="flex items-start gap-2 px-2 pb-1 text-[10px] leading-tight text-destructive">
          <span className="flex-1">{actionError}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setActionError(null)} className="shrink-0 hover:underline">
            ×
          </button>
        </div>
      )}

      <MoveFeedback state={move} />

      {open && (
        <div className="ml-5 mt-0.5 space-y-0.5">
          {documents.length === 0 && (
            <p className="px-2 py-1 text-xs text-muted-foreground">
              No documents yet
            </p>
          )}
          {documents.map((doc) => (
            <DocListItem
              key={doc.id}
              doc={doc}
              onClick={onDocClick}
              moveTargets={moveTargets.filter((f) => f.id !== doc.folderId)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
