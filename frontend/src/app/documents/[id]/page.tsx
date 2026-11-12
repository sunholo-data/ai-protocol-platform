"use client";

// A stable, shareable-to-yourself link to ONE document: /documents/{id}.
//
// Why it exists: a document generated from Discord (or anywhere off the web)
// needs somewhere to OPEN. A raw /api/documents/{id}/download link can't work in
// a browser tab — it needs the Firebase bearer header. This page signs the
// person in, then the backend's ownership check decides (403 for anyone but the
// owner), so the link carries no access of its own. The file never leaves our
// bucket except to its owner's browser. Every state renders (never-silent).
//
// Once allowed, it forwards into the chat of the skill that made the document
// with the document open in the workbench — the same `?doc=` route a project
// home uses, so it looks exactly like opening the file in the app. A document
// with no recorded skill (e.g. an upload) gets the download card instead.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { BRANDING } from "@/lib/branding";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";

type Doc = {
  id: string;
  originalFilename?: string;
  sourceFormat?: string;
  skillId?: string;
  templateUsed?: string;
};
type Load = { kind: "loading" } | { kind: "ok"; doc: Doc } | { kind: "error"; message: string };

const API = "/api/proxy/api/documents";

/** The chat of the skill that made the document, with it open in the workbench. */
export function workbenchHref(doc: Doc): string | null {
  if (!doc.skillId) return null;
  const qs = new URLSearchParams({
    doc: doc.id,
    docName: doc.originalFilename || "Document",
    docFormat: doc.sourceFormat || "",
  });
  return `/chat/${encodeURIComponent(doc.skillId)}?${qs.toString()}`;
}

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { user, loading } = useAuth();
  const router = useRouter();
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user || !id) return;
    void (async () => {
      try {
        const r = await fetchWithAuth(`${API}/${encodeURIComponent(id)}`);
        if (r.status === 403) return setState({ kind: "error", message: "This document belongs to someone else." });
        if (r.status === 404) return setState({ kind: "error", message: "That document doesn't exist (it may have been deleted)." });
        if (!r.ok) return setState({ kind: "error", message: `Couldn't load the document (HTTP ${r.status}).` });
        const doc = { ...((await r.json()) as Doc), id };
        const href = workbenchHref(doc);
        if (href) return router.replace(href);
        setState({ kind: "ok", doc });
      } catch {
        setState({ kind: "error", message: `Couldn't reach ${BRANDING.appName}. Check your connection and refresh.` });
      }
    })();
  }, [loading, user, id, router]);

  const download = async (filename: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${API}/${encodeURIComponent(id)}/download`);
      if (!r.ok) throw new Error(String(r.status));
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      setNotice("Downloaded ✓");
    } catch {
      setNotice("The download failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;
  if (state.kind === "loading") return <Centered>Opening the document…</Centered>;
  if (state.kind === "error") {
    return (
      <Centered>
        <p role="alert" className="text-sm">
          {state.message}
        </p>
      </Centered>
    );
  }
  const name = state.doc.originalFilename || "document";
  return (
    <Centered>
      <div className="w-full max-w-md space-y-4">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Document</p>
        <h1 data-testid="doc-name" className="break-words text-2xl font-semibold">
          {name}
        </h1>
        {state.doc.templateUsed ? (
          <p className="text-sm text-muted-foreground">Styled with {state.doc.templateUsed}</p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => void download(name)}
            disabled={busy}
            className="border border-border px-4 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            {busy ? "Downloading…" : "Download"}
          </button>
          <Link href="/" className="px-4 py-2 text-sm text-muted-foreground hover:underline">
            Open {BRANDING.appName}
          </Link>
        </div>
        {notice ? (
          <p role="status" className="text-sm">
            {notice}
          </p>
        ) : null}
      </div>
    </Centered>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center px-6">{children}</main>;
}
