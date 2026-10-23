"use client";

import type { StreamError } from "@/hooks/useSkillAgent";

/**
 * The visible error for a failed chat turn (NEVER SILENT, CLAUDE.md #8).
 *
 * A `network` error — the stream dropped — is often a phone suspending the app
 * mid-answer. The server may keep the run going and finish it (detached runs,
 * docs/design/v6.40.0/web-push-notifications.md), in which case the answer is
 * in the conversation history: `onReload` (when given) offers that, instead of
 * only re-sending the question.
 */
export function StreamErrorBanner({
  error,
  onRetry,
  onDismiss,
  onReload,
}: {
  error: StreamError;
  onRetry: () => void;
  onDismiss: () => void;
  onReload?: () => void;
}) {
  // Rate-limit / quota renders amber ("wait & retry") to visually distinguish a
  // key/quota issue from a red "the demo is broken" error.
  const amber = error.kind === "rate_limited" || error.interrupted === true;
  const boxTone = amber
    ? "border-amber-500/40 bg-amber-500/10 text-amber-700"
    : "border-destructive/40 bg-destructive/10 text-destructive";
  const btnTone = amber
    ? "border-amber-500/40 hover:bg-amber-500/20"
    : "border-destructive/40 hover:bg-destructive/20";
  const offerReload = error.kind === "network" && Boolean(onReload);
  return (
    <div className={`inline-block max-w-[80%] space-y-2 rounded-md border px-3 py-2 text-sm ${boxTone}`}>
      <p>{error.message}</p>
      {offerReload && (
        <p className="text-xs opacity-80">
          If you left the app while it was answering, the reply may still arrive — reload the conversation to
          check before asking again.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {offerReload && (
          <button type="button" onClick={onReload} className={`rounded border px-2 py-0.5 text-xs ${btnTone}`}>
            Reload conversation
          </button>
        )}
        {error.retryable && (
          <button
            type="button"
            onClick={onRetry}
            className={`rounded border px-2 py-0.5 text-xs ${btnTone}`}
          >
            {error.interrupted ? "Retry answer" : "Try again"}
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="rounded border border-destructive/20 px-2 py-0.5 text-xs text-destructive/70 hover:bg-destructive/10"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
