// GlobalErrorBody — the content of app/global-error.tsx, as its own component.
//
// global-error.tsx must render <html> and <body> itself (it REPLACES the root
// layout). That is fine in a browser and fatal in a shared test document:
// rendering <html> through React Testing Library inside jsdom makes React 19
// warn "This will cause a hydration error" and, under vitest's CI mode
// (`singleFork: true`, one jsdom for every file), the shared document never
// recovers — ci-gate-frontend hung for 25 minutes on exactly that on
// 2026-09-04. So the interactive part lives here, where a test can render and
// click it, and global-error.tsx is a thin document wrapper that tests only
// ever assert on as static markup.
//
// Every style is inline: globals.css is imported by the root layout, which is
// precisely what global-error replaces.

"use client";

export interface GlobalErrorBodyProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export function GlobalErrorBody({ error, reset }: GlobalErrorBodyProps) {
  return (
    <div
      role="alert"
      style={{
        maxWidth: "28rem",
        padding: "2rem",
        border: "1px solid #e4e4e7",
        borderRadius: "0.5rem",
        background: "#ffffff",
        textAlign: "center",
      }}
    >
      <h2 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 600 }}>Something went wrong</h2>
      <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", color: "#52525b" }}>
        The application could not start. Try again, or reload the page.
      </p>
      {error.digest && (
        <p
          data-testid="global-error-digest"
          style={{
            marginTop: "0.75rem",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: "0.75rem",
            color: "#71717a",
          }}
        >
          Reference: {error.digest}
        </p>
      )}
      <button
        type="button"
        onClick={reset}
        style={{
          marginTop: "1rem",
          padding: "0.5rem 1rem",
          fontSize: "0.875rem",
          fontWeight: 500,
          border: "1px solid #d4d4d8",
          borderRadius: "0.375rem",
          background: "#ffffff",
          cursor: "pointer",
        }}
      >
        Try again
      </button>
    </div>
  );
}
