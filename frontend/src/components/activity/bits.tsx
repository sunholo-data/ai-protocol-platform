// Shared activity UI atoms — status dots, copy buttons, labelled detail
// sections, and the small icons every activity feed uses.

"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

/** Tool-call lifecycle colour: running = pulsing orange, error = red, else green. */
export function StatusDot({ status }: { status: string }) {
  const cls =
    status === "running"
      ? "bg-orange-400 animate-pulse"
      : status === "error"
        ? "bg-red-500"
        : "bg-emerald-500";
  return <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`} aria-label={status} />;
}

export function ArrowIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0 text-orange-500/80">
      <path d="M5 12h14" />
      <path d="m13 5 7 7-7 7" />
    </svg>
  );
}

export function WrenchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0 text-muted-foreground">
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.8 2.8-2.1-2.1 2.8-2.8Z" />
    </svg>
  );
}

export const DETAIL_LABEL = "text-[10px] font-medium uppercase tracking-wide text-muted-foreground/60";

/** Copy-to-clipboard button with a brief "Copied" confirmation. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // clipboard unavailable (insecure context) — no-op
        }
      }}
      className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground"
      title={`${label} to clipboard`}
      aria-label={`${label} to clipboard`}
    >
      {copied ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
      {copied ? "Copied" : label}
    </button>
  );
}

/** A labelled detail section: header (label + optional Copy) over a bounded,
 * self-scrolling body — so a huge payload scrolls inside its box, never the page. */
export function Section({ label, copyText, children }: { label: string; copyText?: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between gap-2">
        <span className={DETAIL_LABEL}>{label}</span>
        {copyText ? <CopyButton text={copyText} /> : null}
      </div>
      <div className="max-h-72 overflow-auto rounded border border-border bg-muted/20 p-2">
        {children}
      </div>
    </div>
  );
}
