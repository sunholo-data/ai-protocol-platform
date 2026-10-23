"use client";

import type { useMoveDocument } from "./folderActions";

type MoveState = ReturnType<typeof useMoveDocument>;

/** Pending / error / FILENAME_EXISTS-confirm line for a document move. */
export function MoveFeedback({ state }: { state: MoveState }) {
  const { pending, error, conflict, confirmReplace, cancel, dismissError } = state;
  if (pending) {
    return (
      <p role="status" className="px-2 pb-1 text-[10px] text-muted-foreground leading-tight">
        Moving…
      </p>
    );
  }
  if (conflict) {
    return (
      <div role="alertdialog" aria-label="Replace document?" className="mx-2 mb-1 rounded border border-amber-400/60 px-2 py-1 text-[10px] leading-tight">
        <p className="text-foreground">{conflict.message}</p>
        <div className="mt-1 flex gap-1.5">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); confirmReplace(); }}
            className="rounded border border-destructive/40 px-1.5 py-0.5 text-destructive hover:bg-destructive/10"
          >
            Replace
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); cancel(); }}
            className="rounded border px-1.5 py-0.5 text-muted-foreground hover:bg-accent"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div role="alert" className="flex items-start gap-2 px-2 pb-1 text-[10px] leading-tight text-destructive">
        <span className="flex-1">{error}</span>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={(e) => { e.stopPropagation(); dismissError(); }}
          className="shrink-0 hover:underline"
        >
          ×
        </button>
      </div>
    );
  }
  return null;
}
