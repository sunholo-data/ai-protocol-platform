"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { notify } from "@/components/ui/toast";

/** Copy a reply's text (its markdown). Says "Copied" where you clicked. */
export function CopyMessageButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
        } catch {
          notify.error("Couldn't copy. Select the text and copy it by hand.");
        }
      }}
      className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius)] px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="h-4 w-4 text-success" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
      <span className={copied ? "" : "sr-only"} aria-live="polite">
        {copied ? "Copied" : "Copy"}
      </span>
    </button>
  );
}
