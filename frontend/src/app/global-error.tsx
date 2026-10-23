// app/global-error.tsx — FRONTEND-OBS M1
//
// The last line of defence: a throw in the ROOT layout, which `app/error.tsx`
// cannot catch (error.js does not wrap the layout above it in the same
// segment). This file REPLACES the root layout when active, so it must render
// its own <html> and <body>.
//
// It therefore gets NO global stylesheet — `globals.css` is imported by the
// root layout, which is precisely what has been replaced. Every style is
// inline for that reason; a Tailwind className would render as unstyled text at
// the exact moment the app is already failing. (Next 15.2+ also renders this in
// development, so what you see locally is what a customer sees.)
//
// The content lives in `_components/GlobalErrorBody` so tests can exercise the
// retry without rendering <html> into a shared jsdom document — see that file
// for why that matters. This wrapper is only ever asserted on as static markup.

"use client";

import { useEffect } from "react";

import { GlobalErrorBody } from "./_components/GlobalErrorBody";
import { logger } from "@/lib/logger";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    logger.error(error, { event: "app_global_error", digest: error.digest });
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
          background: "#fafafa",
          color: "#18181b",
        }}
      >
        <GlobalErrorBody error={error} reset={reset} />
      </body>
    </html>
  );
}
