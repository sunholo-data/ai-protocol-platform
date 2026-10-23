"use client";

import { useRef } from "react";
import { PassageInstruction } from "./PassageInstruction";
import type { DocumentActivitySink } from "@/lib/documentActivity";
import { useDocument, type DocumentDetail } from "@/hooks/useDocument";
import type { ParseStatus } from "@/hooks/useDocBrowser";
import { DocumentHeader } from "./DocumentHeader";
import { DocumentFooter } from "./DocumentFooter";
import { DocumentViewer } from "./DocumentViewer";

interface DocumentPanelProps {
  /** DOC-WORKBENCH P4b — send a passage instruction down the ordinary chat
   * rail. Absent (e.g. a preview surface) simply disables the affordance. */
  onSurfaceChatMessage?: (text: string) => void;
  /** DOC-WORKBENCH — document actions report into the Activity feed. */
  activitySink?: DocumentActivitySink;
  docId: string;
}

function isPendingStatus(status: ParseStatus): boolean {
  return status === "pending" || status === "pending_ai_extraction";
}

function pickCaption(doc: DocumentDetail | null): string {
  if (!doc) return "Loading document…";
  if (isPendingStatus(doc.parseStatus)) return "Parsing document…";
  return "Loading content…";
}

function Skeleton({ caption }: { caption: string }) {
  return (
    <div className="flex h-full flex-col" data-testid="doc-panel-loading">
      <div className="h-10 animate-pulse border-b bg-muted/40" />
      <div className="flex-1 space-y-3 p-3">
        <div className="text-xs text-muted-foreground">{caption}</div>
        {[80, 60, 90, 50].map((w) => (
          <div
            key={w}
            className="h-3 animate-pulse rounded bg-muted"
            style={{ width: `${w}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function TerminalMessage({
  doc,
  message,
}: {
  doc: DocumentDetail;
  message: string;
}) {
  return (
    <div className="flex h-full flex-col">
      <DocumentHeader doc={doc} />
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {message}
      </div>
    </div>
  );
}

export function DocumentPanel({ docId, onSurfaceChatMessage, activitySink }: DocumentPanelProps) {
  const { doc, isLoading, error, retry } = useDocument(docId);
  // P4b — the body is watched for text selections so a user can point at a
  // passage and say what to change.
  const bodyRef = useRef<HTMLDivElement>(null);

  if (isLoading || (!doc && !error)) {
    return <Skeleton caption={pickCaption(doc)} />;
  }

  if (error || !doc) {
    return (
      <div
        role="alert"
        className="flex h-full flex-col items-center justify-center gap-2 p-6 text-sm text-muted-foreground"
      >
        <p>{error ?? "Document preview unavailable."}</p>
        <button
          type="button"
          onClick={retry}
          className="rounded border border-border px-2 py-0.5 text-xs text-foreground hover:bg-accent"
        >
          Retry
        </button>
      </div>
    );
  }

  if (doc.parseStatus === "failed") {
    return (
      <TerminalMessage
        doc={doc}
        message={doc.parseError ?? "Document parse failed."}
      />
    );
  }

  if (isPendingStatus(doc.parseStatus)) {
    return <Skeleton caption="Parsing document…" />;
  }

  if (!doc.blocks || doc.blocks.length === 0) {
    return (
      <TerminalMessage doc={doc} message="No preview content for this document." />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-muted/10">
      <DocumentHeader doc={doc} onChanged={retry} activitySink={activitySink} />
      <div ref={bodyRef} className="relative min-h-0 flex-1 overflow-auto">
        <DocumentViewer doc={doc} />
        {/* Positioned against the scroll container so it opens beside the
            selection rather than at the bottom of a long document. */}
        <PassageInstruction docId={doc.id} containerRef={bodyRef} onChanged={retry} activitySink={activitySink} />
      </div>
      {doc.summary && <DocumentFooter summary={doc.summary} />}
    </div>
  );
}
