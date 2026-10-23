"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { useEffect, useMemo, useState } from "react";
import { GCSFileBrowser } from "@/components/doc-browser/GCSFileBrowser";
import { useRecentDocuments } from "@/hooks/useRecentDocuments";
import { addDocumentsToProject } from "@/hooks/useProjects";
import { fetchWithAuth } from "@/lib/apiClient";
import { importByReference, isImportError } from "@/lib/importByReference";
import { FLOATING_SURFACE } from "@/lib/surfaces";

/** The tenant's shared library, when the project's skill has one. */
export interface SharedLibrary {
  bucket: string;
  rootPath?: string;
  label?: string;
  /** import-by-reference needs a skill to attribute the import to. */
  skillId: string;
}

interface AddDocumentsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  projectName: string;
  /** Documents already in the project — shown ticked and disabled. */
  existingDocIds: ReadonlySet<string>;
  library?: SharedLibrary | null;
  /** After a successful add — e.g. to say "Added 3 documents". */
  onAdded?: (count: number) => void;
}

interface PickableDoc {
  id: string;
  originalFilename: string;
  folderId?: string | null;
  projectIds?: string[];
}

interface FolderOption {
  id: string;
  name: string;
}

/** Recent documents come back at most 50 at a time (`/api/documents/recent`). */
const RECENT_LIMIT = 50;

/**
 * "Add documents" on a project home (projects design §6.1, "Select").
 *
 * Two tabs: **Your documents** (the whole library, recent first, with search, a
 * folder filter and multi-select; documents already in the project are ticked
 * and disabled) and **Shared library** (the tenant's shared documents — a pick
 * imports it by reference into the user's own library, then tags it). Every
 * state renders: loading, load error with Retry, an empty library, "Adding N…",
 * and the documents that could not be added, by filename (NEVER SILENT #8).
 */
export function AddDocumentsDialog({
  open,
  onOpenChange,
  projectId,
  projectName,
  existingDocIds,
  library,
  onAdded,
}: AddDocumentsDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content
          className={`${FLOATING_SURFACE} fixed left-1/2 top-1/2 flex max-h-[85vh] w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 p-4 outline-none`}
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-base font-semibold text-foreground">Add documents to {projectName}</Dialog.Title>
          <Tabs.Root defaultValue="mine" className="flex min-h-0 flex-1 flex-col gap-3">
            <Tabs.List aria-label="Where to add from" className="flex gap-1 border-b">
              {[
                ["mine", "Your documents"],
                ["shared", "Shared library"],
              ].map(([value, label]) => (
                <Tabs.Trigger
                  key={value}
                  value={value}
                  className="-mb-px border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
                >
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <Tabs.Content value="mine" className="flex min-h-0 flex-1 flex-col gap-2 outline-none">
              {open && (
                <YourDocuments
                  projectId={projectId}
                  existingDocIds={existingDocIds}
                  onDone={(count) => {
                    onAdded?.(count);
                    onOpenChange(false);
                  }}
                />
              )}
            </Tabs.Content>
            <Tabs.Content value="shared" className="min-h-0 flex-1 overflow-y-auto outline-none">
              <SharedLibraryPicker projectId={projectId} library={library ?? null} onAdded={onAdded} />
            </Tabs.Content>
          </Tabs.Root>
          <Dialog.Close className="absolute right-3 top-3 rounded px-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Close">
            ×
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function YourDocuments({
  projectId,
  existingDocIds,
  onDone,
}: {
  projectId: string;
  existingDocIds: ReadonlySet<string>;
  onDone: (count: number) => void;
}) {
  const recent = useRecentDocuments(RECENT_LIMIT);
  const [folders, setFolders] = useState<FolderOption[]>([]);
  const [folderId, setFolderId] = useState("");
  const [folderDocs, setFolderDocs] = useState<PickableDoc[] | null>(null);
  const [folderStatus, setFolderStatus] = useState<"idle" | "loading" | "error">("idle");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/folders")
      .then((res) => (res.ok ? (res.json() as Promise<{ folders?: FolderOption[] }>) : { folders: [] }))
      .then((body) => {
        if (!cancelled) setFolders(Array.isArray(body.folders) ? body.folders : []);
      })
      // The folder filter is a convenience; without it the recent list still works.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const loadFolder = useMemo(
    () => (id: string) => {
      setFolderStatus("loading");
      fetchWithAuth(`/api/proxy/api/folders/${encodeURIComponent(id)}/documents`)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json() as Promise<{ documents?: PickableDoc[] }>;
        })
        .then((body) => {
          setFolderDocs(Array.isArray(body.documents) ? body.documents : []);
          setFolderStatus("idle");
        })
        .catch(() => setFolderStatus("error"));
    },
    [],
  );

  useEffect(() => {
    if (!folderId) {
      setFolderDocs(null);
      setFolderStatus("idle");
      return;
    }
    loadFolder(folderId);
  }, [folderId, loadFolder]);

  const source: PickableDoc[] = folderId ? (folderDocs ?? []) : recent.documents;
  const q = query.trim().toLowerCase();
  const rows = q ? source.filter((d) => d.originalFilename.toLowerCase().includes(q)) : source;
  const names = new Map(source.map((d) => [d.id, d.originalFilename]));
  const loading = folderId ? folderStatus === "loading" : recent.status === "loading";
  const failed = folderId ? folderStatus === "error" : recent.status === "error";

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function add() {
    const ids = [...selected];
    setAdding(true);
    setError(null);
    const r = await addDocumentsToProject(projectId, ids);
    setAdding(false);
    if (!r.ok) {
      setError(r.message);
      return;
    }
    if (r.failed.length > 0) {
      const list = r.failed.map((f) => f.filename || names.get(f.id) || "a document").join(", ");
      setError(`Couldn't add: ${list}.`);
      // Keep only the failures selected, so Retry is one click.
      setSelected(new Set(r.failed.map((f) => f.id)));
      return;
    }
    onDone(r.added.length);
  }

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          aria-label="Search your documents"
          placeholder="Search by name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="min-w-0 flex-1 rounded-[var(--radius)] border bg-background px-2 py-1 text-sm"
        />
        <select
          aria-label="Folder"
          value={folderId}
          onChange={(e) => setFolderId(e.target.value)}
          className="rounded-[var(--radius)] border bg-background px-2 py-1 text-sm"
        >
          <option value="">Recent</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>

      <div className="min-h-[8rem] flex-1 overflow-y-auto rounded border">
        {loading && (
          <div role="status" aria-label="Loading your documents" className="space-y-1 p-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-7 animate-pulse rounded bg-muted" />
            ))}
          </div>
        )}
        {!loading && failed && (
          <div role="alert" className="flex items-center gap-2 p-3 text-sm text-destructive">
            <span className="flex-1">Couldn&apos;t load your documents.</span>
            <button
              type="button"
              onClick={() => (folderId ? loadFolder(folderId) : recent.refetch())}
              className="rounded border border-destructive/40 px-2 py-0.5 text-xs hover:bg-destructive/10"
            >
              Retry
            </button>
          </div>
        )}
        {!loading && !failed && source.length === 0 && (
          <p className="p-3 text-sm text-muted-foreground">
            {folderId ? "This folder has no documents." : "No documents yet — upload one from “Your files” in the sidebar."}
          </p>
        )}
        {!loading && !failed && source.length > 0 && rows.length === 0 && (
          <p className="p-3 text-sm text-muted-foreground">Nothing matches &ldquo;{query.trim()}&rdquo;.</p>
        )}
        {!loading && !failed && rows.length > 0 && (
          <ul aria-label="Documents to add" className="divide-y">
            {rows.map((d) => {
              // The home's list is paged; the row's own tags catch the rest.
              const inProject = existingDocIds.has(d.id) || Boolean(d.projectIds?.includes(projectId));
              return (
                <li key={d.id}>
                  <label
                    className={`flex items-center gap-2 px-3 py-1.5 text-sm ${inProject ? "text-muted-foreground" : "cursor-pointer hover:bg-accent"}`}
                  >
                    <input
                      type="checkbox"
                      checked={inProject || selected.has(d.id)}
                      disabled={inProject || adding}
                      onChange={() => toggle(d.id)}
                    />
                    <span className="min-w-0 flex-1 truncate">{d.originalFilename}</span>
                    {inProject && <span className="shrink-0 text-xs">In this project</span>}
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Dialog.Close className="rounded border px-3 py-1.5 text-sm hover:bg-accent">Cancel</Dialog.Close>
        <button
          type="button"
          onClick={() => void add()}
          disabled={selected.size === 0 || adding}
          className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {adding
            ? `Adding ${selected.size}…`
            : selected.size === 0
              ? "Add"
              : `Add ${selected.size} document${selected.size === 1 ? "" : "s"}`}
        </button>
      </div>
    </>
  );
}

type PickState = { label: string; kind: "pending" | "done" | "error"; message?: string };

function SharedLibraryPicker({
  projectId,
  library,
  onAdded,
}: {
  projectId: string;
  library: SharedLibrary | null;
  onAdded?: (count: number) => void;
}) {
  const [pick, setPick] = useState<PickState | null>(null);

  if (!library) {
    return (
      <p className="p-3 text-sm text-muted-foreground">
        There&apos;s no shared library for this project&apos;s assistant. Add documents from &ldquo;Your documents&rdquo;
        instead.
      </p>
    );
  }

  async function onPick(bucket: string, objectName: string, label: string) {
    if (!library) return;
    setPick({ label, kind: "pending" });
    // The same per-user copy the workbench makes when a shared document is
    // picked; that copy is what gets tagged (design D2).
    const imported = await importByReference(bucket, objectName, library.skillId);
    if (isImportError(imported)) {
      setPick({ label, kind: "error", message: `Couldn't bring in ${label}: ${imported.message}` });
      return;
    }
    const r = await addDocumentsToProject(projectId, [imported.doc.id]);
    if (!r.ok) {
      setPick({ label, kind: "error", message: r.message });
    } else if (r.failed.length > 0) {
      setPick({ label, kind: "error", message: `Couldn't add ${label}${r.failed[0].reason ? `: ${r.failed[0].reason}` : "."}` });
    } else {
      setPick({ label, kind: "done", message: r.added.length > 0 ? `Added ${label}` : `${label} is already in this project` });
      if (r.added.length > 0) onAdded?.(1);
    }
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Pick a document from {library.label || "the shared library"} to add it. It&apos;s added to your own library
        first, the same as opening it in a chat.
      </p>
      {pick && (
        <p
          role={pick.kind === "error" ? "alert" : "status"}
          className={`text-xs ${pick.kind === "error" ? "text-destructive" : pick.kind === "done" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground"}`}
        >
          {pick.kind === "pending" ? `Adding ${pick.label}…` : pick.message}
        </p>
      )}
      <div className="max-h-[50vh] overflow-y-auto rounded border">
        <GCSFileBrowser bucket={library.bucket} rootPath={library.rootPath ?? ""} onPick={(b, o, l) => void onPick(b, o, l)} />
      </div>
    </div>
  );
}
