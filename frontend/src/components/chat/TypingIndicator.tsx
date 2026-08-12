// Workshop W5b — AG-UI: text events → chat bubbles
// TypingIndicator is shown between RUN_STARTED and the first TEXT_MESSAGE_CONTENT.
// Surfaces three layers of progress, in priority order:
//   1. stageLabel — server-authored STAGE_PROGRESS Custom event (Reading 2 documents…,
//      Thinking…). Wins over toolName so the per-stage breakdown the agent emits
//      stays the source of truth — see docs/design/v6.1.0/ttft-instrumentation.md.
//   2. activeToolName — fallback when no stage label, but a tool is running.
//   3. Bouncing dots — pure waiting, no signal yet.
// Disappears on first token.
// See: docs/talks/workshop.md §W5

"use client";

import { BrandAvatar } from "@/components/chat/BrandAvatar";

interface TypingIndicatorProps {
  /**
   * Server-authored stage label from AG-UI STAGE_PROGRESS Custom events.
   * Takes priority over activeToolName so the agent's own progress signal
   * stays canonical. Null when no STAGE_PROGRESS has fired this run.
   */
  stageLabel?: string | null;
  activeToolName?: string | null;
}

export function TypingIndicator({ stageLabel, activeToolName }: TypingIndicatorProps) {
  const labelText = stageLabel ?? null;
  const toolText = !labelText && activeToolName ? activeToolName : null;

  return (
    <div className="flex items-start gap-3 py-1">
      <BrandAvatar />
      <div className="flex items-center gap-2 rounded-[2px_8px_8px_8px] border border-border bg-[hsl(0,0%,98%)] px-3 py-2.5">
        {labelText ? (
          <>
            <span className="text-xs text-muted-foreground">{labelText}</span>
            <span className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-pulse" />
          </>
        ) : toolText ? (
          <>
            <span className="text-xs text-muted-foreground">
              Using <span className="font-medium text-orange-600">{toolText}</span>…
            </span>
            <span className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-pulse" />
          </>
        ) : (
          <>
            <span
              className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-bounce"
              style={{ animationDelay: "0ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-bounce"
              style={{ animationDelay: "150ms" }}
            />
            <span
              className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-bounce"
              style={{ animationDelay: "300ms" }}
            />
          </>
        )}
      </div>
    </div>
  );
}
