// CodeBlock — a fenced code block in a chat reply, with a copy affordance.
//
// The ONE Data Analyst skill shows the SQL behind every number it reports, and
// the natural next thing a user does with that SQL is run it somewhere else.
// Selecting a multi-line block by hand in a scrolling chat is fiddly, so the
// block carries its own Copy button.
//
// WHY THE TEXT COMES FROM THE HAST NODE, NOT `children`:
// `rehypeHighlight` has already turned the code's text into a tree of React
// <span> elements by the time the `pre` renderer runs, so `String(children)`
// yields "[object Object]". The same trap is documented at the top of
// ChatMarkdown for ```svg blocks, which sidestep the pipeline entirely. Here we
// read the ORIGINAL hast node react-markdown passes alongside the children and
// walk it for text values — the highlighted DOM stays untouched, and what lands
// on the clipboard is exactly what the model wrote.

"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

/** Minimal shape of the hast nodes react-markdown hands to a component. */
interface HastNode {
  type?: string;
  tagName?: string;
  value?: string;
  properties?: { className?: unknown };
  children?: HastNode[];
}

/** Concatenate every text descendant — the source text before highlighting. */
export function hastText(node: unknown): string {
  const n = node as HastNode | undefined;
  if (!n || typeof n !== "object") return "";
  if (n.type === "text") return n.value ?? "";
  if (!Array.isArray(n.children)) return "";
  return n.children.map(hastText).join("");
}

/** The fence's language, read off the inner <code>'s `language-*` class. */
export function hastLanguage(node: unknown): string {
  const n = node as HastNode | undefined;
  const code = n?.children?.find((c) => c.tagName === "code");
  const raw = code?.properties?.className;
  const classes = Array.isArray(raw) ? raw.map(String) : String(raw ?? "").split(/\s+/);
  const match = classes.find((c) => c.startsWith("language-"));
  return match ? match.slice("language-".length) : "";
}

type CopyState = "idle" | "copied" | "failed";

/**
 * Copy button for a code block.
 *
 * Distinct from `activity/bits.tsx`'s CopyButton, which swallows a clipboard
 * rejection to a no-op. A silent nothing-happened on click is exactly what
 * CLAUDE.md #8 forbids, and it is reachable in practice — `navigator.clipboard`
 * is undefined in an insecure context, so any non-https preview host hits it.
 * Here the failure renders.
 */
function CopyCodeButton({ text }: { text: string }) {
  const [state, setState] = useState<CopyState>("idle");

  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      // A markdown fence always carries a trailing newline. Copying it verbatim
      // drops a stray blank line into the paste target — harmless in a SQL
      // editor, untidy everywhere. Only the trailing break is stripped;
      // leading indentation is part of the code.
      await navigator.clipboard.writeText(text.replace(/\n+$/, ""));
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }

  return (
    <button
      type="button"
      onClick={copy}
      data-testid="code-copy"
      className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
      title={state === "failed" ? "Copying failed — select and copy manually" : "Copy to clipboard"}
      aria-label="Copy code to clipboard"
    >
      {state === "copied" ? (
        <Check className="h-3 w-3" aria-hidden />
      ) : (
        <Copy className="h-3 w-3" aria-hidden />
      )}
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
    </button>
  );
}

/**
 * A fenced block: a header strip carrying the language and the actions, over the
 * highlighted code itself.
 *
 * `children` is the already-highlighted tree and is rendered untouched — the
 * header is additive, so highlighting, horizontal scrolling and text selection
 * all behave exactly as before.
 */
export function CodeBlock({
  text,
  language,
  children,
  actions,
}: {
  text: string;
  language?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  // Nothing to copy (an empty fence) → render the plain block, no chrome.
  if (!text.trim()) {
    return (
      <pre className="mb-2 overflow-x-auto rounded border border-border bg-muted p-3 text-xs">{children}</pre>
    );
  }

  return (
    <div className="mb-2 overflow-hidden rounded border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/60 px-2 py-1">
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {language || "code"}
        </span>
        <span className="flex items-center gap-1">
          {actions}
          <CopyCodeButton text={text} />
        </span>
      </div>
      <pre className="overflow-x-auto bg-muted p-3 text-xs">{children}</pre>
    </div>
  );
}
