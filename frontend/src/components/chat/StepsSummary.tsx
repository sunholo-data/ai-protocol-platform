"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ToolCallState } from "@/hooks/useSkillAgent";
import { useToolLabels } from "@/hooks/useToolLabels";
import { requestWorkbenchFocus } from "@/lib/workbenchFocus";
import { ToolCallChip } from "./ToolCallChip";

function duration(calls: ToolCallState[]): string | null {
  const starts = calls.map((c) => c.ts).filter((t): t is number => typeof t === "number");
  const ends = calls.map((c) => c.endTs).filter((t): t is number => typeof t === "number");
  if (starts.length === 0 || ends.length === 0) return null;
  const ms = Math.max(...ends) - Math.min(...starts);
  if (!(ms > 0)) return null;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${Math.max(1, s)}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/**
 * What the assistant did for this reply, folded into one quiet line under its
 * name ("Used Search pull requests and 2 more · 6s"). Open it for each step
 * (with its details) and a way to the full activity log. The answer comes
 * first; the working is one click away.
 */
export function StepsSummary({ toolCalls }: { toolCalls: ToolCallState[] }) {
  const [open, setOpen] = useState(false);
  const labelFor = useToolLabels();
  if (toolCalls.length === 0) return null;

  const failed = toolCalls.filter((t) => t.status === "error").length;
  const running = toolCalls.some((t) => t.status === "running");
  const first = labelFor(toolCalls[0].name);
  const more = toolCalls.length - 1;
  const parts = [
    `${running ? "Using" : "Used"} ${first}${more > 0 ? ` and ${more} more` : ""}`,
    duration(toolCalls),
    failed > 0 ? `${failed} failed` : null,
  ].filter(Boolean);

  return (
    <div data-testid="steps-summary">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="-ml-1 flex items-center gap-1 rounded px-1 py-0.5 text-left text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className={`h-4 w-4 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        <span className={failed > 0 ? "text-warning" : undefined}>{parts.join(" · ")}</span>
      </button>
      {open ? (
        <div className="ml-2 mt-1 space-y-1.5 border-l border-border pl-3">
          <div className="flex flex-wrap gap-1.5">
            {toolCalls.map((tc) => (
              <ToolCallChip key={tc.id} toolCall={tc} />
            ))}
          </div>
          <button
            type="button"
            onClick={() => requestWorkbenchFocus("activity")}
            className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            Full activity log
          </button>
        </div>
      ) : null}
    </div>
  );
}
