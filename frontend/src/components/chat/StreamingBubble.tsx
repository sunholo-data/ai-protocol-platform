// Workshop W5b — AG-UI: text events → chat bubbles
// StreamingBubble accumulates TEXT_MESSAGE_CONTENT deltas in the partial message
// passed from ChatMessageList. A blinking cursor (animate-pulse) signals live
// output. On TEXT_MESSAGE_END the parent swaps this for a finalised MessageBubble
// with no layout shift because the bubble dimensions stay stable during streaming.
// See: docs/talks/workshop.md §W5

"use client";

import type { SkillMessage } from "@/hooks/useSkillAgent";
import { BrandAvatar } from "@/components/chat/BrandAvatar";
import { ThinkingPanel } from "@/components/chat/ThinkingPanel";
import { useToolLabels } from "@/hooks/useToolLabels";

interface StreamingBubbleProps {
  /** A tool running mid-answer: said in words, so the pause doesn't look frozen. */
  activeToolName?: string | null;
  message: SkillMessage;
  skillId: string;
  /** Active skill's avatar — matches the bot MessageBubble (6.11). Null → brand mark. */
  botAvatarUrl?: string | null;
  /** Speaking agent's display name — the delegate for a handed-off turn (6.11). */
  botLabel?: string | null;
  thinkingContent?: string;
  isThinking?: boolean;
}

export function StreamingBubble({
  message,
  skillId,
  botAvatarUrl,
  botLabel,
  thinkingContent,
  isThinking,
  activeToolName,
}: StreamingBubbleProps) {
  const labelFor = useToolLabels();
  return (
    // Same frame as the finished assistant MessageBubble, so the swap on
    // TEXT_MESSAGE_END causes no layout shift.
    <div className="flex items-start gap-3">
      <div className="mt-0.5 hidden shrink-0 md:block">
        {botAvatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={botAvatarUrl}
            alt={skillId}
            className="h-7 w-7 rounded-full border border-border bg-background object-cover"
          />
        ) : (
          <BrandAvatar />
        )}
      </div>
      <div className="flex min-w-0 max-w-[68ch] flex-1 flex-col gap-1.5">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-foreground">{botLabel || skillId}</span>
        </div>
        <div className="min-w-0 space-y-3 text-[15px] leading-[1.7] text-foreground md:text-base">
          {thinkingContent && (
            <ThinkingPanel content={thinkingContent} isThinking={isThinking ?? false} />
          )}
          <p className="whitespace-pre-wrap break-words">
            {message.content}
            <span className="ml-0.5 inline-block h-3.5 w-0.5 bg-primary motion-safe:animate-pulse align-middle" />
          </p>
          {activeToolName && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              Using <span className="font-medium text-foreground">{labelFor(activeToolName)}</span>…
              <span className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
