"use client";

import { useEffect, useRef, useState } from "react";
import { Folder, GitBranch, Plus } from "lucide-react";
import {
  addSource,
  getSource,
  noAnswer,
  removeSource,
  setSourceWritable,
  syncSourceUntilDone,
  useProjectSources,
  type ProjectSource,
  type SourceFileNote,
  type SourceCredential,
  type SourceReport,
  type SyncOptions,
} from "@/hooks/useProjectSources";
import { fetchWithAuth } from "@/lib/apiClient";
import { useFormat, type Formatter } from "@/i18n";

/**
 * The project's sources (github-sources.md §7-§10): GitHub folders and library
 * folders the project stays in step with. Each action shows its pending state,
 * its progress (a sync that reaches the server's time limit continues, and says
 * how many files are left), and a terminal outcome.
 */
export function ProjectSources({ projectId }: { projectId: string }) {
  const { sources, credentials, writers, status, reload } = useProjectSources(projectId);
  const [adding, setAdding] = useState(false);
  // The preview a just-added source ran: previews aren't stored on the source.
  const [previews, setPreviews] = useState<Record<string, SourceReport>>({});

  return (
    <section aria-labelledby="project-sources-heading" className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="project-sources-heading" className="text-base font-semibold text-foreground">
          Sources
        </h2>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2.5 py-1 text-sm hover:bg-accent"
          >
            <Plus className="h-4 w-4" aria-hidden /> Add a source
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Keep this project in step with a folder in GitHub or in your library. Changes arrive within a few minutes; files you
        uploaded yourself are never touched.
      </p>
      {status === "error" && (
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load this project&apos;s sources.{" "}
          <button type="button" className="underline" onClick={reload}>
            Try again
          </button>
        </p>
      )}
      {status === "ready" && sources.length === 0 && !adding && (
        <p className="text-sm text-muted-foreground">No sources yet.</p>
      )}
      <ul className="space-y-2">
        {sources.map((s) => (
          <SourceRow key={s.id} projectId={projectId} source={s} initialReport={previews[s.id]} onChanged={reload} />
        ))}
      </ul>
      {adding && (
        <AddSourceForm
          projectId={projectId}
          credentials={credentials}
          writers={writers}
          onDone={(sourceId, report) => {
            if (report) setPreviews((p) => ({ ...p, [sourceId]: report }));
            setAdding(false);
            reload();
          }}
          onCancel={() => setAdding(false)}
        />
      )}
    </section>
  );
}

type Busy = null | { what: "preview" | "sync" | "remove"; note?: string };

/** How often a running sync's progress is read. */
export const PROGRESS_POLL_MS = 3000;
type Notice = null | { kind: "status" | "alert"; text: string };

function SourceRow({
  projectId,
  source,
  initialReport,
  onChanged,
}: {
  projectId: string;
  source: ProjectSource;
  initialReport?: SourceReport;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const [busy, setBusy] = useState<Busy>(null);
  const [notice, setNotice] = useState<Notice>(initialReport ? outcome(initialReport) : null);
  const [report, setReport] = useState<SourceReport | null>(initialReport ?? source.lastReport ?? null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // A sync runs on the server: it carries on if this page is left, and one may
  // already be running (started here earlier, by the schedule, or elsewhere).
  // While one runs, its progress is read and shown; when it ends, its report.
  const [watching, setWatching] = useState(source.status === "running");
  const [progress, setProgress] = useState(source.progress ?? null);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  // Our own sync whose answer was lost (timed out, connection dropped): it
  // carries on on the server, so it is followed; if it then stops at the time
  // limit, this page continues it, as it would have with the answer.
  const continueRef = useRef<{ opts: SyncOptions; since: number; message: string } | null>(null);
  const runRef = useRef<(opts: SyncOptions) => Promise<void>>(async () => undefined);
  const syncing = busy?.what === "sync";

  useEffect(() => {
    if (source.lastReport) setReport(source.lastReport);
  }, [source.lastReport]);
  useEffect(() => {
    if (source.status === "running") setWatching(true);
  }, [source.status]);

  useEffect(() => {
    if (!watching && !syncing) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      const r = await getSource(projectId, source.id);
      if (cancelled || !r.ok) return;
      setProgress(r.source.progress ?? null);
      // Our own request reports its own outcome; a sync we are only following
      // ends when the source stops running.
      if (watching && !syncing && r.source.status !== "running") {
        setWatching(false);
        setProgress(null);
        const lost = continueRef.current;
        continueRef.current = null;
        const last = r.source.lastReport;
        if (lost && !finishedSince(last, lost.since)) {
          // Nothing ran after the request was sent: it never reached the server.
          setNotice({ kind: "alert", text: lost.message });
          return;
        }
        if (lost && last?.status === "partial" && (last.counts?.remaining ?? 0) > 0) {
          setReport(last);
          void runRef.current({ ...lost.opts, force: false });
          return;
        }
        if (r.source.lastReport) {
          setReport(r.source.lastReport);
          setNotice(outcome(r.source.lastReport));
        }
        onChangedRef.current();
      }
    }, PROGRESS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [watching, syncing, projectId, source.id]);

  const run = async (opts: SyncOptions) => {
    const what = opts.dryRun ? "preview" : "sync";
    const since = Date.now();
    setBusy({ what });
    setNotice(null);
    const r = await syncSourceUntilDone(projectId, source.id, opts, (partial) => {
      setReport(partial);
      setBusy({ what, note: `${partial.counts.remaining ?? 0} files to go` });
    });
    setBusy(null);
    setProgress(null);
    if (!r.ok && r.code === "SOURCE_SYNC_RUNNING") {
      // Not an error: follow the sync that is already running.
      setWatching(true);
      setNotice({ kind: "status", text: "A sync is already running — following it here." });
      return;
    }
    if (!r.ok && !opts.dryRun && noAnswer(r)) {
      // The run goes on without us: follow it rather than call it a failure.
      continueRef.current = { opts, since, message: r.message };
      setWatching(true);
      setNotice({ kind: "status", text: "The sync is still running on the server — following it here." });
      return;
    }
    if (!r.ok) {
      setNotice({ kind: "alert", text: r.message });
      return;
    }
    setReport(r.report);
    setNotice(outcome(r.report));
    if (!opts.dryRun) onChanged();
  };
  runRef.current = run;

  const remove = async () => {
    setBusy({ what: "remove" });
    const r = await removeSource(projectId, source.id);
    setBusy(null);
    setConfirmRemove(false);
    if (!r.ok) setNotice({ kind: "alert", text: r.message });
    else onChanged();
  };

  const held = report?.removalsHeld ?? [];
  return (
    <li className="space-y-2 rounded-md border p-3" data-testid="project-source">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            {source.type === "folder" ? (
              <Folder className="h-4 w-4 shrink-0" aria-hidden />
            ) : (
              <GitBranch className="h-4 w-4 shrink-0" aria-hidden />
            )}
            <span className="truncate">{title(source)}</span>
          </p>
          <p className="text-xs text-muted-foreground">{statusLine(source, fmt)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {source.type === "github" && (
            <button
              type="button"
              disabled={busy !== null || watching}
              onClick={() => void run({ dryRun: true })}
              className="whitespace-nowrap rounded-md border px-2.5 py-1 text-sm hover:bg-accent disabled:opacity-50"
            >
              {busy?.what === "preview" ? "Previewing…" : "Preview"}
            </button>
          )}
          <button
            type="button"
            disabled={busy !== null || watching}
            onClick={() => void run({})}
            className="whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {syncing || watching ? "Syncing…" : "Sync now"}
          </button>
          {confirmRemove ? (
            <span className="inline-flex items-center gap-1 text-sm">
              Stop syncing?
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void remove()}
                className="rounded-md border border-destructive px-2 py-1 text-destructive hover:bg-destructive/10"
              >
                {busy?.what === "remove" ? "Removing…" : "Yes"}
              </button>
              <button type="button" onClick={() => setConfirmRemove(false)} className="rounded-md border px-2 py-1">
                No
              </button>
            </span>
          ) : (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => setConfirmRemove(true)}
              className="whitespace-nowrap rounded-md px-2.5 py-1 text-sm text-muted-foreground hover:bg-accent disabled:opacity-50"
            >
              Remove
            </button>
          )}
        </div>
      </div>
      {busy?.what === "preview" && (
        <p role="status" className="text-xs text-muted-foreground">
          Checking GitHub…
        </p>
      )}
      {(syncing || watching) && (
        <p role="status" className="text-xs text-muted-foreground" data-testid="sync-progress">
          Syncing on the server
          {progress && progress.total > 0 ? ` — ${progress.done} of ${progress.total} files` : "…"}
          {busy?.note ? ` (${busy.note})` : ""}. You can leave this page; it carries on, and the result shows here when you
          come back.
        </p>
      )}
      {notice && (
        <p role={notice.kind} className={`text-sm ${notice.kind === "alert" ? "text-destructive" : "text-muted-foreground"}`}>
          {notice.text}
        </p>
      )}
      {held.length > 0 && !report?.dryRun && (
        <div role="alert" className="space-y-1 rounded-md bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <p>
            {held.length} file{held.length === 1 ? " was" : "s were"} deleted from the source. That is more than the safety
            limit, so nothing was removed from this project yet.
          </p>
          <FileList label="Waiting to be removed" items={held.map((path) => ({ path }))} open />
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run({ confirmRemovals: true })}
            className="rounded-md border border-amber-700 px-2.5 py-1 hover:bg-amber-100 dark:hover:bg-amber-900"
          >
            Remove them
          </button>
        </div>
      )}
      {source.write && <WriteAccess projectId={projectId} source={source} onChanged={onChanged} />}
      {report && <ReportDetails report={report} />}
      {source.type === "folder" && source.status === "new" && (
        <p className="text-xs text-muted-foreground">Sync once to add the folder&apos;s documents; after that it follows the folder.</p>
      )}
    </li>
  );
}

/**
 * Whether the project's AI may commit to this GitHub source (project-source-write.md §7).
 * Shown only where a write credential covers the repository for this person. When
 * the writer GitHub App isn't installed on the repository yet, says how to install
 * it, so a new source can be made writable later without looking it up.
 */
function WriteAccess({
  projectId,
  source,
  onChanged,
}: {
  projectId: string;
  source: ProjectSource;
  onChanged: () => void;
}) {
  const [writable, setWritable] = useState(Boolean(source.writable));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setWritable(Boolean(source.writable)), [source.writable]);
  const access = source.write!;

  const toggle = async (on: boolean) => {
    setPending(true);
    setError(null);
    const r = await setSourceWritable(projectId, source.id, on);
    setPending(false);
    if (!r.ok) {
      setError(r.message);
      return;
    }
    setWritable(Boolean(r.source.writable));
    onChanged();
  };

  if (!access.canWrite && !writable) {
    return (
      <div className="space-y-1 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground" data-testid="source-write-install">
        {access.installUrl ? (
          <>
            <p>
              To let the AI write here, the writer GitHub App must be installed on {source.repo}. An owner of the GitHub
              account installs it, choosing &ldquo;Only select repositories&rdquo; and adding this one, then the switch
              appears here within a few minutes.
            </p>
            <a
              href={access.installUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-block font-medium text-foreground underline underline-offset-2"
            >
              Install the writer App on GitHub
            </a>
          </>
        ) : (
          <p>{access.problem ?? "Writing to this repository can't be checked right now."}</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1 text-sm">
      <label className="inline-flex items-center gap-2">
        <input
          type="checkbox"
          checked={writable}
          disabled={pending}
          onChange={(e) => void toggle(e.target.checked)}
        />
        Let the AI write here
      </label>
      {writable && (
        <p className="text-xs text-muted-foreground">
          The AI in this project&apos;s chats can commit text files to {source.ref ?? "main"} in this folder. Each change is a
          commit in the repository&apos;s history.
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** "org/* except org/clients" — a credential's repositories as a person reads them. */
function reach(c: SourceCredential): string {
  const yes = c.repos.filter((r) => !r.startsWith("!"));
  const no = c.repos.filter((r) => r.startsWith("!")).map((r) => r.slice(1));
  return no.length ? `${yes.join(", ")} except ${no.join(", ")}` : yes.join(", ");
}

function title(s: ProjectSource): string {
  if (s.type === "folder") return `Library folder ${s.folderName || ""}`.trim();
  const where = s.path ? `${s.repo}/${s.path}` : (s.repo ?? "");
  return `${where} (${s.ref ?? "main"}) → ${s.folderPrefix ?? ""}/`;
}

function statusLine(s: ProjectSource, fmt: Formatter): string {
  const when = fmt.relative(s.syncedAt);
  const commit = s.syncedCommit ? ` at ${s.syncedCommit.slice(0, 7)}` : "";
  switch (s.status) {
    case "new":
      return s.type === "github" ? "Not synced yet — preview it, then sync." : "Not synced yet.";
    case "running":
      return "Syncing now…";
    case "ok":
      return `Up to date${commit}${when ? ` · checked ${when}` : ""}`;
    case "partial":
      return `Partly synced${commit}${when ? ` · ${when}` : ""} — it continues on the next run.`;
    case "needs_review":
      return "Removals are waiting for your confirmation.";
    case "failed":
      return `Last sync failed${when ? ` ${when}` : ""}: ${s.lastReport?.error?.message ?? "see below"}`;
    default:
      return s.status;
  }
}

const COUNT_LABELS: [string, string][] = [
  ["added", "added"],
  ["adopted", "matched existing"],
  ["moved", "moved"],
  ["updated", "updated"],
  ["removed", "removed"],
  ["unchanged", "unchanged"],
  ["skipped", "skipped"],
  ["failed", "failed"],
  ["remaining", "to go"],
];

function countsText(counts: Record<string, number>): string {
  return COUNT_LABELS.filter(([k]) => (counts?.[k] ?? 0) > 0)
    .map(([k, label]) => `${counts[k]} ${label}`)
    .join(" · ");
}

/** Whether `report` is from a run that ended after `since` (ms). A minute's
 * allowance for the browser's clock running ahead of the server's. */
function finishedSince(report: SourceReport | null | undefined, since: number): boolean {
  const at = Date.parse(report?.finishedAt ?? "");
  return Number.isFinite(at) && at >= since - 60_000;
}

/** The terminal message after a sync or preview. */
function outcome(r: SourceReport): Notice {
  if (r.error?.message) return { kind: "alert", text: r.error.message };
  if (r.dryRun) {
    const text = countsText(r.counts);
    return { kind: "status", text: text ? `Preview: ${text}. Nothing has changed yet.` : "Preview: nothing to change." };
  }
  if (r.status === "needs_review") return { kind: "alert", text: r.message ?? "Removals need your confirmation." };
  if (r.message && !countsText(r.counts)) return { kind: "status", text: r.message };
  const text = countsText(r.counts);
  if (r.status === "partial") return { kind: "alert", text: `Synced with problems: ${text}.` };
  return { kind: "status", text: text ? `Synced: ${text}.` : "Synced." };
}

function ReportDetails({ report }: { report: SourceReport }) {
  const plan = report.plan ?? {};
  const lists: [string, SourceFileNote[]][] = [
    ["Failed", report.failed ?? []],
    ["Couldn't be read", report.parseFailed ?? []],
    ["Not searchable", report.indexFailed ?? []],
    ["Skipped", report.skipped ?? []],
  ];
  if (report.dryRun) {
    lists.unshift(
      ["Would update", (plan.update ?? []).map((path) => ({ path }))],
      ["Would remove", (plan.remove ?? []).map((path) => ({ path }))],
      ["Would add", (plan.add ?? []).map((path) => ({ path }))],
      ["Would match existing documents", (plan.adopt ?? []).map((path) => ({ path }))],
    );
  }
  const shown = lists.filter(([, items]) => items.length > 0);
  if (shown.length === 0) return null;
  return (
    <div className="space-y-1">
      {shown.map(([label, items]) => (
        <FileList key={label} label={label} items={items} />
      ))}
    </div>
  );
}

function FileList({ label, items, open = false }: { label: string; items: SourceFileNote[]; open?: boolean }) {
  return (
    <details open={open} className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">
        {label} ({items.length})
      </summary>
      <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto pl-4">
        {items.map((it) => (
          <li key={it.path} className="break-all">
            <span className="font-mono">{it.path}</span>
            {(it.reason || it.error) && <span className="text-muted-foreground"> — {it.reason || it.error}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

interface FolderOption {
  id: string;
  name: string;
}

function AddSourceForm({
  projectId,
  credentials,
  writers,
  onDone,
  onCancel,
}: {
  projectId: string;
  credentials: SourceCredential[];
  writers: SourceCredential[];
  onDone: (sourceId: string, report: SourceReport | null) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<"github" | "folder">("github");
  const [repo, setRepo] = useState("");
  const [path, setPath] = useState("");
  const [ref, setRef] = useState("main");
  const [folders, setFolders] = useState<FolderOption[]>([]);
  const [folderId, setFolderId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [writable, setWritable] = useState(false);

  useEffect(() => {
    if (kind !== "folder") return;
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/folders")
      .then((res) => (res.ok ? (res.json() as Promise<{ folders?: FolderOption[] }>) : { folders: [] }))
      .then((body) => {
        if (!cancelled) setFolders(Array.isArray(body.folders) ? body.folders : []);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load your folders.");
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  const noToken = kind === "github" && credentials.length === 0;
  const canSubmit = !pending && !noToken && (kind === "github" ? repo.trim().length > 0 : folderId.length > 0);

  const submit = async () => {
    setPending(true);
    setError(null);
    const r =
      kind === "folder"
        ? await addSource(projectId, { type: "folder", folderId })
        : await addSource(projectId, { type: "github", repo: repo.trim(), path: path.trim(), ref: ref.trim() });
    if (!r.ok) {
      setPending(false);
      setError(r.message);
      return;
    }
    // Writing is a separate step: if the writer App isn't on the repository yet,
    // the source stays read-only and its row says how to install it.
    if (kind === "github" && writable) await setSourceWritable(projectId, r.source.id, true);
    // A GitHub source is previewed before anything changes; a folder source only
    // tags documents into the project, so it syncs straight away.
    const first = await syncSourceUntilDone(projectId, r.source.id, { dryRun: kind === "github" }, () => undefined);
    setPending(false);
    onDone(r.source.id, first.ok ? first.report : null);
  };

  return (
    <form
      className="space-y-3 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit) void submit();
      }}
    >
      <fieldset className="flex flex-wrap gap-4 text-sm">
        <legend className="sr-only">Source type</legend>
        <label className="inline-flex items-center gap-1.5">
          <input type="radio" name="source-kind" checked={kind === "github"} onChange={() => setKind("github")} />
          A folder in GitHub
        </label>
        <label className="inline-flex items-center gap-1.5">
          <input type="radio" name="source-kind" checked={kind === "folder"} onChange={() => setKind("folder")} />
          One of your library folders
        </label>
      </fieldset>
      {kind === "github" ? (
        <div className="grid gap-2 sm:grid-cols-3">
          <label className="space-y-1 text-sm sm:col-span-3">
            <span className="text-muted-foreground">Repository</span>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="owner/name or https://github.com/owner/name"
              className="w-full rounded-md border bg-background px-2 py-1"
            />
          </label>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span className="text-muted-foreground">Folder in the repository (empty for all of it)</span>
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="e.g. clients/acme"
              className="w-full rounded-md border bg-background px-2 py-1"
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-muted-foreground">Branch</span>
            <input value={ref} onChange={(e) => setRef(e.target.value)} className="w-full rounded-md border bg-background px-2 py-1" />
          </label>
          {credentials.length > 0 && (
            <p className="text-xs text-muted-foreground sm:col-span-3">
              You can sync from: {credentials.map(reach).join("; ")}
            </p>
          )}
          {writers.length > 0 && !noToken && (
            <div className="space-y-0.5 sm:col-span-3">
              <label className="inline-flex items-center gap-2 text-sm">
                <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} />
                Let the AI write here
              </label>
              <p className="text-xs text-muted-foreground">
                The AI in this project&apos;s chats can then commit text files to this folder. It needs the writer GitHub App
                installed on the repository; if it isn&apos;t yet, the source is added read-only and says how to install it.
              </p>
            </div>
          )}
          {noToken && (
            <p role="alert" className="text-sm text-destructive sm:col-span-3">
              You don&apos;t have GitHub access on this deployment yet. An admin grants it per repository.
            </p>
          )}
        </div>
      ) : (
        <label className="block space-y-1 text-sm">
          <span className="text-muted-foreground">Folder (its subfolders are included)</span>
          <select
            value={folderId}
            onChange={(e) => setFolderId(e.target.value)}
            className="w-full rounded-md border bg-background px-2 py-1"
          >
            <option value="">Choose a folder…</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {pending && (
        <p role="status" className="text-sm text-muted-foreground">
          {kind === "github" ? "Checking the repository and previewing…" : "Adding the folder's documents…"}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!canSubmit}
          className="whitespace-nowrap rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {kind === "github" ? "Add and preview" : "Add and sync"}
        </button>
        <button type="button" onClick={onCancel} disabled={pending} className="whitespace-nowrap rounded-md border px-3 py-1.5 text-sm hover:bg-accent">
          Cancel
        </button>
      </div>
    </form>
  );
}
