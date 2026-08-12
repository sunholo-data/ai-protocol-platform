"use client";

import { BrandAvatar } from "@/components/chat/BrandAvatar";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

interface AssistantIntroBubbleProps {
  /** Intro text from `skill.welcome.introMessage` (falls back to
   * `skill.initialMessage` at the source). Rendered with markdown so the
   * skill author can use bold/italics/links if useful. */
  content: string;
  /** Optional skill display name surfaced in the caption. */
  skillName?: string;
}

/**
 * AssistantIntroBubble (v6.4.0 4.5 SKILL-ONBOARDING M3).
 *
 * Synthetic first-turn assistant bubble shown when chat is fresh
 * (messages.length === 0, sessionId === null, !enteredViaResume) AND the
 * active skill declares either welcome.introMessage or the legacy
 * initialMessage. Caption signals it's not server-side state — moment the
 * user sends their first message, the live messages array grows and this
 * naturally falls off the list.
 *
 * Lifted shape from MessageBubble but kept lightweight — no tool calls,
 * no streaming, no citations. Pure presentation.
 */
export function AssistantIntroBubble({ content, skillName }: AssistantIntroBubbleProps) {
  return (
    <div
      className="flex w-full gap-3"
      role="group"
      aria-label="Assistant introduction"
    >
      <BrandAvatar />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-foreground">
            {skillName ?? "Assistant"}
          </span>
          <span
            className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70"
            aria-label="Not stored in session history"
          >
            Intro · not stored
          </span>
        </div>
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-foreground">
          <ChatMarkdown content={content} navigateToBlock={() => {}} />
        </div>
      </div>
    </div>
  );
}
