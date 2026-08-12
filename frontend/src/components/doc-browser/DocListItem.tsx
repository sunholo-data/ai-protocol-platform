"use client";

import { useState } from "react";
import type { ParsedDocument, ParseStatus } from "@/hooks/useDocBrowser";
import { fetchWithAuth } from "@/lib/apiClient";
import { DocumentPreview } from "@/components/document/DocumentPreview";

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

interface DocListItemProps {
  doc: ParsedDocument;
  onClick: (doc: ParsedDocument) => void;
}

export function DocListItem({ doc, onClick }: DocListItemProps) {
  const [reparsing, setReparsing] = useState(false);
  const [reparseError, setReparseError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

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

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      await fetchWithAuth(`/api/proxy/api/documents/${doc.id}`, { method: "DELETE" });
      // Firestore real-time listener removes the item automatically.
    } catch {
      setReparseError("Delete failed — try again");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  const row = (
    <div
      className="w-full group"
      onMouseLeave={() => setConfirmDelete(false)}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={() => onClick(doc)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(doc); } }}
        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring cursor-pointer"
        title={errorMsg ?? dot.title}
      >
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${dot.color}`}
          aria-label={dot.title}
        />
        <span className="min-w-0 flex-1 truncate text-foreground">
          {doc.originalFilename}
        </span>
        {doc.sourceFormat && (
          <span
            className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-medium uppercase ${fmtColor}`}
          >
            {doc.sourceFormat}
          </span>
        )}
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting}
          title={confirmDelete ? "Click again to confirm delete" : "Delete document"}
          className={`shrink-0 opacity-0 group-hover:opacity-100 rounded p-0.5 transition-opacity disabled:opacity-50 ${
            confirmDelete
              ? "text-destructive hover:bg-destructive/10"
              : "text-muted-foreground hover:text-destructive hover:bg-destructive/10"
          }`}
        >
          {deleting ? (
            <span className="block h-3 w-3 text-[10px] leading-3">…</span>
          ) : confirmDelete ? (
            <svg className="h-3 w-3" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zM4.5 7.5h7a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1z"/>
            </svg>
          ) : (
            <svg className="h-3 w-3" viewBox="0 0 16 16" fill="currentColor">
              <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0V6z"/>
              <path fillRule="evenodd" d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1v1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z"/>
            </svg>
          )}
        </button>
      </div>
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
      {reparseError && (
        <p className="px-2 pb-1 text-[10px] text-destructive leading-tight">
          {reparseError}
        </p>
      )}
    </div>
  );

  // Hover preview for every document (lazy — mounts only when the tooltip
  // opens). Image/PDF → rendered page image; docparse'd (docx, pptx, …) →
  // structured block preview; otherwise a doc icon.
  return (
    <DocumentPreview source={{ kind: "doc", docId: doc.id }} label={doc.originalFilename}>
      {row}
    </DocumentPreview>
  );
}
