"use client";

import { MAX_INSTRUCTIONS_CHARS, saveMyInstructions, useMyInstructions } from "@/hooks/useMyInstructions";
import { InstructionsEditor } from "./InstructionsEditor";

export const YOUR_INSTRUCTIONS_PLACEHOLDER =
  "I work on the quarterly planning team. Keep answers concise and include dates for action items.";

/**
 * "Your instructions" — the default project's instructions (projects design
 * §6.5): one text the agent gets in every chat, whichever assistant you use.
 * Shown on the new-chat landing, where every chat starts, and in settings.
 */
export function YourInstructionsCard({ className = "" }: { className?: string }) {
  const { instructions, status, retry } = useMyInstructions();

  if (status === "loading") {
    return <div role="status" aria-label="Loading your instructions" className={`h-24 animate-pulse rounded-lg bg-muted ${className}`} />;
  }
  if (status === "error") {
    return (
      <div role="alert" className={`flex items-center gap-2 rounded-lg border border-destructive/40 p-3 text-sm text-destructive ${className}`}>
        <span className="flex-1">Couldn&apos;t load your instructions.</span>
        <button type="button" onClick={retry} className="rounded border border-destructive/40 px-2 py-0.5 text-xs hover:bg-destructive/10">
          Retry
        </button>
      </div>
    );
  }
  return (
    <div className={`rounded-lg border border-border bg-background/80 p-4 shadow-sm backdrop-blur ${className}`} data-testid="your-instructions">
      <InstructionsEditor
        label="Your instructions"
        description="Tell the assistant about you and how you like answers. Used in every chat."
        placeholder={YOUR_INSTRUCTIONS_PLACEHOLDER}
        value={instructions}
        maxLength={MAX_INSTRUCTIONS_CHARS}
        onSave={async (text) => {
          const r = await saveMyInstructions(text);
          return r.ok ? null : r.message;
        }}
      />
    </div>
  );
}
