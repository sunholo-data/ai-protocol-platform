"use client";

// v6.34.0 DOC-WORKBENCH P2 — "we need a download link, they will create
// documents for use offline".
//
// It cannot be a plain `<a href>`. The bytes live behind an AUTHENTICATED route
// (`/api/documents/{id}/download` re-checks `doc.userId == request.auth.uid`),
// and a browser navigation does not carry the Firebase bearer — so the link
// would 401. The bytes are fetched with `fetchWithAuth`, turned into an object
// URL, and handed to a synthetic anchor. Same shape as `DocumentViewer`'s
// authenticated preview fetch.
//
// It is deliberately NOT a signed or public GCS URL. A generated document is
// derived from customer context and inherits its sensitivity; a link on
// `storage.googleapis.com` is on the public internet, which is precisely what
// CLAUDE.md's security rule forbids.

import { useCallback, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

interface DocumentDownloadButtonProps {
  docId: string;
  filename: string;
  className?: string;
}

export function DocumentDownloadButton({ docId, filename, className }: DocumentDownloadButtonProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = useCallback(async () => {
    setBusy(true);
    setError(null);
    let objectUrl: string | null = null;
    try {
      const res = await fetchWithAuth(
        `/api/proxy/api/documents/${encodeURIComponent(docId)}/download`,
      );
      if (!res.ok) {
        // NEVER-SILENT #8: a failed download says so, in place. A button that
        // greys and does nothing is the failure mode this rule exists for.
        setError(res.status === 403 ? "You don't have access to this file." : `Download failed (${res.status})`);
        return;
      }
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename || "document";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    } finally {
      // Revoke on the next tick — Safari cancels an in-flight save if the
      // object URL is released synchronously after click().
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl as string), 10_000);
      setBusy(false);
    }
  }, [docId, filename]);

  return (
    <span className="flex shrink-0 items-center gap-1">
      {error && (
        <span className="text-[10px] text-destructive" role="alert">
          {error}
        </span>
      )}
      <button
        type="button"
        onClick={handleDownload}
        disabled={busy}
        title={`Download ${filename}`}
        aria-label={`Download ${filename}`}
        className={
          className ??
          "shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
        }
      >
        <span aria-hidden="true">{busy ? "…" : "⬇"}</span>
        <span>Download</span>
      </button>
    </span>
  );
}
