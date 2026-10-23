"use client";

// v6.34.0 DOC-WORKBENCH P4b — highlight a passage and say what to change.
//
// Revised 2026-09-01 after the first real use on test. Three things were wrong:
//
//   1. The box appeared at the BOTTOM of the panel, so on a long document
//      selecting a paragraph near the top scrolled you away from it. It now
//      opens next to the selection.
//   2. It went through the chat rail, which regenerated the WHOLE document —
//      ~2 minutes on a 91-block file, and the model had to reproduce every other
//      paragraph verbatim, silently risking changes nobody asked for. It now
//      calls `/revise-passage`, which sends only the selected passage to the
//      model and splices the result back into the stored markdown. The rest of
//      the document is unchanged BY CONSTRUCTION.
//   3. There was no sign anything happened. It now shows a working state and a
//      confirmation, and the document updates underneath live.

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { reportDocumentAction, type DocumentActivitySink } from "@/lib/documentActivity";

interface PassageInstructionProps {
  docId: string;
  /** Called after a successful edit so the panel re-reads the document. */
  onChanged?: () => void;
  /** Reports the rewrite into the Activity feed. */
  activitySink?: DocumentActivitySink;
  /** Element to watch for text selections. */
  containerRef: React.RefObject<HTMLElement | null>;
}

/** Selections shorter than this are usually a stray click, not a passage. */
const MIN_SELECTION_CHARS = 3;

interface Anchor {
  text: string;
  /** Viewport-relative position of the selection, for placing the popover. */
  top: number;
  left: number;
}

export function PassageInstruction({ docId, containerRef, onChanged, activitySink }: PassageInstructionProps) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  /** The backend's own current step, streamed. Null when not working. */
  const [stage, setStage] = useState<string | null>(null);
  /** Seconds since submit — a still label reads as a hang, a counting one does not. */
  const [elapsed, setElapsed] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const onMouseUp = (event: MouseEvent) => {
      // A click inside the popover itself must not re-read the selection.
      if (popoverRef.current?.contains(event.target as Node)) return;

      const selection = window.getSelection();
      const text = selection?.toString().trim() ?? "";
      if (text.length < MIN_SELECTION_CHARS) return;
      const anchorNode = selection?.anchorNode;
      if (!anchorNode || !el.contains(anchorNode)) return;

      // Position from the selection's own rect so the box opens where the user
      // is looking, not at the bottom of a long document. Guarded: a detached
      // range (and jsdom) has no getBoundingClientRect, and failing to place the
      // popover must not cost the user the feature — it just opens top-left.
      let rect: DOMRect | null = null;
      try {
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        rect = typeof range?.getBoundingClientRect === "function" ? range.getBoundingClientRect() : null;
      } catch {
        rect = null;
      }
      const host = el.getBoundingClientRect();
      setAnchor({
        text,
        top: rect ? rect.bottom - host.top + el.scrollTop + 6 : 0,
        left: rect ? Math.max(8, rect.left - host.left) : 8,
      });
      setError(null);
      setDone(false);
    };

    el.addEventListener("mouseup", onMouseUp);
    return () => el.removeEventListener("mouseup", onMouseUp);
  }, [containerRef]);

  useEffect(() => {
    if (anchor) inputRef.current?.focus();
  }, [anchor]);

  const dismiss = useCallback(() => {
    setAnchor(null);
    setInstruction("");
    setError(null);
    setDone(false);
  }, []);

  const submit = useCallback(async () => {
    const trimmed = instruction.trim();
    if (!trimmed || !anchor || busy) return;

    setBusy(true);
    setError(null);
    setStage(null);
    setElapsed(0);
    const startedAt = Date.now();
    const ticker = window.setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 1000);
    const settle = reportDocumentAction(activitySink, "rewrite_passage", `“${anchor.text.slice(0, 60)}” → ${trimmed}`);

    const fail = (message: string) => {
      setError(message);
      setStage(null);
      settle({ ok: false, message });
    };

    try {
      // The STREAMING sibling: the backend already emits "Preparing the
      // document…" / "Rewriting the passage…" / "Converting to a document…",
      // and on the plain JSON route every one of them was dropped on the floor.
      // We render the labels it actually produces rather than inventing a
      // plausible-looking sequence of our own.
      const res = await fetchWithAuth(
        `/api/proxy/api/documents/${encodeURIComponent(docId)}/revise-passage/stream`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ anchorText: anchor.text, instruction: trimmed }),
        },
      );

      if (!res.ok || !res.body) {
        const detail = await res.json().catch(() => null);
        fail(
          (detail?.detail?.message as string | undefined) ??
            (typeof detail?.detail === "string" ? detail.detail : null) ??
            `Couldn't apply that change (${res.status})`,
        );
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminal: { type: string; message?: string } | null = null;

      for (;;) {
        const { done: streamDone, value } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line; keep any partial tail.
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let frame: { type: string; label?: string; message?: string };
          try {
            frame = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          if (frame.type === "progress" && frame.label) setStage(frame.label);
          else if (frame.type === "done" || frame.type === "error") terminal = frame;
        }
      }

      // A stream that ended without saying how is exactly the silence this
      // replaced, so treat it as a failure rather than a quiet success (#8).
      if (!terminal) {
        fail("The rewrite stopped without finishing — nothing has been changed.");
        return;
      }
      if (terminal.type === "error") {
        fail(terminal.message ?? "Couldn't apply that change");
        return;
      }

      setStage(null);
      setDone(true);
      setInstruction("");
      settle({ ok: true, message: "Passage rewritten" });
      // Do not rely on the live listener alone — if it has dropped, the edit
      // would land invisibly.
      onChanged?.();
      window.setTimeout(() => setAnchor(null), 1200);
    } catch (err) {
      fail(err instanceof Error ? err.message : "Couldn't apply that change");
    } finally {
      window.clearInterval(ticker);
      setBusy(false);
    }
  }, [instruction, anchor, busy, docId, onChanged, activitySink]);

  if (!anchor) return null;

  const preview = anchor.text.length > 70 ? `${anchor.text.slice(0, 70).trimEnd()}…` : anchor.text;

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label="Revise selected passage"
      style={{ top: anchor.top, left: anchor.left }}
      // `bg-popover` is not a token in this app's theme, so it resolved to nothing
      // and the panel was transparent — the document text showed straight through
      // it. `bg-background` is defined and opaque.
      className="absolute z-30 w-[22rem] max-w-[calc(100%-1rem)] rounded-lg border bg-background shadow-xl ring-1 ring-black/5"
    >
      <div className="flex items-start gap-2 border-b px-3 py-2">
        <p className="min-w-0 flex-1 text-[11px] leading-snug text-muted-foreground" title={anchor.text}>
          Editing: <span className="italic text-foreground">“{preview}”</span>
        </p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Cancel"
          className="shrink-0 rounded px-1 text-xs text-muted-foreground hover:bg-accent"
        >
          ✕
        </button>
      </div>

      <div className="flex items-center gap-2 p-2">
        <input
          ref={inputRef}
          type="text"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            } else if (e.key === "Escape") {
              dismiss();
            }
          }}
          disabled={busy}
          placeholder="Make it more concise…"
          aria-label="What should change about this passage"
          className="min-w-0 flex-1 rounded border bg-background px-2 py-1 text-xs disabled:opacity-60"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !instruction.trim()}
          className="shrink-0 rounded bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Rewriting…" : "Rewrite"}
        </button>
      </div>

      {(busy || done || error) && (
        <div className="border-t px-3 py-1.5">
          {busy && (
            <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground" role="status" aria-live="polite">
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 shrink-0 animate-spin rounded-full border border-current border-t-transparent"
              />
              <span className="min-w-0 flex-1 truncate">{stage ?? "Rewriting just this passage…"}</span>
              {elapsed > 2 && <span className="shrink-0 tabular-nums opacity-70">{elapsed}s</span>}
            </p>
          )}
          {done && !error && <p className="text-[10px] text-emerald-600">Updated — the document has been rewritten.</p>}
          {error && (
            <p className="text-[10px] text-destructive" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
