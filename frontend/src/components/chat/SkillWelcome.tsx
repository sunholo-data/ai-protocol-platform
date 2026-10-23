"use client";

import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

/**
 * The fresh-chat welcome: the skill's icon and name, centred, with its intro
 * underneath — the "Hey, early bird" moment of a native chat app, but saying
 * which skill you are talking to. Replaces the boxed intro bubble on the
 * landing screen; the transcript still uses AssistantIntroBubble.
 */
export function SkillWelcome({
  name,
  avatar,
  intro,
}: {
  name: string;
  avatar?: string | null;
  intro?: string | null;
}) {
  return (
    // The halo is the page colour, so the text stays crisp over skill art in both
    // themes without a box behind it.
    <div className="flex flex-col items-center text-center [text-shadow:0_0_6px_hsl(var(--background)),0_0_14px_hsl(var(--background)),0_0_28px_hsl(var(--background))]" role="group" aria-label={`${name} introduction`}>
      {avatar ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={avatar} alt="" className="mb-4 h-16 w-16 rounded-full bg-background object-cover" />
      ) : null}
      <h1 className="text-xl font-bold text-foreground md:text-2xl">{name}</h1>
      {intro ? (
        <div className="mt-2 max-w-md text-[15px] leading-[1.7] text-muted-foreground md:text-base">
          <ChatMarkdown content={intro} navigateToBlock={() => {}} />
        </div>
      ) : null}
    </div>
  );
}
