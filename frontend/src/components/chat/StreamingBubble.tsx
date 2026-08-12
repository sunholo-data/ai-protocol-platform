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

interface StreamingBubbleProps {
  message: SkillMessage;
  skillId: string;
  /** Active skill's avatar — matches the bot MessageBubble (6.11). Null → brand mark. */
  botAvatarUrl?: string | null;
  /** Speaking agent's display name — the delegate for a handed-off turn (6.11). */
  botLabel?: string | null;
  thinkingContent?: string;
  isThinking?: boolean;
}

export function StreamingBubble({ message, skillId, botAvatarUrl, botLabel, thinkingContent, isThinking }: StreamingBubbleProps) {
  return (
    <div className="flex items-start gap-3">
      {botAvatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={botAvatarUrl}
          alt={skillId}
          className="h-7 w-7 shrink-0 rounded-full border border-border bg-background object-cover"
        />
      ) : (
        <BrandAvatar />
      )}
      <div className="flex max-w-[80%] flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-orange-600">{botLabel || skillId}</span>
        </div>
        <div className="rounded-[2px_8px_8px_8px] border-l-[3px] border-orange-400 bg-[hsl(0,0%,98%)] px-3 py-2 text-sm">
          {thinkingContent && (
            <ThinkingPanel content={thinkingContent} isThinking={isThinking ?? false} />
          )}
          <p className="whitespace-pre-wrap">
            {message.content}
            <span className="ml-0.5 inline-block h-3.5 w-0.5 bg-orange-400 animate-pulse align-middle" />
          </p>
        </div>
      </div>
    </div>
  );
}
