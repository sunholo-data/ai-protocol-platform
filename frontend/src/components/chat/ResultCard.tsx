"use client";

import { ChevronRight, PanelRightOpen } from "lucide-react";
import { requestWorkbenchReveal } from "@/lib/workbenchFocus";

/**
 * A result in the conversation: one card under the reply that made it. Opens
 * the result beside the chat (full screen on a phone, with a way back).
 */
export function ResultCard({ surfaceId, title, note }: { surfaceId: string; title: string; note?: string }) {
  return (
    <button
      type="button"
      onClick={() => requestWorkbenchReveal(surfaceId)}
      className="flex w-full max-w-sm items-center gap-3 rounded-[var(--radius)] border border-border bg-background px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <PanelRightOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-foreground">{title}</span>
        {note ? <span className="block truncate text-xs text-muted-foreground">{note}</span> : null}
      </span>
      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
    </button>
  );
}
