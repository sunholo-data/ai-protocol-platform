"use client";

import { useEffect, useRef, useState, type MutableRefObject, type RefObject } from "react";
import { createPortal } from "react-dom";
import { getIdToken } from "@/lib/firebase";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
import {
  type FolderFile,
  type FolderUploadSummary,
  type UploadOutcome,
  collectEntryFiles,
  directoryDropEntries,
  filesFromDirectoryInput,
  folderPathFromRelative,
  isUnsupportedError,
  partitionFolderFiles,
  runWithConcurrency,
  summarizeOutcomes,
} from "./folderUpload";

interface UploadProgress {
  filename: string;
  pct: number;
  status: "uploading" | "done" | "error";
  error?: string;
}

interface UploadDropZoneProps {
  folderId?: string;
  skillId?: string;
  /** Upload from a project's page: the backend also adds each stored file to
   * this project, in the same request, so an aborted folder upload leaves
   * every finished file in the project. */
  projectId?: string;
  onUploadComplete?: (docId: string, filename: string) => void;
  /** Wrapper spacing. Defaults to the sidebar inset; a host that already
   * provides its own gutter (the fresh-chat landing) passes "". */
  className?: string;
  /** "zone" (default): the dashed drop target. "button": a compact round `+`
   * for the chat composer — same upload pipeline, with its progress and
   * errors shown in a small panel above it. */
  variant?: "zone" | "button" | "composer";
  /** variant="composer": set to a function that opens the file picker, so a
   * menu item (the composer's + menu) can start an upload. */
  openRef?: MutableRefObject<(() => void) | null>;
  /** variant="composer": dropping files anywhere on this element uploads them,
   * with a full-size "Drop to start a chat about this file" overlay. */
  dropTarget?: RefObject<HTMLElement | null>;
}

const MAX_CONCURRENT = 4;
/** Folder uploads can be hundreds of files — keep the pool small. */
const FOLDER_CONCURRENCY = 3;
/** Into a project, the project's list (API-backed) refetches at most this
 * often while a folder uploads, so files appear as they land. */
const FOLDER_REFRESH_MS = 3000;

interface FolderBatch {
  total: number;
  completed: number;
  summary: FolderUploadSummary | null;
}

interface PostResult {
  status: number; // 0 = network error, -1 = stalled, -2 = cancelled
  body: string;
}

/** The backend saved the file but couldn't add it to the project — its reason. */
function projectErrorOf(body: string): string | null {
  try {
    const reason = (JSON.parse(body) as { projectError?: unknown }).projectError;
    return typeof reason === "string" && reason ? reason : null;
  } catch {
    return null;
  }
}

function notAddedMessage(reason: string): string {
  return `Saved to your library, but not added to the project: ${reason}`;
}

/** FastAPI `detail` is a string or a structured {code, message}; fall back to the status. */
function errorMessage(res: PostResult): string {
  if (res.status === 0) return "Network error";
  if (res.status === STALLED) return "Upload stalled. Try again.";
  if (res.status === CANCELLED) return "Cancelled";
  let message = `Upload failed (${res.status})`;
  try {
    const detail = (JSON.parse(res.body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      message = detail;
    } else if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
      message = (detail as { message: string }).message;
    }
  } catch {
    // non-JSON body — keep the generic status message
  }
  return message;
}

const STALLED = -1;
const CANCELLED = -2;
/** No upload progress for this long means the connection is stuck. */
const UPLOAD_STALL_MS = 60_000;
/** Once every byte is sent, how long the server may take to answer (parsing). */
const SERVER_REPLY_MS = 5 * 60_000;

/**
 * POST one file to the upload endpoint. Never rejects: network errors resolve
 * as status 0, a stalled upload as STALLED, a cancelled one as CANCELLED.
 * `onAbortable` receives a function that cancels this upload.
 */
async function postFile(
  file: File,
  fields: Record<string, string>,
  onProgress?: (pct: number) => void,
  onAbortable?: (cancel: () => void) => void,
): Promise<PostResult> {
  const token = await getIdToken();
  const formData = new FormData();
  formData.append("file", file);
  for (const [k, v] of Object.entries(fields)) if (v) formData.append(k, v);

  return new Promise<PostResult>((resolve) => {
    const xhr = new XMLHttpRequest();
    let ended: number | null = null; // why we aborted, if we did
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const arm = (ms: number) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        ended = STALLED;
        xhr.abort();
      }, ms);
    };
    const finish = (result: PostResult) => {
      clearTimeout(watchdog);
      resolve(result);
    };
    xhr.open("POST", "/api/proxy/api/documents/upload");
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      arm(UPLOAD_STALL_MS);
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100));
    };
    xhr.upload.onload = () => arm(SERVER_REPLY_MS);
    xhr.onload = () => finish({ status: xhr.status, body: xhr.responseText });
    xhr.onerror = () => finish({ status: 0, body: "" });
    xhr.onabort = () => finish({ status: ended ?? CANCELLED, body: "" });
    onAbortable?.(() => {
      ended = CANCELLED;
      xhr.abort();
    });
    arm(UPLOAD_STALL_MS);
    xhr.send(formData);
  });
}

/** "42%" while sending, "Processing…" once sent, plus a cancel control. */
function UploadPct({ pct, onCancel }: { pct: number; onCancel: () => void }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span>{pct >= 100 ? "Processing…" : `${pct}%`}</span>
      <button
        type="button"
        onClick={onCancel}
        aria-label="Cancel upload"
        title="Cancel upload"
        className="rounded px-1 leading-none hover:bg-muted hover:text-foreground"
      >
        ×
      </button>
    </span>
  );
}

export function UploadDropZone({
  folderId = "",
  skillId = "",
  projectId = "",
  onUploadComplete,
  className = "px-3 py-2",
  variant = "zone",
  openRef,
  dropTarget,
}: UploadDropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress[]>([]);
  const [folderBatch, setFolderBatch] = useState<FolderBatch | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  // `webkitdirectory` isn't in React's input types — set it on the DOM node.
  useEffect(() => {
    folderInputRef.current?.setAttribute("webkitdirectory", "");
  }, [variant]);
  const cancelRef = useRef(new Map<string, () => void>());
  const semaphoreRef = useRef(0);
  const queueRef = useRef<File[]>([]);
  const lastRefreshRef = useRef(0);

  function updateProgress(filename: string, patch: Partial<UploadProgress>) {
    setProgress((prev) => {
      const idx = prev.findIndex((p) => p.filename === filename);
      if (idx === -1) return [...prev, { filename, pct: 0, status: "uploading", ...patch }];
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      return next;
    });
  }

  async function uploadFile(file: File) {
    updateProgress(file.name, { status: "uploading", pct: 0 });
    const res = await postFile(
      file,
      { folder_id: folderId, skill_id: skillId, project_id: projectId },
      (pct) => updateProgress(file.name, { pct }),
      (cancel) => cancelRef.current.set(file.name, cancel),
    );
    cancelRef.current.delete(file.name);
    if (res.status <= 0 || res.status >= 400) {
      // Surface the backend's specific reason (NEVER-SILENT) rather than a bare status.
      updateProgress(file.name, { status: "error", pct: 0, error: errorMessage(res) });
      return;
    }
    // The library may be on the API fallback (no Firestore session) — tell it to refetch.
    notifyDocumentsChanged();
    const notAdded = projectErrorOf(res.body);
    if (notAdded) {
      updateProgress(file.name, { status: "error", pct: 100, error: notAddedMessage(notAdded) });
      return;
    }
    updateProgress(file.name, { status: "done", pct: 100 });
    try {
      const data = JSON.parse(res.body) as { docId?: string };
      if (data.docId) onUploadComplete?.(data.docId, file.name);
    } catch {
      // ignore parse error
    }
  }

  /** One file of a folder upload → its outcome. Loose top-level files of a
   * mixed drop have no directory, so they go where single uploads go. */
  async function uploadFolderFile({ file, relativePath }: FolderFile): Promise<UploadOutcome> {
    const folderPath = folderPathFromRelative(relativePath);
    const fields: Record<string, string> = folderPath
      ? { folder_path: folderPath, skip_unchanged: "true", skill_id: skillId, project_id: projectId }
      : { folder_id: folderId, skill_id: skillId, project_id: projectId };
    const res = await postFile(file, fields);
    setFolderBatch((b) => (b ? { ...b, completed: b.completed + 1 } : b));
    if (res.status <= 0 || res.status >= 400) {
      const message = errorMessage(res);
      return {
        name: relativePath,
        status: isUnsupportedError(res.status, message) ? "unsupported" : "failed",
        error: message,
      };
    }
    if (projectId) refreshWhileUploading();
    const notAdded = projectErrorOf(res.body);
    if (notAdded) return { name: relativePath, status: "failed", error: notAddedMessage(notAdded) };
    let unchanged = false;
    try {
      unchanged = Boolean((JSON.parse(res.body) as { unchanged?: boolean }).unchanged);
    } catch {
      // a 2xx with an unreadable body still uploaded
    }
    return { name: relativePath, status: unchanged ? "unchanged" : "uploaded" };
  }

  /** Throttled: a project's document list grows while its folder uploads. */
  function refreshWhileUploading() {
    const now = Date.now();
    if (now - lastRefreshRef.current < FOLDER_REFRESH_MS) return;
    lastRefreshRef.current = now;
    notifyDocumentsChanged();
  }

  async function uploadFolder(files: FolderFile[]) {
    const { keep, junk } = partitionFolderFiles(files);
    lastRefreshRef.current = 0;
    setFolderBatch({ total: keep.length, completed: 0, summary: null });
    const outcomes = await runWithConcurrency(keep, FOLDER_CONCURRENCY, uploadFolderFile);
    // With Firestore the library lists (useDocBrowser) are live listeners, so
    // new folders/documents appear on their own; without a Firebase session
    // they read the API, which needs this nudge. Folder uploads deliberately
    // do NOT call onUploadComplete — that opens each doc as a workbench tab.
    setFolderBatch({ total: keep.length, completed: keep.length, summary: summarizeOutcomes(outcomes, junk.length) });
    notifyDocumentsChanged();
  }

  async function drainQueue() {
    while (queueRef.current.length > 0 && semaphoreRef.current < MAX_CONCURRENT) {
      const file = queueRef.current.shift();
      if (!file) break;
      semaphoreRef.current++;
      uploadFile(file).finally(() => {
        semaphoreRef.current--;
        void drainQueue();
      });
    }
  }

  function enqueue(files: FileList | File[]) {
    const arr = Array.from(files);
    queueRef.current.push(...arr);
    void drainQueue();
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    // Entries must be read synchronously — the DataTransfer empties after this handler.
    const entries = directoryDropEntries(e.dataTransfer);
    if (!entries) {
      enqueue(e.dataTransfer.files);
      return;
    }
    setFolderBatch({ total: 0, completed: 0, summary: null });
    void Promise.all(entries.map((entry) => collectEntryFiles(entry)))
      .then((nested) => uploadFolder(nested.flat()))
      .catch((err: unknown) => {
        setFolderBatch({
          total: 0,
          completed: 0,
          summary: {
            uploaded: 0,
            unchanged: 0,
            skipped: 0,
            failed: 1,
            failedNames: [{ name: "Dropped folder", error: err instanceof Error ? err.message : "Could not read the folder" }],
          },
        });
      });
  }

  function onFolderInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files ? filesFromDirectoryInput(e.target.files) : [];
    e.target.value = "";
    if (files.length > 0) void uploadFolder(files);
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) enqueue(e.target.files);
    e.target.value = "";
  }

  const active = progress.filter((p) => p.status === "uploading");
  const done = progress.filter((p) => p.status === "done").length;
  const errors = progress.filter((p) => p.status === "error");
  const folderBusy = folderBatch !== null && folderBatch.summary === null;

  const fileInput = (
    <input
      ref={inputRef}
      type="file"
      multiple
      className="hidden"
      accept=".pdf,.docx,.pptx,.xlsx,.odt,.odp,.ods,.epub,.eml,.mbox,.html,.htm,.md,.csv,.txt,.png,.jpg,.jpeg,.tex,.mp4,.m4v,.mov,.webm,.mp3,.wav,.m4a,.ogg,.flac"
      onChange={onInputChange}
    />
  );

  if (variant === "composer") {
    const status = active.length > 0 || errors.length > 0;
    const overlayHost = dropTarget?.current ?? null;
    return (
      <>
        <ComposerDropWiring
          dropTarget={dropTarget}
          openRef={openRef}
          open={() => inputRef.current?.click()}
          onDragging={setIsDragging}
          onDrop={onDrop}
        />
        {fileInput}
        {isDragging && overlayHost
          ? createPortal(
              <div
                aria-hidden
                className="pointer-events-none absolute inset-3 z-40 grid place-items-center rounded-[var(--radius)] border-2 border-dashed border-primary bg-background/90 motion-safe:animate-in motion-safe:fade-in-0"
              >
                <div className="text-center">
                  <svg className="mx-auto h-8 w-8 text-primary" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                    <path d="M12 16V8M9 11l3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1" strokeLinecap="round" />
                  </svg>
                  <p className="mt-2 text-sm font-medium text-foreground">Drop to add it to this chat</p>
                </div>
              </div>,
              overlayHost,
            )
          : null}
        {/* NEVER SILENT: an upload in flight or failed shows above the composer. */}
        {status ? (
          <div
            role="status"
            className="absolute bottom-full left-0 z-20 mb-2 w-64 space-y-1 rounded-[var(--radius)] border border-border bg-background p-2 text-xs shadow-sm"
          >
            {active.map((p) => (
              <div key={p.filename} className="flex items-center justify-between gap-2 text-muted-foreground">
                <span className="truncate">{p.filename}</span>
                <UploadPct pct={p.pct} onCancel={() => cancelRef.current.get(p.filename)?.()} />
              </div>
            ))}
            {errors.map((p) => (
              <p key={p.filename} role="alert" className="text-destructive">
                {p.filename}: {p.error}
              </p>
            ))}
          </div>
        ) : null}
      </>
    );
  }

  if (variant === "button") {
    const status = active.length > 0 || errors.length > 0;
    return (
      <div className="relative shrink-0">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          aria-label="Attach a document"
          title="Attach a document"
          className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
            <path d="M12 5v14M5 12h14" strokeLinecap="round" />
          </svg>
        </button>
        {fileInput}
        {/* NEVER SILENT: an upload in flight or failed is visible right here. */}
        {status ? (
          <div
            role="status"
            className="absolute bottom-full left-0 z-20 mb-2 w-64 space-y-1 rounded-[var(--radius)] border border-border bg-background p-2 text-xs"
          >
            {active.map((p) => (
              <div key={p.filename} className="flex items-center justify-between gap-2 text-muted-foreground">
                <span className="truncate">{p.filename}</span>
                <UploadPct pct={p.pct} onCancel={() => cancelRef.current.get(p.filename)?.()} />
              </div>
            ))}
            {errors.map((p) => (
              <p key={p.filename} className="text-destructive">
                {p.filename}: {p.error}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className={["space-y-2", className].filter(Boolean).join(" ")}>
      <button
        type="button"
        onDragEnter={() => setIsDragging(true)}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={[
          "flex w-full cursor-pointer flex-col items-center justify-center rounded-[var(--radius)] border border-dashed px-6 py-8 text-center transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          isDragging ? "border-primary bg-primary/5" : "border-border bg-muted/50 hover:border-primary",
        ].join(" ")}
      >
        <svg className="mb-2 h-6 w-6 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M12 16V8M9 11l3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1" strokeLinecap="round" />
        </svg>
        {/* Phones have no drag-and-drop, so "drop files here" is wrong there:
            show a plain secondary action instead. It needs no handler — the
            click bubbles to the zone, which opens the picker. (Spans, not <p>:
            the zone is a <button>, which holds phrasing content only.) */}
        <span className="hidden text-sm font-medium text-foreground md:block">
          {isDragging ? "Drop to upload" : "Drop files here or click to browse"}
        </span>
        <span
          role="presentation"
          className="inline-flex min-h-[44px] items-center rounded-[var(--radius)] border border-primary bg-background px-4 text-sm font-medium text-primary md:hidden"
        >
          Upload a document
        </span>
        <span className="mt-1 block text-xs text-muted-foreground">
          PDF, DOCX, PPTX, XLSX, CSV, TXT, MD…
        </span>
      </button>
      {fileInput}

      <button
        type="button"
        onClick={() => folderInputRef.current?.click()}
        disabled={folderBusy}
        className="w-full rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-60"
      >
        Upload folder
      </button>
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        data-testid="folder-input"
        onChange={onFolderInputChange}
      />

      {folderBatch ? <FolderBatchStatus batch={folderBatch} onDismiss={() => setFolderBatch(null)} /> : null}

      {/* Active uploads */}
      {active.map((p) => (
        <div key={p.filename} className="space-y-0.5">
          <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span className="truncate">{p.filename}</span>
            <UploadPct pct={p.pct} onCancel={() => cancelRef.current.get(p.filename)?.()} />
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width]"
              style={{ width: `${p.pct}%` }}
            />
          </div>
        </div>
      ))}

      {/* Errors */}
      {errors.map((p) => (
        <p key={p.filename} role="alert" className="text-[11px] text-destructive">
          {p.filename}: {p.error}
        </p>
      ))}

      {/* Summary */}
      {done > 0 && active.length === 0 && errors.length === 0 && (
        <p className="text-center text-[11px] text-muted-foreground">
          {done} file{done !== 1 ? "s" : ""} uploaded
        </p>
      )}
    </div>
  );
}

/** NEVER SILENT: a folder upload shows live progress, then a terminal summary. */
function FolderBatchStatus({ batch, onDismiss }: { batch: FolderBatch; onDismiss: () => void }) {
  const { summary } = batch;
  if (!summary) {
    const pct = batch.total > 0 ? Math.round((batch.completed / batch.total) * 100) : 0;
    return (
      <div role="status" className="space-y-0.5">
        <p className="text-[11px] text-muted-foreground">
          {batch.total === 0 ? "Reading folder…" : `Uploading ${batch.completed} of ${batch.total}…`}
        </p>
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  const parts = [`${summary.uploaded} uploaded`];
  if (summary.unchanged) parts.push(`${summary.unchanged} unchanged`);
  if (summary.skipped) parts.push(`${summary.skipped} skipped (not supported)`);
  if (summary.failed) parts.push(`${summary.failed} failed`);
  const nothing = summary.uploaded + summary.unchanged + summary.skipped + summary.failed === 0;

  const failedList = (
    <ul className="space-y-0.5">
      {summary.failedNames.map((f) => (
        <li key={f.name} className="break-all">
          {f.name}
          {f.error ? `: ${f.error}` : ""}
        </li>
      ))}
    </ul>
  );

  return (
    <div role="status" className="space-y-1 text-[11px]">
      <div className="flex items-start justify-between gap-2">
        <p className={summary.failed ? "text-destructive" : "text-muted-foreground"}>
          {nothing ? "The folder had no files to upload." : `Folder upload: ${parts.join(", ")}.`}
        </p>
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="text-muted-foreground hover:text-foreground">
          ×
        </button>
      </div>
      {summary.failed > 0 ? (
        <div className="text-destructive">
          {summary.failedNames.length > 5 ? (
            <details>
              <summary className="cursor-pointer">Show {summary.failedNames.length} failed files</summary>
              {failedList}
            </details>
          ) : (
            failedList
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Wires the composer variant: exposes "open the picker" through `openRef`, and
 * listens for files dragged over `dropTarget`. A counter, not a boolean, so
 * moving between child elements doesn't flicker the overlay off.
 */
function ComposerDropWiring({
  dropTarget,
  openRef,
  open,
  onDragging,
  onDrop,
}: {
  dropTarget?: RefObject<HTMLElement | null>;
  openRef?: MutableRefObject<(() => void) | null>;
  open: () => void;
  onDragging: (v: boolean) => void;
  onDrop: (e: React.DragEvent) => void;
}) {
  const openLatest = useRef(open);
  const dropLatest = useRef(onDrop);
  openLatest.current = open;
  dropLatest.current = onDrop;

  useEffect(() => {
    if (!openRef) return;
    openRef.current = () => openLatest.current();
    return () => {
      openRef.current = null;
    };
  }, [openRef]);

  useEffect(() => {
    const el = dropTarget?.current;
    if (!el) return;
    let depth = 0;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth += 1;
      onDragging(true);
    };
    const over = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) onDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = 0;
      // The handler reads only preventDefault and dataTransfer.
      dropLatest.current(e as unknown as React.DragEvent);
    };
    el.addEventListener("dragenter", enter);
    el.addEventListener("dragover", over);
    el.addEventListener("dragleave", leave);
    el.addEventListener("drop", drop);
    return () => {
      el.removeEventListener("dragenter", enter);
      el.removeEventListener("dragover", over);
      el.removeEventListener("dragleave", leave);
      el.removeEventListener("drop", drop);
    };
  }, [dropTarget, onDragging]);

  return null;
}
