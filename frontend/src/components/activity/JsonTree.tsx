// Shared collapsible JSON tree — a friendlier read than a raw JSON blob for
// structured tool results. Used by the chat Activity tab and admin analytics.

"use client";

import { useState } from "react";

/** A single JSON primitive, lightly colour-coded. */
function JsonPrimitive({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground/50">null</span>;
  if (typeof value === "string")
    return <span className="break-all text-emerald-700 dark:text-emerald-400">&quot;{value}&quot;</span>;
  if (typeof value === "number")
    return <span className="text-sky-700 dark:text-sky-400">{String(value)}</span>;
  if (typeof value === "boolean")
    return <span className="text-purple-700 dark:text-purple-400">{String(value)}</span>;
  return <span>{String(value)}</span>;
}

/** Recursive, collapsible key→value tree for structured tool results. Deep
 * levels start collapsed. */
function JsonNode({ label, value, depth = 0 }: { label?: string; value: unknown; depth?: number }) {
  const isContainer = value !== null && typeof value === "object";
  const [open, setOpen] = useState(depth < 2); // first couple of levels open, deeper collapsed

  if (!isContainer) {
    return (
      <div className="flex gap-1.5">
        {label !== undefined && <span className="shrink-0 text-muted-foreground/80">{label}:</span>}
        <JsonPrimitive value={value} />
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);
  const open$ = isArray ? "[" : "{";
  const close$ = isArray ? "]" : "}";

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 hover:text-foreground"
      >
        <span className={`text-muted-foreground/50 transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        {label !== undefined && <span className="text-muted-foreground/80">{label}:</span>}
        <span className="text-muted-foreground/50">
          {open ? open$ : `${open$} ${entries.length} ${close$}`}
        </span>
      </button>
      {open && (
        <div className="ml-2 border-l border-border/60 pl-2">
          {entries.map(([k, v]) => (
            <JsonNode key={k} label={k} value={v} depth={depth + 1} />
          ))}
          <div className="text-muted-foreground/50">{close$}</div>
        </div>
      )}
    </div>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="font-mono text-[11px] leading-relaxed text-foreground/80">
      <JsonNode value={value} />
    </div>
  );
}
