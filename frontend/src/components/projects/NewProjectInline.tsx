"use client";

import { useEffect, useId, useRef, useState } from "react";

export const MAX_PROJECT_NAME = 80;

interface NewProjectInlineProps {
  /** Pre-filled name (E3 pre-fills from the document; E1 starts empty). */
  initialName?: string;
  /** Create it. Resolve to an error message to show, or null on success. */
  onCreate: (name: string) => Promise<string | null>;
  onCancel: () => void;
  className?: string;
}

/**
 * The inline "name your project" field shared by the sidebar's "+ New project"
 * (E1) and a document's "Add to project… → + New project…" (E3). Enter
 * creates, Esc cancels — the same gesture as renaming a folder or document.
 * Pending shows "Creating…"; a server refusal (e.g. the 409 "You already have a
 * project called 'X'.") is shown verbatim under the field (NEVER SILENT #8).
 */
export function NewProjectInline({ initialName = "", onCreate, onCancel, className = "" }: NewProjectInlineProps) {
  const id = useId();
  const [name, setName] = useState(initialName);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the project a name.");
      return;
    }
    if (trimmed.length > MAX_PROJECT_NAME) {
      setError(`A project name can be at most ${MAX_PROJECT_NAME} characters.`);
      return;
    }
    setPending(true);
    setError(null);
    const message = await onCreate(trimmed);
    setPending(false);
    if (message) setError(message);
  }

  return (
    <div className={`space-y-1 px-2 py-1 ${className}`} onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center gap-1.5">
        <input
          ref={inputRef}
          aria-label="New project name"
          aria-describedby={`${id}-hint`}
          aria-invalid={error ? true : undefined}
          value={name}
          disabled={pending}
          maxLength={MAX_PROJECT_NAME + 20}
          placeholder="Project name"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onCancel();
            }
          }}
          className="min-w-0 flex-1 rounded border bg-background px-1.5 py-0.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
        />
        <span id={`${id}-hint`} className="shrink-0 text-[10px] text-muted-foreground" role={pending ? "status" : undefined}>
          {pending ? "Creating…" : "Enter ↵ / Esc"}
        </span>
      </div>
      {error && (
        <p role="alert" className="text-[10px] leading-tight text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
