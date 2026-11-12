"use client";

import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

/**
 * One way to confirm that something happened: `notify.success("Saved")`,
 * `notify.error("Couldn't save")`, or `notify("Deleted", { action: { label:
 * "Undo", onClick } })`. Toasts stack bottom-right, are announced to screen
 * readers, and dismiss themselves. Field-level errors stay inline, next to the
 * field; this is for outcomes of actions.
 */
type Tone = "info" | "success" | "error";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

interface ToastItem {
  id: number;
  tone: Tone;
  message: string;
  action?: ToastAction;
}

const DURATION_MS: Record<Tone, number> = { info: 4000, success: 4000, error: 8000 };
const MAX_VISIBLE = 3;

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

// Each toast's countdown. Hovering or focusing the stack pauses them all, so
// an Undo can't vanish while the pointer or keyboard is on it.
const timers = new Map<number, { handle?: number; remaining: number; startedAt: number }>();
let paused = false;

function dismiss(id: number) {
  const t = timers.get(id);
  if (t?.handle !== undefined) window.clearTimeout(t.handle);
  timers.delete(id);
  items = items.filter((t) => t.id !== id);
  emit();
}

function startTimer(id: number) {
  const t = timers.get(id);
  if (!t || typeof window === "undefined") return;
  t.startedAt = Date.now();
  t.handle = window.setTimeout(() => dismiss(id), t.remaining);
}

function pauseAll() {
  if (paused) return;
  paused = true;
  timers.forEach((t) => {
    if (t.handle === undefined) return;
    window.clearTimeout(t.handle);
    t.handle = undefined;
    t.remaining = Math.max(1000, t.remaining - (Date.now() - t.startedAt));
  });
}

function resumeAll() {
  if (!paused) return;
  paused = false;
  timers.forEach((_, id) => startTimer(id));
}

function push(tone: Tone, message: string, opts: { action?: ToastAction; durationMs?: number } = {}) {
  const id = nextId++;
  items = [...items, { id, tone, message, action: opts.action }].slice(-MAX_VISIBLE);
  emit();
  // An action (Undo) gets a little longer to reach.
  const ms = opts.durationMs ?? DURATION_MS[tone] + (opts.action ? 2000 : 0);
  timers.set(id, { remaining: ms, startedAt: Date.now() });
  if (!paused) startTimer(id);
  return id;
}

type NotifyOpts = { action?: ToastAction; durationMs?: number };
export const notify = Object.assign((message: string, opts?: NotifyOpts) => push("info", message, opts), {
  success: (message: string, opts?: NotifyOpts) => push("success", message, opts),
  error: (message: string, opts?: NotifyOpts) => push("error", message, opts),
  dismiss,
});

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}
const getSnapshot = () => items;
const NO_TOASTS: ToastItem[] = [];
const getServerSnapshot = () => NO_TOASTS;

const TONE_CLASS: Record<Tone, string> = {
  info: "border-border",
  success: "border-success/40",
  error: "border-destructive/40",
};

const DOT_CLASS: Record<Tone, string> = {
  info: "bg-muted-foreground",
  success: "bg-success",
  error: "bg-destructive",
};

/** Mounted once, in AppProviders. */
export function Toaster() {
  const toasts = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return (
    <div
      aria-live="polite"
      onMouseEnter={pauseAll}
      onMouseLeave={resumeAll}
      onFocus={pauseAll}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) resumeAll();
      }}
      className="pointer-events-none fixed inset-x-4 bottom-4 z-[100] flex flex-col items-end gap-2 sm:left-auto sm:right-4"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          className={cn(
            "pointer-events-auto flex w-full max-w-sm items-center gap-3 rounded-md border bg-popover px-3 py-2.5 text-sm text-popover-foreground shadow-lg",
            "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-200",
            TONE_CLASS[t.tone],
          )}
        >
          <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full", DOT_CLASS[t.tone])} />
          <span className="min-w-0 flex-1">{t.message}</span>
          {t.action && (
            <button
              type="button"
              onClick={() => {
                t.action?.onClick();
                dismiss(t.id);
              }}
              className="shrink-0 rounded-md px-2 py-1 text-sm font-medium text-primary hover:bg-muted"
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
            className="shrink-0 rounded-md px-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <span aria-hidden>×</span>
          </button>
        </div>
      ))}
    </div>
  );
}
