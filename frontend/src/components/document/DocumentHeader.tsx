"use client";

import { useState } from "react";
import type { DocumentDetail } from "@/hooks/useDocument";
import { DocumentDownloadButton } from "./DocumentDownloadButton";
import { DocumentFormatting } from "./DocumentFormatting";
import { DocumentVersions } from "./DocumentVersions";
import type { DocumentActivitySink } from "@/lib/documentActivity";

// v6.34.0 DOC-WORKBENCH. Revised 2026-09-01 after first use on test: the
// controls were bare glyphs (↓ ¶ ⟲) and nobody could tell what they did. They
// now carry their own labels, and the panels they open are toggles you can see
// the state of. An icon is a reminder of a thing you already know, not a way to
// learn it.

const FORMAT_COLORS: Record<string, string> = {
  pdf: "bg-red-100 text-red-700",
  docx: "bg-blue-100 text-blue-700",
  pptx: "bg-orange-100 text-orange-700",
  xlsx: "bg-green-100 text-green-700",
  csv: "bg-green-100 text-green-700",
  md: "bg-purple-100 text-purple-700",
  txt: "bg-muted text-muted-foreground",
};

interface DocumentHeaderProps {
  doc: DocumentDetail;
  /** Re-read the document after an edit — the live listener is best-effort. */
  onChanged?: () => void;
  /** Document actions report into the Activity feed. */
  activitySink?: DocumentActivitySink;
}

function ToolbarButton({
  label,
  hint,
  icon,
  active,
  onClick,
}: {
  label: string;
  hint: string;
  icon: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={active}
      title={hint}
      className={`flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[11px] transition-colors ${
        active
          ? "border-foreground/20 bg-accent text-foreground"
          : "border-transparent text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
    >
      <span aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </button>
  );
}

export function DocumentHeader({ doc, onChanged, activitySink }: DocumentHeaderProps) {
  const [panel, setPanel] = useState<"formatting" | "versions" | null>(null);
  const toggle = (next: "formatting" | "versions") => setPanel((p) => (p === next ? null : next));

  const fmtColor = FORMAT_COLORS[doc.sourceFormat.toLowerCase()] ?? "bg-muted text-muted-foreground";

  return (
    <div className="border-b">
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={doc.originalFilename}>
          {doc.originalFilename}
        </span>
        {doc.sourceFormat && (
          <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${fmtColor}`}>
            {doc.sourceFormat}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
        <ToolbarButton
          label="Formatting"
          hint="Restyle this document from a template, or upload a new one"
          icon="🎨"
          active={panel === "formatting"}
          onClick={() => toggle("formatting")}
        />
        <ToolbarButton
          label="History"
          hint="Earlier versions of this document"
          icon="🕘"
          active={panel === "versions"}
          onClick={() => toggle("versions")}
        />
        <DocumentDownloadButton
          docId={doc.id}
          filename={doc.originalFilename}
          className="flex shrink-0 items-center gap-1 rounded border border-transparent px-2 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
        />
        <span className="ml-auto hidden text-[10px] text-muted-foreground sm:block">
          Select any text to edit it
        </span>
      </div>

      {panel === "formatting" && (
        <DocumentFormatting
          docId={doc.id}
          currentTemplate={doc.template}
          onRevised={onChanged}
          activitySink={activitySink}
        />
      )}
      {panel === "versions" && (
        <DocumentVersions docId={doc.id} filename={doc.originalFilename} onRestored={onChanged} activitySink={activitySink} />
      )}
    </div>
  );
}
