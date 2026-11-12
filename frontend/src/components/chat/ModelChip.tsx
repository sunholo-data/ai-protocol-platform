"use client";

import { X } from "lucide-react";

/**
 * Beside the composer's +, while a chat isn't on Auto: which model level the
 * chat uses, and × to go back to Auto. On Auto there is nothing here.
 */
export function ModelChip({ label, onClear }: { label: string | null; onClear: () => void }) {
  if (!label) return null;
  return (
    <span className="inline-flex max-w-[12rem] items-center gap-1 rounded-full border border-border bg-muted py-0.5 pl-2.5 pr-1 text-xs text-foreground">
      <span className="truncate" title={`Model: ${label}`}>
        {label}
      </span>
      <button
        type="button"
        onClick={onClear}
        aria-label={`Use Auto instead of ${label}`}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </span>
  );
}
