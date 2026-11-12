// Workshop W5b — AG-UI: text events → chat bubbles
// TypingIndicator is shown between RUN_STARTED and the first TEXT_MESSAGE_CONTENT.
// Surfaces three layers of progress, in priority order:
//   1. stageLabel — server-authored STAGE_PROGRESS Custom event (Reading 2 documents…,
//      Thinking…). Wins over toolName so the per-stage breakdown the agent emits
//      stays the source of truth — see docs/design/v6.1.0/ttft-instrumentation.md.
//   2. activeToolName — fallback when no stage label, but a tool is running.
//   3. Bouncing dots — pure waiting, no signal yet. After a few seconds of
//      that the dots say so in words, so a slow start never looks like a hang.
// Disappears on first token.
// See: docs/talks/workshop.md §W5

"use client";

import { useEffect, useState } from "react";
import { BrandAvatar } from "@/components/chat/BrandAvatar";
import { useToolLabels } from "@/hooks/useToolLabels";

interface TypingIndicatorProps {
  /**
   * Server-authored stage label from AG-UI STAGE_PROGRESS Custom events.
   * Takes priority over activeToolName so the agent's own progress signal
   * stays canonical. Null when no STAGE_PROGRESS has fired this run.
   */
  stageLabel?: string | null;
  /** Raw name of the running tool — shown as its friendly label (CLAUDE.md #9). */
  activeToolName?: string | null;
}

export function TypingIndicator({ stageLabel, activeToolName }: TypingIndicatorProps) {
  const labelText = stageLabel ?? null;
  const labelFor = useToolLabels();
  const toolText = !labelText && activeToolName ? labelFor(activeToolName) : null;
  const waited = useSecondsSinceMount();
  const waitText = waited >= 20 ? "Still working…" : waited >= 5 ? "Working on it…" : null;

  return (
    // Unboxed, like the assistant message it stands in for; avatar on desktop only.
    <div
      className="flex items-start gap-3 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300"
      role="status"
      aria-live="polite"
    >
      <div className="hidden shrink-0 md:block">
        <BrandAvatar />
      </div>
      <div className="flex min-h-7 items-center gap-2">
        {labelText ? (
          <>
            <span className="text-sm text-muted-foreground">{labelText}</span>
            <span className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
          </>
        ) : toolText ? (
          <>
            <span className="text-sm text-muted-foreground">
              Using <span className="font-medium text-foreground">{toolText}</span>…
            </span>
            <span className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
          </>
        ) : (
          <>
            <span
              className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-bounce"
              style={{ animationDelay: "0ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-bounce"
              style={{ animationDelay: "150ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-bounce"
              style={{ animationDelay: "300ms" }}
            />
            {waitText && <span className="ml-1 text-sm text-muted-foreground">{waitText}</span>}
          </>
        )}
      </div>
    </div>
  );
}

/** Whole seconds since the indicator appeared (ticks once a second). */
function useSecondsSinceMount(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, []);
  return seconds;
}
