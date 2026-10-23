"use client";

import { useEffect, useId, useRef, useState } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import type { Folder, ParsedDocument, ParseStatus } from "@/hooks/useDocBrowser";
import { fetchWithAuth } from "@/lib/apiClient";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
import { DocumentPreview } from "@/components/document/DocumentPreview";
import { DOC_DRAG_TYPE, MENU_CONTENT_CLASS, MENU_ITEM_CLASS, renameDocument, useMoveDocument } from "./folderActions";
import { MoveFeedback } from "./MoveFeedback";
import Link from "next/link";
import { AddToProjectMenu, type ProjectMenuFeedback } from "@/components/projects/AddToProjectMenu";
import { NewProjectInline } from "@/components/projects/NewProjectInline";
import { ProjectChips } from "@/components/projects/ProjectChips";
import { createProjectWithFreeName } from "@/hooks/useProjects";

/** Keep menu clicks/keys from reaching the row (which would open the doc). */
const stop = (e: React.SyntheticEvent) => e.stopPropagation();

const STATUS_DOT: Record<ParseStatus, { color: string; title: string }> = {
  parsed: { color: "bg-emerald-500", title: "Parsed" },
  pending: { color: "bg-amber-400 animate-pulse", title: "Pending" },
  pending_ai_extraction: { color: "bg-sky-400 animate-pulse", title: "Extracting" },
  failed: { color: "bg-destructive", title: "Failed" },
};

const FORMAT_COLORS: Record<string, string> = {
  pdf: "bg-red-100 text-red-700",
  docx: "bg-blue-100 text-blue-700",
  pptx: "bg-orange-100 text-orange-700",
  xlsx: "bg-green-100 text-green-700",
  csv: "bg-green-100 text-green-700",
  md: "bg-purple-100 text-purple-700",
  txt: "bg-muted text-muted-foreground",
};

/** Length of a filename without its extension (`report.pdf` → 6), for selecting the stem. */
function stemLength(name: string): number {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? dot : name.length;
}

interface DocListItemProps {
  doc: ParsedDocument;
  onClick: (doc: ParsedDocument) => void;
  /** Folders this document can be moved to (by name — never shown as ids). */
  moveTargets?: Folder[];
}

export function DocListItem({ doc, onClick, moveTargets = [] }: DocListItemProps) {
  const nameId = useId();
  const move = useMoveDocument();
  const [reparsing, setReparsing] = useState(false);
  const [reparseError, setReparseError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Delete (always confirmed first)
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Rename — same inline-edit pattern as a folder (DocListFolder).
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(doc.originalFilename);
  const [renaming, setRenaming] = useState(false);
  // The name the server returned, shown until the list catches up with it
  // (i.e. while the prop still carries the name it was renamed FROM).
  const [renamed, setRenamed] = useState<{ from: string; to: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const keepFocusRef = useRef(false);

  // Projects: "Add to project…" feedback, and the inline "+ New project…" field (E3).
  const [projectFeedback, setProjectFeedback] = useState<ProjectMenuFeedback | null>(null);
  const [namingProject, setNamingProject] = useState(false);

  const name = renamed && renamed.from === doc.originalFilename ? renamed.to : doc.originalFilename;

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.setSelectionRange(0, stemLength(inputRef.current.value));
    }
  }, [editing]);

  const dot = STATUS_DOT[doc.parseStatus] ?? STATUS_DOT.pending;
  const fmtColor =
    FORMAT_COLORS[doc.sourceFormat.toLowerCase()] ??
    "bg-muted text-muted-foreground";

  const errorMsg = doc.parseStatus === "failed" && doc.parseError
    ? doc.parseError
    : doc.parseStatus === "failed"
    ? "Parse failed — content unavailable"
    : null;

  // Show retry for failed docs, and for "parsed" docs with no blocks (uploaded
  // before the pipeline stored blocks in Firestore). a2uiComponents is an
  // optional render layer written by a separate pipeline, not a content signal.
  const needsReparse =
    doc.parseStatus === "failed" ||
    (doc.parseStatus === "parsed" && !doc.blockCount);

  async function handleReparse(e: React.MouseEvent) {
    e.stopPropagation();
    setReparsing(true);
    setReparseError(null);
    try {
      const res = await fetchWithAuth(`/api/proxy/api/documents/${doc.id}/reparse`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setReparseError((body as { detail?: string }).detail ?? `Error ${res.status}`);
      }
      // On success: Firestore real-time listener updates parseStatus automatically.
    } catch {
      setReparseError("Network error — try again");
    } finally {
      setReparsing(false);
    }
  }

  function askDelete() {
    setActionError(null);
    setEditing(false);
    setConfirmingDelete(true);
  }

  async function confirmDelete() {
    setDeleting(true);
    setActionError(null);
    try {
      const res = await fetchWithAuth(`/api/proxy/api/documents/${doc.id}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = (body as { detail?: unknown }).detail;
        setActionError(typeof detail === "string" ? detail : `Delete failed (HTTP ${res.status})`);
      } else {
        // Firestore listener removes the item; the API fallback needs a nudge.
        notifyDocumentsChanged();
      }
    } catch {
      setActionError("Delete failed — try again");
    } finally {
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }

  function startNewProject() {
    keepFocusRef.current = true;
    setProjectFeedback(null);
    setNamingProject(true);
  }

  /** E3: create the project with this document in it. The name was pre-filled,
   * so a clash gets " (2)" rather than an error; the user stays where they are. */
  async function createProjectWithDoc(projectName: string): Promise<string | null> {
    const result = await createProjectWithFreeName({ name: projectName, docIds: [doc.id] });
    if (!result.ok) return result.message;
    setNamingProject(false);
    const failure = result.documents?.failed[0];
    setProjectFeedback(
      failure
        ? {
            kind: "error",
            message: `Created ${result.project.name}, but couldn't add ${name}${failure.reason ? `: ${failure.reason}` : "."}`,
          }
        : { kind: "done", message: `Added to ${result.project.name}`, projectId: result.project.id },
    );
    return null;
  }

  function startRename() {
    keepFocusRef.current = true;
    setDraft(name);
    setActionError(null);
    setConfirmingDelete(false);
    setEditing(true);
  }

  function cancelRename() {
    setEditing(false);
    setActionError(null);
  }

  async function saveRename() {
    const next = draft.trim();
    if (!next) {
      setActionError("A document name can't be empty.");
      return;
    }
    if (next === name) {
      cancelRename();
      return;
    }
    setRenaming(true);
    setActionError(null);
    const result = await renameDocument(doc.id, next);
    setRenaming(false);
    if (result.ok) {
      setRenamed({ from: doc.originalFilename, to: result.name });
      setEditing(false);
    } else {
      setActionError(result.message);
    }
  }

  const editRow = (
    <div className="flex w-full items-center gap-1.5 px-2 py-1">
      <input
        ref={inputRef}
        aria-label="Document name"
        aria-describedby={`${nameId}-hint`}
        value={draft}
        disabled={renaming}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") { e.preventDefault(); void saveRename(); }
          else if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
        }}
        className="min-w-0 flex-1 rounded border bg-background px-1.5 py-0.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
      />
      <span id={`${nameId}-hint`} className="shrink-0 text-[10px] text-muted-foreground">
        {renaming ? "Saving…" : "Enter ↵ / Esc"}
      </span>
    </div>
  );

  const docRow = (
    <div
      role="button"
      tabIndex={0}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          DOC_DRAG_TYPE,
          JSON.stringify({ id: doc.id, folderId: doc.folderId, filename: doc.originalFilename }),
        );
        // A folder drop MOVES the document; a project drop ADDS it (copy).
        e.dataTransfer.effectAllowed = "copyMove";
      }}
      onClick={() => onClick(doc)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(doc); } }}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring cursor-pointer"
      title={errorMsg ?? dot.title}
    >
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${dot.color}`}
        aria-label={dot.title}
      />
      <span id={nameId} className="min-w-0 flex-1 truncate text-foreground">
        {name}
      </span>
      <ProjectChips projectIds={doc.projectIds} />
      {doc.source === "generated" && (
        <span
          className="shrink-0 rounded bg-violet-100 px-1 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300"
          title="Created by the assistant"
        >
          Created
        </span>
      )}
      {doc.sourceFormat && (
        <span
          className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-medium uppercase ${fmtColor}`}
        >
          {doc.sourceFormat}
        </span>
      )}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          aria-label="Document actions"
          aria-describedby={nameId}
          disabled={move.pending}
          onClick={stop}
          onKeyDown={stop}
          className="shrink-0 rounded px-1 text-muted-foreground opacity-0 hover:bg-accent focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring group-hover:opacity-100 data-[state=open]:opacity-100 disabled:opacity-50"
        >
          {move.pending ? "…" : "⋯"}
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            className={MENU_CONTENT_CLASS}
            onClick={stop}
            onKeyDown={stop}
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
            <DropdownMenu.Separator className="my-1 h-px bg-border" />
            <DropdownMenu.Label className="px-2 py-1 text-[10px] font-medium text-muted-foreground">
              {moveTargets.length > 0 ? "Move to…" : "No other folders to move to"}
            </DropdownMenu.Label>
            {moveTargets.map((f) => (
              <DropdownMenu.Item
                key={f.id}
                className={MENU_ITEM_CLASS}
                aria-label={`Move to ${f.name}`}
                onSelect={() => void move.move(doc.id, f.id)}
              >
                {f.name}
              </DropdownMenu.Item>
            ))}
            <DropdownMenu.Separator className="my-1 h-px bg-border" />
            {/* A folder holds a document; projects only tag it (it stays put). */}
            <AddToProjectMenu
              docId={doc.id}
              filename={name}
              projectIds={doc.projectIds ?? []}
              onFeedback={setProjectFeedback}
              onNewProject={startNewProject}
            />
            <DropdownMenu.Separator className="my-1 h-px bg-border" />
            <DropdownMenu.Item className={`${MENU_ITEM_CLASS} text-destructive`} onSelect={askDelete}>
              Delete
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); askDelete(); }}
        onKeyDown={stop}
        disabled={deleting}
        title="Delete document"
        aria-label="Delete document"
        aria-describedby={nameId}
        className="shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 rounded p-0.5 transition-opacity disabled:opacity-50 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
      >
        {deleting ? (
          <span className="block h-3 w-3 text-[10px] leading-3">…</span>
        ) : (
          <svg className="h-3 w-3" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0V6z"/>
            <path fillRule="evenodd" d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1v1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z"/>
          </svg>
        )}
      </button>
    </div>
  );

  const row = (
    <div className="w-full group">
      {editing ? editRow : docRow}
      {confirmingDelete && (
        <div
          role="alertdialog"
          aria-label="Delete document?"
          className="mx-2 mb-1 rounded border border-destructive/40 px-2 py-1 text-[10px] leading-tight"
        >
          <p className="text-foreground">Delete &lsquo;{name}&rsquo;? This can&rsquo;t be undone.</p>
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
      {(errorMsg || needsReparse) && (
        <div className="flex items-center gap-2 px-2 pb-1">
          {errorMsg && (
            <p className="flex-1 text-[10px] text-destructive leading-tight">
              {errorMsg}
            </p>
          )}
          {!errorMsg && needsReparse && (
            <p className="flex-1 text-[10px] text-muted-foreground leading-tight">
              No content — re-parse to load
            </p>
          )}
          {needsReparse && (
            <button
              type="button"
              onClick={handleReparse}
              disabled={reparsing}
              className="shrink-0 rounded border border-destructive/40 px-1.5 py-0.5 text-[10px] text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              {reparsing ? "…" : "Retry"}
            </button>
          )}
        </div>
      )}
      <MoveFeedback state={move} />
      {namingProject && (
        <NewProjectInline
          initialName={name.slice(0, stemLength(name))}
          onCreate={createProjectWithDoc}
          onCancel={() => setNamingProject(false)}
        />
      )}
      {projectFeedback && (
        <div
          role={projectFeedback.kind === "error" ? "alert" : "status"}
          className={`flex items-start gap-2 px-2 pb-1 text-[10px] leading-tight ${
            projectFeedback.kind === "error" ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          <span className="flex-1">
            {projectFeedback.message}
            {projectFeedback.kind === "done" && projectFeedback.projectId && (
              <>
                {" · "}
                <Link href={`/projects/${encodeURIComponent(projectFeedback.projectId)}/chat`} className="underline underline-offset-2 hover:text-foreground">
                  Open
                </Link>
              </>
            )}
          </span>
          {projectFeedback.kind !== "pending" && (
            <button type="button" aria-label="Dismiss" onClick={() => setProjectFeedback(null)} className="shrink-0 hover:underline">
              ×
            </button>
          )}
        </div>
      )}
      {actionError && (
        <div role="alert" className="flex items-start gap-2 px-2 pb-1 text-[10px] leading-tight text-destructive">
          <span className="flex-1">{actionError}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setActionError(null)} className="shrink-0 hover:underline">
            ×
          </button>
        </div>
      )}
      {reparseError && (
        <p className="px-2 pb-1 text-[10px] text-destructive leading-tight">
          {reparseError}
        </p>
      )}
    </div>
  );

  // While renaming (or naming a new project), the row holds a text box: no
  // hover preview over it.
  if (editing || namingProject) return row;

  // Hover preview for every document (lazy — mounts only when the tooltip
  // opens, after Radix's 250ms hover delay). Image/PDF → page-1 image from the
  // owner-checked /api/documents/{id}/thumbnail route (format known here, so
  // no record fetch first); docparse'd (docx, pptx, …) → structured block
  // preview; otherwise a doc icon. Failures show "Preview unavailable".
  return (
    <DocumentPreview
      source={{
        kind: "doc",
        docId: doc.id,
        format: doc.sourceFormat || undefined,
        version: doc.updatedAt ?? doc.createdAt ?? null,
      }}
      label={name}
    >
      {row}
    </DocumentPreview>
  );
}
