"use client";

import { useEffect, useRef, useState } from "react";
import { useMyMemories, type MemoryItem } from "@/hooks/useMyMemories";
import { useConfirm } from "@/components/ui/confirm";
import { useFormat, type Formatter } from "@/i18n";

const PAGE = 50;
const CONFIRM_WORD = "forget";

function when(iso: string | null, fmt: Formatter): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : fmt.date(d, { day: "numeric", month: "short", year: "numeric" });
}

/**
 * What the assistant remembers — personal, or one project's (`projectId`) —
 * with delete-one and forget-everything (memory-screen design §3). Every action
 * ends in a visible result or reason (NEVER SILENT, #8).
 */
export function MemoryList({ projectId = null }: { projectId?: string | null }) {
  const { memories, status, error, truncated, retry, remove, removeAll } = useMyMemories(projectId);
  const [shown, setShown] = useState(PAGE);
  const [notice, setNotice] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  if (status === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading memory…
      </p>
    );
  }
  if (status === "unavailable") {
    return <p className="text-sm text-muted-foreground">Memory isn&apos;t switched on in this workspace.</p>;
  }
  if (status === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive">
        <span>Couldn&apos;t load your memory: {error}</span>
        <button type="button" onClick={retry} className="rounded border px-2 py-0.5 text-foreground hover:bg-accent">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {memories.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="memory-empty">
          Nothing remembered yet. Memories appear a few minutes after a conversation pauses.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border" aria-label={projectId ? "Project memory" : "Your memory"}>
          {memories.slice(0, shown).map((m) => (
            <MemoryRow
              key={m.ref}
              memory={m}
              onDelete={async () => {
                const r = await remove(m.ref);
                setNotice(r.ok ? { kind: "done", text: "Deleted." } : { kind: "error", text: r.message });
                return r.ok;
              }}
            />
          ))}
        </ul>
      )}
      {memories.length > shown && (
        <button
          type="button"
          onClick={() => setShown((n) => n + PAGE)}
          className="rounded border px-3 py-1 text-sm text-muted-foreground hover:bg-accent"
        >
          Show more
        </button>
      )}
      {truncated && memories.length <= shown && (
        <p className="text-xs text-muted-foreground">Showing the newest {memories.length}.</p>
      )}
      {notice && (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          data-testid="memory-notice"
          className={`text-sm ${notice.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}
        >
          {notice.text}
        </p>
      )}
      {memories.length > 0 && (
        <ForgetEverything
          projectScoped={Boolean(projectId)}
          onConfirm={async () => {
            const r = await removeAll();
            setNotice(r.ok ? { kind: "done", text: "Everything was forgotten." } : { kind: "error", text: r.message });
            return r.ok;
          }}
        />
      )}
    </div>
  );
}

function MemoryRow({ memory, onDelete }: { memory: MemoryItem; onDelete: () => Promise<boolean> }) {
  const confirm = useConfirm();
  const fmt = useFormat();
  const [busy, setBusy] = useState(false);
  const date = when(memory.updatedAt, fmt);
  return (
    <li className="flex flex-wrap items-start gap-2 px-3 py-2 text-sm">
      <div className="min-w-0 flex-1">
        <p className="text-foreground">{memory.fact}</p>
        {date && <p className="text-xs text-muted-foreground">Saved {date}</p>}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          const ok = await confirm({
            title: "Delete this memory?",
            description: memory.fact,
            confirmLabel: "Delete",
            destructive: true,
          });
          if (!ok) return;
          setBusy(true);
          if (!(await onDelete())) setBusy(false);
        }}
        aria-label={`Delete: ${memory.fact}`}
        className="shrink-0 rounded px-2 py-0.5 text-xs text-muted-foreground hover:bg-red-100 hover:text-red-700 disabled:opacity-50"
      >
        {busy ? "Deleting…" : "Delete"}
      </button>
    </li>
  );
}

function ForgetEverything({ projectScoped, onConfirm }: { projectScoped: boolean; onConfirm: () => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  // Not a plain useConfirm: this one needs the typed word. Focus moves into the
  // panel when it opens and back to its button when it closes.
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  useEffect(() => {
    if (open) inputRef.current?.focus();
    else if (returnFocus.current) {
      returnFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [open]);
  const close = () => {
    returnFocus.current = true;
    setOpen(false);
    setTyped("");
  };
  const what = projectScoped ? "everything this project's chats remember" : "everything the assistant remembers about you";

  if (!open) {
    return (
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="rounded border border-destructive/40 px-3 py-1 text-sm text-destructive hover:bg-destructive/10"
      >
        Forget everything…
      </button>
    );
  }
  return (
    <div
      role="alertdialog"
      aria-label="Forget everything?"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !busy) {
          e.preventDefault();
          close();
        }
      }}
      className="space-y-2 rounded-lg border border-destructive/30 p-3 text-sm"
    >
      <p className="text-foreground">
        This deletes {what}. It can&apos;t be undone. Type <strong>{CONFIRM_WORD}</strong> to confirm.
      </p>
      <input
        ref={inputRef}
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        aria-label={`Type ${CONFIRM_WORD} to confirm`}
        className="w-40 rounded border bg-background px-2 py-1 text-sm"
      />
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || typed.trim().toLowerCase() !== CONFIRM_WORD}
          onClick={async () => {
            setBusy(true);
            const ok = await onConfirm();
            setBusy(false);
            if (ok) close();
          }}
          className="rounded border border-destructive/40 px-3 py-1 text-destructive hover:bg-destructive/10 disabled:opacity-50"
        >
          {busy ? "Forgetting…" : "Forget everything"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={close}
          className="rounded border px-3 py-1 text-muted-foreground hover:bg-accent disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
