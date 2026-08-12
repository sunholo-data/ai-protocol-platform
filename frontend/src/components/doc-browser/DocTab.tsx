"use client";

import { DocumentPreview } from "@/components/document/DocumentPreview";

const FORMAT_COLORS: Record<string, string> = {
  pdf: "bg-red-100 text-red-700",
  docx: "bg-blue-100 text-blue-700",
  pptx: "bg-orange-100 text-orange-700",
  xlsx: "bg-green-100 text-green-700",
  csv: "bg-green-100 text-green-700",
  md: "bg-purple-100 text-purple-700",
};

export interface DocTabData {
  id: string;
  filename: string;
  format: string;
  /** Whether this doc is sent to the agent on the next turn. Defaults to true on open. */
  included: boolean;
}

interface DocTabProps {
  tab: DocTabData;
  isActive: boolean;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onToggleInclude: (id: string) => void;
}

export function DocTab({ tab, isActive, onSelect, onClose, onToggleInclude }: DocTabProps) {
  const fmtColor =
    FORMAT_COLORS[tab.format.toLowerCase()] ?? "bg-muted text-muted-foreground";
  const includeLabel = tab.included
    ? `Exclude ${tab.filename} from agent context`
    : `Include ${tab.filename} in agent context`;

  const tabEl = (
    <div
      role="tab"
      aria-selected={isActive}
      className={[
        "group flex shrink-0 cursor-pointer items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs",
        isActive
          ? "border-primary bg-background text-foreground"
          : "border-transparent text-muted-foreground hover:bg-accent hover:text-foreground",
        tab.included ? "" : "opacity-60",
      ].join(" ")}
      onClick={() => onSelect(tab.id)}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={tab.included}
        aria-label={includeLabel}
        title={includeLabel}
        onClick={(e) => {
          e.stopPropagation();
          onToggleInclude(tab.id);
        }}
        className={[
          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border",
          tab.included
            ? "border-primary bg-primary text-primary-foreground"
            : "border-muted-foreground/40 bg-transparent",
        ].join(" ")}
      >
        {tab.included && (
          <svg
            className="h-2.5 w-2.5"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path d="M2 6.5l2.5 2.5L10 3.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </button>
      {tab.format && (
        <span className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-medium uppercase ${fmtColor}`}>
          {tab.format}
        </span>
      )}
      <span className="max-w-[120px] truncate">{tab.filename}</span>
      <button
        type="button"
        aria-label={`Close ${tab.filename}`}
        onClick={(e) => {
          e.stopPropagation();
          onClose(tab.id);
        }}
        className="ml-0.5 rounded p-0.5 opacity-0 hover:bg-muted group-hover:opacity-100"
      >
        <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M2 2l8 8M10 2l-8 8" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );

  // Hover preview (lazy). Image/PDF → page image; docparse'd → structured
  // block preview. The tab sits at the top of the workbench, so open below it.
  return (
    <DocumentPreview source={{ kind: "doc", docId: tab.id }} label={tab.filename} side="bottom">
      {tabEl}
    </DocumentPreview>
  );
}
