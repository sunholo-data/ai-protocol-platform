"use client";

import { useEffect, useId, useState } from "react";

interface InstructionsEditorProps {
  label: string;
  description?: string;
  placeholder?: string;
  value: string;
  maxLength: number;
  /** Persist. Resolve to an error message to show, or null when saved. */
  onSave: (text: string) => Promise<string | null>;
  className?: string;
}

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };

/**
 * An instructions card that saves on blur — used for a project's
 * instructions and for "Your instructions" (projects design §6.2, §6.5).
 *
 * Collapsed to two lines until focused. Every save says what happened right
 * under the box: "Saving…", "Saved", or the error with Retry (NEVER SILENT #8).
 * Leaving it unchanged saves nothing.
 */
export function InstructionsEditor({
  label,
  description,
  placeholder,
  value,
  maxLength,
  onSave,
  className = "",
}: InstructionsEditorProps) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  // Follow the server value when it changes underneath us (another tab, a
  // refetch) — but never while the user is typing.
  useEffect(() => {
    if (!focused) setDraft(value);
  }, [value, focused]);

  const tooLong = draft.length > maxLength;

  async function commit(text: string) {
    if (tooLong) {
      setSave({ kind: "error", message: `At most ${maxLength.toLocaleString()} characters — this is ${draft.length.toLocaleString()}.` });
      return;
    }
    setSave({ kind: "saving" });
    const message = await onSave(text);
    setSave(message ? { kind: "error", message } : { kind: "saved" });
  }

  return (
    <div className={`space-y-1.5 ${className}`}>
      <label htmlFor={id} className="block text-sm font-medium text-foreground">
        {label}
      </label>
      {description && (
        <p id={`${id}-desc`} className="text-xs text-muted-foreground">
          {description}
        </p>
      )}
      <textarea
        id={id}
        aria-describedby={description ? `${id}-desc` : undefined}
        value={draft}
        placeholder={placeholder}
        rows={focused ? 6 : 2}
        onFocus={() => setFocused(true)}
        onChange={(e) => {
          setDraft(e.target.value);
          if (save.kind === "saved") setSave({ kind: "idle" });
        }}
        onBlur={() => {
          setFocused(false);
          if (draft !== value) void commit(draft);
        }}
        className="w-full resize-y rounded-[var(--radius)] border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
      />
      <div className="flex min-h-[1rem] items-center justify-between gap-2 text-xs">
        <span>
          {save.kind === "saving" && (
            <span role="status" className="text-muted-foreground">
              Saving…
            </span>
          )}
          {save.kind === "saved" && (
            <span role="status" className="text-emerald-700 dark:text-emerald-400">
              Saved
            </span>
          )}
          {save.kind === "error" && (
            <span role="alert" className="text-destructive">
              {save.message}{" "}
              {!tooLong && (
                <button type="button" onClick={() => void commit(draft)} className="underline underline-offset-2">
                  Retry
                </button>
              )}
            </span>
          )}
        </span>
        {(focused || tooLong) && (
          <span className={tooLong ? "text-destructive" : "text-muted-foreground"}>
            {draft.length.toLocaleString()} / {maxLength.toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}
