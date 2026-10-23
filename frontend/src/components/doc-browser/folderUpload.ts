/**
 * Folder upload helpers for UploadDropZone — pure, so they unit-test without
 * a DOM: relative path → library folder name, client-side junk filtering,
 * drag-and-drop directory traversal, a small concurrency pool and the
 * terminal summary.
 *
 * Library folders are flat on the backend: the relative directory path
 * (`Reports/2026`) is itself the folder name, sent as the `folder_path` form
 * field together with `skip_unchanged=true`.
 */

/** One file picked from a folder, with its path relative to the drop/pick root. */
export interface FolderFile {
  file: File;
  /** e.g. `Reports/2026/q1.pdf`. A loose top-level file is just `q1.pdf`. */
  relativePath: string;
}

export type UploadOutcomeStatus = "uploaded" | "unchanged" | "unsupported" | "failed";

export interface UploadOutcome {
  /** Friendly name shown to the user — the relative path for folder uploads. */
  name: string;
  status: UploadOutcomeStatus;
  error?: string;
}

export interface FolderUploadSummary {
  uploaded: number;
  unchanged: number;
  /** Not supported by the backend + junk filtered out before upload. */
  skipped: number;
  failed: number;
  failedNames: { name: string; error?: string }[];
}

/** Directory part of a relative path: `Reports/2026/q1.pdf` → `Reports/2026`; `q1.pdf` → ``. */
export function folderPathFromRelative(relativePath: string): string {
  const parts = relativePath.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.slice(0, -1).join("/");
}

/**
 * Obvious junk that should never reach the library: dotfiles (`.DS_Store`),
 * anything under a dot-directory (`.git/…`) and `node_modules/`.
 */
export function isJunkPath(relativePath: string): boolean {
  const parts = relativePath.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.some((p) => p.startsWith(".") || p === "node_modules");
}

/** Split picked/dropped files into the ones to upload and the junk to skip (counted, never silent). */
export function partitionFolderFiles(files: FolderFile[]): { keep: FolderFile[]; junk: FolderFile[] } {
  const keep: FolderFile[] = [];
  const junk: FolderFile[] = [];
  for (const f of files) (isJunkPath(f.relativePath) ? junk : keep).push(f);
  return { keep, junk };
}

/** Files from an `<input webkitdirectory>` — each carries `webkitRelativePath`. */
export function filesFromDirectoryInput(list: FileList | File[]): FolderFile[] {
  return Array.from(list).map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  }));
}

// ── Drag-and-drop directory traversal ────────────────────────────────────────

/** `readEntries` pages (Chrome returns ≤100 per call): call until an empty batch. */
export async function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  const all: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise<File>((resolve, reject) => entry.file(resolve, reject));
}

/** Recursively collect every file under an entry, with its path relative to the drop root. */
export async function collectEntryFiles(entry: FileSystemEntry, prefix = ""): Promise<FolderFile[]> {
  const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await entryFile(entry as FileSystemFileEntry);
    return [{ file, relativePath }];
  }
  if (entry.isDirectory) {
    const children = await readAllEntries((entry as FileSystemDirectoryEntry).createReader());
    const nested = await Promise.all(children.map((c) => collectEntryFiles(c, relativePath)));
    return nested.flat();
  }
  return [];
}

/**
 * Entries for a drop, or null when the browser lacks `webkitGetAsEntry` or
 * nothing dropped is a directory (caller keeps the plain-file behaviour).
 * MUST be called synchronously inside the drop handler — the DataTransfer is
 * emptied once the event returns.
 */
export function directoryDropEntries(dt: DataTransfer): FileSystemEntry[] | null {
  const items = dt.items ? Array.from(dt.items) : [];
  if (items.length === 0 || typeof items[0].webkitGetAsEntry !== "function") return null;
  const entries = items
    .filter((i) => i.kind === "file")
    .map((i) => i.webkitGetAsEntry())
    .filter((e): e is FileSystemEntry => e !== null);
  return entries.some((e) => e.isDirectory) ? entries : null;
}

// ── Concurrency + summary ────────────────────────────────────────────────────

/** Run `worker` over `items` with at most `limit` in flight; results keep input order. */
export async function runWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  async function lane() {
    while (next < items.length) {
      const i = next++;
      results[i] = await worker(items[i]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return results;
}

/** A 400 whose detail names an unsupported type is "skipped", not "failed". */
export function isUnsupportedError(status: number, message: string): boolean {
  return status === 400 && /unsupported|not supported/i.test(message);
}

export function summarizeOutcomes(outcomes: UploadOutcome[], junkCount = 0): FolderUploadSummary {
  const summary: FolderUploadSummary = { uploaded: 0, unchanged: 0, skipped: junkCount, failed: 0, failedNames: [] };
  for (const o of outcomes) {
    if (o.status === "uploaded") summary.uploaded++;
    else if (o.status === "unchanged") summary.unchanged++;
    else if (o.status === "unsupported") summary.skipped++;
    else {
      summary.failed++;
      summary.failedNames.push({ name: o.name, error: o.error });
    }
  }
  return summary;
}
