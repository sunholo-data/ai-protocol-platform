"use client";

// v6.34.0 DOC-WORKBENCH P3 — "show we can iterate on the document".
//
// Regenerating a document updates it IN PLACE (the backend dedup path reuses the
// doc-id, which is what makes "change the second paragraph and redo it" behave
// like editing rather than piling up copies). Until P3 that silently destroyed
// the previous draft, which makes iterating a one-way door.
//
// This surfaces the history the backend now keeps: every rendering is an ADK
// artifact version, stored in the user's OWN per-client bucket. Each version can
// be downloaded; the current one is marked.
//
// Deliberately NOT an editor. Edits are expressed as a regeneration from
// markdown — there is no write path into a .docx — so this panel reads history
// and hands the revision itself back to the assistant.

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { reportDocumentAction, type DocumentActivitySink } from "@/lib/documentActivity";

interface VersionsResponse {
  docId: string;
  filename: string;
  currentVersion: number | null;
  versions: number[];
}

interface DocumentVersionsProps {
  docId: string;
  filename: string;
  /** Called after a restore so the viewer re-reads the document. */
  onRestored?: () => void;
  /** Reports the restore into the Activity feed. */
  activitySink?: DocumentActivitySink;
}

export function DocumentVersions({ docId, filename, onRestored, activitySink }: DocumentVersionsProps) {
  const [data, setData] = useState<VersionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/documents/${encodeURIComponent(docId)}/versions`,
        );
        if (cancelled) return;
        if (!res.ok) {
          // NEVER-SILENT #8 — an empty panel with no explanation is the failure
          // this rule exists for.
          setError(res.status === 403 ? "You don't have access to this document." : `Couldn't load history (${res.status})`);
          return;
        }
        setData((await res.json()) as VersionsResponse);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load history");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [docId]);

  const [restoring, setRestoring] = useState<number | null>(null);

  // Clicking a version RESTORES it — the viewer renders the current document
  // from Firestore, so this is how an older version gets back on screen.
  // Non-destructive: the restore is itself a new version, so nothing is lost.
  const handleRestore = useCallback(
    async (version: number) => {
      setRestoring(version);
      setError(null);
      const settle = reportDocumentAction(activitySink, "restore_version", `version ${version}`);
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/documents/${encodeURIComponent(docId)}/restore/${version}`,
          { method: "POST" },
        );
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          const message =
            (detail?.detail?.message as string | undefined) ?? `Couldn't restore v${version} (${res.status})`;
          setError(message);
          settle({ ok: false, message });
          return;
        }
        settle({ ok: true, message: `Restored version ${version}` });
        // Refresh the list — the restore added a version of its own.
        const listed = await fetchWithAuth(
          `/api/proxy/api/documents/${encodeURIComponent(docId)}/versions`,
        );
        if (listed.ok) setData((await listed.json()) as VersionsResponse);
        onRestored?.();
      } catch (err) {
        const message = err instanceof Error ? err.message : `Couldn't restore v${version}`;
        setError(message);
        settle({ ok: false, message });
      } finally {
        setRestoring(null);
      }
    },
    [docId, onRestored, activitySink],
  );

  const handleDownloadVersion = useCallback(
    async (version: number) => {
      let objectUrl: string | null = null;
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/documents/${encodeURIComponent(docId)}/download?version=${version}`,
        );
        if (!res.ok) {
          setError(`Couldn't open v${version} (${res.status})`);
          return;
        }
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objectUrl;
        a.download = `v${version} - ${filename}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (err) {
        setError(err instanceof Error ? err.message : `Couldn't open v${version}`);
      } finally {
        if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl as string), 10_000);
      }
    },
    [docId, filename],
  );

  if (loading) {
    return <p className="px-3 py-2 text-[11px] text-muted-foreground">Loading history…</p>;
  }

  if (error) {
    return (
      <p className="px-3 py-2 text-[11px] text-destructive" role="alert">
        {error}
      </p>
    );
  }

  const versions = data?.versions ?? [];

  if (versions.length === 0) {
    // Not an error: an uploaded document, or one created before versioning
    // shipped, simply has no history. Say which, rather than showing nothing.
    return (
      <p className="px-3 py-2 text-[11px] text-muted-foreground">
        No earlier versions — this document hasn&apos;t been regenerated yet.
      </p>
    );
  }

  return (
    <div className="px-3 py-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        Version history
      </p>
      <p className="mb-1.5 text-[10px] text-muted-foreground">
        Every time this document is regenerated it is saved here. Click a version to bring it back —
        nothing is lost, restoring just adds a new version.
      </p>
      {/* Capped: a document under active iteration passes 10 versions quickly,
          and an uncapped list pushed the rest of the panel off screen. */}
      <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto pr-1">
        {[...versions].reverse().map((v) => {
          const isCurrent = v === data?.currentVersion;
          return (
            <li key={v} className="flex items-center gap-1 text-[11px]">
              <button
                type="button"
                onClick={() => !isCurrent && handleRestore(v)}
                disabled={isCurrent || restoring !== null}
                title={isCurrent ? "This is the version on screen" : `Show version ${v} — restores it as the current document`}
                aria-label={isCurrent ? `Version ${v}, current` : `Restore version ${v}`}
                className={`flex min-w-0 flex-1 items-center gap-2 rounded px-1.5 py-1 text-left ${
                  isCurrent
                    ? "font-medium text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                }`}
              >
                <span>Version {v}</span>
                {isCurrent && (
                  <span className="rounded bg-emerald-100 px-1 py-0.5 text-[9px] font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    on screen
                  </span>
                )}
                {restoring === v && <span className="text-[10px]">restoring…</span>}
              </button>
              <button
                type="button"
                onClick={() => handleDownloadVersion(v)}
                className="shrink-0 rounded px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
                title={`Download version ${v} as a file`}
                aria-label={`Download version ${v}`}
              >
                ⬇
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
