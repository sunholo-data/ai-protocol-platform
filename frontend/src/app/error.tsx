// app/error.tsx — FRONTEND-OBS M1
//
// Route-segment error boundary. Catches a render throw below this segment and
// keeps the app shell (and therefore navigation) alive, instead of Next's
// default behaviour of tearing the segment down to nothing.
//
// Next 15.5 contract: the recovery prop is `reset`. `retry` arrived in 16.2
// (unstable) / 16.3 (stable) and does not exist here.
//
// Server Component errors reach us with a GENERIC message plus `error.digest`
// — Next deliberately withholds the real message from the client so a server
// error cannot leak internals into the browser. The digest is the join key back
// to the server-side log, which is why it is shown: it is the one piece of this
// screen worth a customer quoting in a bug report.

"use client";

import { useEffect } from "react";

import { logger } from "@/lib/logger";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // FRONTEND-OBS M3 replaces this with `logger.error(error, { digest })` so
    // the failure reaches Cloud Logging. Until then console.error at least
    // keeps it visible in devtools rather than nowhere.
    logger.error(error, { event: "app_error", digest: error.digest });
  }, [error]);

  return (
    <div className="flex min-h-[50vh] flex-1 items-center justify-center p-6">
      <div
        role="alert"
        className="max-w-md rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-center"
      >
        <h2 className="text-lg font-semibold text-foreground">Something went wrong</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {/*
            Deliberately not `error.message`. A client-side throw can quote the
            payload that caused it, and in this app a payload is customer
            document content. The detail goes to the log, not the screen.
          */}
          This part of the page could not be displayed. Your work is not lost — try again,
          or reload the page.
        </p>
        {error.digest && (
          <p
            data-testid="error-digest"
            className="mt-3 font-mono text-xs text-muted-foreground"
          >
            Reference: {error.digest}
          </p>
        )}
        <button
          type="button"
          onClick={reset}
          className="mt-4 rounded-md border border-input bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
